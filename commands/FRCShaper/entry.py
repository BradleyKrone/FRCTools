import adsk.core
import adsk.fusion
import os
from ...lib import fusionAddInUtils as futil
from ... import config
from . import shaper_data as data
from . import shaper_gen as gen

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_FRCShaperDialog'
CMD_NAME = 'FRC Shaper'
CMD_Description = ('Click hole rims and press Enter. A plain hole gets a bolt sized to its plate '
                   'stack -- washer, spacers for any air gap, nylock nut. A motor mount\'s pilot '
                   'or bolt hole gets that motor, lined up, jointed and bolted in.')

# Specify that the command will be promoted to the panel.
IS_PROMOTED = True

# Resource location for command icons.
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []

# The motor mount found under the first pick (shaper_gen.find_motor_pattern), or None when
# the picks are plain holes (bolt stacks). Re-read on every selection change.
_motor_pattern = None

# The plan text last shown (read by MCP test scripts -- the dialog's inputs aren't reachable
# from outside the command).
last_plan_text = ''


# Executed when add-in is run.
def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)

    panel = config.get_frc_panel()
    control = panel.controls.addCommand(cmd_def)
    control.isPromotedByDefault = IS_PROMOTED
    control.isPromoted = IS_PROMOTED


# Executed when add-in is stopped.
def stop():
    panel = config.get_frc_panel()
    command_control = panel.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    if command_control:
        command_control.isPromoted = False
        command_control.deleteMe()

    if command_definition:
        command_definition.deleteMe()

    global local_handlers
    local_handlers = []


def _dropdown(inputs, inp_id, name, items, selected=0):
    dd = inputs.addDropDownCommandInput(inp_id, name, adsk.core.DropDownStyles.TextListDropDownStyle)
    for i, item in enumerate(items):
        dd.listItems.add(item, i == selected, '')
    return dd


def command_created(args: adsk.core.CommandCreatedEventArgs):
    global _motor_pattern
    _motor_pattern = None
    inputs = args.command.commandInputs

    holesInp = inputs.addSelectionInput(
        'holes', 'Holes', 'Click each hole\'s rim on the face the bolt head (or the motor) sits on')
    holesInp.addSelectionFilter('CircularEdges')
    holesInp.setSelectionLimits(1, 0)
    holesInp.tooltip = ('Click a hole\'s circular edge on the face the bolt head sits on -- as many '
                        'holes as you like. Click a motor mount\'s pilot hole (or one of its bolt '
                        'holes) on the face the motor sits against to place a motor instead.')

    planInp = inputs.addTextBoxCommandInput('plan', 'Plan', 'Pick a hole.', 5, True)
    planInp.isFullWidth = True

    bolt = inputs.addGroupCommandInput('bolt_group', 'Bolt Stack')
    bolt.isExpanded = True
    b = bolt.children
    _dropdown(b, 'thread', 'Thread', (data.THREAD_AUTO,) + tuple(data.THREADS))
    _dropdown(b, 'head', 'Head', data.HEADS)
    _dropdown(b, 'nut', 'Nut', data.NUTS)
    b.addBoolValueInput('washer_head', 'Washer Under Head', True, '', True)
    b.addBoolValueInput('washer_nut', 'Washer Under Nut', True, '', True)
    spacersInp = b.addBoolValueInput('spacers', 'Fill Gaps With Spacers', True, '', True)
    spacersInp.tooltip = ('An air gap between plates on the bolt (a box tube, a standoff) gets a '
                          'round spacer cut to length.')
    gapInp = b.addValueInput('max_gap', 'Max Gap', 'in', futil.inchValue(1.5))
    gapInp.tooltip = ('The stack carries on through the next plate on the hole\'s axis only if the '
                      'air gap to it is no bigger than this.')

    motor = inputs.addGroupCommandInput('motor_group', 'Motor')
    motor.isExpanded = True
    motor.isVisible = False
    m = motor.children
    m.addDropDownCommandInput('motor', 'Motor', adsk.core.DropDownStyles.TextListDropDownStyle)
    boltMotorInp = m.addBoolValueInput('bolt_motor', 'Bolt It In', True, '', True)
    boltMotorInp.tooltip = ('A bolt (and washer, if on) up through every plate hole into the motor, '
                            'sized to the plate. Uses the Bolt Stack head/washer settings.')

    flipInp = inputs.addBoolValueInput('flip', 'Flip', True, '', False)
    flipInp.tooltip = 'Use when a bolt comes out on the wrong side of the picked face.'

    futil.add_handler(args.command.activate, command_activate, local_handlers=local_handlers)
    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


