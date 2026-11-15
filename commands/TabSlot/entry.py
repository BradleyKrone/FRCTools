import adsk.core
import adsk.fusion
import os
from types import SimpleNamespace
from ...lib import fusionAddInUtils as futil
from ... import config
from ..CamSetup import cam_data
from . import tab_slot_gen as gen

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_TabSlotDialog'
CMD_NAME = 'Tab & Slot'
CMD_Description = ('Add CNC tabs to a plate whose edge butts another plate, and cut matching '
                   'slots, with dogbone relief sized to the router bit')

# Edit command -- only reachable from the right-click menu of a Tab & Slot group/feature/plate.
EDIT_CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_TabSlotEdit'
EDIT_CMD_NAME = 'Edit Tab & Slot'
EDIT_CMD_Description = 'Change the settings of an existing Tab & Slot joint and rebuild it in place'

IS_PROMOTED = True
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

IN_TO_CM = gen.IN_TO_CM
CUSTOM_BIT = 'Custom'

# Bit dropdown label -> diameter (in), from the CAM Setup tool list.
BITS = {tool['description']: tool['diameter'] for tool in cam_data.TOOLS.values()}

local_handlers = []
ui_handlers = []            # the right-click (marking menu) hook, alive while the add-in runs

# Last-used values (inches), so the next run opens with them.
_last = {
    'count': 2,
    'tab_width': 1.0,
    'margin': 0.5,
    # 1/8" by default: a 1/4" bit can't cut the slot for a 1/8" plate.
    'bit': cam_data.TOOLS['1/8']['description'],
    'bit_dia': 0.125,
    'clear_w': 0.005,
    'clear_l': 0.010,
    'protrusion': 0.0,
    'corner_round': 0.03125,
}

PICK_PROMPT = 'Pick the tab plate(s) and the slot plate(s).'
PICK_JOINT = 'Pick the joint to edit'
EDIT_NOTE = 'Editing: no live preview -- click Rebuild to apply the changes to this joint.'
# Inputs that stay locked in the Edit dialog until a joint is chosen in the Joint list.
_EDIT_LOCKED = ('tab_body', 'slot_body', 'count', 'tab_width', 'margin', 'bit', 'bit_dia',
                'clear_w', 'clear_l', 'protrusion', 'corner_round')

# Problems with the current picks, worked out in inputChanged and used by validateInputs.
_state = {'problems': [PICK_PROMPT], 'blocking': [PICK_PROMPT]}

# (run id, data) found by the right-click menu, consumed by the Edit dialog.
_menu_target = None
CHOOSE = ('choose', None)   # right-click on something unidentifiable (a timeline group): pick in the dialog


def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)

    panel = config.get_frc_panel()
    control = panel.controls.addCommand(cmd_def)
    control.isPromotedByDefault = IS_PROMOTED
    control.isPromoted = IS_PROMOTED

    edit_def = ui.commandDefinitions.addButtonDefinition(EDIT_CMD_ID, EDIT_CMD_NAME, EDIT_CMD_Description, ICON_FOLDER)
    futil.add_handler(edit_def.commandCreated, edit_command_created)
    futil.add_handler(ui.markingMenuDisplaying, ui_marking_menu, local_handlers=ui_handlers)


def stop():
    panel = config.get_frc_panel()
    command_control = panel.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    if command_control:
        command_control.isPromoted = False
        command_control.deleteMe()

    if command_definition:
        command_definition.deleteMe()

    edit_def = ui.commandDefinitions.itemById(EDIT_CMD_ID)
    if edit_def:
        edit_def.deleteMe()

    global local_handlers, ui_handlers
    for handler in ui_handlers:
        try:
            ui.markingMenuDisplaying.remove(handler)
        except Exception:
            pass
    ui_handlers = []
    local_handlers = []


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.log(f'{CMD_NAME} Command Created Event')
    _build_dialog(args, dict(_last), edit=None)


