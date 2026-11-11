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

PICK_PROMPT = 'Pick the tab plate and the slot plate.'
EDIT_NOTE = 'Editing: no live preview -- click OK to rebuild this joint in place.'

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
    runs = gen.list_runs(adsk.fusion.Design.cast(app.activeProduct))
    if target == CHOOSE or target is None:
        target = runs[0] if runs else None
    if target is None:
        ui.messageBox('There is no Tab & Slot joint in this design to edit.', EDIT_CMD_NAME)
        return
    run_id, data = target
    _build_dialog(args, _run_values(data), edit={'run_id': run_id, 'data': data, 'runs': runs})


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


def _switch_run(inputs, index: int):
    """Edit dialog: load another run's settings and plates after the Joint dropdown changes."""
    runs = _state['edit']['runs']
    run_id, data = runs[index]
    design = adsk.fusion.Design.cast(app.activeProduct)
    _state['edit'] = {'run_id': run_id, 'data': data, 'runs': runs}
    _state['edit_bodies'] = (gen._resolve(design, data['tab_token']), gen._resolve(design, data['slot_token']))
    vals = _run_values(data)
    inputs.itemById('count').value = vals['count']
    for key in ('tab_width', 'margin', 'clear_w', 'clear_l', 'protrusion', 'corner_round'):
        inputs.itemById(key).value = vals[key] * IN_TO_CM
    for li in inputs.itemById('bit').listItems:
        li.isSelected = li.name == vals['bit']
    bit_dia = inputs.itemById('bit_dia')
    bit_dia.value = BITS.get(vals['bit'], vals['bit_dia']) * IN_TO_CM
    bit_dia.isEnabled = vals['bit'] == CUSTOM_BIT
    tab, slot = _state['edit_bodies']
    for input_id, body in (('tab_body', tab), ('slot_body', slot)):
        sel = inputs.itemById(input_id)
        sel.clearSelection()
        if body is not None:
            sel.addSelection(body)


def _build_dialog(args, vals: dict, edit):
    cmd = args.command
    inputs = cmd.commandInputs
    if edit is not None:
        cmd.okButtonText = 'Rebuild'
        runs = edit.get('runs') or []
        if len(runs) > 1:
            run_dd = inputs.addDropDownCommandInput('run', 'Joint', adsk.core.DropDownStyles.TextListDropDownStyle)
            run_dd.tooltip = 'Which Tab & Slot joint to edit'
            for (rid, _), label in zip(runs, _run_labels(runs)):
                run_dd.listItems.add(label, rid == edit['run_id'])

    tab_sel = inputs.addSelectionInput('tab_body', 'Tab Plate',
                                       'The plate whose edge sits on the other plate -- it gets the tabs')
    tab_sel.addSelectionFilter('SolidBodies')
    tab_sel.setSelectionLimits(1, 1)

    slot_sel = inputs.addSelectionInput('slot_body', 'Slot Plate',
                                        'The plate the edge sits on -- it gets the slots')
    slot_sel.addSelectionFilter('SolidBodies')
    slot_sel.setSelectionLimits(1, 1)

    # Reset first: after a successful run `problems` is empty, and indexing it here threw,
    # leaving a dialog with only the two pickers (no handlers wired either).
    _state.clear()
    _state.update(problems=[PICK_PROMPT], blocking=[PICK_PROMPT], joint_key=None, joint=None,
                  joint_clean=True, preview_live=False, edit=edit, edit_bodies=(None, None))
    if edit is not None:
        data = edit['data']
        design = adsk.fusion.Design.cast(app.activeProduct)
        _state['edit_bodies'] = (gen._resolve(design, data['tab_token']),
                                 gen._resolve(design, data['slot_token']))
    info = inputs.addTextBoxCommandInput('info', 'Detected', PICK_PROMPT, 3, True)
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
        'How far the tabs stick out past the slot plate (0 = flush)'
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
    tab_sel, slot_sel = inputs.itemById('tab_body'), inputs.itemById('slot_body')
    tab = tab_sel.selection(0).entity if tab_sel.selectionCount > 0 else None
    slot = slot_sel.selection(0).entity if slot_sel.selectionCount > 0 else None
    return tab, slot


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


def _pair_key(tab, slot):
    return (tab.entityToken, slot.entityToken)


def _stored_joint(data):
    """The joint measured when the edited run was first built (the live plates now carry its
    tabs and slots, so they can't be re-measured until the rebuild rolls back past them)."""
    jd = data['joint']
    return SimpleNamespace(length=jd['length'], t_a=jd['t_a'], t_b=jd['t_b'],
                           extra={'b_w_range': tuple(jd['b_w_range'])})


