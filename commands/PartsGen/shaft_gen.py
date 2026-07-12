import adsk.core
import adsk.fusion
import math
from ...lib import fusionAddInUtils as futil

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
IN_TO_CM = 2.54

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PART_SHAFT  = 'Shaft'
LEN_FACES   = 'Between Two Faces'

SHAFT_HALF_HEX         = '1/2" Hex Shaft'
SHAFT_THREE_EIGHTH_HEX = '3/8" Hex Shaft'
SHAFT_CUSTOM           = 'Custom (Round Tube)'

SHAFT_HALF_HEX_CR_CM         = (0.500 / math.sqrt(3)) * IN_TO_CM
SHAFT_THREE_EIGHTH_HEX_CR_CM = (0.375 / math.sqrt(3)) * IN_TO_CM
SHAFT_BORE_RADIUS_CM          = (0.159 / 2.0) * IN_TO_CM

ATTR_GROUP      = 'FRCTools_PartsGen'
ATTR_PART_TYPE  = 'part_type'
ATTR_SHAFT_TYPE = 'shaft_type'
ATTR_CUSTOM_OD  = 'custom_od_expr'
ATTR_CUSTOM_ID  = 'custom_id_expr'
ATTR_LEN_EXPR   = 'custom_len_expr'


# ===========================================================================
# Helpers
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
    """Extrude `profile` either to face2 (parametric) or by a fixed length."""
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

    design       = adsk.fusion.Design.cast(app.activeProduct)
    rootComp     = design.rootComponent
    start_marker = design.timeline.markerPosition
    trans        = adsk.core.Matrix3D.create()
    try:
        workingOcc   = rootComp.occurrences.addNewComponent(trans)
    except RuntimeError:
        futil.popup_error(
            'Cannot create shaft: this document is in Part Design mode, '
            'which only supports a single component.\n\n'
            'Please open or create an Assembly document and try again.'
        )
        return
    workingComp  = workingOcc.component

    if len_type == LEN_FACES:
        face1: adsk.fusion.BRepFace = face1Sel.selection(0).entity
        face2: adsk.fusion.BRepFace = face2Sel.selection(0).entity
        centroid1       = _bbox_center(face1)
        centroid2       = _bbox_center(face2)
        ext_dir         = _extrude_direction(face1, centroid1, centroid2)
        sketch_plane    = face1
        face2_target    = face2
        custom_len_expr = None
    else:
        face1           = None
        face2_target    = None
        centroid1       = adsk.core.Point3D.create(0, 0, 0)
        ext_dir         = adsk.fusion.ExtentDirections.PositiveExtentDirection
        custom_len_expr = customLenInp.expression
        sketch_plane    = rootComp.xYConstructionPlane

    sketch: adsk.fusion.Sketch = workingComp.sketches.addWithoutEdges(sketch_plane)
    sketch.name = 'ShaftProfile'

    c1_sk  = sketch.modelToSketchSpace(centroid1)
    center = adsk.core.Point3D.create(c1_sk.x, c1_sk.y, 0.0)

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

    futil.group_timeline_features(design, start_marker, workingComp.name)