def edit_command_created(args: adsk.core.CommandCreatedEventArgs):
    """The same dialog, filled in from the joint's stored settings, with both plates re-picked."""
    global _menu_target
    futil.log(f'{EDIT_CMD_NAME} Command Created Event')
    target, _menu_target = _menu_target, None
    all_runs = gen.list_runs(adsk.fusion.Design.cast(app.activeProduct))
    if not all_runs:
        ui.messageBox('There is no Tab & Slot joint in this design to edit.', EDIT_CMD_NAME)
        return
    if target is None or target[0] == 'choose':
        # Couldn't tell which joint was right-clicked (a timeline group, or a plate several joints
        # use): the user picks it. Never guess -- a guess edits the wrong joint without a word.
        runs = (target[1] if target is not None and target[1] else all_runs)
        if len(runs) == 1:
            run_id, data = runs[0]
            _build_dialog(args, _run_values(data), edit={'run_id': run_id, 'data': data, 'runs': runs})
        else:
            _build_dialog(args, dict(_last), edit={'run_id': None, 'data': None, 'runs': runs})
        return
    run_id, data = target
    _build_dialog(args, _run_values(data), edit={'run_id': run_id, 'data': data, 'runs': all_runs})


def _run_values(data) -> dict:
    """Dialog values (inches) from a run's stored data."""
    prm = data['params']
    vals = dict(_last)
    vals.update({k: prm[k] / IN_TO_CM for k in ('tab_width', 'margin', 'bit_dia', 'clear_w',
                                                 'clear_l', 'protrusion', 'corner_round')})
    vals['count'] = int(prm['count'])
    vals['bit'] = data.get('bit_label') if data.get('bit_label') in list(BITS) + [CUSTOM_BIT] else CUSTOM_BIT
    return vals


def _run_labels(runs) -> list:
    """Unique dropdown labels for the runs (group names, numbered when two match)."""
    labels, seen = [], {}
    for _, data in runs:
        name = data.get('group_name', CMD_NAME)
        seen[name] = seen.get(name, 0) + 1
        labels.append(name if seen[name] == 1 else f'{name} #{seen[name]}')
    return labels


def _lock_inputs(inputs, locked: bool):
    """Grey out the joint's inputs until a joint is chosen (Edit opened on an unknown joint)."""
    custom = inputs.itemById('bit').selectedItem.name == CUSTOM_BIT
    for input_id in _EDIT_LOCKED:
        inp = inputs.itemById(input_id)
        if inp is not None:
            inp.isEnabled = not locked and (input_id != 'bit_dia' or custom)


def _switch_run(inputs, index: int):
    """Edit dialog: load another run's settings and plates after the Joint dropdown changes."""
    runs = _state['edit']['runs']
    index -= _state.get('run_offset', 0)          # the "Pick the joint to edit" placeholder
    if index < 0:
        return
    _lock_inputs(inputs, False)
    run_id, data = runs[index]
    design = adsk.fusion.Design.cast(app.activeProduct)
    _state['edit'] = {'run_id': run_id, 'data': data, 'runs': runs}
    _state['edit_bodies'] = _resolve_plates(design, data)
    vals = _run_values(data)
    inputs.itemById('count').value = vals['count']
    for key in ('tab_width', 'margin', 'clear_w', 'clear_l', 'protrusion', 'corner_round'):
        inputs.itemById(key).value = vals[key] * IN_TO_CM
    for li in inputs.itemById('bit').listItems:
        li.isSelected = li.name == vals['bit']
    bit_dia = inputs.itemById('bit_dia')
    bit_dia.value = BITS.get(vals['bit'], vals['bit_dia']) * IN_TO_CM
    bit_dia.isEnabled = vals['bit'] == CUSTOM_BIT
    _select_plates(inputs, *_state['edit_bodies'])


def _select_plates(inputs, tabs, slots):
    """Put the run's plates back in the two pickers. addSelection goes to whichever selection
    input has FOCUS, not the one it is called on (seen live: all 8 plates landed in Tab Plates,
    Slot Plates stayed empty and Rebuild greyed out), so focus each picker before filling it."""
    for input_id, bodies in (('tab_body', tabs), ('slot_body', slots)):
        sel = inputs.itemById(input_id)
        sel.hasFocus = True
        sel.clearSelection()
        for body in bodies:
            if body is not None:
                sel.addSelection(body)
    inputs.itemById('tab_body').hasFocus = True