def _joint_for(tab, slot, fresh: bool):
    """The joint for these picks. In inputChanged the previous preview's tabs/slots are still on
    the bodies (seen live: a 10 in joint measured 1.141 in -- a shoulder piece between tabs), so
    re-measure only when the picks change or `fresh` (executePreview, where Fusion has already
    rolled the old preview back); otherwise reuse the last clean measurement."""
    edit = _state.get('edit')
    if edit is not None and (tab, slot) == _state.get('edit_bodies'):
        _state['joint_clean'] = True
        return _stored_joint(edit['data'])
    key = _pair_key(tab, slot)
    if fresh or _state.get('joint_key') != key:
        _state['joint_key'] = key
        _state['joint'] = gen.find_joint(tab, slot)
        # A re-pick while a preview is up may have measured the preview's geometry.
        _state['joint_clean'] = fresh or not _state.get('preview_live', False)
    return _state['joint']


def _refresh(inputs, fresh: bool = False):
    """Check the joint and layout, and show the result in the Detected box. Sets
    `_state['problems']` (all of them, shown) and `_state['blocking']` (what disables OK)."""
    problems, lines, hard = [], [], True
    tab, slot = _picks(inputs)
    if tab is None or slot is None:
        problems.append(PICK_PROMPT)
    elif tab == slot:
        problems.append('Pick two different bodies.')
    elif not gen.is_editable(tab) or not gen.is_editable(slot):
        problems.append('One of the bodies is in a linked component and can\'t be edited.')
    elif _state.get('edit') is not None and (tab, slot) != _state.get('edit_bodies'):
        problems.append('Editing rebuilds this joint on its original plates -- re-pick those two, '
                        'or run Tab & Slot from the FRC panel for a new joint.')
    else:
        joint = _joint_for(tab, slot, fresh)
        if joint is None:
            problems.append('The tab plate\'s edge doesn\'t sit flat on the slot plate\'s face. '
                            'Butt the edge against the face (T or L joint) and try again.')
        else:
            lines.append(f'Joint {joint.length / IN_TO_CM:.3f} in, tab plate {joint.t_a / IN_TO_CM:.3f} in, '
                         f'slot plate {joint.t_b / IN_TO_CM:.3f} in')
            params = _params(inputs)
            _, layout_problems = gen.layout_tabs(joint, params)
            problems.extend(layout_problems)
            lines.extend(gen.warnings(joint, params))
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
        tab, slot = _picks(inputs)
        params = _params(inputs)
        bit_label = inputs.itemById('bit').selectedItem.name
        if edit is not None:
            data = dict(edit['data'], bit_label=bit_label)
            report = gen.rebuild_run(design, edit['run_id'], tab.entityToken, slot.entityToken, params, data)
        else:
            start_marker = design.timeline.markerPosition
            report = gen.build(tab, slot, params, constrain=True)
            group_name = f'{CMD_NAME} ({_part_name(tab)} -> {_part_name(slot)})'
            _group_timeline(design, start_marker, group_name)
            gen.tag_run(report, gen.make_data(tab, slot, params, report['joint'], bit_label, group_name))
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
            tab, slot = _picks(inputs)
            gen.build(tab, slot, _params(inputs), constrain=False, joint=_state['joint'])
            _state['preview_live'] = True
    except Exception:
        futil.handle_error(CMD_NAME, show_message_box=False)
    # Constraining is too slow for preview -- let execute rebuild the committed result.
    args.isValidResult = False


def command_activate(args: adsk.core.CommandEventArgs):
    """Edit only: re-pick the joint's two plates. Must be here, not in commandCreated --
    addSelection does nothing while the inputs are still being built (LESSONS_LEARNED)."""
    inputs = args.command.commandInputs
    tab, slot = _state.get('edit_bodies', (None, None))
    try:
        if tab is not None:
            inputs.itemById('tab_body').addSelection(tab)
        if slot is not None:
            inputs.itemById('slot_body').addSelection(slot)
    except Exception:
        futil.log(f'{EDIT_CMD_NAME}: could not re-select the original plates')
    if tab is None or slot is None:
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
    if changed.id == 'tab_body':
        # Once the tab plate is picked, move straight on to the slot plate.
        slot_sel = inputs.itemById('slot_body')
        if inputs.itemById('tab_body').selectionCount == 1 and slot_sel.selectionCount == 0:
            slot_sel.hasFocus = True
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
    _state['edit_bodies'] = (None, None)


# ---------------------------------------------------------------------------
# Right-click menu
# ---------------------------------------------------------------------------

def _find_run(entity):
    """(run id, data) for a right-clicked Tab & Slot group / sketch / feature / plate, or None."""
    run_id, data = gen.read_run(entity)
    if run_id:
        return run_id, data
    if adsk.fusion.BRepBody.cast(entity) is not None:
        design = adsk.fusion.Design.cast(app.activeProduct)
        run_id, data = gen.read_run_for_body(design, entity)
        if run_id:
            return run_id, data
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
