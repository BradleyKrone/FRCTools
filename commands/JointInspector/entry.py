import adsk.core
import adsk.fusion
import os
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_JointInspectorDialog'
CMD_NAME = 'Joint Inspector'
CMD_Description = ('Select a body and see every joint that '
                    'touches it -- pick one to highlight what it connects to and which face/edge/point '
                    'each side is attached to, right in the viewport.')

# Specify that the command will be promoted to the panel.
IS_PROMOTED = False

# Resource location for command icons.
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected. Cleared on every command_destroy
# (rewired fresh in the next command_created), unlike _addin_handlers below.
local_handlers = []

# Handlers that must survive across multiple open/close cycles of the command
# dialog -- currently just the custom-event handler wired once in start().
# Kept separate from local_handlers, which command_destroy() clears every time
# the dialog closes.
_addin_handlers = []

# (joint, matched_side) pairs for the currently selected target, rebuilt whenever
# target_entity changes. matched_side is 0 or 1 -- which of occurrenceOne/
# occurrenceTwo on the joint corresponds to "this side" (the selected target).
_current_matches = []

# True when target_entity resolved to a Component with multiple occurrence
# instances and there was no assemblyContext available to tell which one was
# actually picked -- OK is disabled rather than silently guessing.
_target_ambiguous = False

# CustomGraphicsGroup drawn on the root component to show the currently
# selected joint's connection -- rebuilt every time the joint selection
# changes, torn down in command_destroy. Kept as a plain module global
# (not local_handlers) since it isn't an event handler.
_highlight_group = None

# Index into _current_matches of the joint the table currently shows selected, or -1.
# The highlight is drawn from executePreview rather than straight from
# inputChanged: Fusion tears down every CustomGraphicsGroup on the design each
# time it rebuilds the command preview (confirmed live -- the group went
# isValid=False and rootComponent.customGraphicsGroups.count dropped to 0
# between two validateInputs calls, with no _clear_highlight() of ours in
# between). Graphics drawn from inputChanged therefore flash up and vanish on
# the very next rebuild; executePreview is re-invoked after each rebuild, so
# drawing there is what makes the highlight stick.
_selected_idx = -1

# Guards the loop in _set_selected_row() that unchecks every other row's
# checkbox -- without this, each of those programmatic .value = False writes
# would recurse straight back into _command_input_changed's 'joint_row_'
# branch.
_syncing_rows = False

# [(component, its original .opacity), ...] for every component this session
# has ghosted via the 'ghost_others' checkbox, and
# {fullPathName: (occurrence, its original .isLightBulbOn), ...} for every
# individual occurrence it has hidden outright. Two mechanisms, because
# neither alone can express "show only what this joint connects" in a real
# FRC assembly:
#
# * Component.opacity is the ONLY settable opacity in the API
#   (Occurrence.visibleOpacity is the read-only *effective* value, and its own
#   doc says "To set the opacity use the opacity property of the Component
#   object"). It dims every occurrence referencing that component *and*
#   everything nested inside those, so it cannot tell two instances of a
#   shared part apart. Ghosting a component whenever *any* of its occurrences
#   was unrelated is what shipped first, and it ghosted the user's entire
#   194-occurrence shooter assembly: the picked part (Thunder Hex 0.375 v7, 4
#   occurrences) and the part its joint connected to (Side_Plate, 2
#   occurrences) are both multi-instance, so both sides of the joint dimmed
#   along with everything else. 132 of that design's 194 occurrences belong to
#   multi-instance components, so almost nothing survived the rule.
# * Occurrence.isLightBulbOn is genuinely per-occurrence -- it is the browser
#   light bulb beside each individual instance -- but it hides rather than
#   dims, losing the surrounding context a ghost preserves.
#
# So each is used where it is right (see _apply_ghosting): opacity dims whole
# components that are entirely unrelated, isLightBulbOn hides only the
# unrelated instances of a component that is *also* on the joint somewhere.
#
# Occurrence.appearance is deliberately NOT used, even though it is the one
# mechanism that could genuinely dim a single occurrence: both a direct write
# from inside this command's event handlers, and the same write deferred to a
# CustomEvent handler (the escape hatch _EDIT_JOINT_EVENT_ID uses for a
# different "can't do X from inside this event" restriction), were confirmed
# live to break things -- the direct write throws "Cannot modify the design
# from a read-only context", and even the deferred version destabilizes the
# still-open target_entity SelectionCommandInput, causing it to lose its
# selection a moment after being set (confirmed live, reported by the user).
#
# Identity: hidden occurrences are keyed by Occurrence.fullPathName, a plain
# reliable string. Ghosted components can't be keyed at all -- Component has
# no usable hash, and its entityToken collides constantly (57 collisions
# across unrelated components in the same 194-occurrence assembly, e.g.
# "44x v9" and "4inch_Fairline v2" reporting the identical token) -- but its
# `==` is reliable, so that list is searched with a linear `==` scan (see
# _find_ghosted()). Assemblies this tool deals with (tens to a few hundred
# occurrences) make the O(n) scan negligible for a UI action.
_ghosted_components = []
_hidden_occurrences = {}

# The joint to select/reveal once this command finishes closing, or None. Set
# when the user clicks 'edit_joint_btn'; Fusion cannot have two command
# dialogs open at once, so the handoff happens from command_destroy(), after
# this command has fully torn down.
#
# This only selects and reveals the joint (positions the timeline at it and
# selects it, so the user's very next action -- double-clicking it in the
# browser -- opens Fusion's real Edit Joint dialog). Confirmed live that there
# is no way to make that dialog itself open pre-loaded with an existing
# joint's data via the public API: selecting the Joint (with or without
# rolling the timeline first) and executing 'EditJointAssembleCmd' -- the
# built-in "Edit Joint " command definition -- always opened it blank, as if
# creating a new joint, never bound to the one that was selected. Fusion's own
# double-click-to-edit for a joint isn't exposed as a matching, scriptable API
# call.
_joint_to_edit = None

