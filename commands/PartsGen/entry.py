import adsk.core
import adsk.fusion
import os
import math
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_PartsGenDialog'
CMD_NAME = 'Parts Gen'
CMD_Description = 'Create FRC robot parts (shafts and tubes)'

IS_PROMOTED = False

ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

local_handlers         = []
edit_local_handlers   = []
ui_handlers            = []
_edit_target_occ       = None   # occurrence being edited; set in ui_command_starting
_selected_partsgen_occ = None   # currently-selected PartsGen occ; tracked by ui_selection_changed

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
IN_TO_CM = 2.54

# ---------------------------------------------------------------------------
# Part types
# ---------------------------------------------------------------------------
PART_SHAFT = 'Shaft'
PART_TUBE  = 'Tube'

# ---------------------------------------------------------------------------
# Shaft types
# ---------------------------------------------------------------------------
SHAFT_HALF_HEX       = '1/2" Hex Shaft'
SHAFT_THREE_EIGHTH_HEX = '3/8" Hex Shaft'
SHAFT_CUSTOM         = 'Custom (Round Tube)'

# Half-inch hex: flat-to-flat = 0.500 in  →  circumradius = 0.500 / sqrt(3)
SHAFT_HALF_HEX_CR_CM       = (0.500 / math.sqrt(3)) * IN_TO_CM
# Three-eighth hex: flat-to-flat = 0.375 in  →  circumradius = 0.375 / sqrt(3)
SHAFT_THREE_EIGHTH_HEX_CR_CM = (0.375 / math.sqrt(3)) * IN_TO_CM
# Bore shared by both standard hex shafts
SHAFT_BORE_RADIUS_CM = (0.159 / 2.0) * IN_TO_CM

# ---------------------------------------------------------------------------
# Tube thickness options  (inches, label)
# ---------------------------------------------------------------------------
THICK_1_16   = '1/16"'
THICK_1_8    = '1/8"'
THICK_CUSTOM = 'Custom'

THICKNESS_MAP = {
    THICK_1_16: 0.0625 * IN_TO_CM,
    THICK_1_8:  0.125  * IN_TO_CM,
}

# ---------------------------------------------------------------------------
# Hole placement
# ---------------------------------------------------------------------------
HOLE_OFFSET_CM = 0.5 * IN_TO_CM   # 0.5 in from two edges

# ---------------------------------------------------------------------------
# Hole size options
# ---------------------------------------------------------------------------
HOLE_RIVENUT = 'Rivenut (19/64")'
HOLE_10_32   = '10-32 (13/64")'
HOLE_CUSTOM  = 'Custom'

HOLE_SIZE_MAP = {
    HOLE_RIVENUT: (19.0 / 64.0) * IN_TO_CM,
    HOLE_10_32:   (13.0 / 64.0) * IN_TO_CM,
}

# ---------------------------------------------------------------------------
# Length options
# ---------------------------------------------------------------------------
LEN_FACES  = 'Between Two Faces'
LEN_CUSTOM = 'Custom Length'

# ---------------------------------------------------------------------------
# Edit command  (shown only via the right-click marking menu)
# ---------------------------------------------------------------------------
EDIT_CMD_ID          = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_PartsGenEdit'
EDIT_CMD_NAME        = 'Edit with PartsGen'
EDIT_CMD_Description = 'Edit an existing PartsGen part'

# Attribute group / keys — persisted on the component so it can be re-edited
ATTR_GROUP        = 'FRCTools_PartsGen'
ATTR_PART_TYPE    = 'part_type'
ATTR_SHAFT_TYPE   = 'shaft_type'
ATTR_CUSTOM_OD    = 'custom_od_expr'
ATTR_CUSTOM_ID    = 'custom_id_expr'
ATTR_TUBE_WIDTH   = 'tube_width_expr'
ATTR_TUBE_HEIGHT  = 'tube_height_expr'
ATTR_TUBE_THICK   = 'tube_thickness'
ATTR_CUSTOM_THICK = 'custom_thickness_expr'
ATTR_ADD_HOLES    = 'tube_add_holes'
ATTR_HOLE_SIZE    = 'hole_size'
ATTR_HOLE_DIAM    = 'hole_diam_expr'
ATTR_LEN_EXPR     = 'custom_len_expr'


# ===========================================================================
# start / stop
# ===========================================================================

def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER
    )
    futil.add_handler(cmd_def.commandCreated, command_created)

    submenu = config.get_solid_submenu()
    control = submenu.controls.addCommand(cmd_def)
    control.isPromoted = IS_PROMOTED

    # Edit command — shown only via the right-click marking menu; no toolbar button
    edit_cmd_def = ui.commandDefinitions.addButtonDefinition(
        EDIT_CMD_ID, EDIT_CMD_NAME, EDIT_CMD_Description, ICON_FOLDER
    )
    futil.add_handler(edit_cmd_def.commandCreated, edit_command_created)

    futil.add_handler(ui.commandStarting,        ui_command_starting, local_handlers=ui_handlers)
    futil.add_handler(ui.activeSelectionChanged, ui_selection_changed, local_handlers=ui_handlers)
    futil.add_handler(ui.markingMenuDisplaying,  ui_marking_menu,      local_handlers=ui_handlers)


def stop():
    submenu = config.get_solid_submenu()
    command_control  = submenu.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)
    edit_cmd_def       = ui.commandDefinitions.itemById(EDIT_CMD_ID)

    if command_control:
        command_control.isPromoted = False
        command_control.deleteMe()
    if command_definition:
        command_definition.deleteMe()
    if edit_cmd_def:
        edit_cmd_def.deleteMe()

    global ui_handlers
    ui_handlers = []


# ===========================================================================
# command_created  –  build the dialog
# ===========================================================================

