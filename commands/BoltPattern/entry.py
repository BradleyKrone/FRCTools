import adsk.core
import adsk.fusion
import os
import math
import typing
from ...lib import fusionAddInUtils as futil
from ... import config
app = adsk.core.Application.get()
ui = app.userInterface


# TODO *** Specify the command identity information. ***
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_BoltPatternDialog'
CMD_NAME = 'FRC Bolt Pattern'
CMD_Description = 'Create a bolt pattern for common FRC motors'

# Specify that the command will be promoted to the panel.
IS_PROMOTED = False

# Resource location for command icons, here we assume a sub folder in this directory named "resources".
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []

# Sketch that was active when the dialog opened, and the sketch-space point the user
# clicked in empty space (None until they click). Used when no point/circle is selected.
_active_sketch: adsk.fusion.Sketch = None
_click_pt: adsk.core.Point3D = None

# How close (in pixels) a click must be to the selected center to count as re-picking it.
CLICK_PICK_TOLERANCE_PX = 10

# Bolt Pattern struct
class BoltPattern(typing.NamedTuple) :
    name: str = ""
    centerDia: float = 0.0
    patternDia: float = 0.0
    holeSize: float = 0.0
    numberOfHoles: int = 0
    suppression: list[int] = []

# Selection of Bolt Patterns
bolt_patterns: list[BoltPattern] = [
    # Name, center hole radius, pattern radius, hole size, # of holes, suppression
    BoltPattern('Kraken X60', 0.75, 2.0, 0.196, 12, [0,0,0,0,0,0,0,0,0,0,0,1]),
    BoltPattern('Kraken X44', 0.75, 1.375, 0.196, 12, [0,0,0,0,0,0,0,0,0,0,0,1]),
    # BoltPattern('NEO Vortex', 0.75, 2.0, 0.196, 8, [0,0,0,1,0,0,0,1]),
    # BoltPattern('NEO 550', 0.5118, 0.9843, 0.125, 4, []),
    BoltPattern('REV MAXPlanetary', 1.125, 2.0, 0.196, 8, [0,0,1,1,0,0,1,1]),
    # BoltPattern('2" MultiMotor', 0.75, 2.0, 0.196, 24, [0,1,0,0,0,1, 0,1,0,0,0,1, 0,1,0,0,0,1, 0,1,0,0,0,1]),
    BoltPattern('Hex Bearing Retention', 1.125, 1.422, 0.159, 6, [0,0,0,0,0,0]),
]

# Executed when add-in is run.
def start():
    # Create a command Definition.
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)

    # Define an event handler for the command created event. It will be called when the button is clicked.
    futil.add_handler(cmd_def.commandCreated, command_created)

    # ******** Add a button into the UI so the user can run the command. ********
    # Find the the FRCTools submenu.
    submenu = config.get_sketch_create_submenu()

    # Create the button command control in the UI.
    control = submenu.controls.addCommand(cmd_def)

    # Specify if the command is promoted to the main toolbar. 
    control.isPromoted = IS_PROMOTED

# Executed when add-in is stopped.
def stop():

    # Get the various UI elements for this command
    submenu = config.get_sketch_create_submenu()
    command_control = submenu.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    # Delete the button command control
    if command_control:
        command_control.isPromoted = False
        command_control.deleteMe()

    # Delete the command definition
    if command_definition:
        command_definition.deleteMe()

# Function that is called when a user clicks the corresponding button in the UI.
# This defines the contents of the command dialog and connects to the command related events.
def command_created(args: adsk.core.CommandCreatedEventArgs):

    # General logging for debug.
    # futil.log(f'{CMD_NAME} command Created Event')
    
    # Set dialog size to be wider
    args.command.setDialogInitialSize(250, 300)  # width, height in pixels

    # https://help.autodesk.com/view/fusion360/ENU/?contextId=CommandInputs
    inputs = args.command.commandInputs

    global _active_sketch, _click_pt
    _active_sketch = adsk.fusion.Sketch.cast( app.activeProduct.activeEditObject )
    _click_pt = None

    # Create a selection input. Selecting is optional: clicking empty space in the
    # active sketch places a new (free) center point there instead.
    centerSelection = inputs.addSelectionInput('center_selection', 'Center', 'Click a location, or select a point/circle')
    centerSelection.addSelectionFilter( "SketchPoints" )
    centerSelection.addSelectionFilter( "SketchCircles" )
    centerSelection.setSelectionLimits( 0, 1 )

    # Read-only readout of the clicked location. Changing its value from the mouse
    # click handler is also what makes Fusion re-validate and re-run the preview.
    clickLocation = inputs.addStringValueInput('click_location', 'Location', '')
    clickLocation.isReadOnly = True

    # Bolt Patterns
    boltPattern = inputs.addDropDownCommandInput('bolt_pattern', 'Bolt Pattern', adsk.core.DropDownStyles.TextListDropDownStyle)
    for bp in bolt_patterns:
        boltPattern.listItems.add( bp.name, True, '')
    boltPattern.listItems.item( 0 ).isSelected = True

    # Center hole options
    centerHoleGroup = inputs.addGroupCommandInput('center_hole_group', 'Center Hole Options')
    centerHoleGroup.isExpanded = True
    
    # Checkbox to suppress center hole
    suppressCenterHole = centerHoleGroup.children.addBoolValueInput('suppress_center_hole', 'Suppress Center Hole', True, '', False)
    
    # Value input for center hole diameter (in inches)
    selected_pattern = bolt_patterns[0]  # Default pattern
    centerHoleSize = centerHoleGroup.children.addValueInput('center_hole_size', 'Center Hole Diameter', 'in', adsk.core.ValueInput.createByString(f'{selected_pattern.centerDia} in'))

    # TODO Connect to the events that are needed by this command.
    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.mouseClick, command_mouse_click, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


