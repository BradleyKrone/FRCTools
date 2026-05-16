import adsk.core
import adsk.fusion
import os
import traceback
import typing
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_QuickCircle'
CMD_NAME = 'Quick Dimension Circle'
CMD_Description = 'Automatically dimension circles with common FRC hole sizes'

# Specify that the command will be promoted to the panel
IS_PROMOTED = True

# Resource location for command icons
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers
local_handlers = []

# Hole size definitions (name, diameter in inches, description)
class HoleSize(typing.NamedTuple):
    name: str
    diameter: float
    description: str

# Common FRC hole sizes
hole_sizes: list[HoleSize] = [
    HoleSize('10-32 Clearance', 0.203125, '#7 drill (10-32 clearance)'),
    HoleSize('10-32 Tap', 0.1590, '#7 drill (10-32 clearance)'),
    HoleSize('1/4-20 Clearance', 0.265625, 'F drill (1/4-20 clearance)'),
    HoleSize('Rivnut Hole', 0.296875, '10-32 rivnut installation hole'),
    HoleSize('1/2 Hex Bearing"', 1.125, '1/2" hex bearing'),
    HoleSize('1/2 Hex Bushing', 0.75, '1/2" hex bushing'),
    HoleSize('Spline Bearing', 1.85039, 'Spine Bearing installation hole'),
    HoleSize('Custom', 0.0, 'Enter custom diameter'),
]

# Executed when add-in is run
def start():
    # Create a command Definition
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)

    # Define an event handler for the command created event
    futil.add_handler(cmd_def.commandCreated, command_created)

    # Add button to the FRCTools submenu in Sketch Create panel
    submenu = config.get_sketch_create_submenu()

    # Create the button command control in the UI
    control = submenu.controls.addCommand(cmd_def)

    # Specify if the command is promoted to the main toolbar
    control.isPromoted = IS_PROMOTED

# Executed when add-in is stopped
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

    global local_handlers
    local_handlers = []

# Function that is called when a user clicks the button
def command_created(args: adsk.core.CommandCreatedEventArgs):
    # General logging for debug
    # futil.log(f'{CMD_NAME} command Created Event')

    # Get command inputs
    inputs = args.command.commandInputs
    
    # Set the dialog width to prevent text wrapping
    args.command.setDialogInitialSize(350, 300)
    args.command.setDialogMinimumSize(350, 300)

    # Create a dropdown for hole size selection
    holeSizeDropdown = inputs.addDropDownCommandInput('hole_size', 'Hole Size', adsk.core.DropDownStyles.TextListDropDownStyle)
    for hole_size in hole_sizes:
        holeSizeDropdown.listItems.add(hole_size.name, False, '')
    holeSizeDropdown.listItems.item(0).isSelected = True  # Select first item by default

    # Add a value input for custom diameter (initially disabled)
    customDiameterInput = inputs.addValueInput('custom_diameter', 'Custom Diameter', 'in', 
                                               adsk.core.ValueInput.createByReal(0.25))
    customDiameterInput.isEnabled = False

    # Add a value input for diameter offset (tolerance)
    offsetInput = inputs.addValueInput('diameter_offset', 'Diameter Offset', 'in',
                                       adsk.core.ValueInput.createByReal(0.0))
    offsetInput.tooltip = 'Add tolerance to hole diameter'

    # Add description text
    descriptionText = inputs.addTextBoxCommandInput('description', 'Description', 
                                                    hole_sizes[0].description, 2, True)

    # Create a selection input to select circles
    circleSelection = inputs.addSelectionInput('circle_selection', 'Select Circles', 
                                               'Select the circles to dimension')
    circleSelection.addSelectionFilter('SketchCircles')
    circleSelection.setSelectionLimits(1, 0)  # At least 1, no maximum

    # Connect to the events that are needed by this command
    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)

# This event handler is called when the user clicks the OK button
def command_execute(args: adsk.core.CommandEventArgs):
    try:
        inputs = args.command.commandInputs
        holeSizeDropdownInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
        customDiameterInp: adsk.core.ValueCommandInput = inputs.itemById('custom_diameter')
        offsetInp: adsk.core.ValueCommandInput = inputs.itemById('diameter_offset')
        circleSelectionInp: adsk.core.SelectionCommandInput = inputs.itemById('circle_selection')

        # Get the selected hole size
        selectedIndex = holeSizeDropdownInp.selectedItem.index
        selectedHoleSize = hole_sizes[selectedIndex]

        # Get the offset value (in cm, Fusion converts automatically)
        offset_cm = offsetInp.value
        offset_inches = offset_cm / 2.54

        # Determine the diameter to use
        if selectedHoleSize.name == 'Custom':
            # Custom input value is already in cm (Fusion converts it automatically)
            diameter_cm = customDiameterInp.value
            diameter_inches = diameter_cm / 2.54
        else:
            # Predefined values are in inches, need to convert to cm
            diameter_inches = selectedHoleSize.diameter
            diameter_cm = diameter_inches * 2.54
        
        # Apply the offset to the diameter
        diameter_inches += offset_inches
        diameter_cm += offset_cm

        # Get the active design
        design = app.activeProduct
        if not design:
            ui.messageBox('No active design found.')
            return

        # Process each selected circle
        dimensionsAdded = 0
        for i in range(circleSelectionInp.selectionCount):
            selection = circleSelectionInp.selection(i)
            circle: adsk.fusion.SketchCircle = selection.entity

            if circle.objectType == adsk.fusion.SketchCircle.classType():
                # Get the sketch
                sketch = circle.parentSketch

                # Check if the circle already has a diameter dimension
                existingDim = None
                for dim in sketch.sketchDimensions:
                    if dim.objectType == adsk.fusion.SketchDiameterDimension.classType():
                        dimCircle = dim.entity
                        if dimCircle == circle:
                            existingDim = dim
                            break

                if existingDim:
                    # Update existing dimension
                    existingDim.value = diameter_cm
                else:
                    # Add new diameter dimension
                    centerPt = circle.centerSketchPoint.geometry
                    textPoint = adsk.core.Point3D.create(
                        centerPt.x + circle.radius * 0.7,
                        centerPt.y + circle.radius * 0.7,
                        centerPt.z
                    )
                    dimension = sketch.sketchDimensions.addDiameterDimension(circle, textPoint)
                    dimension.value = diameter_cm

                dimensionsAdded += 1

        # Show offset in message if it's not zero
        if abs(offset_inches) > 0.0001:
            ui.messageBox(f'Applied {selectedHoleSize.name} dimension ({diameter_inches:.4f}") with {offset_inches:+.4f}" offset to {dimensionsAdded} circle(s)')
        else:
            ui.messageBox(f'Applied {selectedHoleSize.name} dimension ({diameter_inches:.4f}") to {dimensionsAdded} circle(s)')

    except Exception as e:
        ui.messageBox(f'Failed:\n{traceback.format_exc()}')