def command_created(args: adsk.core.CommandCreatedEventArgs):
    inputs = args.command.commandInputs

    # --- Part type -----------------------------------------------------------
    partTypeInp = inputs.addDropDownCommandInput(
        'part_type', 'Part Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    partTypeInp.listItems.add(PART_SHAFT, True, '')
    partTypeInp.listItems.add(PART_TUBE, False, '')

    # --- Shaft group ---------------------------------------------------------
    shaftTypeInp = inputs.addDropDownCommandInput(
        'shaft_type', 'Shaft Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    shaftTypeInp.listItems.add(SHAFT_HALF_HEX, True, '')
    shaftTypeInp.listItems.add(SHAFT_THREE_EIGHTH_HEX, False, '')
    shaftTypeInp.listItems.add(SHAFT_CUSTOM, False, '')

    customOD = inputs.addValueInput(
        'custom_od', 'Outer Diameter', 'in',
        adsk.core.ValueInput.createByString('0.75 in')
    )
    customOD.isVisible = False

    customID = inputs.addValueInput(
        'custom_id', 'Bore Diameter', 'in',
        adsk.core.ValueInput.createByString('0.159 in')
    )
    customID.isVisible = False

    # --- Tube group ----------------------------------------------------------
    tubeWidthInp = inputs.addValueInput(
        'tube_width', 'Width', 'in',
        adsk.core.ValueInput.createByString('2 in')
    )
    tubeWidthInp.isVisible = False

    tubeHeightInp = inputs.addValueInput(
        'tube_height', 'Height', 'in',
        adsk.core.ValueInput.createByString('1 in')
    )
    tubeHeightInp.isVisible = False

    tubeThickInp = inputs.addDropDownCommandInput(
        'tube_thickness', 'Wall Thickness', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    tubeThickInp.listItems.add(THICK_1_16, False, '')
    tubeThickInp.listItems.add(THICK_1_8, True, '')
    tubeThickInp.listItems.add(THICK_CUSTOM, False, '')
    tubeThickInp.isVisible = False

    customThickInp = inputs.addValueInput(
        'custom_thickness', 'Custom Thickness', 'in',
        adsk.core.ValueInput.createByString('0.1 in')
    )
    customThickInp.isVisible = False

    # --- Tube face holes -----------------------------------------------------
    tubeHolesInp = inputs.addBoolValueInput('tube_add_holes', 'Add Corner Holes', True, '', True)
    tubeHolesInp.isVisible = False

    holeSizeInp = inputs.addDropDownCommandInput(
        'hole_size', 'Hole Size', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    holeSizeInp.listItems.add(HOLE_RIVENUT, True, '')
    holeSizeInp.listItems.add(HOLE_10_32, False, '')
    holeSizeInp.listItems.add(HOLE_CUSTOM, False, '')
    holeSizeInp.isVisible = False

    holeDiamInp = inputs.addValueInput(
        'hole_diameter', 'Custom Hole Diameter', 'in',
        adsk.core.ValueInput.createByString('0.25 in')
    )
    holeDiamInp.isVisible = False

    # --- Length group (shared) -----------------------------------------------
    lenTypeInp = inputs.addDropDownCommandInput(
        'length_type', 'Length', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    lenTypeInp.listItems.add(LEN_FACES, True, '')
    lenTypeInp.listItems.add(LEN_CUSTOM, False, '')

    face1Sel = inputs.addSelectionInput(
        'face1_selection', 'Face 1', 'Select the starting planar face'
    )
    face1Sel.addSelectionFilter('PlanarFaces')
    face1Sel.setSelectionLimits(1, 1)

    face2Sel = inputs.addSelectionInput(
        'face2_selection', 'Face 2', 'Select the ending planar face'
    )
    face2Sel.addSelectionFilter('PlanarFaces')
    face2Sel.setSelectionLimits(1, 1)

    customLenInp = inputs.addValueInput(
        'custom_length', 'Length', 'in',
        adsk.core.ValueInput.createByString('6 in')
    )
    customLenInp.isVisible = False

    # Wire events
    futil.add_handler(args.command.execute,        command_execute,        local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged,   command_input_changed,  local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview,        local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy,        command_destroy,        local_handlers=local_handlers)


# ===========================================================================
# inputChanged  –  show / hide inputs dynamically
# ===========================================================================

def command_input_changed(args: adsk.core.InputChangedEventArgs):
    inputs = args.inputs

    partTypeInp:    adsk.core.DropDownCommandInput   = inputs.itemById('part_type')
    shaftTypeInp:   adsk.core.DropDownCommandInput   = inputs.itemById('shaft_type')
    customOD:       adsk.core.ValueCommandInput      = inputs.itemById('custom_od')
    customID:       adsk.core.ValueCommandInput      = inputs.itemById('custom_id')
    tubeWidthInp:   adsk.core.ValueCommandInput      = inputs.itemById('tube_width')
    tubeHeightInp:  adsk.core.ValueCommandInput      = inputs.itemById('tube_height')
    tubeThickInp:   adsk.core.DropDownCommandInput   = inputs.itemById('tube_thickness')
    customThickInp: adsk.core.ValueCommandInput      = inputs.itemById('custom_thickness')
    tubeHolesInp:   adsk.core.BoolValueCommandInput  = inputs.itemById('tube_add_holes')
    holeSizeInp:    adsk.core.DropDownCommandInput   = inputs.itemById('hole_size')
    holeDiamInp:    adsk.core.ValueCommandInput      = inputs.itemById('hole_diameter')
    lenTypeInp:     adsk.core.DropDownCommandInput   = inputs.itemById('length_type')
    face1Sel:       adsk.core.SelectionCommandInput  = inputs.itemById('face1_selection')
    face2Sel:       adsk.core.SelectionCommandInput  = inputs.itemById('face2_selection')
    customLenInp:   adsk.core.ValueCommandInput      = inputs.itemById('custom_length')

    part_is_shaft    = (partTypeInp.selectedItem.name == PART_SHAFT)
    is_custom_shaft  = (shaftTypeInp.selectedItem.name == SHAFT_CUSTOM)
    is_custom_thick  = (tubeThickInp.selectedItem.name == THICK_CUSTOM)
    is_custom_hole   = (holeSizeInp.selectedItem.name  == HOLE_CUSTOM)
    is_between_faces = (lenTypeInp.selectedItem.name   == LEN_FACES)

    # Shaft inputs
    shaftTypeInp.isVisible   = part_is_shaft
    customOD.isVisible       = part_is_shaft and is_custom_shaft
    customID.isVisible       = part_is_shaft and is_custom_shaft

    # Tube inputs
    tubeWidthInp.isVisible   = not part_is_shaft
    tubeHeightInp.isVisible  = not part_is_shaft
    tubeThickInp.isVisible   = not part_is_shaft
    customThickInp.isVisible = not part_is_shaft and is_custom_thick
    tubeHolesInp.isVisible   = not part_is_shaft
    holeSizeInp.isVisible    = not part_is_shaft and tubeHolesInp.value
    holeDiamInp.isVisible    = not part_is_shaft and tubeHolesInp.value and is_custom_hole

    # Length inputs
    face1Sel.isVisible    = is_between_faces
    face2Sel.isVisible    = is_between_faces
    customLenInp.isVisible = not is_between_faces

    # Keep Fusion's internal selection validator in sync with visibility.
    # When face inputs are hidden (Custom Length), min=0 allows OK to be pressed.
    if is_between_faces:
        face1Sel.setSelectionLimits(1, 1)
        face2Sel.setSelectionLimits(1, 1)
    else:
        face1Sel.setSelectionLimits(0, 1)
        face2Sel.setSelectionLimits(0, 1)

    # Auto-advance to Face 2 once Face 1 is filled
    if args.input.id == 'face1_selection' and face1Sel.selectionCount >= 1:
        face2Sel.hasFocus = True


# ===========================================================================
# execute / preview
# ===========================================================================

def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    partTypeInp: adsk.core.DropDownCommandInput = inputs.itemById('part_type')
    if partTypeInp.selectedItem.name == PART_SHAFT:
        _create_shaft(inputs)
    else:
        _create_tube(inputs)


def command_preview(args: adsk.core.CommandEventArgs):
    command_execute(args)
    args.isValidResult = True


# ===========================================================================
# validateInputs
# ===========================================================================

def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs

    partTypeInp:    adsk.core.DropDownCommandInput  = inputs.itemById('part_type')
    shaftTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('shaft_type')
    customOD:       adsk.core.ValueCommandInput     = inputs.itemById('custom_od')
    customID:       adsk.core.ValueCommandInput     = inputs.itemById('custom_id')
    tubeWidthInp:   adsk.core.ValueCommandInput     = inputs.itemById('tube_width')
    tubeHeightInp:  adsk.core.ValueCommandInput     = inputs.itemById('tube_height')
    tubeThickInp:   adsk.core.DropDownCommandInput  = inputs.itemById('tube_thickness')
    customThickInp: adsk.core.ValueCommandInput     = inputs.itemById('custom_thickness')
    lenTypeInp:     adsk.core.DropDownCommandInput  = inputs.itemById('length_type')
    face1Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face1_selection')
    face2Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face2_selection')
    customLenInp:   adsk.core.ValueCommandInput     = inputs.itemById('custom_length')

    part_type = partTypeInp.selectedItem.name
    len_type  = lenTypeInp.selectedItem.name

    # Length validation (shared)
    if len_type == LEN_FACES:
        if face1Sel.selectionCount < 1 or face2Sel.selectionCount < 1:
            args.areInputsValid = False
            return
    else:
        if customLenInp.value <= 0:
            args.areInputsValid = False
            return

    if part_type == PART_SHAFT:
        if shaftTypeInp.selectedItem.name == SHAFT_CUSTOM:
            od = customOD.value
            id_ = customID.value
            if od <= 0 or id_ <= 0 or id_ >= od:
                args.areInputsValid = False
                return
    else:
        w = tubeWidthInp.value
        h = tubeHeightInp.value
        if tubeThickInp.selectedItem.name == THICK_CUSTOM:
            t = customThickInp.value
        else:
            t = THICKNESS_MAP[tubeThickInp.selectedItem.name]
        if w <= 0 or h <= 0 or t <= 0 or t >= min(w, h) / 2.0:
            args.areInputsValid = False
            return
        tubeHolesInp: adsk.core.BoolValueCommandInput = inputs.itemById('tube_add_holes')
        if tubeHolesInp.value:
            holeSizeInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
            if holeSizeInp.selectedItem.name == HOLE_CUSTOM:
                holeDiamInp: adsk.core.ValueCommandInput = inputs.itemById('hole_diameter')
                if holeDiamInp.value <= 0:
                    args.areInputsValid = False
                    return

    args.areInputsValid = True


# ===========================================================================
# destroy
# ===========================================================================

def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers
    local_handlers = []


# ===========================================================================
# Helpers — geometry
# ===========================================================================

def _bbox_center(face: adsk.fusion.BRepFace) -> adsk.core.Point3D:
    bb = face.boundingBox
    return adsk.core.Point3D.create(
        (bb.minPoint.x + bb.maxPoint.x) / 2.0,
        (bb.minPoint.y + bb.maxPoint.y) / 2.0,
        (bb.minPoint.z + bb.maxPoint.z) / 2.0,
    )


def _draw_hex(sketch: adsk.fusion.Sketch,
              center: adsk.core.Point3D,
              circumradius_cm: float):
    """Draw a regular hexagon (flat-side up/down) centred at `center` in sketch space."""
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


def _largest_profile(sketch: adsk.fusion.Sketch) -> adsk.fusion.Profile:
    """Return the sketch profile with the largest area."""
    best = sketch.profiles.item(0)
    best_area = best.areaProperties().area
    for i in range(1, sketch.profiles.count):
        p = sketch.profiles.item(i)
        a = p.areaProperties().area
        if a > best_area:
            best_area = a
            best = p
    return best


def _extrude_direction(face1: adsk.fusion.BRepFace,
                       centroid1: adsk.core.Point3D,
                       centroid2: adsk.core.Point3D):
    """Return the ExtentDirection such that the extrusion goes from face1 toward face2."""
    dx = centroid2.x - centroid1.x
    dy = centroid2.y - centroid1.y
    dz = centroid2.z - centroid1.z
    _, n = face1.evaluator.getNormalAtPoint(face1.pointOnFace)
    dot = dx * n.x + dy * n.y + dz * n.z
    return (
        adsk.fusion.ExtentDirections.PositiveExtentDirection
        if dot >= 0
        else adsk.fusion.ExtentDirections.NegativeExtentDirection
    )


def _extrude_one_side(comp: adsk.fusion.Component,
                      profile: adsk.fusion.Profile,
                      operation: adsk.fusion.FeatureOperations,
                      face2_or_none,
                      custom_len_expr,
                      ext_dir: adsk.fusion.ExtentDirections) -> adsk.fusion.ExtrudeFeature:
    """Extrude `profile` either to face2 (parametric) or by a fixed length.

    ``custom_len_expr`` is the raw expression string from the ValueCommandInput
    (e.g. "6 in" or a user-parameter name like "myLength").  Using
    ``createByString`` instead of ``createByReal`` preserves any parametric
    link so the feature updates when the user parameter changes.
    """
    extrudes = comp.features.extrudeFeatures
    extInput = extrudes.createInput(profile, operation)
    if face2_or_none is not None:
        extent = adsk.fusion.ToEntityExtentDefinition.create(face2_or_none, False)
    else:
        extent = adsk.fusion.DistanceExtentDefinition.create(
            adsk.core.ValueInput.createByString(custom_len_expr)
        )
    extInput.setOneSideExtent(extent, ext_dir)
    return extrudes.add(extInput)


# ===========================================================================
# Shaft creation
# ===========================================================================

def _create_shaft(inputs: adsk.core.CommandInputs):
    shaftTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('shaft_type')
    customOD:       adsk.core.ValueCommandInput     = inputs.itemById('custom_od')
    customID:       adsk.core.ValueCommandInput     = inputs.itemById('custom_id')
    lenTypeInp:     adsk.core.DropDownCommandInput  = inputs.itemById('length_type')
    face1Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face1_selection')
    face2Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face2_selection')
    customLenInp:   adsk.core.ValueCommandInput     = inputs.itemById('custom_length')

    shaft_type = shaftTypeInp.selectedItem.name
    len_type   = lenTypeInp.selectedItem.name

    # Resolve face references and sketch plane
    design    = adsk.fusion.Design.cast(app.activeProduct)
    rootComp  = design.rootComponent
    trans     = adsk.core.Matrix3D.create()
    workingOcc  = rootComp.occurrences.addNewComponent(trans)
    workingComp = workingOcc.component

    if len_type == LEN_FACES:
        face1: adsk.fusion.BRepFace = face1Sel.selection(0).entity
        face2: adsk.fusion.BRepFace = face2Sel.selection(0).entity
        centroid1 = _bbox_center(face1)
        centroid2 = _bbox_center(face2)
        ext_dir   = _extrude_direction(face1, centroid1, centroid2)
        sketch_plane = face1
        face2_target = face2
        custom_len_expr = None
    else:
        # Custom length — sketch on the XY construction plane at the origin.
        # Use .expression (the raw string the user typed, e.g. "6 in" or a
        # parameter name) so Fusion keeps a parametric link when a user
        # parameter is supplied.
        face1           = None
        face2_target    = None
        centroid1       = adsk.core.Point3D.create(0, 0, 0)
        ext_dir         = adsk.fusion.ExtentDirections.PositiveExtentDirection
        custom_len_expr = customLenInp.expression
        sketch_plane    = rootComp.xYConstructionPlane

    sketch: adsk.fusion.Sketch = workingComp.sketches.addWithoutEdges(sketch_plane)
    sketch.name = 'ShaftProfile'

    # Map centroid1 to sketch space for the cross-section centre
    c1_sk   = sketch.modelToSketchSpace(centroid1)
    center  = adsk.core.Point3D.create(c1_sk.x, c1_sk.y, 0.0)

    # --- Draw outer profile -------------------------------------------------
    if shaft_type == SHAFT_HALF_HEX:
        workingComp.name = 'Shaft_HalfInchHex'
        _draw_hex(sketch, center, SHAFT_HALF_HEX_CR_CM)
    elif shaft_type == SHAFT_THREE_EIGHTH_HEX:
        workingComp.name = 'Shaft_ThreeEighthHex'
        _draw_hex(sketch, center, SHAFT_THREE_EIGHTH_HEX_CR_CM)
    else:
        od_cm = customOD.value
        od_in = od_cm / IN_TO_CM
        workingComp.name = f'Shaft_Custom_{od_in:.4g}in'
        sketch.sketchCurves.sketchCircles.addByCenterRadius(center, od_cm / 2.0)

    if sketch.profiles.count < 1:
        futil.popup_error('Parts Gen: could not create a valid outer sketch profile.')
        return

    outer_profile = _largest_profile(sketch)

    # --- Extrude outer body -------------------------------------------------
    outer_feat = _extrude_one_side(
        workingComp, outer_profile,
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        face2_target, custom_len_expr, ext_dir
    )
    body = outer_feat.bodies.item(0)

    # --- Cut bore -----------------------------------------------------------
    bore_sketch: adsk.fusion.Sketch = workingComp.sketches.addWithoutEdges(sketch_plane)
    bore_sketch.name = 'BoreProfile'

    if shaft_type in (SHAFT_HALF_HEX, SHAFT_THREE_EIGHTH_HEX):
        bore_r = SHAFT_BORE_RADIUS_CM
    else:
        bore_r = customID.value / 2.0

    bore_sketch.sketchCurves.sketchCircles.addByCenterRadius(center, bore_r)

    if bore_sketch.profiles.count < 1:
        futil.popup_error('Parts Gen: could not create bore profile.')
        return

    bore_profile = bore_sketch.profiles.item(0)

    bore_extrudes = workingComp.features.extrudeFeatures
    bore_input = bore_extrudes.createInput(
        bore_profile, adsk.fusion.FeatureOperations.CutFeatureOperation
    )
    if face2_target is not None:
        bore_extent = adsk.fusion.ToEntityExtentDefinition.create(face2_target, False)
    else:
        bore_extent = adsk.fusion.DistanceExtentDefinition.create(
            adsk.core.ValueInput.createByString(custom_len_expr)
        )
    bore_input.setOneSideExtent(bore_extent, ext_dir)
    bore_input.participantBodies = [body]
    bore_extrudes.add(bore_input)

    # --- Save PartsGen attributes for right-click edit ----------------------
    try:
        comp_attrs = workingComp.attributes
        comp_attrs.add(ATTR_GROUP, ATTR_PART_TYPE,  PART_SHAFT)
        comp_attrs.add(ATTR_GROUP, ATTR_SHAFT_TYPE, shaftTypeInp.selectedItem.name)
        if shaftTypeInp.selectedItem.name == SHAFT_CUSTOM:
            comp_attrs.add(ATTR_GROUP, ATTR_CUSTOM_OD, customOD.expression)
            comp_attrs.add(ATTR_GROUP, ATTR_CUSTOM_ID, customID.expression)
        if len_type == LEN_FACES:
            d = math.sqrt(
                (centroid2.x - centroid1.x) ** 2 +
                (centroid2.y - centroid1.y) ** 2 +
                (centroid2.z - centroid1.z) ** 2
            )
            comp_attrs.add(ATTR_GROUP, ATTR_LEN_EXPR, f'{d / IN_TO_CM:.6g} in')
        else:
            comp_attrs.add(ATTR_GROUP, ATTR_LEN_EXPR, custom_len_expr)
    except Exception:
        futil.log('PartsGen: failed to save shaft attributes')


# ===========================================================================
# Tube creation
# ===========================================================================

def _create_tube(inputs: adsk.core.CommandInputs):
    tubeWidthInp:   adsk.core.ValueCommandInput     = inputs.itemById('tube_width')
    tubeHeightInp:  adsk.core.ValueCommandInput     = inputs.itemById('tube_height')
    tubeThickInp:   adsk.core.DropDownCommandInput  = inputs.itemById('tube_thickness')
    customThickInp: adsk.core.ValueCommandInput     = inputs.itemById('custom_thickness')
    lenTypeInp:     adsk.core.DropDownCommandInput  = inputs.itemById('length_type')
    face1Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face1_selection')
    face2Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face2_selection')
    customLenInp:   adsk.core.ValueCommandInput     = inputs.itemById('custom_length')

    w_cm = tubeWidthInp.value
    h_cm = tubeHeightInp.value
    if tubeThickInp.selectedItem.name == THICK_CUSTOM:
        t_cm = customThickInp.value
    else:
        t_cm = THICKNESS_MAP[tubeThickInp.selectedItem.name]

    len_type  = lenTypeInp.selectedItem.name

    design    = adsk.fusion.Design.cast(app.activeProduct)
    rootComp  = design.rootComponent
    trans     = adsk.core.Matrix3D.create()
    workingOcc  = rootComp.occurrences.addNewComponent(trans)
    workingComp = workingOcc.component

    w_in = w_cm / IN_TO_CM
    h_in = h_cm / IN_TO_CM
    t_in = t_cm / IN_TO_CM
    workingComp.name = f'Tube_{w_in:.4g}x{h_in:.4g}_T{t_in:.4g}in'

    if len_type == LEN_FACES:
        face1: adsk.fusion.BRepFace = face1Sel.selection(0).entity
        face2: adsk.fusion.BRepFace = face2Sel.selection(0).entity
        centroid1       = _bbox_center(face1)
        centroid2       = _bbox_center(face2)
        ext_dir         = _extrude_direction(face1, centroid1, centroid2)
        sketch_plane    = face1
        face2_target    = face2
        custom_len_expr = None
        _, face1_normal = face1.evaluator.getNormalAtPoint(face1.pointOnFace)
        extrusion_axis  = face1_normal
    else:
        face1           = None
        face2_target    = None
        centroid1       = adsk.core.Point3D.create(0, 0, 0)
        ext_dir         = adsk.fusion.ExtentDirections.PositiveExtentDirection
        custom_len_expr = customLenInp.expression
        sketch_plane    = rootComp.xYConstructionPlane
        extrusion_axis  = adsk.core.Vector3D.create(0, 0, 1)

    # --- Outer rectangle sketch ---------------------------------------------
    outer_sketch: adsk.fusion.Sketch = workingComp.sketches.addWithoutEdges(sketch_plane)
    outer_sketch.name = 'TubeOuterProfile'

    # Centre the cross-section on face1 centroid (or origin for custom length)
    c_sk   = outer_sketch.modelToSketchSpace(centroid1)
    cx, cy = c_sk.x, c_sk.y

    hw = w_cm / 2.0   # half-width
    hh = h_cm / 2.0   # half-height

    lines = outer_sketch.sketchCurves.sketchLines
    corners_outer = [
        adsk.core.Point3D.create(cx - hw, cy - hh, 0),
        adsk.core.Point3D.create(cx + hw, cy - hh, 0),
        adsk.core.Point3D.create(cx + hw, cy + hh, 0),
        adsk.core.Point3D.create(cx - hw, cy + hh, 0),
    ]
    for i in range(4):
        lines.addByTwoPoints(corners_outer[i], corners_outer[(i + 1) % 4])

    if outer_sketch.profiles.count < 1:
        futil.popup_error('Parts Gen: could not create outer tube profile.')
        return

    outer_profile = _largest_profile(outer_sketch)

    # --- Extrude outer solid ------------------------------------------------
    outer_feat = _extrude_one_side(
        workingComp, outer_profile,
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        face2_target, custom_len_expr, ext_dir
    )
    body = outer_feat.bodies.item(0)

    # --- Inner rectangle (cut) sketch ---------------------------------------
    inner_sketch: adsk.fusion.Sketch = workingComp.sketches.addWithoutEdges(sketch_plane)
    inner_sketch.name = 'TubeInnerProfile'

    ihw = (w_cm - 2 * t_cm) / 2.0
    ihh = (h_cm - 2 * t_cm) / 2.0
    corners_inner = [
        adsk.core.Point3D.create(cx - ihw, cy - ihh, 0),
        adsk.core.Point3D.create(cx + ihw, cy - ihh, 0),
        adsk.core.Point3D.create(cx + ihw, cy + ihh, 0),
        adsk.core.Point3D.create(cx - ihw, cy + ihh, 0),
    ]
    inner_lines = inner_sketch.sketchCurves.sketchLines
    for i in range(4):
        inner_lines.addByTwoPoints(corners_inner[i], corners_inner[(i + 1) % 4])

    if inner_sketch.profiles.count < 1:
        futil.popup_error('Parts Gen: could not create inner tube profile.')
        return

    inner_profile = _largest_profile(inner_sketch)

    cut_extrudes  = workingComp.features.extrudeFeatures
    cut_input     = cut_extrudes.createInput(
        inner_profile, adsk.fusion.FeatureOperations.CutFeatureOperation
    )
    if face2_target is not None:
        cut_extent = adsk.fusion.ToEntityExtentDefinition.create(face2_target, False)
    else:
        cut_extent = adsk.fusion.DistanceExtentDefinition.create(
            adsk.core.ValueInput.createByString(custom_len_expr)
        )
    cut_input.setOneSideExtent(cut_extent, ext_dir)
    cut_input.participantBodies = [body]
    cut_extrudes.add(cut_input)

    # --- Holes on each outer face --------------------------------------------
    try:
        tubeHolesInp: adsk.core.BoolValueCommandInput = inputs.itemById('tube_add_holes')
        if tubeHolesInp is not None and tubeHolesInp.value:
            holeSizeInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
            if holeSizeInp.selectedItem.name == HOLE_CUSTOM:
                hole_diam_cm = inputs.itemById('hole_diameter').value
            else:
                hole_diam_cm = HOLE_SIZE_MAP[holeSizeInp.selectedItem.name]
            _add_face_holes(workingComp, body, t_cm, hole_diam_cm, extrusion_axis, custom_len_expr)
    except Exception:
        futil.handle_error('PartsGen _add_face_holes', show_message_box=True)

    # --- Save PartsGen attributes for right-click edit ----------------------
    try:
        comp_attrs = workingComp.attributes
        comp_attrs.add(ATTR_GROUP, ATTR_PART_TYPE,   PART_TUBE)
        comp_attrs.add(ATTR_GROUP, ATTR_TUBE_WIDTH,  tubeWidthInp.expression)
        comp_attrs.add(ATTR_GROUP, ATTR_TUBE_HEIGHT, tubeHeightInp.expression)
        comp_attrs.add(ATTR_GROUP, ATTR_TUBE_THICK,  tubeThickInp.selectedItem.name)
        if tubeThickInp.selectedItem.name == THICK_CUSTOM:
            comp_attrs.add(ATTR_GROUP, ATTR_CUSTOM_THICK, customThickInp.expression)
        _ahi = inputs.itemById('tube_add_holes')
        _add = _ahi.value if _ahi else False
        comp_attrs.add(ATTR_GROUP, ATTR_ADD_HOLES, str(_add))
        if _add:
            _hsi = inputs.itemById('hole_size')
            if _hsi:
                comp_attrs.add(ATTR_GROUP, ATTR_HOLE_SIZE, _hsi.selectedItem.name)
                if _hsi.selectedItem.name == HOLE_CUSTOM:
                    _hdi = inputs.itemById('hole_diameter')
                    if _hdi:
                        comp_attrs.add(ATTR_GROUP, ATTR_HOLE_DIAM, _hdi.expression)
        if len_type == LEN_FACES:
            d = math.sqrt(
                (centroid2.x - centroid1.x) ** 2 +
                (centroid2.y - centroid1.y) ** 2 +
                (centroid2.z - centroid1.z) ** 2
            )
            comp_attrs.add(ATTR_GROUP, ATTR_LEN_EXPR, f'{d / IN_TO_CM:.6g} in')
        else:
            comp_attrs.add(ATTR_GROUP, ATTR_LEN_EXPR, custom_len_expr)
    except Exception:
        futil.log('PartsGen: failed to save tube attributes')


# ===========================================================================
# Tube face holes — seed hole + feature rectangular pattern
# ===========================================================================

def _add_face_holes(comp: adsk.fusion.Component,
                    body: adsk.fusion.BRepBody,
                    wall_thickness_cm: float,
                    hole_diam_cm: float,
                    extrusion_axis: adsk.core.Vector3D,
                    custom_len_expr=None):
    """Drill holes on each outer long face.

    One seed hole is sketched and extruded, then a **feature** rectangular
    pattern replicates that cut.  Because the replication lives at the
    feature level (not the sketch level), Fusion re-drives every cut
    instance on each recompute — so when ``custom_len_expr`` is a user
    parameter the hole count updates automatically alongside the tube length.

    Pattern quantity along the tube axis:
      - Custom Length mode: ``floor(expr / 1 in)`` — fully parametric.
      - Between-Faces mode: computed once from the actual face edge length.
    """
    futil.log(f'_add_face_holes: starting, diam={hole_diam_cm:.4f} cm, faces={body.faces.count}')

    bb = body.boundingBox
    body_cx = (bb.minPoint.x + bb.maxPoint.x) / 2.0
    body_cy = (bb.minPoint.y + bb.maxPoint.y) / 2.0
    body_cz = (bb.minPoint.z + bb.maxPoint.z) / 2.0

    # --- Collect outer wall faces -------------------------------------------
    outer_faces = []
    seen_ids    = set()

    for face in body.faces:
        _, face_normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
        if abs(face_normal.dotProduct(extrusion_axis)) > 0.5:
            continue
        face_center = _bbox_center(face)
        to_face = adsk.core.Vector3D.create(
            face_center.x - body_cx,
            face_center.y - body_cy,
            face_center.z - body_cz,
        )
        if to_face.dotProduct(face_normal) <= 0:
            continue
        fid = face.tempId
        if fid in seen_ids:
            continue
        seen_ids.add(fid)
        outer_faces.append(face)

    futil.log(f'_add_face_holes: {len(outer_faces)} outer faces')

    holeArea = hole_diam_cm ** 2 * math.pi / 4.0

    for face in outer_faces:
        try:
            # --- Sketch: seed hole + direction construction lines -----------
            sketch: adsk.fusion.Sketch = comp.sketches.add(face)
            sketch.name = 'SeedHole'

            sketchEdges = adsk.core.ObjectCollection.create()
            for edge in face.edges:
                projected = sketch.project(edge)
                if projected.count > 0:
                    sketchEdges.add(projected.item(0))

            edge_list = []
            for idx in range(sketchEdges.count):
                e = sketchEdges.item(idx)
                try:
                    if e.length > 1e-4:
                        edge_list.append(e)
                except Exception:
                    pass

            if len(edge_list) < 2:
                futil.log('  _add_face_holes: insufficient edges, skipping face')
                continue

            edge_list.sort(key=lambda e: e.length, reverse=True)
            longEdge  = edge_list[0]
            shortEdge = edge_list[-1]

            lengthIn = longEdge.length  / IN_TO_CM
            widthIn  = shortEdge.length / IN_TO_CM

            if widthIn < 0.9:
                futil.log(f'  _add_face_holes: face too narrow ({widthIn:.2f}"), skipping')
                continue

            leUnitVec = futil.sketchLineUnitVec(longEdge)
            seUnitVec = futil.sketchLineUnitVec(shortEdge)
            cornerPoint = None

            if longEdge.startSketchPoint.geometry.isEqualTo(shortEdge.startSketchPoint.geometry):
                cornerPoint = longEdge.startSketchPoint
            elif longEdge.startSketchPoint.geometry.isEqualTo(shortEdge.endSketchPoint.geometry):
                cornerPoint = longEdge.startSketchPoint
                seUnitVec = futil.multVector2D(seUnitVec, -1.0)
            elif longEdge.endSketchPoint.geometry.isEqualTo(shortEdge.startSketchPoint.geometry):
                cornerPoint = longEdge.endSketchPoint
                leUnitVec = futil.multVector2D(leUnitVec, -1.0)
            elif longEdge.endSketchPoint.geometry.isEqualTo(shortEdge.endSketchPoint.geometry):
                cornerPoint = longEdge.endSketchPoint
                leUnitVec = futil.multVector2D(leUnitVec, -1.0)
                seUnitVec = futil.multVector2D(seUnitVec, -1.0)

            if cornerPoint is None:
                futil.log('  _add_face_holes: no shared corner, skipping face')
                continue

            # Seed hole circle
            diag = leUnitVec.copy()
            diag.add(seUnitVec)
            seedPt = adsk.core.Point3D.create(
                cornerPoint.geometry.x + diag.x,
                cornerPoint.geometry.y + diag.y,
                0.0,
            )
            cornerHole = sketch.sketchCurves.sketchCircles.addByCenterRadius(
                seedPt, hole_diam_cm / 2.0
            )

            textPt = futil.offsetPoint3D(cornerHole.centerSketchPoint.geometry, 0.1, 0.1, 0)
            diamDim = sketch.sketchDimensions.addDiameterDimension(cornerHole, textPt)
            diamDim.value = hole_diam_cm

            textPt = cornerPoint.geometry
            widthDim = sketch.sketchDimensions.addOffsetDimension(
                longEdge, cornerHole.centerSketchPoint, textPt)
            widthDim.value = HOLE_OFFSET_CM

            lengthDim = sketch.sketchDimensions.addOffsetDimension(
                shortEdge, cornerHole.centerSketchPoint, textPt)
            lengthDim.value = HOLE_OFFSET_CM

            # Construction lines from the corner in the two pattern directions.
            # Starting from cornerPoint (already a sketch vertex) avoids any
            # coincident-constraint conflict with the circle centre.
            # leUnitVec → along tube axis (from this corner toward far end).
            # seUnitVec → across the face width.
            cp = cornerPoint.geometry
            len_dir_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
                cp,
                adsk.core.Point3D.create(
                    cp.x + leUnitVec.x * 20.0,
                    cp.y + leUnitVec.y * 20.0,
                    0.0,
                ),
            )
            len_dir_line.isConstruction = True

            wid_dir_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
                cp,
                adsk.core.Point3D.create(
                    cp.x + seUnitVec.x * 20.0,
                    cp.y + seUnitVec.y * 20.0,
                    0.0,
                ),
            )
            wid_dir_line.isConstruction = True

            # --- Extrude-cut the single seed hole --------------------------
            seed_profile = None
            for profile in sketch.profiles:
                if abs(holeArea - profile.areaProperties().area) < holeArea * 0.05:
                    seed_profile = profile
                    break

            if seed_profile is None:
                futil.log('  _add_face_holes: seed profile not found')
                continue

            extrudes = comp.features.extrudeFeatures
            cutInput = extrudes.createInput(
                seed_profile, adsk.fusion.FeatureOperations.CutFeatureOperation
            )
            cutExtent = adsk.fusion.DistanceExtentDefinition.create(
                adsk.core.ValueInput.createByReal(wall_thickness_cm)
            )
            cutInput.setOneSideExtent(
                cutExtent, adsk.fusion.ExtentDirections.NegativeExtentDirection
            )
            cutInput.participantBodies = [body]
            seed_cut = extrudes.add(cutInput)

            # --- Feature rectangular pattern — replicates the seed cut -----
            # Using a feature pattern (not a sketch pattern) means that when
            # the tube length parameter updates, Fusion recomputes the pattern
            # quantity and re-drives every cut extrusion automatically.
            n_width  = max(1, int(widthIn))
            n_length = max(1, int(lengthIn))

            if custom_len_expr is not None:
                qty_length = adsk.core.ValueInput.createByString(
                    f'floor({custom_len_expr} / 1 in)'
                )
            else:
                qty_length = futil.Value(n_length)

            futil.log(f'  _add_face_holes: {n_width}×{n_length} pattern '
                      f'({widthIn:.2f}"×{lengthIn:.2f}")')

            feat_col = adsk.core.ObjectCollection.create()
            feat_col.add(seed_cut)

            pat_feats = comp.features.rectangularPatternFeatures
            pat_input = pat_feats.createInput(
                feat_col,
                len_dir_line,                              # direction 1 = tube axis
                qty_length,                                # parametric or fixed count
                adsk.core.ValueInput.createByString('1 in'),
                adsk.fusion.PatternDistanceType.SpacingPatternDistanceType,
            )
            if n_width > 1:
                pat_input.directionTwoEntity = wid_dir_line
                pat_input.quantityTwo        = futil.Value(n_width)
                pat_input.distanceTwo        = adsk.core.ValueInput.createByString('1 in')
            pat_feats.add(pat_input)

        except Exception:
            futil.handle_error('_add_face_holes face', show_message_box=True)


# ===========================================================================
# Helpers — entity → component / occurrence lookup
# ===========================================================================

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


def _find_partsgen_occ(entity) -> adsk.fusion.Occurrence:
    """Return the PartsGen Occurrence that owns entity, or None.

    In 3D design mode, entities from args.selectedEntities are proxy objects
    whose body.parentComponent resolves to the ROOT component rather than
    the sub-component.  We therefore use two preferred paths before falling
    back to the original token-search approach:

    1. entity.assemblyContext  — directly returns the Occurrence for any
       proxy BRep entity (face / edge / body) clicked in 3D root context.
    2. entity.nativeObject     — unwraps the proxy to get the native object,
       whose parentComponent IS the sub-component.
    """
    # --- Path 1: assemblyContext (fastest, most reliable in 3D root context) ---
    try:
        occ = entity.assemblyContext
        if occ and occ.isValid:
            comp = occ.component
            if comp.attributes.itemByName(ATTR_GROUP, ATTR_PART_TYPE):
                return occ
    except Exception:
        pass

    # --- Path 2: nativeObject (unwrap proxy, get native parentComponent) -------
    try:
        native = entity.nativeObject
        if native:
            comp = _get_entity_component(native)
            if comp and comp.attributes.itemByName(ATTR_GROUP, ATTR_PART_TYPE):
                token = comp.entityToken
                design = adsk.fusion.Design.cast(app.activeProduct)
                for occ in design.rootComponent.allOccurrences:
                    try:
                        if occ.component.entityToken == token:
                            return occ
                    except Exception:
                        pass
    except Exception:
        pass

    # --- Path 3: direct parentComponent (works inside component edit context) --
    comp = _get_entity_component(entity)
    if not comp:
        return None
    if not comp.attributes.itemByName(ATTR_GROUP, ATTR_PART_TYPE):
        return None
    try:
        token = comp.entityToken
        design = adsk.fusion.Design.cast(app.activeProduct)
        for occ in design.rootComponent.allOccurrences:
            try:
                if occ.component.entityToken == token:
                    return occ
            except Exception:
                pass
    except Exception:
        pass
    return None


# ===========================================================================
# UI-level event handlers  (selection tracking + marking menu)
# Mirrors the CCDistance pattern: track selection via activeSelectionChanged,
# save the target in commandStarting, add menu item always and show/hide it.
# ===========================================================================

def ui_selection_changed(args: adsk.core.ActiveSelectionEventArgs):
    """Track which (if any) PartsGen occurrence is currently selected."""
    global _selected_partsgen_occ

    _selected_partsgen_occ = None
    if app.activeProduct.objectType != adsk.fusion.Design.classType():
        return

    for sel in args.currentSelection:
        try:
            occ = _find_partsgen_occ(sel.entity)
            if occ:
                _selected_partsgen_occ = occ
                break
        except Exception:
            pass


def ui_command_starting(args: adsk.core.ApplicationCommandEventArgs):
    """Before a command starts, save the selected PartsGen occurrence.

    Firing a command clears the active selection, so ui_selection_changed will
    set _selected_partsgen_occ back to None.  We capture it here first.
    """
    global _edit_target_occ
    if _selected_partsgen_occ:
        _edit_target_occ = _selected_partsgen_occ


def ui_marking_menu(args: adsk.core.MarkingMenuEventArgs):
    """Always insert the Edit menu item; show it only when a PartsGen part is selected.

    Follows the CCDistance pattern: the menu item lives in the control list at all
    times (with a duplicate guard) and its visibility is toggled on every call.
    """
    global _edit_target_occ

    if app.activeProduct.objectType != adsk.fusion.Design.classType():
        return

    controls = args.linearMarkingMenu.controls

    # --- Ensure the menu item exists in this menu instance ------------------
    edit_menu_item = controls.itemById(EDIT_CMD_ID)
    if not edit_menu_item:
        edit_cmd_def = ui.commandDefinitions.itemById(EDIT_CMD_ID)
        if not edit_cmd_def:
            return
        try:
            # Append at end of the linear menu (safest across all Fusion versions)
            edit_menu_item = controls.addCommand(edit_cmd_def, '', False)
        except Exception:
            futil.log('PartsGen: could not add edit item to marking menu')
            return

    if not edit_menu_item:
        return

    # --- Find which PartsGen occurrence is relevant -------------------------
    # Search order:
    #   1. _selected_partsgen_occ — set by ui_selection_changed on left-click
    #   2. args.selectedEntities  — entities under the cursor when right-clicking
    #   3. ui.activeSelections    — current active selection (left-click state)
    found_occ = _selected_partsgen_occ

    if not found_occ:
        for ent in (args.selectedEntities or []):
            try:
                found_occ = _find_partsgen_occ(ent)
                if found_occ:
                    break
            except Exception:
                pass

    if not found_occ:
        for i in range(ui.activeSelections.count):
            try:
                sel = ui.activeSelections.item(i)
                if sel:
                    found_occ = _find_partsgen_occ(sel.entity)
                    if found_occ:
                        break
            except Exception:
                pass

    # Cache the target now so edit_command_created can use it even if selection
    # is cleared before the command fires.
    if found_occ:
        _edit_target_occ = found_occ

    edit_menu_item.isVisible = (found_occ is not None)


# ===========================================================================
# Edit command — created (dialog pre-populated from stored attributes)
# ===========================================================================

def edit_command_created(args: adsk.core.CommandCreatedEventArgs):
    global _edit_target_occ

    inputs = args.command.commandInputs

    # Read stored attributes from the targeted occurrence's component
    attrs = {}
    if _edit_target_occ:
        comp = _edit_target_occ.component
        for key in (ATTR_PART_TYPE, ATTR_SHAFT_TYPE, ATTR_CUSTOM_OD, ATTR_CUSTOM_ID,
                    ATTR_TUBE_WIDTH, ATTR_TUBE_HEIGHT, ATTR_TUBE_THICK, ATTR_CUSTOM_THICK,
                    ATTR_ADD_HOLES, ATTR_HOLE_SIZE, ATTR_HOLE_DIAM, ATTR_LEN_EXPR):
            a = comp.attributes.itemByName(ATTR_GROUP, key)
            if a:
                attrs[key] = a.value

    def _s(key, default=''):
        return attrs.get(key, default)

    def _b(key, default=True):
        v = attrs.get(key)
        return (v.lower() == 'true') if v is not None else default

    part_type       = _s(ATTR_PART_TYPE,    PART_SHAFT)
    shaft_type      = _s(ATTR_SHAFT_TYPE,   SHAFT_HALF_HEX)
    is_shaft        = (part_type == PART_SHAFT)
    is_custom_shaft = (shaft_type == SHAFT_CUSTOM)

    tube_thick      = _s(ATTR_TUBE_THICK,   THICK_1_8)
    is_custom_thick = (tube_thick == THICK_CUSTOM)

    add_holes       = _b(ATTR_ADD_HOLES,    True)
    hole_size       = _s(ATTR_HOLE_SIZE,    HOLE_RIVENUT)
    is_custom_hole  = (hole_size == HOLE_CUSTOM)

    len_expr        = _s(ATTR_LEN_EXPR,     '6 in')
    od_expr         = _s(ATTR_CUSTOM_OD,    '0.75 in')
    id_expr         = _s(ATTR_CUSTOM_ID,    '0.159 in')
    w_expr          = _s(ATTR_TUBE_WIDTH,   '2 in')
    h_expr          = _s(ATTR_TUBE_HEIGHT,  '1 in')
    thick_expr      = _s(ATTR_CUSTOM_THICK, '0.1 in')
    hole_diam_expr  = _s(ATTR_HOLE_DIAM,    '0.25 in')

    # --- Part type ---
    partTypeInp = inputs.addDropDownCommandInput(
        'part_type', 'Part Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    partTypeInp.listItems.add(PART_SHAFT, is_shaft,     '')
    partTypeInp.listItems.add(PART_TUBE,  not is_shaft, '')

    # --- Shaft group ---
    shaftTypeInp = inputs.addDropDownCommandInput(
        'shaft_type', 'Shaft Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    shaftTypeInp.listItems.add(SHAFT_HALF_HEX,         shaft_type == SHAFT_HALF_HEX,         '')
    shaftTypeInp.listItems.add(SHAFT_THREE_EIGHTH_HEX, shaft_type == SHAFT_THREE_EIGHTH_HEX, '')
    shaftTypeInp.listItems.add(SHAFT_CUSTOM,           is_custom_shaft,                      '')
    shaftTypeInp.isVisible = is_shaft

    customOD = inputs.addValueInput(
        'custom_od', 'Outer Diameter', 'in',
        adsk.core.ValueInput.createByString(od_expr)
    )
    customOD.isVisible = is_shaft and is_custom_shaft

    customID = inputs.addValueInput(
        'custom_id', 'Bore Diameter', 'in',
        adsk.core.ValueInput.createByString(id_expr)
    )
    customID.isVisible = is_shaft and is_custom_shaft

    # --- Tube group ---
    tubeWidthInp = inputs.addValueInput(
        'tube_width', 'Width', 'in',
        adsk.core.ValueInput.createByString(w_expr)
    )
    tubeWidthInp.isVisible = not is_shaft

    tubeHeightInp = inputs.addValueInput(
        'tube_height', 'Height', 'in',
        adsk.core.ValueInput.createByString(h_expr)
    )
    tubeHeightInp.isVisible = not is_shaft

    tubeThickInp = inputs.addDropDownCommandInput(
        'tube_thickness', 'Wall Thickness', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    tubeThickInp.listItems.add(THICK_1_16,   tube_thick == THICK_1_16,    '')
    tubeThickInp.listItems.add(THICK_1_8,    tube_thick == THICK_1_8,     '')
    tubeThickInp.listItems.add(THICK_CUSTOM, is_custom_thick,             '')
    tubeThickInp.isVisible = not is_shaft

    customThickInp = inputs.addValueInput(
        'custom_thickness', 'Custom Thickness', 'in',
        adsk.core.ValueInput.createByString(thick_expr)
    )
    customThickInp.isVisible = not is_shaft and is_custom_thick

    # --- Tube face holes ---
    tubeHolesInp = inputs.addBoolValueInput('tube_add_holes', 'Add Corner Holes', True, '', add_holes)
    tubeHolesInp.isVisible = not is_shaft

    holeSizeInp = inputs.addDropDownCommandInput(
        'hole_size', 'Hole Size', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    holeSizeInp.listItems.add(HOLE_RIVENUT, hole_size == HOLE_RIVENUT, '')
    holeSizeInp.listItems.add(HOLE_10_32,   hole_size == HOLE_10_32,   '')
    holeSizeInp.listItems.add(HOLE_CUSTOM,  is_custom_hole,            '')
    holeSizeInp.isVisible = not is_shaft and add_holes

    holeDiamInp = inputs.addValueInput(
        'hole_diameter', 'Custom Hole Diameter', 'in',
        adsk.core.ValueInput.createByString(hole_diam_expr)
    )
    holeDiamInp.isVisible = not is_shaft and add_holes and is_custom_hole

    # --- Length — always use Custom Length in edit mode; face refs are gone ---
    lenTypeInp = inputs.addDropDownCommandInput(
        'length_type', 'Length', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    lenTypeInp.listItems.add(LEN_FACES,  False, '')
    lenTypeInp.listItems.add(LEN_CUSTOM, True,  '')

    face1Sel = inputs.addSelectionInput(
        'face1_selection', 'Face 1', 'Select the starting planar face'
    )
    face1Sel.addSelectionFilter('PlanarFaces')
    face1Sel.setSelectionLimits(0, 1)
    face1Sel.isVisible = False

    face2Sel = inputs.addSelectionInput(
        'face2_selection', 'Face 2', 'Select the ending planar face'
    )
    face2Sel.addSelectionFilter('PlanarFaces')
    face2Sel.setSelectionLimits(0, 1)
    face2Sel.isVisible = False

    customLenInp = inputs.addValueInput(
        'custom_length', 'Length', 'in',
        adsk.core.ValueInput.createByString(len_expr)
    )
    customLenInp.isVisible = True

    # Wire events — reuse the same input-changed and validate handlers
    futil.add_handler(args.command.execute,        edit_command_execute,   local_handlers=edit_local_handlers)
    futil.add_handler(args.command.inputChanged,   command_input_changed,  local_handlers=edit_local_handlers)
    futil.add_handler(args.command.executePreview, edit_command_preview,   local_handlers=edit_local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=edit_local_handlers)
    futil.add_handler(args.command.destroy,        edit_command_destroy,   local_handlers=edit_local_handlers)


# ===========================================================================
# Edit command — execute (delete old occurrence, recreate with new params)
# ===========================================================================

def edit_command_execute(args: adsk.core.CommandEventArgs):
    global _edit_target_occ

    if _edit_target_occ:
        try:
            _edit_target_occ.deleteMe()
        except Exception:
            futil.log('PartsGen edit: could not delete old occurrence')
        _edit_target_occ = None

    inputs = args.command.commandInputs
    partTypeInp: adsk.core.DropDownCommandInput = inputs.itemById('part_type')
    if partTypeInp.selectedItem.name == PART_SHAFT:
        _create_shaft(inputs)
    else:
        _create_tube(inputs)


def edit_command_preview(args: adsk.core.CommandEventArgs):
    # No live preview for edit mode — the old component stays visible until OK.
    pass


def edit_command_destroy(args: adsk.core.CommandEventArgs):
    global edit_local_handlers, _edit_target_occ
    edit_local_handlers = []
    _edit_target_occ    = None