# Returns the center SketchPoint to build the pattern on: the selected point/circle if
# there is one, otherwise a new point at the clicked location (left free for the user
# to dimension). Returns None if neither is available.
def _resolve_center(inputs: adsk.core.CommandInputs) -> adsk.fusion.SketchPoint:
    centerSelection: adsk.core.SelectionCommandInput = inputs.itemById('center_selection')
    if centerSelection.selectionCount > 0:
        selectedEntity = centerSelection.selection(0).entity
        if selectedEntity.objectType == adsk.fusion.SketchCircle.classType() :
            return selectedEntity.centerSketchPoint
        elif selectedEntity.objectType == adsk.fusion.SketchPoint.classType() :
            return selectedEntity
        futil.popup_error( f'  Cannot handle object type = {selectedEntity.objectType}')
        return None

    if _click_pt is not None and _active_sketch is not None and _active_sketch.isValid:
        return _active_sketch.sketchPoints.add( _click_pt )
    return None


# Called when the user clicks in the graphics window while the dialog is open.
def command_mouse_click(args: adsk.core.MouseEventArgs):
    global _click_pt
    if _active_sketch is None or not _active_sketch.isValid:
        return

    inputs = args.firingEvent.sender.commandInputs
    centerSelection: adsk.core.SelectionCommandInput = inputs.itemById('center_selection')
    viewport = args.viewport
    viewPt = args.viewportPosition

    # A click on the already-selected point/circle is the selection itself,
    # not a request to place a new point.
    if centerSelection.selectionCount > 0:
        entity = centerSelection.selection(0).entity
        if futil.sketchEntityViewDistance( entity, viewport, viewPt ) <= CLICK_PICK_TOLERANCE_PX:
            return

    sketchPt = futil.viewClickToSketchPoint( _active_sketch, viewport, viewPt )
    if sketchPt is None:
        return

    _click_pt = sketchPt
    centerSelection.clearSelection()
    clickLocation: adsk.core.StringValueCommandInput = inputs.itemById('click_location')
    clickLocation.value = f'{sketchPt.x / 2.54:.3f}, {sketchPt.y / 2.54:.3f} in'


