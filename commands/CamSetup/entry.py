import adsk.core
import adsk.fusion
import os
from ...lib import fusionAddInUtils as futil
from ... import config
from . import cam_data
from . import cam_builder
from . import feature_finder
app = adsk.core.Application.get()
ui = app.userInterface

#  *** Specify the command identity information. ***
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_CamSetupDialog'
CMD_NAME = 'CAM Setup'
CMD_Description = ('Create the setup and toolpaths for FRC parts: pick the machine, the parts and the '
                   'material, and the holes, pockets, cut-outs and profile are machined automatically')

# Specify that the command will be promoted to the panel.
IS_PROMOTED = True

# Resource location for command icons, here we assume a sub folder in this directory named "resources".
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []

PART_TYPES = ['Auto', 'Plate', 'Tube']

# Last choices, so the dialog reopens the way it was left.
_last = {'machine': list(cam_data.MACHINES)[0], 'material': cam_data.MATERIALS[0],
         'part_type': PART_TYPES[0], 'tabs': True}


# Executed when add-in is run.
def start():
    # Create a command Definition.
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)

    # Define an event handler for the command created event. It will be called when the button is clicked.
    futil.add_handler(cmd_def.commandCreated, command_created)

    # ******** Add a button into the UI so the user can run the command. ********
    # Get the FRC panel in the Manufacture workspace.
    panel = config.get_frc_cam_panel()
    if panel is None:
        futil.log(f'{CMD_NAME}: FRC Manufacture panel missing, no button added')
        return

    # Create the button command control in the UI.
    control = panel.controls.addCommand(cmd_def)

    # Specify if the command is promoted to the main toolbar.
    control.isPromotedByDefault = IS_PROMOTED
    control.isPromoted = IS_PROMOTED


# Executed when add-in is stopped.
def stop():

    # Get the various UI elements for this command
    panel = config.get_frc_cam_panel()
    command_control = panel.controls.itemById(CMD_ID) if panel else None
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    # Delete the button command control
    if command_control:
        command_control.isPromoted = False
        command_control.deleteMe()

    # Delete the command definition
    if command_definition:
        command_definition.deleteMe()


def _add_dropdown(inputs, id, name, items, selected):
    dd = inputs.addDropDownCommandInput(id, name, adsk.core.DropDownStyles.TextListDropDownStyle)
    for item in items:
        dd.listItems.add(item, item == selected)
    return dd


# Function that is called when a user clicks the corresponding button in the UI.
# This defines the contents of the command dialog and connects to the command related events.
def command_created(args: adsk.core.CommandCreatedEventArgs):
    inputs = args.command.commandInputs

    _add_dropdown(inputs, 'machine', 'Machine', list(cam_data.MACHINES), _last['machine'])

    parts = inputs.addSelectionInput('parts', 'Parts',
                                     'Select the bodies or components to cut. Each plate gets its own '
                                     'setup; a box tube gets one setup per face that has holes.')
    parts.addSelectionFilter('SolidBodies')
    parts.addSelectionFilter('Occurrences')
    parts.setSelectionLimits(1, 0)

    _add_dropdown(inputs, 'material', 'Material', cam_data.MATERIALS, _last['material'])
    _add_dropdown(inputs, 'part_type', 'Part Type', PART_TYPES, _last['part_type'])

    tabs = inputs.addBoolValueInput('tabs', 'Tabs on Profile', True, '', _last['tabs'])
    tabs.tooltip = (f'{cam_data.TAB_COUNT} tabs, {cam_data.TAB_WIDTH}" wide x {cam_data.TAB_HEIGHT}" thick, '
                    'on each plate\'s outside profile')

    summary = inputs.addTextBoxCommandInput('summary', 'Found', 'Select parts to see what will be machined.', 4, True)
    summary.isFullWidth = False

    # Connect to the events that are needed by this command.
    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


def _selected_bodies(sel_input):
    # Selected bodies, plus every visible solid body of each selected component.
    bodies = []
    for i in range(sel_input.selectionCount):
        ent = sel_input.selection(i).entity
        body = adsk.fusion.BRepBody.cast(ent)
        if body:
            bodies.append(body)
            continue
        occ = adsk.fusion.Occurrence.cast(ent)
        if occ:
            stack = [occ]
            while stack:
                o = stack.pop()
                bodies += [b for b in o.bRepBodies if b.isSolid and b.isVisible]
                stack += list(o.childOccurrences)
    seen = []
    for b in bodies:
        if b not in seen:
            seen.append(b)
    return seen


def _read(inputs):
    return {
        'machine': inputs.itemById('machine').selectedItem.name,
        'material': inputs.itemById('material').selectedItem.name,
        'part_type': inputs.itemById('part_type').selectedItem.name,
        'tabs': inputs.itemById('tabs').value,
        'bodies': _selected_bodies(inputs.itemById('parts')),
    }


# This event handler is called when the user clicks the OK button in the command dialog.
def command_execute(args: adsk.core.CommandEventArgs):
    progress = None
    try:
        opts = _read(args.command.commandInputs)
        _last.update({k: opts[k] for k in ('machine', 'material', 'part_type', 'tabs')})
        if not opts['bodies']:
            ui.messageBox('No solid bodies were selected.', CMD_NAME)
            return

        progress = ui.progressBar
        def report(msg, i, n):
            progress.show(f'{CMD_NAME}: {msg}...', 0, max(n, 1), False)
            progress.progressValue = i
            adsk.doEvents()

        result = cam_builder.build_all(opts['bodies'], opts['machine'], opts['material'],
                                       part_type=opts['part_type'], tabs=opts['tabs'],
                                       log=futil.log, progress=report)
        progress.hide()

        lines = [f'{len(result["setups"])} setup(s) created for {opts["machine"]}, {opts["material"]}.', '']
        lines += result['summary']
        if result['failed_ops']:
            lines += ['', 'These toolpaths did not generate cleanly -- check them:']
            lines += [f'  - {name}' for name in result['failed_ops']]
        if result['warnings']:
            lines += ['', 'Warnings:']
            lines += [f'  - {w}' for w in result['warnings']]
        lines += ['', 'Feeds are the "starting point" values from the Shapeoko notes -- '
                      'simulate before cutting.']
        ui.messageBox('\n'.join(lines), CMD_NAME)
    except:
        if progress:
            progress.hide()
        futil.handle_error(f'{CMD_NAME} execute', show_message_box=True)


# This event handler is called when the user changes anything in the command dialog.
# Analysis only -- nothing here writes to the design.
def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed = args.input
    inputs = args.inputs
    if changed.id not in ('parts', 'part_type'):
        return
    summary = inputs.itemById('summary')
    try:
        opts = _read(inputs)
        if not opts['bodies']:
            summary.text = 'Select parts to see what will be machined.'
            return
        lines = []
        for body in opts['bodies']:
            try:
                feats = feature_finder.analyze(body, opts['part_type'])
                lines.append(feature_finder.summarize(feats))
                lines += feats.warnings
            except Exception as e:
                lines.append(str(e))
        summary.text = '\n'.join(lines)
    except:
        futil.handle_error(f'{CMD_NAME} inputChanged')


# This event handler is called when the user interacts with any of the inputs in the dialog
# which allows you to verify that all of the inputs are valid and enables the OK button.
def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    args.areInputsValid = args.inputs.itemById('parts').selectionCount > 0


# This event handler is called when the command terminates.
def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers
    local_handlers = []
