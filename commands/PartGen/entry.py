import adsk.core
import adsk.fusion
import os
import math
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_PartGenDialog'
CMD_NAME = 'Part Generator'
CMD_Description = 'Generate FRC shaft and tube parts'

IS_PROMOTED = False

ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

local_handlers = []

# Shaft types: (id, display_name, flat_to_flat_inches, bore_dia_inches)
# flat_to_flat is the "across flats" measurement for hex shafts.
# None means the user supplies the dimension (Custom Round Tube).
SHAFT_TYPES = [
    ('half_hex',        '1/2" Hex Shaft',   0.5,   0.159),
    ('three_eight_hex', '3/8" Hex Shaft',   0.375, 0.159),
    ('custom_round',    'Custom Round Tube', None,  None),
]

LENGTH_MODE_BETWEEN = 'Between Two Faces'
LENGTH_MODE_CUSTOM  = 'Custom Length'


# ---------------------------------------------------------------------------
# Add-in lifecycle
# ---------------------------------------------------------------------------

def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
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

    global local_handlers
    local_handlers = []


# ---------------------------------------------------------------------------
# Command creation – builds the dialog
# ---------------------------------------------------------------------------

def command_created(args: adsk.core.CommandCreatedEventArgs):
    inputs = args.command.commandInputs

    # --- Shaft type ---
    shaftTypeInp = inputs.addDropDownCommandInput(
        'shaft_type', 'Shaft Type',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    for _, shaft_name, _, _ in SHAFT_TYPES:
        shaftTypeInp.listItems.add(shaft_name, False, '')
    shaftTypeInp.listItems.item(0).isSelected = True

    # --- Custom round tube dimensions (hidden unless custom_round selected) ---
    default_units = 'in'
    outerDiaInp = inputs.addValueInput(
        'outer_diameter', 'Outer Diameter', default_units,
        adsk.core.ValueInput.createByString('1.0'))
    outerDiaInp.isVisible = False

    innerDiaInp = inputs.addValueInput(
        'inner_diameter', 'Inner Diameter', default_units,
        adsk.core.ValueInput.createByString('0.834'))   # ~1/16" wall on 1" OD
    innerDiaInp.isVisible = False

    # --- Length mode ---
    lengthModeInp = inputs.addDropDownCommandInput(
        'length_mode', 'Length Mode',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    lengthModeInp.listItems.add(LENGTH_MODE_BETWEEN, False, '')
    lengthModeInp.listItems.add(LENGTH_MODE_CUSTOM,  False, '')
    lengthModeInp.listItems.item(0).isSelected = True   # default: Between Two Faces

    # --- "Between Two Faces" inputs ---
    face1Inp = inputs.addSelectionInput(
        'face1', 'Start Face', 'Select the start face of the shaft')
    face1Inp.addSelectionFilter('PlanarFaces')
    face1Inp.setSelectionLimits(1, 1)

    face2Inp = inputs.addSelectionInput(
        'face2', 'End Face', 'Select the end face of the shaft')
    face2Inp.addSelectionFilter('PlanarFaces')
    face2Inp.setSelectionLimits(1, 1)
    face2Inp.isEnabled = False  # enabled after face1 is chosen

    # --- "Custom Length" inputs (hidden by default) ---
    placePlaneInp = inputs.addSelectionInput(
        'place_plane', 'Placement Plane',
        'Select the face or plane to start the shaft on')
    placePlaneInp.addSelectionFilter('PlanarFaces')
    placePlaneInp.addSelectionFilter('ConstructionPlanes')
    placePlaneInp.setSelectionLimits(1, 1)
    placePlaneInp.isVisible = False

    customLengthInp = inputs.addValueInput(
        'custom_length', 'Length', default_units,
        adsk.core.ValueInput.createByString('6.0'))
    customLengthInp.isVisible = False

    futil.add_handler(args.command.execute,         command_execute,        local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged,    command_input_changed,  local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview,  command_preview,        local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs,  command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy,         command_destroy,        local_handlers=local_handlers)


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    shaftTypeInp:  adsk.core.DropDownCommandInput = inputs.itemById('shaft_type')
    lengthModeInp: adsk.core.DropDownCommandInput = inputs.itemById('length_mode')

    shaftIdx = shaftTypeInp.selectedItem.index
    _, shaft_name, flat_to_flat, bore_dia = SHAFT_TYPES[shaftIdx]
    lengthMode = lengthModeInp.selectedItem.name

    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        rootComp = design.rootComponent
        trans = adsk.core.Matrix3D.create()
        workingOcc = rootComp.occurrences.addNewComponent(trans)
        workingComp = workingOcc.component
        # Give the component a readable name
        safe_name = shaft_name.replace('"', '').replace(' ', '_')
        workingComp.name = safe_name

        if lengthMode == LENGTH_MODE_BETWEEN:
            face1Inp: adsk.core.SelectionCommandInput = inputs.itemById('face1')
            face2Inp: adsk.core.SelectionCommandInput = inputs.itemById('face2')
            face1: adsk.fusion.BRepFace = face1Inp.selection(0).entity
            face2: adsk.fusion.BRepFace = face2Inp.selection(0).entity
            _create_shaft_between_faces(
                workingComp, shaftIdx, flat_to_flat, bore_dia, face1, face2, inputs)
        else:
            placePlaneInp: adsk.core.SelectionCommandInput = inputs.itemById('place_plane')
            customLengthInp: adsk.core.ValueCommandInput  = inputs.itemById('custom_length')
            place_plane = placePlaneInp.selection(0).entity
            length_cm   = customLengthInp.value          # Fusion stores values in cm
            _create_shaft_custom_length(
                workingComp, shaftIdx, flat_to_flat, bore_dia, place_plane, length_cm, inputs)

    except:
        futil.handle_error('PartGen Execute Failed', True)


# ---------------------------------------------------------------------------
# Core shaft-creation helpers
# ---------------------------------------------------------------------------

def _create_shaft_between_faces(
        comp: adsk.fusion.Component,
        shaft_type_idx: int,
        flat_to_flat: float,
        bore_dia: float,
        face1: adsk.fusion.BRepFace,
        face2: adsk.fusion.BRepFace,
        inputs: adsk.core.CommandInputs):
    """
    Create a shaft whose length is parametrically defined by the distance
    between face1 and face2.  Uses ToEntityExtentDefinition so that the
    shaft automatically updates when either face moves.
    """
    sketch = comp.sketches.add(face1)
    sketch.name = 'ShaftProfile'

    _draw_profile(sketch, shaft_type_idx, flat_to_flat, bore_dia, inputs)

    ring_profile = _get_ring_profile(sketch)

    extrusions = comp.features.extrudeFeatures
    ext_input = extrusions.createInput(
        ring_profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

    # ToEntityExtentDefinition makes the extrusion parametrically reach face2
    to_entity = adsk.fusion.ToEntityExtentDefinition.create(face2, False)
    ext_input.setOneSideExtent(
        to_entity, adsk.fusion.ExtentDirections.PositiveExtentDirection)

    extrusions.add(ext_input)


def _create_shaft_custom_length(
        comp: adsk.fusion.Component,
        shaft_type_idx: int,
        flat_to_flat: float,
        bore_dia: float,
        place_plane,
        length_cm: float,
        inputs: adsk.core.CommandInputs):
    """Create a shaft with a user-specified length on the given plane."""
    sketch = comp.sketches.add(place_plane)
    sketch.name = 'ShaftProfile'

    _draw_profile(sketch, shaft_type_idx, flat_to_flat, bore_dia, inputs)

    ring_profile = _get_ring_profile(sketch)

    extrusions = comp.features.extrudeFeatures
    ext_input = extrusions.createInput(
        ring_profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

    distance_extent = adsk.fusion.DistanceExtentDefinition.create(
        adsk.core.ValueInput.createByReal(length_cm))
    ext_input.setOneSideExtent(
        distance_extent, adsk.fusion.ExtentDirections.PositiveExtentDirection)

    extrusions.add(ext_input)


# ---------------------------------------------------------------------------
# Profile-drawing helpers
# ---------------------------------------------------------------------------

def _draw_profile(
        sketch: adsk.fusion.Sketch,
        shaft_type_idx: int,
        flat_to_flat: float,
        bore_dia: float,
        inputs: adsk.core.CommandInputs):
    """Dispatch to the correct profile drawer based on shaft type."""
    center = adsk.core.Point3D.create(0, 0, 0)

    if shaft_type_idx in (0, 1):
        # Hex shaft with a centre bore
        _draw_hex_profile(sketch, center, flat_to_flat, bore_dia)
    else:
        # Custom round tube
        outer_dia_inp: adsk.core.ValueCommandInput = inputs.itemById('outer_diameter')
        inner_dia_inp: adsk.core.ValueCommandInput = inputs.itemById('inner_diameter')
        outer_dia_in = outer_dia_inp.value / 2.54   # cm → inches
        inner_dia_in = inner_dia_inp.value / 2.54
        _draw_round_profile(sketch, center, outer_dia_in, inner_dia_in)


def _draw_hex_profile(
        sketch: adsk.fusion.Sketch,
        center: adsk.core.Point3D,
        flat_to_flat_in: float,
        bore_dia_in: float):
    """
    Draw a regular hexagon defined by its across-flats dimension and an
    inner bore circle, both centred on `center`.

    For a regular hexagon:
        circumradius = (flat_to_flat / sqrt(3))
    Because flat_to_flat = sqrt(3) * side_length and
    circumradius == side_length for a regular hexagon.
    """
    circumradius_cm = (flat_to_flat_in / math.sqrt(3)) * 2.54

    lines  = sketch.sketchCurves.sketchLines
    gc     = sketch.geometricConstraints

    # Six vertices at 30° offset so flat sides are at top/bottom
    pts = [
        adsk.core.Point3D.create(
            center.x + circumradius_cm * math.cos(math.radians(30 + i * 60)),
            center.y + circumradius_cm * math.sin(math.radians(30 + i * 60)),
            0)
        for i in range(6)
    ]

    hex_lines = [
        lines.addByTwoPoints(pts[i], pts[(i + 1) % 6])
        for i in range(6)
    ]

    # Make all sides equal so the hexagon stays regular
    for i in range(1, 6):
        gc.addEqual(hex_lines[0], hex_lines[i])

    # Centre bore circle
    if bore_dia_in is not None:
        bore_radius_cm = (bore_dia_in / 2.0) * 2.54
        sketch.sketchCurves.sketchCircles.addByCenterRadius(center, bore_radius_cm)


def _draw_round_profile(
        sketch: adsk.fusion.Sketch,
        center: adsk.core.Point3D,
        outer_dia_in: float,
        inner_dia_in: float):
    """Draw concentric outer and inner circles for a round tube profile."""
    outer_r_cm = (outer_dia_in / 2.0) * 2.54
    inner_r_cm = (inner_dia_in / 2.0) * 2.54
    circles = sketch.sketchCurves.sketchCircles
    circles.addByCenterRadius(center, outer_r_cm)
    circles.addByCenterRadius(center, inner_r_cm)


# ---------------------------------------------------------------------------
# Profile selection helper
# ---------------------------------------------------------------------------

def _get_ring_profile(sketch: adsk.fusion.Sketch) -> adsk.fusion.Profile:
    """
    Return the 'ring' profile – the one whose cross-section represents the
    shaft material (outer shape minus any inner bore).

    A ring profile has exactly two loops: one outer and one inner.
    If no such profile exists (e.g. solid shaft with no bore), fall back to
    the largest single-loop profile.
    """
    profiles = sketch.profiles

    # Prefer a profile with two loops (outer shell + inner bore cutout)
    for i in range(profiles.count):
        p = profiles.item(i)
        if p.profileLoops.count == 2:
            return p

    # Fallback: return the profile with the largest area
    best = profiles.item(0)
    best_area = best.areaProperties().area
    for i in range(1, profiles.count):
        p = profiles.item(i)
        area = p.areaProperties().area
        if area > best_area:
            best_area = area
            best = p
    return best


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def command_preview(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    lengthModeInp: adsk.core.DropDownCommandInput = inputs.itemById('length_mode')
    face1Inp:      adsk.core.SelectionCommandInput = inputs.itemById('face1')
    face2Inp:      adsk.core.SelectionCommandInput = inputs.itemById('face2')
    placePlaneInp: adsk.core.SelectionCommandInput = inputs.itemById('place_plane')

    lengthMode = lengthModeInp.selectedItem.name

    if lengthMode == LENGTH_MODE_BETWEEN:
        if face1Inp.selectionCount < 1 or face2Inp.selectionCount < 1:
            return
    else:
        if placePlaneInp.selectionCount < 1:
            return

    command_execute(args)
    args.isValidResult = True


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed_input = args.input
    inputs         = args.inputs

    shaftTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('shaft_type')
    lengthModeInp:  adsk.core.DropDownCommandInput  = inputs.itemById('length_mode')
    face1Inp:       adsk.core.SelectionCommandInput = inputs.itemById('face1')
    face2Inp:       adsk.core.SelectionCommandInput = inputs.itemById('face2')
    placePlaneInp:  adsk.core.SelectionCommandInput = inputs.itemById('place_plane')
    customLengthInp: adsk.core.ValueCommandInput    = inputs.itemById('custom_length')
    outerDiaInp:    adsk.core.ValueCommandInput     = inputs.itemById('outer_diameter')
    innerDiaInp:    adsk.core.ValueCommandInput     = inputs.itemById('inner_diameter')

    shaft_idx      = shaftTypeInp.selectedItem.index
    is_custom_round = (shaft_idx == 2)
    length_mode    = lengthModeInp.selectedItem.name
    is_between     = (length_mode == LENGTH_MODE_BETWEEN)

    # Custom round tube dimension fields
    outerDiaInp.isVisible = is_custom_round
    innerDiaInp.isVisible = is_custom_round

    # Between-faces fields
    face1Inp.isVisible      = is_between
    face2Inp.isVisible      = is_between

    # Enable face2 only once face1 has been selected
    face2Inp.isEnabled = is_between and (face1Inp.selectionCount >= 1)

    # Custom-length fields
    placePlaneInp.isVisible  = not is_between
    customLengthInp.isVisible = not is_between


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs

    lengthModeInp:  adsk.core.DropDownCommandInput  = inputs.itemById('length_mode')
    face1Inp:       adsk.core.SelectionCommandInput = inputs.itemById('face1')
    face2Inp:       adsk.core.SelectionCommandInput = inputs.itemById('face2')
    placePlaneInp:  adsk.core.SelectionCommandInput = inputs.itemById('place_plane')
    customLengthInp: adsk.core.ValueCommandInput    = inputs.itemById('custom_length')

    length_mode = lengthModeInp.selectedItem.name

    if length_mode == LENGTH_MODE_BETWEEN:
        args.areInputsValid = (
            face1Inp.selectionCount >= 1 and
            face2Inp.selectionCount >= 1)
    else:
        args.areInputsValid = (
            placePlaneInp.selectionCount >= 1 and
            customLengthInp.value > 0)


def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers
    local_handlers = []