def _options(inputs: adsk.core.CommandInputs) -> gen.StackOptions:
    def _sel(inp_id):
        return inputs.itemById(inp_id).selectedItem.name

    def _val(inp_id):
        return inputs.itemById(inp_id).value
    return gen.StackOptions(thread=_sel('thread'), head=_sel('head'), nut=_sel('nut'),
                            washer_head=_val('washer_head'), washer_nut=_val('washer_nut'),
                            spacers=_val('spacers'), max_gap=_val('max_gap') / gen.IN,
                            flip=_val('flip'), bolt_motor=_val('bolt_motor'))


def _picks(inputs: adsk.core.CommandInputs):
    sel = inputs.itemById('holes')
    picks = []
    for i in range(sel.selectionCount):
        try:
            picks.append(sel.selection(i).entity)
        except RuntimeError:
            pass
    return picks


def _refresh(inputs: adsk.core.CommandInputs, changed_id: str):
    """Re-detect bolt vs motor (on a pick change) and rewrite the plan text."""
    global _motor_pattern, last_plan_text
    picks = _picks(inputs)
    motorDD: adsk.core.DropDownCommandInput = inputs.itemById('motor')
    if changed_id == 'holes':
        _motor_pattern = gen.find_motor_pattern(picks[0]) if picks else None
        motorDD.listItems.clear()
        if _motor_pattern is not None:
            for i, name in enumerate(_motor_pattern['motors']):
                motorDD.listItems.add(name, i == 0, '')
    is_motor = _motor_pattern is not None
    inputs.itemById('motor_group').isVisible = is_motor
    inputs.itemById('flip').isVisible = not is_motor
    nut_on = inputs.itemById('nut').selectedItem.name == data.NUT_LOCKNUT
    inputs.itemById('washer_nut').isVisible = nut_on

    planInp = inputs.itemById('plan')
    if not picks:
        planInp.text = last_plan_text = 'Pick a hole.'
        return
    if is_motor:
        name = motorDD.selectedItem.name if motorDD.selectedItem else _motor_pattern['motors'][0]
        holes = _motor_pattern['holes_by_motor'].get(name, [])
        lines = [f'Motor mount: {name} ({len(holes)} bolt holes on its '
                 f'{data.MOTORS[name]["pattern"]}" circle).']
        if not data.MOTORS[name].get('urn'):
            lines.append(f'No {name} model in the Motors folder yet -- nothing to place.')
        if len(picks) > 1:
            lines.append('Only the first pick is used for a motor.')
        planInp.text = last_plan_text = '\n'.join(lines)
        return

    opts = _options(inputs)
    lines = []
    for i, edge in enumerate(picks):
        try:
            plan = gen.plan_stack(edge, opts)
        except Exception:
            futil.handle_error('FRC Shaper: plan')
            plan = dict(error='could not read this hole')
        lines.append(f'{i + 1}: ' + (plan.get('error') or plan['text']))
    planInp.text = last_plan_text = '\n'.join(lines)
    planInp.numRows = max(3, min(len(lines) + 1, 10))


def command_activate(args: adsk.core.CommandEventArgs):
    # Holes picked before the button was clicked land in the selection without an
    # inputChanged, so plan them here.
    try:
        _refresh(args.command.commandInputs, 'holes')
    except Exception:
        futil.handle_error('FRC Shaper: activate')


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    try:
        _refresh(args.inputs.command.commandInputs, args.input.id)
    except Exception:
        futil.handle_error('FRC Shaper: input changed')


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    args.areInputsValid = len(_picks(args.inputs)) > 0


def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    picks = _picks(inputs)
    opts = _options(inputs)
    try:
        if _motor_pattern is not None:
            motorDD = inputs.itemById('motor')
            name = motorDD.selectedItem.name if motorDD.selectedItem else _motor_pattern['motors'][0]
            occ, notes = gen.place_motor(_motor_pattern, name, opts)
            if notes:
                futil.popup_error('FRC Shaper:\n\n' + '\n'.join(notes))
            return
        placed, failed = gen.build_stacks(picks, opts)
        futil.log(f'FRC Shaper: placed {placed} bolt stack(s)')
        if failed:
            futil.popup_error('FRC Shaper: some holes were skipped (pick order):\n\n'
                              + '\n'.join(f'{n}: {why}' for n, why in failed))
    except Exception:
        futil.handle_error('FRC Shaper: execute', show_message_box=True)


def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers, _motor_pattern
    local_handlers = []
    _motor_pattern = None