def _resolve_plates(design, data):
    """([tab plates], [slot plates]) of a run; an entry is None if that plate is gone."""
    return ([gen._resolve(design, t) for t in gen.tokens(data, 'tab')],
            [gen._resolve(design, t) for t in gen.tokens(data, 'slot')])


def _build_dialog(args, vals: dict, edit):
    cmd = args.command
    inputs = cmd.commandInputs
    run_offset = 0
    if edit is not None:
        cmd.okButtonText = 'Rebuild'
        runs = edit.get('runs') or []
        if len(runs) > 1 or edit['run_id'] is None:
            run_dd = inputs.addDropDownCommandInput('run', 'Joint', adsk.core.DropDownStyles.TextListDropDownStyle)
            run_dd.tooltip = 'Which Tab & Slot joint to edit'
            if edit['run_id'] is None:
                run_dd.listItems.add(PICK_JOINT, True)
                run_offset = 1
            for (rid, _), label in zip(runs, _run_labels(runs)):
                run_dd.listItems.add(label, rid == edit['run_id'])

    # Both pickers take any number of plates: every tab plate whose edge sits on a slot plate
    # becomes a joint (e.g. six dividers into one lid in a single run).
    tab_sel = inputs.addSelectionInput('tab_body', 'Tab Plates',
                                       'The plates whose edge sits on another plate -- they get the tabs. '
                                       'Pick as many as you like.')
    tab_sel.addSelectionFilter('SolidBodies')
    tab_sel.setSelectionLimits(1, 0)

    slot_sel = inputs.addSelectionInput('slot_body', 'Slot Plates',
                                        'The plates the edges sit on -- they get the slots. '
                                        'Pick as many as you like.')
    slot_sel.addSelectionFilter('SolidBodies')
    slot_sel.setSelectionLimits(1, 0)

    # Reset first: after a successful run `problems` is empty, and indexing it here threw,
    # leaving a dialog with only the two pickers (no handlers wired either).
    _state.clear()
    _state.update(problems=[PICK_PROMPT], blocking=[PICK_PROMPT], joint_key=None, joints=[], notes=[],
                  joint_clean=True, preview_live=False, edit=edit, edit_bodies=([], []),
                  run_offset=run_offset)
    if edit is not None and edit['data'] is not None:
        _state['edit_bodies'] = _resolve_plates(adsk.fusion.Design.cast(app.activeProduct), edit['data'])
    info = inputs.addTextBoxCommandInput('info', 'Detected', PICK_PROMPT, 4, True)
    info.isFullWidth = True

    inputs.addIntegerSpinnerCommandInput('count', 'Tab Count', 1, 50, 1, vals['count'])
    inputs.addValueInput('tab_width', 'Tab Width', 'in', futil.inchValue(vals['tab_width']))
    inputs.addValueInput('margin', 'End Margin', 'in', futil.inchValue(vals['margin']))

    bit_dd = inputs.addDropDownCommandInput('bit', 'Router Bit', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label in list(BITS) + [CUSTOM_BIT]:
        bit_dd.listItems.add(label, label == vals['bit'])
    bit_in = BITS.get(vals['bit'], vals['bit_dia'])
    bit_dia = inputs.addValueInput('bit_dia', 'Bit Diameter', 'in', futil.inchValue(bit_in))
    bit_dia.isEnabled = vals['bit'] == CUSTOM_BIT

    inputs.addValueInput('clear_w', 'Slot Width Clearance', 'in', futil.inchValue(vals['clear_w'])).tooltip = \
        'Extra gap on EACH side across the slot (plate thickness direction)'
    inputs.addValueInput('clear_l', 'Slot Length Clearance', 'in', futil.inchValue(vals['clear_l'])).tooltip = \
        'Extra gap at EACH end of the slot (along the joint)'
    inputs.addValueInput('protrusion', 'Tab Protrusion', 'in', futil.inchValue(vals['protrusion'])).tooltip = \
        'How far the tabs stick out past the slot plate (0 = flush; negative = stop short). Tabs always ' \
        'reach through the slot plate\'s thickness + this, so they follow a thickness change'
    inputs.addValueInput('corner_round', 'Corner Round', 'in', futil.inchValue(vals['corner_round'])).tooltip = \
        'Fillet on the sharp points beside each dogbone so the bit runs smoothly (0 = off)'

    futil.add_handler(cmd.execute,        command_execute,        local_handlers=local_handlers)
    futil.add_handler(cmd.executePreview, command_preview,        local_handlers=local_handlers)
    futil.add_handler(cmd.inputChanged,   command_input_changed,  local_handlers=local_handlers)
    futil.add_handler(cmd.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(cmd.destroy,        command_destroy,        local_handlers=local_handlers)
    if edit is not None:
        futil.add_handler(cmd.activate, command_activate, local_handlers=local_handlers)


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def _picks(inputs):
    """([picked tab plates], [picked slot plates])."""
    def picked(input_id):
        sel = inputs.itemById(input_id)
        return [sel.selection(i).entity for i in range(sel.selectionCount)]
    return picked('tab_body'), picked('slot_body')


def _current_picks(inputs):
    """_picks, except that while editing, the run's own plates picked in EITHER picker count as
    its saved tab/slot split. Fusion can drop a re-selected plate into the wrong picker (seen live:
    the edit check failed and Rebuild greyed out), and an edit only ever rebuilds on those plates."""
    tabs, slots = _picks(inputs)
    edit = _state.get('edit')
    if edit is not None and edit.get('run_id') is not None:
        e_tabs, e_slots = (list(filter(None, bodies)) for bodies in _state['edit_bodies'])
        # Seen live: the slot plates also landed in Tab Plates (8 + 2 picks for 8 plates).
        if e_tabs and e_slots and _same_bodies(_unique(tabs + slots), _unique(e_tabs + e_slots)):
            return e_tabs, e_slots
    return tabs, slots


def _unique(bodies) -> list:
    out = []
    for b in bodies:
        if not any(b == o for o in out):
            out.append(b)
    return out


def _same_bodies(a, b) -> bool:
    return len(a) == len(b) and all(any(x == y for y in b) for x in a)


def _params(inputs) -> gen.Params:
    bit_label = inputs.itemById('bit').selectedItem.name
    bit_dia = inputs.itemById('bit_dia').value
    if bit_label != CUSTOM_BIT:
        bit_dia = BITS[bit_label] * IN_TO_CM
    return gen.Params(
        count=inputs.itemById('count').value,
        tab_width=inputs.itemById('tab_width').value,
        margin=inputs.itemById('margin').value,
        bit_dia=bit_dia,
        clear_w=inputs.itemById('clear_w').value,
        clear_l=inputs.itemById('clear_l').value,
        protrusion=inputs.itemById('protrusion').value,
        corner_round=inputs.itemById('corner_round').value,
    )


def _remember(inputs):
    p = _params(inputs)
    _last.update(count=p.count, tab_width=p.tab_width / IN_TO_CM, margin=p.margin / IN_TO_CM,
                 bit=inputs.itemById('bit').selectedItem.name, bit_dia=p.bit_dia / IN_TO_CM,
                 clear_w=p.clear_w / IN_TO_CM, clear_l=p.clear_l / IN_TO_CM,
                 protrusion=p.protrusion / IN_TO_CM, corner_round=p.corner_round / IN_TO_CM)


def _part_name(body) -> str:
    """The picked plate's component name (its occurrence name without ':1'), else the body name."""
    occ = getattr(body, 'assemblyContext', None)
    return occ.component.name if occ is not None else body.name


def _plates_label(bodies) -> str:
    """'Battery_Box_Top', 'Battery_Dividers x6', or '3 plates' -- for the timeline group name."""
    names = []
    for b in bodies:
        if _part_name(b) not in names:
            names.append(_part_name(b))
    if len(bodies) == 1:
        return names[0]
    if len(names) == 1:
        return f'{names[0]} x{len(bodies)}'
    return f'{len(bodies)} plates'


def _group_timeline(design, start_marker: int, name: str):
    """Put every sketch and feature this run made into one named timeline group. Unlike
    futil.group_timeline_features, a failure is shown in the Text Command window with the reason."""
    timeline = design.timeline
    end_marker = timeline.markerPosition - 1
    if end_marker <= start_marker:
        return
    try:
        group = timeline.timelineGroups.add(start_marker, end_marker)
        group.name = name
        group.isCollapsed = True
    except Exception as e:
        futil.log(f'{CMD_NAME}: could not group timeline items {start_marker}..{end_marker}: {e}',
                  adsk.core.LogLevels.WarningLogLevel, force_console=True)


def _picks_key(tabs, slots):
    return (tuple(b.entityToken for b in tabs), tuple(b.entityToken for b in slots))


def _stored_joints(data):
    """The joints measured when the edited run was first built (the live plates now carry its
    tabs and slots, so they can't be re-measured until the rebuild rolls back past them)."""
    return [SimpleNamespace(length=jd['length'], t_a=jd['t_a'], t_b=jd['t_b'],
                            extra={'b_w_range': tuple(jd['b_w_range']), 'name': jd.get('name', 'joint')})
            for jd in gen.stored_joints(data)]


def _joints_for(tabs, slots, fresh: bool):
    """([joints], [notes]) for these picks. In inputChanged the previous preview's tabs/slots are
    still on the bodies (seen live: a 10 in joint measured 1.141 in -- a shoulder piece between
    tabs), so re-measure only when the picks change or `fresh` (executePreview, where Fusion has
    already rolled the old preview back); otherwise reuse the last clean measurement."""
    edit = _state.get('edit')
    if edit is not None and _same_bodies(tabs, _state['edit_bodies'][0]) and \
            _same_bodies(slots, _state['edit_bodies'][1]):
        _state['joint_clean'] = True
        return _stored_joints(edit['data']), []
    key = _picks_key(tabs, slots)
    if fresh or _state.get('joint_key') != key:
        _state['joint_key'] = key
        _state['joints'], _state['notes'] = gen.find_joints(tabs, slots)
        # A re-pick while a preview is up may have measured the preview's geometry.
        _state['joint_clean'] = fresh or not _state.get('preview_live', False)
    return _state['joints'], _state['notes']


def _span(values) -> str:
    lo, hi = min(values) / IN_TO_CM, max(values) / IN_TO_CM
    return f'{lo:.3f}' if hi - lo < 0.0005 else f'{lo:.3f}-{hi:.3f}'


def _refresh(inputs, fresh: bool = False):
    """Check the joints and layout, and show the result in the Detected box. Sets
    `_state['problems']` (all of them, shown) and `_state['blocking']` (what disables OK)."""
    problems, lines, hard = [], [], True
    tabs, slots = _current_picks(inputs)
    edit = _state.get('edit')
    if edit is not None and edit.get('run_id') is None:
        problems.append('First choose the joint to edit in the Joint list at the top -- '
                        'its settings and plates load, then they can be changed.')
    elif not tabs or not slots:
        problems.append(PICK_PROMPT)
    elif len(tabs) == 1 and len(slots) == 1 and tabs[0] == slots[0]:
        problems.append('Pick two different bodies.')
    elif not all(gen.is_editable(b) for b in tabs + slots):
        problems.append('One of the bodies is in a linked component and can\'t be edited.')
    elif edit is not None and not (_same_bodies(tabs, _state['edit_bodies'][0]) and
                                   _same_bodies(slots, _state['edit_bodies'][1])):
        problems.append('Editing rebuilds this joint on its original plates -- re-pick those, '
                        'or run Tab & Slot from the FRC panel for a new joint.')
    else:
        joints, notes = _joints_for(tabs, slots, fresh)
        if not joints:
            problems.append('No tab plate\'s edge sits flat on a slot plate\'s face. '
                            'Butt the edge against the face (T or L joint) and try again.')
        else:
            head = 'Joint' if len(joints) == 1 else f'{len(joints)} joints,'
            lines.append(f'{head} {_span([j.length for j in joints])} in, '
                         f'tab plate {_span([j.t_a for j in joints])} in, '
                         f'slot plate {_span([j.t_b for j in joints])} in')
            params = _params(inputs)
            if edit is None:
                problems.extend(gen.plan_units(joints)[1])
            problems.extend(gen.layout_problems(joints, params))
            for j in joints:
                for note in gen.warnings(j, params):
                    if note not in lines:
                        lines.append(note)
            lines.extend(notes)
        # Don't block on a measurement that may include an old preview: blocking would stop
        # the preview that re-measures it cleanly (executePreview only runs on valid inputs).
        hard = _state.get('joint_clean', True)
    if _state.get('edit') is not None:
        lines.append(EDIT_NOTE)
    _state['problems'] = problems
    _state['blocking'] = problems if hard else []
    # Problems first -- the box shows 3 lines and the reason the preview vanished must be visible.
    inputs.itemById('info').text = '\n'.join(problems + lines)


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def command_execute(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Execute Event')
    inputs = args.command.commandInputs
    design = adsk.fusion.Design.cast(app.activeProduct)
    edit = _state.get('edit')
    try:
        tabs, slots = _current_picks(inputs)
        params = _params(inputs)
        bit_label = inputs.itemById('bit').selectedItem.name
        if edit is not None:
            data = dict(edit['data'], bit_label=bit_label)
            report = gen.rebuild_run(design, edit['run_id'], [b.entityToken for b in tabs],
                                     [b.entityToken for b in slots], params, data)
        else:
            # Measure before anything changes; the whole batch is one group and one editable run.
            start_marker = design.timeline.markerPosition
            joints, _ = gen.find_joints(tabs, slots)
            report = gen.build_many(tabs, slots, params, constrain=True, joints=joints)
            used_tabs = [b for b in tabs if any(j.extra['tab'] == b for j in joints)]
            used_slots = [b for b in slots if any(j.extra['slot'] == b for j in joints)]
            group_name = f'{CMD_NAME} ({_plates_label(used_tabs)} -> {_plates_label(used_slots)})'
            _group_timeline(design, start_marker, group_name)
            gen.tag_run(report, gen.make_data(used_tabs, used_slots, params, joints, bit_label, group_name))
        _remember(inputs)
        loose = [name for name, ok in report.get('fully_constrained', {}).items() if not ok]
        if loose:
            futil.log(f'{CMD_NAME}: not fully constrained: {loose}', force_console=True)
    except Exception as e:
        futil.handle_error(CMD_NAME, show_message_box=False)
        args.executeFailed = True
        args.executeFailedMessage = str(e)


def command_preview(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    _state['preview_live'] = False
    try:
        # The old preview is gone here, so this is a clean re-measure for the Detected box too.
        _refresh(inputs, fresh=True)
        # Editing: the plates still carry the old tabs/slots, so there is nothing sensible to
        # preview on -- OK rolls back and rebuilds.
        if not _state['problems'] and _state.get('edit') is None:
            tabs, slots = _picks(inputs)
            gen.build_many(tabs, slots, _params(inputs), constrain=False, joints=_state['joints'])
            _state['preview_live'] = True
    except Exception:
        futil.handle_error(CMD_NAME, show_message_box=False)
    # Constraining is too slow for preview -- let execute rebuild the committed result.
    args.isValidResult = False


def command_activate(args: adsk.core.CommandEventArgs):
    """Edit only: re-pick the joint's plates. Must be here, not in commandCreated --
    addSelection does nothing while the inputs are still being built (LESSONS_LEARNED)."""
    inputs = args.command.commandInputs
    tabs, slots = _state.get('edit_bodies', ([], []))
    try:
        _select_plates(inputs, tabs, slots)
    except Exception:
        futil.log(f'{EDIT_CMD_NAME}: could not re-select the original plates')
    edit = _state.get('edit') or {}
    if edit.get('run_id') is None:
        _lock_inputs(inputs, True)
    if edit.get('run_id') is not None and (not tabs or not slots or any(b is None for b in tabs + slots)):
        inputs.itemById('info').text = ('The original plates could not be found -- they may have been '
                                        'deleted. Cancel and run Tab & Slot again.')
    else:
        _refresh(inputs)


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed = args.input
    inputs = changed.parentCommand.commandInputs
    if changed.id == 'run' and _state.get('edit') is not None:
        try:
            _switch_run(inputs, changed.selectedItem.index)
        except Exception:
            futil.handle_error(EDIT_CMD_NAME, show_message_box=False)
    # No auto-jump to the slot picker after the first tab plate: several tab plates can be picked.
    if changed.id == 'bit':
        label = inputs.itemById('bit').selectedItem.name
        bit_dia = inputs.itemById('bit_dia')
        bit_dia.isEnabled = label == CUSTOM_BIT
        if label != CUSTOM_BIT:
            bit_dia.value = BITS[label] * IN_TO_CM
    try:
        _refresh(inputs)
    except Exception:
        futil.handle_error(CMD_NAME, show_message_box=False)


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    args.areInputsValid = not _state['blocking']


def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Destroy Event')
    global local_handlers
    local_handlers = []
    _state['edit'] = None
    _state['edit_bodies'] = ([], [])


# ---------------------------------------------------------------------------
# Right-click menu
# ---------------------------------------------------------------------------

def _find_run(entity):
    """(run id, data) for a right-clicked Tab & Slot group / sketch / feature / plate, or None."""
    run_id, data = gen.read_run(entity)
    if run_id:
        return run_id, data
    if adsk.fusion.BRepBody.cast(entity) is not None:
        # A plate can belong to several joints (e.g. one back plate slotted for three sides):
        # one -> edit it; several -> let the dialog ask which.
        design = adsk.fusion.Design.cast(app.activeProduct)
        runs = gen.runs_for_body(design, entity)
        if len(runs) == 1:
            return runs[0]
        if runs:
            return ('choose', runs)
    return None


def ui_marking_menu(args: adsk.core.MarkingMenuEventArgs):
    """Show 'Edit Tab & Slot' in the right-click menu when a Tab & Slot run is under the cursor.
    The item always stays in the menu (duplicate-guarded) and only its visibility changes --
    the same pattern as PartsGen's 'Edit with PartsGen'."""
    global _menu_target
    product = app.activeProduct
    if product is None or product.objectType != adsk.fusion.Design.classType():
        return
    controls = args.linearMarkingMenu.controls
    item = controls.itemById(EDIT_CMD_ID)
    if not item:
        edit_def = ui.commandDefinitions.itemById(EDIT_CMD_ID)
        if not edit_def:
            return
        try:
            item = controls.addCommand(edit_def, '', False)
        except Exception:
            futil.log(f'{EDIT_CMD_NAME}: could not add the right-click menu item')
            return

    # Everything under the cursor. A right-clicked timeline GROUP comes through as an entity of
    # None (seen live) and the API has no "selected" flag on timeline items, so it can't be
    # identified -- see the fallback below.
    entities, unknown = [], False
    for ent in list(args.selectedEntities or []):
        if ent is None:
            unknown = True
        else:
            entities.append(ent)
    for i in range(ui.activeSelections.count):
        try:
            ent = ui.activeSelections.item(i).entity
        except Exception:
            ent = None
        if ent is None:
            unknown = True
        else:
            entities.append(ent)

    found = None
    for ent in entities:
        try:
            found = _find_run(ent)
        except Exception:
            found = None
        if found:
            break
    if not found and unknown:
        # Probably a timeline group: offer Edit if the design has any Tab & Slot joint, and let
        # the dialog's Joint dropdown pick which one.
        try:
            if gen.list_runs(adsk.fusion.Design.cast(product)):
                found = CHOOSE
        except Exception:
            found = None
    if found:
        _menu_target = found
    item.isVisible = found is not None