# Custom event used to defer closing this command + selecting the joint until
# *after* the button click that requests it has finished processing. Both
# Command.doExecute() and ui.terminateActiveCommand() raise "can not terminate
# command during a command event" when called directly from an input-changed
# handler (confirmed live) -- firing a CustomEvent and doing the actual work
# in its handler is the standard workaround, since a fired custom event is
# queued and only runs once Fusion is idle, outside the original event's call
# stack.
_EDIT_JOINT_EVENT_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_JointInspectorEditJointEvent'
_edit_joint_event = None

_THIS_SIDE_COLOR = (0, 200, 60)
# Deliberately NOT blue: Fusion paints the target body with its own blue
# selection highlight (the body stays selected in `target_entity` the whole
# time the dialog is open), which swamped both a 0.35-opacity green "this
# side" wash and a blue "other side" one -- every tube came out blue-ish and
# the highlight read as "nothing happened". Orange is the one strong colour
# that cannot be confused with Fusion's selection blue.
_OTHER_SIDE_COLOR = (255, 130, 0)
_CONNECTOR_COLOR = (230, 0, 200)

# Custom graphics are depth-tested against the model and Fusion offers no
# "draw on top" flag (the whole CustomGraphicsEntity API is deleteMe /
# get|setOpacity / isVisible / isSelectable / depthPriority / viewScale /
# viewPlacement -- `depthPriority` only orders custom graphics against *each
# other*, not against solid bodies). So a joint attached to a face or edge
# that points away from the camera gets drawn perfectly and is still totally
# invisible, buried inside the solid. Every side therefore also gets a
# translucent whole-body wash (too big to hide) and a view-scaled origin
# marker (constant pixel size, so it never shrinks to an unnoticeable speck).
_BODY_OPACITY = 0.65
_EXACT_OPACITY = 0.9
_MARKER_PIXELS_THIS = 22.0
_MARKER_PIXELS_OTHER = 13.0

# Relative draw order *within* the highlight group: body wash at the bottom,
# then the exact face/edge, then the origin markers on top.
_DEPTH_BODY = 1
_DEPTH_EXACT = 10
_DEPTH_MARKER = 100

# How transparent a ghosted (non-participating) component becomes. Not 0.0:
# fully invisible would hide context (e.g. what a ghosted part would collide
# with), a faint wash is enough to make the highlighted pair pop.
_GHOST_OPACITY = 0.1


# Executed when add-in is run.
def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)

    submenu = config.get_solid_submenu()
    control = submenu.controls.addCommand(cmd_def)
    control.isPromoted = IS_PROMOTED

    global _edit_joint_event
    # Guard against a leftover registration from a prior run/reload that
    # didn't get a matching stop() (e.g. after a crash) -- registerCustomEvent
    # returns None rather than raising if the id is already taken.
    try:
        app.unregisterCustomEvent(_EDIT_JOINT_EVENT_ID)
    except Exception:
        pass
    _edit_joint_event = app.registerCustomEvent(_EDIT_JOINT_EVENT_ID)
    futil.add_handler(_edit_joint_event, _on_edit_joint_event, local_handlers=_addin_handlers)