# This event handler is called when the command needs to compute a new preview
def command_preview(args: adsk.core.CommandEventArgs):
    try:
        inputs = args.command.commandInputs
        holeSizeDropdownInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
        customDiameterInp: adsk.core.ValueCommandInput = inputs.itemById('custom_diameter')
        offsetInp: adsk.core.ValueCommandInput = inputs.itemById('diameter_offset')
        circleSelectionInp: adsk.core.SelectionCommandInput = inputs.itemById('circle_selection')

        # Get the selected hole size
        selectedIndex = holeSizeDropdownInp.selectedItem.index
        selectedHoleSize = hole_sizes[selectedIndex]

        # Get the offset value (in cm, Fusion converts automatically)
        offset_cm = offsetInp.value

        # Determine the diameter to use
        if selectedHoleSize.name == 'Custom':
            # Custom input value is already in cm (Fusion converts it automatically)
            diameter_cm = customDiameterInp.value
        else:
            # Predefined values are in inches, need to convert to cm
            diameter_inches = selectedHoleSize.diameter
            diameter_cm = diameter_inches * 2.54
        
        # Apply the offset to the diameter
        diameter_cm += offset_cm

        # Get the active design
        design = app.activeProduct
        if not design:
            return

        # Process each selected circle for preview
        for i in range(circleSelectionInp.selectionCount):
            selection = circleSelectionInp.selection(i)
            circle: adsk.fusion.SketchCircle = selection.entity

            if circle.objectType == adsk.fusion.SketchCircle.classType():
                # Get the sketch
                sketch = circle.parentSketch

                # Check if the circle already has a diameter dimension
                existingDim = None
                for dim in sketch.sketchDimensions:
                    if dim.objectType == adsk.fusion.SketchDiameterDimension.classType():
                        dimCircle = dim.entity
                        if dimCircle == circle:
                            existingDim = dim
                            break

                if existingDim:
                    # Update existing dimension
                    existingDim.value = diameter_cm
                else:
                    # Add new diameter dimension
                    centerPt = circle.centerSketchPoint.geometry
                    textPoint = adsk.core.Point3D.create(
                        centerPt.x + circle.radius * 0.7,
                        centerPt.y + circle.radius * 0.7,
                        centerPt.z
                    )
                    dimension = sketch.sketchDimensions.addDiameterDimension(circle, textPoint)
                    dimension.value = diameter_cm

        args.isValidResult = True

    except:
        args.isValidResult = False

# This event handler is called when the user changes anything in the command dialog
def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed_input = args.input
    inputs = args.inputs

    # If the hole size selection changed, update the description and custom diameter visibility
    if changed_input.id == 'hole_size':
        holeSizeDropdownInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
        customDiameterInp: adsk.core.ValueCommandInput = inputs.itemById('custom_diameter')
        descriptionTextInp: adsk.core.TextBoxCommandInput = inputs.itemById('description')
        
        selectedIndex = holeSizeDropdownInp.selectedItem.index
        selectedHoleSize = hole_sizes[selectedIndex]
        
        # Update description
        descriptionTextInp.text = selectedHoleSize.description
        
        # Enable/disable custom diameter input
        if selectedHoleSize.name == 'Custom':
            customDiameterInp.isEnabled = True
        else:
            customDiameterInp.isEnabled = False

# This event handler is called when the user interacts with any of the inputs
def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs
    
    circleSelectionInp: adsk.core.SelectionCommandInput = inputs.itemById('circle_selection')
    holeSizeDropdownInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
    customDiameterInp: adsk.core.ValueCommandInput = inputs.itemById('custom_diameter')
    
    # Validate that at least one circle is selected
    if circleSelectionInp.selectionCount < 1:
        args.areInputsValid = False
        return
    
    # If custom is selected, validate the custom diameter
    selectedIndex = holeSizeDropdownInp.selectedItem.index
    selectedHoleSize = hole_sizes[selectedIndex]
    
    if selectedHoleSize.name == 'Custom' and customDiameterInp.value <= 0:
        args.areInputsValid = False
        return
    
    args.areInputsValid = True

# This event handler is called when the command terminates
def command_destroy(args: adsk.core.CommandEventArgs):
    # General logging for debug
    # futil.log(f'{CMD_NAME} Command Destroy Event')

    global local_handlers
    local_handlers = []
