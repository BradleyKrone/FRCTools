import adsk.core
import adsk.fusion
import os
import math
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface


CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_ShaftDialog'
CMD_NAME = 'Shaft Creation'
CMD_Description = 'Create a cylindrical or hex shaft between two planar faces'

IS_PROMOTED = False

ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []

# Fusion 360 internal length unit is cm. 1 inch = 2.54 cm.
IN_TO_CM = 2.54

# Shaft type labels
SHAFT_HEX = 'Hex Shaft (1/2" Hex)'
SHAFT_CUSTOM = 'Custom'

# Hex shaft: flat-to-flat diameter = 0.505 inches
# For a regular hexagon: circumradius = flat_to_flat / sqrt(3)
HEX_CIRCUMRADIUS_CM = (0.505 / math.sqrt(3)) * IN_TO_CM


def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)

    submenu = config.get_solid_submenu()
    control = submenu.controls.addCommand(cmd_def)
    control.isPromoted = IS_PROMOTED


def stop():
    submenu = config.get_solid_submenu()
    command_control = submenu.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    if command_control:
        command_control.isPromoted = False
        command_control.deleteMe()

    if command_definition:
        command_definition.deleteMe()


def command_created(args: adsk.core.CommandCreatedEventArgs):
    inputs = args.command.commandInputs

    # Face 1 selection
    face1Sel = inputs.addSelectionInput('face1_selection', 'Face 1', 'Select the first planar face')
    face1Sel.addSelectionFilter('PlanarFaces')
    face1Sel.setSelectionLimits(1, 1)

    # Face 2 selection
    face2Sel = inputs.addSelectionInput('face2_selection', 'Face 2', 'Select the second planar face')
    face2Sel.addSelectionFilter('PlanarFaces')
    face2Sel.setSelectionLimits(1, 1)

    # Shaft type dropdown
    shaftTypeInp = inputs.addDropDownCommandInput(
        'shaft_type', 'Shaft Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    shaftTypeInp.listItems.add(SHAFT_HEX, True, '')
    shaftTypeInp.listItems.add(SHAFT_CUSTOM, False, '')

    # Custom diameter (hidden until 'Custom' is selected)
    custom_default = adsk.core.ValueInput.createByString('0.5 in')
    customDia = inputs.addValueInput('custom_diameter', 'Diameter', 'in', custom_default)
    customDia.isVisible = False

    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


def _bbox_center(face: adsk.fusion.BRepFace) -> adsk.core.Point3D:
    """Return the bounding-box center of a BRepFace as a model-space Point3D."""
    bb = face.boundingBox
    return adsk.core.Point3D.create(
        (bb.minPoint.x + bb.maxPoint.x) / 2.0,
        (bb.minPoint.y + bb.maxPoint.y) / 2.0,
        (bb.minPoint.z + bb.maxPoint.z) / 2.0,
    )


def _draw_hex(sketch: adsk.fusion.Sketch, center: adsk.core.Point3D, circumradius_cm: float):
    """Draw a regular hexagon centered at `center` in sketch space.
    circumradius_cm is the center-to-vertex distance (= flat_to_flat / sqrt(3)).
    Vertex at 0° gives flat faces on top and bottom.
    """
    cx, cy = center.x, center.y
    vertices = [
        adsk.core.Point3D.create(
            cx + circumradius_cm * math.cos(math.radians(i * 60)),
            cy + circumradius_cm * math.sin(math.radians(i * 60)),
            0.0,
        )
        for i in range(6)
    ]
    lines = sketch.sketchCurves.sketchLines
    for i in range(6):
        lines.addByTwoPoints(vertices[i], vertices[(i + 1) % 6])


def _smallest_profile(sketch: adsk.fusion.Sketch) -> adsk.fusion.Profile:
    """Return the sketch profile with the smallest area (the drawn hex or circle)."""
    best = sketch.profiles.item(0)
    best_area = best.areaProperties().area
    for i in range(1, sketch.profiles.count):
        p = sketch.profiles.item(i)
        area = p.areaProperties().area
        if area < best_area:
            best_area = area
            best = p
    return best


def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs

    face1Sel: adsk.core.SelectionCommandInput = inputs.itemById('face1_selection')
    face2Sel: adsk.core.SelectionCommandInput = inputs.itemById('face2_selection')
    shaftTypeInp: adsk.core.DropDownCommandInput = inputs.itemById('shaft_type')
    customDia: adsk.core.ValueCommandInput = inputs.itemById('custom_diameter')

    face1: adsk.fusion.BRepFace = face1Sel.selection(0).entity
    face2: adsk.fusion.BRepFace = face2Sel.selection(0).entity

    shaft_type = shaftTypeInp.selectedItem.name
    centroid1 = _bbox_center(face1)
    centroid2 = _bbox_center(face2)

    # Compute the vector between the two face centroids (used for direction only)
    dx = centroid2.x - centroid1.x
    dy = centroid2.y - centroid1.y
    dz = centroid2.z - centroid1.z

    # Determine which direction to extrude relative to face1's outward normal.
    # Use the surface evaluator so this works for any planar face type (not just
    # faces whose geometry casts cleanly to adsk.core.Plane).
    _, face1_normal = face1.evaluator.getNormalAtPoint(face1.pointOnFace)
    dot = dx * face1_normal.x + dy * face1_normal.y + dz * face1_normal.z
    ext_dir = (
        adsk.fusion.ExtentDirections.PositiveExtentDirection
        if dot >= 0
        else adsk.fusion.ExtentDirections.NegativeExtentDirection
    )

    # Create a new sub-component to hold the shaft
    design = adsk.fusion.Design.cast(app.activeProduct)
    rootComp = design.rootComponent
    trans = adsk.core.Matrix3D.create()
    workingOcc = rootComp.occurrences.addNewComponent(trans)
    workingComp = workingOcc.component

    # addWithoutEdges creates the sketch on face1's plane without projecting
    # face1's boundary edges into the sketch. Projected edges would form extra
    # closed profiles and cause the wrong geometry to be extruded.
    sketch: adsk.fusion.Sketch = workingComp.sketches.addWithoutEdges(face1)
    sketch.name = 'ShaftProfile'

    # Convert model-space centroid1 into sketch-local 2D coordinates
    c1_sketch = sketch.modelToSketchSpace(centroid1)
    center = adsk.core.Point3D.create(c1_sketch.x, c1_sketch.y, 0.0)

    if shaft_type == SHAFT_HEX:
        workingComp.name = 'Shaft_HalfInchHex'
        _draw_hex(sketch, center, HEX_CIRCUMRADIUS_CM)
    else:
        diameter_cm = customDia.value
        diameter_in = diameter_cm / IN_TO_CM
        workingComp.name = f'Shaft_{diameter_in:.4g}in'
        sketch.sketchCurves.sketchCircles.addByCenterRadius(center, diameter_cm / 2.0)

    if sketch.profiles.count < 1:
        futil.popup_error('Shaft Creation: could not create a valid sketch profile on Face 1.')
        return

    # Pick the smallest-area profile — that is the drawn hex or circle, not any
    # surrounding reference geometry Fusion may have pulled in.
    profile = _smallest_profile(sketch)

    # Extrude to face2 parametrically — the shaft length updates if the faces move
    extrudes = workingComp.features.extrudeFeatures
    extInput = extrudes.createInput(profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    toEntityExtent = adsk.fusion.ToEntityExtentDefinition.create(face2, False)
    extInput.setOneSideExtent(toEntityExtent, ext_dir)
    extrudes.add(extInput)


def command_preview(args: adsk.core.CommandEventArgs):
    command_execute(args)
    args.isValidResult = True


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed_input = args.input
    inputs = args.inputs

    # Auto-advance focus to Face 2 once Face 1 is selected
    if changed_input.id == 'face1_selection':
        face1Sel: adsk.core.SelectionCommandInput = inputs.itemById('face1_selection')
        face2Sel: adsk.core.SelectionCommandInput = inputs.itemById('face2_selection')
        if face1Sel.selectionCount >= 1:
            face2Sel.hasFocus = True

    # Show/hide the custom diameter field based on shaft type
    elif changed_input.id == 'shaft_type':
        shaftTypeInp: adsk.core.DropDownCommandInput = inputs.itemById('shaft_type')
        customDia: adsk.core.ValueCommandInput = inputs.itemById('custom_diameter')
        customDia.isVisible = (shaftTypeInp.selectedItem.name == SHAFT_CUSTOM)


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs

    face1Sel: adsk.core.SelectionCommandInput = inputs.itemById('face1_selection')
    face2Sel: adsk.core.SelectionCommandInput = inputs.itemById('face2_selection')
    shaftTypeInp: adsk.core.DropDownCommandInput = inputs.itemById('shaft_type')
    customDia: adsk.core.ValueCommandInput = inputs.itemById('custom_diameter')

    if face1Sel.selectionCount < 1 or face2Sel.selectionCount < 1:
        args.areInputsValid = False
        return

    if shaftTypeInp.selectedItem.name == SHAFT_CUSTOM and customDia.value <= 0:
        args.areInputsValid = False
        return

    args.areInputsValid = True


def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers
    local_handlers = []