# Executed when add-in is stopped.
def stop():
    submenu = config.get_solid_submenu()
    command_control = submenu.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    if command_control:
        command_control.isPromoted = False
        command_control.deleteMe()

    if command_definition:
        command_definition.deleteMe()

    global local_handlers, _addin_handlers, _edit_joint_event
    local_handlers = []
    _addin_handlers = []
    try:
        app.unregisterCustomEvent(_EDIT_JOINT_EVENT_ID)
    except Exception:
        pass
    _edit_joint_event = None
    # Best-effort cleanup of a custom event a previous version of this file
    # registered (an appearance-based ghosting approach, since reverted --
    # see LESSONS_LEARNED.md); harmless no-op once nothing has it registered.
    try:
        app.unregisterCustomEvent(f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_JointInspectorGhostingEvent')
    except Exception:
        pass


# Called when the user clicks the button -- builds the command dialog.
def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.log(f'{CMD_NAME} Command Created Event')

    inputs = args.command.commandInputs

    targetInp = inputs.addSelectionInput(
        'target_entity', 'Component / Body',
        'Select a body to see the joints connected to it.'
    )
    # Mixing a whole-body filter ('SolidBodies') with sub-entity filters
    # ('Faces'/'Edges') on the same input made every click select nothing at
    # all (verified live) -- every other selection input in this codebase only
    # ever combines filters from one family (e.g. CCDistance's three Sketch*
    # filters together), never a body-level filter with a face/edge-level one.
    # A single 'SolidBodies' filter is the exact pattern already proven to
    # work standalone in Tubify/Lighten. _resolve_target() already walks up
    # from a picked body to its owning Occurrence via assemblyContext, so
    # clicking a body still resolves the right component.
    targetInp.addSelectionFilter('SolidBodies')
    targetInp.setSelectionLimits(1, 1)

    inputs.addTextBoxCommandInput('target_info', '', 'Select a body.', 2, True)

    ghostInp = inputs.addBoolValueInput(
        'ghost_others', 'Ghost unrelated components', True,
        '', True
    )
    # Worth spelling out, because the hidden instances are the surprising half:
    # only opacity can ghost, and opacity is shared by every instance of a
    # component (see the _ghosted_components comment), so a part that is on the
    # joint at one instance has to have its other instances hidden outright.
    ghostInp.tooltip = (
        'Dim everything the selected joint does not connect. Other instances of a part '
        'that the joint does use are hidden rather than dimmed -- Fusion shares one opacity '
        'across every instance of a component, so dimming them would dim the joint\'s own part too.'
    )

    # A persistently visible, clickable list of joints instead of a dropdown --
    # one row per joint, each a checkbox in column 0 plus a read-only text
    # label in column 1 carrying the joint's name; _set_selected_row() enforces
    # that only one checkbox is ever checked at a time. Two separate controls
    # rather than the checkbox's own label text, because a BoolValueCommandInput
    # placed in a TableCommandInput was confirmed live to render only the
    # checkbox glyph -- its text argument never shows inside a table cell.
    # Rows are (re)built in _command_input_changed() once a target body
    # resolves to some joints.
    jointTableInp = inputs.addTableCommandInput('joint_table', 'Joints', 2, '1:4')
    jointTableInp.isEnabled = False

    inputs.addTextBoxCommandInput('joint_info', '', 'No joint selected.', 8, True)

    editJointBtn = inputs.addBoolValueInput('edit_joint_btn', 'Edit Joint', False, '', False)
    editJointBtn.isEnabled = False

    global _current_matches, _target_ambiguous, _selected_idx, _joint_to_edit, _syncing_rows
    global _ghosted_components, _hidden_occurrences
    _current_matches = []
    _target_ambiguous = False
    _selected_idx = -1
    _joint_to_edit = None
    _syncing_rows = False
    _ghosted_components = []
    _hidden_occurrences = {}

    # Wire up event handlers. Nothing here creates geometry, but executePreview
    # is still required: it is the only place custom graphics survive Fusion's
    # preview rebuilds (see _selected_idx).
    futil.add_handler(args.command.execute,        command_execute,        local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview,        local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged,   command_input_changed,  local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy,        command_destroy,        local_handlers=local_handlers)


# ---------------------------------------------------------------------------
# Reactive UI
# ---------------------------------------------------------------------------

def command_input_changed(args: adsk.core.InputChangedEventArgs):
    try:
        _command_input_changed(args)
    except Exception:
        futil.handle_error(f'{CMD_NAME} command_input_changed', show_message_box=True)


def _command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed = args.input
    inputs = args.inputs
    global _current_matches, _target_ambiguous

    if changed.id == 'target_entity':
        _reset_joint_ui(inputs)

        targetInp: adsk.core.SelectionCommandInput = inputs.itemById('target_entity')
        targetInfoInp: adsk.core.TextBoxCommandInput = inputs.itemById('target_info')

        if targetInp.selectionCount == 0:
            _target_ambiguous = False
            targetInfoInp.text = 'Select a body.'
            return

        entity = targetInp.selection(0).entity
        target_occ, ambiguous = _resolve_target(entity)
        _target_ambiguous = ambiguous

        if ambiguous:
            targetInfoInp.formattedText = (
                '<div style="color:#b00020">Multiple instances of this component exist and the '
                'exact one picked could not be determined. Select it from the browser tree, or '
                'pick a face/edge on it instead.</div>'
            )
            return

        target_label = target_occ.fullPathName if target_occ is not None else 'Ground / Root Component'
        _current_matches = _find_joint_matches(target_occ)

        if not _current_matches:
            targetInfoInp.text = f'{target_label} -- no joints found.'
            return

        targetInfoInp.text = f'{target_label} -- {len(_current_matches)} joint(s) found.'

        jointTableInp: adsk.core.TableCommandInput = inputs.itemById('joint_table')
        jointTableInp.clear()
        for i, (joint, matched_side) in enumerate(_current_matches):
            other_occ = _other_side(joint, matched_side)
            other_label = other_occ.fullPathName if other_occ is not None else 'Ground'
            tag = ' [As-built]' if _is_as_built(joint) else ''
            label = f'{_joint_name(joint)}{tag}  ->  {other_label}'
            rowInp = inputs.addBoolValueInput(f'joint_row_{i}', '', True, '', i == 0)
            labelInp = inputs.addTextBoxCommandInput(f'joint_label_{i}', '', label, 1, True)
            jointTableInp.addCommandInput(rowInp, i, 0)
            jointTableInp.addCommandInput(labelInp, i, 1)
        jointTableInp.isEnabled = True

        _render_joint_info(inputs, 0)

    elif changed.id.startswith('joint_row_'):
        idx = int(changed.id[len('joint_row_'):])
        if changed.value:
            _set_selected_row(inputs, idx)
            _render_joint_info(inputs, idx)
        elif _selected_idx == idx:
            # The user unchecked the currently-active row -- nothing selected.
            _render_joint_info(inputs, -1)

    elif changed.id == 'ghost_others':
        _update_ghosting(inputs)

    elif changed.id == 'edit_joint_btn':
        global _joint_to_edit
        if 0 <= _selected_idx < len(_current_matches):
            _joint_to_edit = _current_matches[_selected_idx][0]
            # Can't close this command from here directly -- doExecute()/
            # terminateActiveCommand() both raise "can not terminate command
            # during a command event" when called from inside inputChanged
            # (confirmed live). Fire a custom event instead: Fusion queues it
            # and runs the handler once idle, outside this event's call
            # stack, where closing the command and selecting the joint is safe.
            app.fireCustomEvent(_EDIT_JOINT_EVENT_ID)


def _set_selected_row(inputs: adsk.core.CommandInputs, idx: int):
    """Uncheck every joint_row_* checkbox except row `idx`, guarded against
    re-entrantly firing _command_input_changed for each row it unchecks."""
    global _syncing_rows
    if _syncing_rows:
        return
    _syncing_rows = True
    try:
        for i in range(len(_current_matches)):
            if i == idx:
                continue
            rowInp: adsk.core.BoolValueCommandInput = inputs.itemById(f'joint_row_{i}')
            if rowInp is not None:
                rowInp.value = False
    finally:
        _syncing_rows = False


def _reset_joint_ui(inputs: adsk.core.CommandInputs):
    global _current_matches, _selected_idx
    _current_matches = []
    _selected_idx = -1
    jointTableInp: adsk.core.TableCommandInput = inputs.itemById('joint_table')
    jointTableInp.clear()
    jointTableInp.isEnabled = False
    inputs.itemById('joint_info').text = 'No joint selected.'
    inputs.itemById('edit_joint_btn').isEnabled = False
    _clear_highlight()
    _clear_ghosting()
    _refresh_viewport()


def _render_joint_info(inputs: adsk.core.CommandInputs, idx: int):
    """Update the dialog text and record which joint is selected.

    Deliberately does NOT draw -- command_preview() owns the viewport, because
    only graphics created there survive Fusion's preview rebuilds.
    """
    global _selected_idx
    jointInfoInp: adsk.core.TextBoxCommandInput = inputs.itemById('joint_info')
    editJointBtn: adsk.core.BoolValueCommandInput = inputs.itemById('edit_joint_btn')

    if idx < 0 or idx >= len(_current_matches):
        _selected_idx = -1
        _clear_highlight()
        _clear_ghosting()
        jointInfoInp.text = 'No joint selected.'
        editJointBtn.isEnabled = False
        _refresh_viewport()
        return

    _selected_idx = idx
    editJointBtn.isEnabled = True

    joint, matched_side = _current_matches[idx]
    other_occ = _other_side(joint, matched_side)
    other_label = other_occ.fullPathName if other_occ is not None else 'Ground / Root Component'
    type_label = 'As-built joint' if _is_as_built(joint) else 'Joint'

    legend = (
        f'<b>{_joint_name(joint)}</b> ({type_label})<br>'
        f'Connects to: <b>{other_label}</b><br><br>'
        f'<span style="color:rgb{_OTHER_SIDE_COLOR}">&#9632;</span> <b>Orange</b> = the body this '
        f'joint connects to<br>'
        f'<span style="color:rgb{_THIS_SIDE_COLOR}">&#9632;</span> Green = this side (the body you '
        f'picked also keeps Fusion&apos;s own blue selection highlight)'
    )

    jointInfoInp.formattedText = legend
    _update_ghosting(inputs)


def command_preview(args: adsk.core.CommandEventArgs):
    """Draw the selected joint's highlight.

    Fusion calls this after every preview rebuild -- which is also what wipes
    the previous CustomGraphicsGroup -- so redrawing unconditionally here is
    what keeps the highlight on screen instead of flashing once and vanishing.
    """
    try:
        if 0 <= _selected_idx < len(_current_matches):
            joint, matched_side = _current_matches[_selected_idx]
            _draw_joint_highlight(joint, matched_side)
        else:
            _clear_highlight()
            _refresh_viewport()
    except Exception:
        # Never fail silently here: a swallowed exception in the redraw path
        # once looked exactly like "the tool just stopped working".
        futil.handle_error(f'{CMD_NAME} command_preview', show_message_box=False)

    # Nothing is being built -- keep the command in preview mode so Fusion
    # doesn't treat this as a computed result.
    args.isValidResult = False


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    try:
        inputs = args.inputs
        targetInp: adsk.core.SelectionCommandInput = inputs.itemById('target_entity')
        args.areInputsValid = targetInp.selectionCount == 1 and not _target_ambiguous
    except Exception:
        futil.handle_error(f'{CMD_NAME} command_validate_input', show_message_box=False)
        args.areInputsValid = False


def command_execute(args: adsk.core.CommandEventArgs):
    try:
        if 0 <= _selected_idx < len(_current_matches):
            joint, _ = _current_matches[_selected_idx]
            futil.log(f'{CMD_NAME}: last inspected "{_joint_name(joint)}"')
    except Exception:
        futil.handle_error(f'{CMD_NAME} command_execute', show_message_box=True)


def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers, _current_matches, _target_ambiguous, _selected_idx
    local_handlers = []
    _current_matches = []
    _target_ambiguous = False
    _selected_idx = -1
    _clear_highlight()
    _clear_ghosting()


def _on_edit_joint_event(args: adsk.core.CustomEventArgs):
    """Runs once Fusion is idle, after the 'edit_joint_btn' click that fired
    this event has finished processing -- see _EDIT_JOINT_EVENT_ID for why
    this can't happen directly in that click's own inputChanged handler.
    """
    global _joint_to_edit
    joint = _joint_to_edit
    _joint_to_edit = None
    if joint is None:
        return

    try:
        ui.terminateActiveCommand()
    except Exception:
        futil.handle_error(f'{CMD_NAME}: closing Joint Inspector for edit', show_message_box=False)

    _reveal_joint_for_edit(joint)


def _reveal_joint_for_edit(joint):
    """Position the timeline at `joint` and select it, so the user's very
    next action -- double-clicking it in the browser -- opens Fusion's real
    Edit Joint dialog for it.

    Only called after Joint Inspector's own dialog has fully closed -- Fusion
    can't show another command's dialog while this one is still open, and
    mutating ui.activeSelections while target_entity's SelectionCommandInput
    was still live was previously found to break that input's own
    pick-gathering (see LESSONS_LEARNED.md).

    This does NOT open the native Edit Joint dialog itself -- confirmed live
    that there's no way to do that pre-loaded with an existing joint via the
    public API (see the _joint_to_edit comment above for what was tried).
    """
    try:
        joint.timelineObject.rollTo(False)
        ui.activeSelections.clear()
        ui.activeSelections.add(joint)
    except Exception:
        futil.handle_error(f'{CMD_NAME} reveal joint for edit', show_message_box=True)


# ---------------------------------------------------------------------------
# Entity / occurrence resolution
# ---------------------------------------------------------------------------

def _get_entity_component(entity) -> adsk.fusion.Component:
    """Return the parent Component for a BRep entity or Occurrence, or None."""
    try:
        ot = entity.objectType
        if ot in (adsk.fusion.BRepFace.classType(),
                  adsk.fusion.BRepEdge.classType(),
                  adsk.fusion.BRepVertex.classType()):
            return entity.body.parentComponent
        elif ot == adsk.fusion.BRepBody.classType():
            return entity.parentComponent
        elif ot == adsk.fusion.Occurrence.classType():
            return entity.component
    except Exception:
        pass
    return None


def _resolve_target(entity):
    """Resolve whatever the user picked to the Occurrence it belongs to.

    Returns (occurrence_or_None, ambiguous_bool). occurrence is None when the
    entity belongs directly to the root component (i.e. it's "ground", not
    joined through any occurrence). ambiguous is True when the entity's owning
    Component has multiple occurrence instances and there was no
    assemblyContext available to tell which one was actually picked -- the
    caller must not silently guess in that case.
    """
    design = adsk.fusion.Design.cast(app.activeProduct)

    if entity.objectType == adsk.fusion.Occurrence.classType():
        return entity, False

    try:
        occ = entity.assemblyContext
        if occ and occ.isValid:
            return occ, False
    except Exception:
        pass

    native = entity
    try:
        if entity.nativeObject:
            native = entity.nativeObject
    except Exception:
        pass

    comp = _get_entity_component(native)
    if comp is None:
        return None, False

    if comp == design.rootComponent:
        return None, False

    matches = [occ for occ in design.rootComponent.allOccurrences if occ.component == comp]
    if len(matches) == 1:
        return matches[0], False
    if len(matches) == 0:
        return None, False
    return None, True


def _joint_occurrence(joint, side):
    """`joint.occurrenceOne`/`occurrenceTwo` for side 0/1, or None for "ground".

    Reading these raises `RuntimeError: 2 : InternalValidationError : jointOcc`
    (not return None) when that side of the joint is attached to the root
    component rather than to an occurrence -- confirmed live from a real
    traceback. Left unguarded it aborted the whole `target_entity` handler, so
    `_current_matches` was never assigned and the joint dropdown stayed empty
    for that selection *and every selection after it*. "Attached to the root"
    is exactly the None case the rest of this module already means by ground.
    """
    try:
        return joint.occurrenceOne if side == 0 else joint.occurrenceTwo
    except Exception as err:
        futil.log(f'{CMD_NAME}: joint "{_joint_name(joint)}" side {side} has no '
                  f'occurrence, treating as ground ({err})')
        return None


def _joint_name(joint):
    try:
        return joint.name
    except Exception:
        return '<unnamed joint>'


def _is_as_built(joint) -> bool:
    try:
        return joint.objectType == adsk.fusion.AsBuiltJoint.classType()
    except Exception:
        return False


def _side_matches(occ, target_occ) -> bool:
    if target_occ is None:
        return occ is None
    if occ is None:
        return False
    return occ.fullPathName == target_occ.fullPathName


def _find_joint_matches(target_occ):
    design = adsk.fusion.Design.cast(app.activeProduct)
    matches = []
    # allJoints and allAsBuiltJoints are two separate flattened collections on
    # Component (regular joints and as-built joints are different API classes
    # -- adsk.fusion.Joint vs adsk.fusion.AsBuiltJoint -- with no single
    # collection covering both), so both have to be walked to find every joint
    # touching the target.
    all_joints = list(design.rootComponent.allJoints) + list(design.rootComponent.allAsBuiltJoints)
    for joint in all_joints:
        if _side_matches(_joint_occurrence(joint, 0), target_occ):
            matches.append((joint, 0))
        elif _side_matches(_joint_occurrence(joint, 1), target_occ):
            matches.append((joint, 1))
    return matches


def _other_side(joint, matched_side):
    return _joint_occurrence(joint, 1 if matched_side == 0 else 0)


# ---------------------------------------------------------------------------
# Ghosting -- dim every component not part of the selected joint
# ---------------------------------------------------------------------------
#
# Component.opacity is inherited by every occurrence that references it *and*
# every occurrence nested inside those -- so a Component cannot be lit for one
# of its occurrences and dim for another; whichever state it's set to applies
# to every occurrence sharing it, everywhere in the tree, simultaneously. See
# the module-level `_ghosted_components` comment for why that is the only
# opacity there is, and why Occurrence.appearance can't be used to work around
# it here.
#
# So the occurrence tree is walked once and every distinct Component sorted
# into one of three cases by how its occurrences relate to the joint (an
# occurrence "participates" when it is one of the joint's two sides, an
# ancestor of one, or nested inside one -- see _is_kept):
#
#   every occurrence participates  -> leave the component fully lit.
#   no occurrence participates     -> dim the whole component via
#                                     Component.opacity. Context is preserved:
#                                     a ghost is faint, not invisible.
#   mixed                          -> the component is on the joint at one
#                                     instance and off it at others (an FRC
#                                     assembly reuses the same tube/bearing/
#                                     spacer/pulley Component everywhere).
#                                     Dimming it would dim the joint's own
#                                     part, so instead leave the component lit
#                                     and hide just the unrelated occurrences
#                                     via Occurrence.isLightBulbOn, which is
#                                     genuinely per-instance.
#
# The mixed case is the whole point: without it, a joint between two instances
# of a shared part ghosts both of its own sides, which in a real assembly
# (where most components are multi-instance) washes out the entire model.
# If the isLightBulbOn write is ever rejected it is logged and skipped, and
# that component simply stays lit -- degraded, but never washed out.

def _ancestor_paths(occ):
    """fullPathName of `occ` and every occurrence it's nested under, up to
    (not including) the root component. Empty for `occ is None` (the
    ground/root side of a joint has no occurrence to build a path from)."""
    paths = set()
    while occ is not None:
        paths.add(occ.fullPathName)
        occ = occ.assemblyContext
    return paths


def _is_kept(full_path, keep_paths, leaf_paths):
    """True if `full_path` is a participating occurrence, one of its
    ancestors, or nested inside one of the two participating occurrences."""
    if full_path in keep_paths:
        return True
    return any(full_path.startswith(leaf + '+') for leaf in leaf_paths)


def _collect_component_keep_status(occurrences, keep_paths, leaf_paths, groups):
    """Recursively visit every occurrence in the design, recording for each
    distinct Component (compared via `==`, since Component has no usable
    hash -- see `_ghosted_components` above) how its occurrences relate to the
    joint. `groups` is a list of `[component, any_kept, all_kept,
    unrelated_occurrences]` entries, mutated in place; a linear `==` scan is
    used to find each occurrence's entry for the same reason
    `_ghosted_components` is a list rather than a dict.

    `any_kept`/`all_kept` are what separate the three cases in `_apply_ghosting`;
    `unrelated_occurrences` carries the specific instances to hide when a
    component turns out to be mixed."""
    for occ in occurrences:
        comp = occ.component
        kept = _is_kept(occ.fullPathName, keep_paths, leaf_paths)
        entry = None
        for g in groups:
            if g[0] == comp:
                entry = g
                break
        if entry is None:
            entry = [comp, False, True, []]
            groups.append(entry)
        if kept:
            entry[1] = True
        else:
            entry[2] = False
            entry[3].append(occ)
        _collect_component_keep_status(occ.childOccurrences, keep_paths, leaf_paths, groups)


def _find_ghosted(comp):
    """Index of `comp` in `_ghosted_components`, or -1. See the module-level
    `_ghosted_components` comment for why this is a linear `==` scan rather
    than a dict lookup keyed by some token."""
    for i, (comp2, _original) in enumerate(_ghosted_components):
        if comp2 == comp:
            return i
    return -1


def _restore_if_ghosted(comp):
    idx = _find_ghosted(comp)
    if idx < 0:
        return
    comp2, original_opacity = _ghosted_components.pop(idx)
    try:
        comp2.opacity = original_opacity
    except Exception as err:
        futil.log(f'{CMD_NAME}: could not restore opacity for "{comp2.name}": {err}')


def _ghost_component(comp):
    if _find_ghosted(comp) >= 0:
        return
    try:
        original_opacity = comp.opacity
        comp.opacity = _GHOST_OPACITY
        _ghosted_components.append((comp, original_opacity))
    except Exception as err:
        futil.log(f'{CMD_NAME}: could not ghost "{comp.name}": {err}')


def _hide_occurrence(path, occ):
    """Switch one occurrence's browser light bulb off, remembering its original
    state. Unlike opacity this really is per-instance, so it is what lets a
    shared part be hidden at one occurrence while the joint's own instance of
    that same component stays lit."""
    if path in _hidden_occurrences:
        return
    try:
        original = occ.isLightBulbOn
        occ.isLightBulbOn = False
        _hidden_occurrences[path] = (occ, original)
    except Exception as err:
        # Never silent: if this ever gets rejected the way an
        # Occurrence.appearance write does, the Text Command window is how we
        # find out -- the component just stays lit instead.
        futil.log(f'{CMD_NAME}: could not hide "{path}": {err}')


def _unhide_occurrence(path):
    entry = _hidden_occurrences.pop(path, None)
    if entry is None:
        return
    occ, original = entry
    try:
        occ.isLightBulbOn = original
    except Exception as err:
        futil.log(f'{CMD_NAME}: could not restore visibility for "{path}": {err}')


def _apply_ghosting(target_occ, other_occ):
    """Make only the joint's two sides (and whatever they're nested inside)
    stand out: dim whole components that have nothing to do with the joint,
    and hide the individual unrelated instances of components that do. Safe to
    call repeatedly as the joint selection changes -- already-ghosted
    components and already-hidden occurrences are left alone (not re-saved over
    their own ghost/hidden value) and anything no longer excluded is restored."""
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        return
    root = design.rootComponent

    keep_paths = _ancestor_paths(target_occ) | _ancestor_paths(other_occ)
    leaf_paths = {occ.fullPathName for occ in (target_occ, other_occ) if occ is not None}

    groups = []
    _collect_component_keep_status(root.occurrences, keep_paths, leaf_paths, groups)

    desired_hidden = {}
    for comp, any_kept, all_kept, unrelated in groups:
        if all_kept:
            _restore_if_ghosted(comp)
        elif not any_kept:
            # Nothing about this component touches the joint -- dim the whole
            # thing, which keeps it on screen as context.
            _ghost_component(comp)
        else:
            # Mixed: dimming would take the joint's own part down with it, so
            # keep the component lit and drop just the other instances.
            _restore_if_ghosted(comp)
            for occ in unrelated:
                desired_hidden[occ.fullPathName] = occ

    # Reconcile rather than clear-and-rebuild, so switching joints doesn't
    # flash every previously-hidden occurrence back on and off again.
    for path in list(_hidden_occurrences.keys()):
        if path not in desired_hidden:
            _unhide_occurrence(path)
    for path, occ in desired_hidden.items():
        _hide_occurrence(path, occ)

    _refresh_viewport()


def _clear_ghosting():
    for comp, original_opacity in _ghosted_components:
        try:
            comp.opacity = original_opacity
        except Exception as err:
            futil.log(f'{CMD_NAME}: could not restore opacity for "{comp.name}": {err}')
    _ghosted_components.clear()

    for path in list(_hidden_occurrences.keys()):
        _unhide_occurrence(path)

    _refresh_viewport()


def _update_ghosting(inputs: adsk.core.CommandInputs):
    ghostInp: adsk.core.BoolValueCommandInput = inputs.itemById('ghost_others')
    if ghostInp is None or not ghostInp.value:
        _clear_ghosting()
        return

    if 0 <= _selected_idx < len(_current_matches):
        joint, matched_side = _current_matches[_selected_idx]
        _apply_ghosting(_joint_occurrence(joint, matched_side), _other_side(joint, matched_side))
    else:
        _clear_ghosting()


# ---------------------------------------------------------------------------
# Joint geometry -- viewport highlight
# ---------------------------------------------------------------------------
#
# Rather than describe each side's attachment geometry in text, draw it
# directly in the viewport via CustomGraphics: this side's face(s)/edge(s)/
# point in green, the other side's in blue, plus a marker at each side's
# joint origin and a connector line between them. CustomGraphics live outside
# the undo stack and don't touch ui.activeSelections, so this doesn't fight
# the target_entity SelectionCommandInput (see LESSONS_LEARNED.md).

def _unwrap(geom_or_origin):
    """A joint side is either a plain JointGeometry or a JointOrigin wrapping
    one -- always go through this before touching entityOne/entityTwo/origin."""
    if geom_or_origin is None:
        return None
    if geom_or_origin.objectType == adsk.fusion.JointOrigin.classType():
        return geom_or_origin.geometry
    return geom_or_origin


def _solid_color(rgb):
    r, g, b = rgb
    return adsk.fusion.CustomGraphicsSolidColorEffect.create(adsk.core.Color.create(r, g, b, 255))


def _add_mesh(group, mesh, rgb, opacity, depth):
    if mesh is None:
        return
    coords = adsk.fusion.CustomGraphicsCoordinates.create(mesh.nodeCoordinatesAsDouble)
    entity = group.addMesh(coords, mesh.nodeIndices, mesh.normalVectorsAsDouble, [])
    entity.color = _solid_color(rgb)
    entity.setOpacity(opacity, True)
    entity.depthPriority = depth


def _highlight_body(group, body: adsk.fusion.BRepBody, rgb):
    """Translucent wash over the side's whole body.

    This is what makes a joint readable from any camera angle: the exact
    face/edge/point below is frequently hidden inside the solid, but a whole
    tube changing colour never is.
    """
    if body is None:
        return
    try:
        _add_mesh(group, body.meshManager.displayMeshes.bestMesh, rgb, _BODY_OPACITY, _DEPTH_BODY)
    except Exception as err:
        futil.log(f'{CMD_NAME}: could not wash body: {err}')


def _highlight_face(group, face: adsk.fusion.BRepFace, rgb):
    _add_mesh(group, face.meshManager.displayMeshes.bestMesh, rgb, _EXACT_OPACITY, _DEPTH_EXACT)


def _highlight_edge(group, edge: adsk.fusion.BRepEdge, rgb):
    entity = group.addCurve(edge.geometry)
    entity.color = _solid_color(rgb)
    entity.weight = 6
    entity.depthPriority = _DEPTH_EXACT


def _point_from_entity(entity):
    """Best-effort Point3D for a joint-geometry entity that isn't a face/edge."""
    try:
        ot = entity.objectType
        if ot == adsk.fusion.BRepVertex.classType():
            return entity.geometry
        elif ot == adsk.fusion.ConstructionPoint.classType():
            return entity.geometry
        elif ot == adsk.fusion.SketchPoint.classType():
            return entity.worldGeometry
    except Exception:
        pass
    return None


def _add_marker(group, point: adsk.core.Point3D, rgb, pixels):
    """A 3D crosshair at `point`, sized in *pixels* rather than centimetres.

    A fixed centimetre size (the previous 0.6cm) is nearly invisible on an
    assembly-sized model -- and the two sides of a joint usually share one
    origin, so they land on top of each other. `viewScale` keeps the marker a
    constant on-screen size at any zoom, and the two sides use different pixel
    sizes so the coincident case reads as two nested crosshairs.
    """
    if point is None:
        return
    x, y, z = point.x, point.y, point.z
    coords = adsk.fusion.CustomGraphicsCoordinates.create([
        x - 1.0, y, z,  x + 1.0, y, z,
        x, y - 1.0, z,  x, y + 1.0, z,
        x, y, z - 1.0,  x, y, z + 1.0,
    ])
    entity = group.addLines(coords, [], False, [])
    entity.color = _solid_color(rgb)
    entity.weight = 4
    entity.depthPriority = _DEPTH_MARKER
    try:
        entity.viewScale = adsk.fusion.CustomGraphicsViewScale.create(pixels, point)
    except Exception as err:
        futil.log(f'{CMD_NAME}: viewScale unavailable for marker: {err}')


def _side_body(joint_geometry):
    """The BRepBody a joint side hangs off, or None for non-BRep geometry
    (a ConstructionPoint or SketchPoint has no owning body)."""
    for entity in (joint_geometry.entityOne, joint_geometry.entityTwo):
        if entity is None:
            continue
        try:
            if entity.body is not None:
                return entity.body
        except Exception:
            continue
    return None


def _highlight_side(group, joint_geometry, rgb, marker_pixels):
    if joint_geometry is None:
        return

    _highlight_body(group, _side_body(joint_geometry), rgb)

    for entity in (joint_geometry.entityOne, joint_geometry.entityTwo):
        if entity is None:
            continue
        ot = entity.objectType
        if ot == adsk.fusion.BRepFace.classType():
            _highlight_face(group, entity, rgb)
        elif ot == adsk.fusion.BRepEdge.classType():
            _highlight_edge(group, entity, rgb)
        else:
            _add_marker(group, _point_from_entity(entity), rgb, marker_pixels)

    try:
        _add_marker(group, joint_geometry.origin, rgb, marker_pixels)
    except Exception:
        pass


def _clear_highlight():
    global _highlight_group
    if _highlight_group is None:
        return
    group = _highlight_group
    _highlight_group = None
    try:
        if group.isValid:
            group.deleteMe()
    except Exception as err:
        futil.log(f'{CMD_NAME}: failed to delete highlight graphics: {err}')


def _occurrence_bodies(occ):
    """All BRepBody in an occurrence, or in the root component for `occ` is
    None (ground) -- used to wash an as-built joint's side, since an
    AsBuiltJoint (unlike a regular Joint) has no per-side geometry entity to
    trace back to a single body."""
    try:
        if occ is not None:
            return list(occ.bRepBodies)
        design = adsk.fusion.Design.cast(app.activeProduct)
        return list(design.rootComponent.bRepBodies)
    except Exception:
        return []


def _draw_joint_highlight(joint, matched_side):
    global _highlight_group
    _clear_highlight()

    design = adsk.fusion.Design.cast(app.activeProduct)
    group = design.rootComponent.customGraphicsGroups.add()
    # Take ownership of the group immediately, not after it's fully populated:
    # if any of the drawing calls below raise, the caller swallows it and a
    # group assigned only at the end would never be reachable by the next
    # _clear_highlight() -- leaking a half-drawn highlight that then sticks in
    # the viewport on top of every later one.
    _highlight_group = group

    if _is_as_built(joint):
        _draw_asbuilt_highlight(group, joint, matched_side)
    else:
        _draw_regular_highlight(group, joint, matched_side)

    _refresh_viewport()


def _draw_regular_highlight(group, joint, matched_side):
    this_geom = _unwrap(joint.geometryOrOriginOne if matched_side == 0 else joint.geometryOrOriginTwo)
    other_geom = _unwrap(joint.geometryOrOriginTwo if matched_side == 0 else joint.geometryOrOriginOne)

    _highlight_side(group, this_geom, _THIS_SIDE_COLOR, _MARKER_PIXELS_THIS)
    _highlight_side(group, other_geom, _OTHER_SIDE_COLOR, _MARKER_PIXELS_OTHER)

    if this_geom is not None and other_geom is not None:
        try:
            coords = adsk.fusion.CustomGraphicsCoordinates.create([
                this_geom.origin.x, this_geom.origin.y, this_geom.origin.z,
                other_geom.origin.x, other_geom.origin.y, other_geom.origin.z,
            ])
            connector = group.addLines(coords, [], False, [])
            connector.color = _solid_color(_CONNECTOR_COLOR)
            connector.weight = 3
            connector.depthPriority = _DEPTH_EXACT
        except Exception:
            pass


def _draw_asbuilt_highlight(group, joint, matched_side):
    """An AsBuiltJoint has a single shared `geometry` (the joint's computed
    coordinate system at its current, already-assembled position) instead of
    a geometryOrOriginOne/Two pair -- there's no separate per-side attachment
    geometry to highlight. Wash each side's whole occurrence instead (every
    body in it, since we can't trace the shared geometry's entities back to
    "this side" vs "other side" the way a regular joint's two geometries let
    us), and mark the one shared origin.
    """
    this_occ = _joint_occurrence(joint, matched_side)
    other_occ = _joint_occurrence(joint, 1 if matched_side == 0 else 0)

    for body in _occurrence_bodies(this_occ):
        _highlight_body(group, body, _THIS_SIDE_COLOR)
    for body in _occurrence_bodies(other_occ):
        _highlight_body(group, body, _OTHER_SIDE_COLOR)

    try:
        geom = _unwrap(joint.geometry)
    except Exception:
        geom = None

    if geom is not None:
        # The joint's exact attachment entities (face/edge/point), colored
        # per side when the owning body tells us which side it's on -- falls
        # back to "this side" green when that can't be determined (e.g. the
        # entity's body isn't in either occurrence's own bRepBodies, which
        # happens for a proxy body in a nested component).
        this_bodies = _occurrence_bodies(this_occ)
        other_bodies = _occurrence_bodies(other_occ)
        for entity in (geom.entityOne, geom.entityTwo):
            if entity is None:
                continue
            body = None
            try:
                body = entity.body
            except Exception:
                pass
            color = _OTHER_SIDE_COLOR if body is not None and body in other_bodies and body not in this_bodies \
                else _THIS_SIDE_COLOR
            ot = entity.objectType
            if ot == adsk.fusion.BRepFace.classType():
                _highlight_face(group, entity, color)
            elif ot == adsk.fusion.BRepEdge.classType():
                _highlight_edge(group, entity, color)
            else:
                _add_marker(group, _point_from_entity(entity), color, _MARKER_PIXELS_THIS)

        # Both sides share this one origin (the joint was captured from parts
        # already in place, not assembled by picking two separate origins),
        # so mark it once per side's colour/size for the same nested-crosshair
        # look a coincident regular-joint origin gets.
        try:
            _add_marker(group, geom.origin, _THIS_SIDE_COLOR, _MARKER_PIXELS_THIS)
            _add_marker(group, geom.origin, _OTHER_SIDE_COLOR, _MARKER_PIXELS_OTHER)
        except Exception:
            pass


def _refresh_viewport():
    # CustomGraphics changes made via the API don't always trigger an
    # automatic repaint on their own -- force one so switching joints in the
    # dropdown updates the viewport immediately instead of only on the next
    # unrelated redraw (camera orbit, etc). Only ever called once per redraw
    # cycle -- calling it twice back-to-back (e.g. once from a clear and
    # again from the draw that follows) was observed to occasionally raise,
    # which then silently dropped the highlight group reference.
    try:
        app.activeViewport.refresh()
    except Exception as err:
        futil.log(f'{CMD_NAME}: viewport refresh failed: {err}')