# This event handler is called when the user clicks the OK button in the command dialog or 
# is immediately called after the created event not command inputs were created for the dialog.
def command_execute(args: adsk.core.CommandEventArgs):
    # General logging for debug.
    # futil.log(f'{CMD_NAME} Command Execute Event')

    inputs = args.command.commandInputs
    boltPatternInp: adsk.core.DropDownCommandInput = inputs.itemById('bolt_pattern')
    suppressCenterHoleInp: adsk.core.BoolValueCommandInput = inputs.itemById('suppress_center_hole')
    centerHoleSizeInp: adsk.core.ValueCommandInput = inputs.itemById('center_hole_size')

    centerPt = _resolve_center( inputs )
    if centerPt is None:
        return

    boltPattern = bolt_patterns[ boltPatternInp.selectedItem.index ]
    sketch = centerPt.parentSketch

    # Create the bolt pattern center hole (only if not suppressed)
    if not suppressCenterHoleInp.value:
        # Use user-specified size instead of pattern default
        centerHoleDia = centerHoleSizeInp.value
        centerHole = sketch.sketchCurves.sketchCircles.addByCenterRadius( centerPt, centerHoleDia / 2 )
        textPt = futil.offsetPoint3D( centerHole.centerSketchPoint.geometry, centerHoleDia/4, centerHoleDia/4, 0 )
        centerDim = sketch.sketchDimensions.addDiameterDimension( centerHole, textPt )
        centerDim.value = centerHoleDia

    # Create the bolt pattern bolt circle
    patternDiaCm = boltPattern.patternDia * 2.54
    holeSizeCm = boltPattern.holeSize * 2.54
    boltCircle = sketch.sketchCurves.sketchCircles.addByCenterRadius( centerPt, patternDiaCm / 2 )
    boltCircle.isConstruction = True
    textPt = futil.offsetPoint3D( boltCircle.centerSketchPoint.geometry, -patternDiaCm/4, patternDiaCm/4, 0 )
    boltCirDim = sketch.sketchDimensions.addDiameterDimension( boltCircle, textPt )
    boltCirDim.value = patternDiaCm

    # Create a single bolt hole, drawn already on the bolt circle so the solver
    # doesn't drag a free (clicked) center point toward it.
    boltCenter = futil.offsetPoint3D( centerPt.geometry, patternDiaCm / 2, 0, 0 )
    boltHole = sketch.sketchCurves.sketchCircles.addByCenterRadius( boltCenter, holeSizeCm / 2 )
    sketch.geometricConstraints.addCoincident( boltHole.centerSketchPoint, boltCircle )
    textPt = futil.offsetPoint3D( boltHole.centerSketchPoint.geometry, holeSizeCm/4, holeSizeCm/4, 0 )
    boltDim = sketch.sketchDimensions.addDiameterDimension( boltHole, textPt )
    boltDim.value = holeSizeCm

    # Create the hole pattern
    cirPattern = sketch.geometricConstraints.createCircularPatternInput( [boltHole], centerPt )
    # cirPattern.totalAngle = 2 * math.pi
    cirPattern.quantity = futil.Value(boltPattern.numberOfHoles)

    if len(boltPattern.suppression) > 0:
        boolSuppression = []
        for s in boltPattern.suppression :
            boolSuppression.append( s == 1 )
        # Remove first element bc it cannot be suppressed
        boolSuppression.pop(0)
        cirPattern.isSuppressed = boolSuppression

    sketch.geometricConstraints.addCircularPattern( cirPattern )

# This event handler is called when the command needs to compute a new preview in the graphics window.
def command_preview(args: adsk.core.CommandEventArgs):
    # General logging for debug.
    # futil.log(f'{CMD_NAME} Command Preview Event')

    command_execute( args )
    args.isValidResult = True


# This event handler is called when the user changes anything in the command dialog
# allowing you to modify values of other inputs based on that change.
def command_input_changed(args: adsk.core.InputChangedEventArgs):
    global _click_pt
    changed_input = args.input
    inputs = args.inputs

    # General logging for debug.
    # futil.log(f'{CMD_NAME} Input Changed Event fired from a change to {changed_input.id}')
    
    # Selecting an existing point/circle takes over from a clicked location.
    if changed_input.id == 'center_selection':
        centerSelection: adsk.core.SelectionCommandInput = changed_input
        if centerSelection.selectionCount > 0 and _click_pt is not None:
            _click_pt = None
            clickLocation: adsk.core.StringValueCommandInput = changed_input.parentCommand.commandInputs.itemById('click_location')
            clickLocation.value = ''

    # If the bolt pattern selection changed, update the center hole size default
    if changed_input.id == 'bolt_pattern':
        boltPatternInp: adsk.core.DropDownCommandInput = inputs.itemById('bolt_pattern')
        centerHoleSizeInp: adsk.core.ValueCommandInput = inputs.itemById('center_hole_size')
        
        selected_pattern = bolt_patterns[boltPatternInp.selectedItem.index]
        centerHoleSizeInp.expression = f'{selected_pattern.centerDia} in'



# This event handler is called when the user interacts with any of the inputs in the dialog
# which allows you to verify that all of the inputs are valid and enables the OK button.
def command_validate_input(args: adsk.core.ValidateInputsEventArgs):

    # futil.log(f'{CMD_NAME} Command Validate Event')

    inputs = args.inputs
    
    # Check if center is selected
    centerSelection: adsk.core.SelectionCommandInput = inputs.itemById('center_selection')
    centerHoleSizeInp: adsk.core.ValueCommandInput = inputs.itemById('center_hole_size')
    
    # Validate inputs
    hasCenter = centerSelection.selectionCount > 0 or _click_pt is not None
    if hasCenter and centerHoleSizeInp.value > 0:
        args.areInputsValid = True
    else:
        args.areInputsValid = False

# This event handler is called when the command terminates.
def command_destroy(args: adsk.core.CommandEventArgs):
    # General logging for debug.
    # futil.log(f'{CMD_NAME} Command Destroy Event')

    global local_handlers, _active_sketch, _click_pt
    local_handlers = []
    _active_sketch = None
    _click_pt = None
