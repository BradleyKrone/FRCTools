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

# WCP rounded hex stock (wcproducts.com/products/shaft-stock): a regular hex whose six
# corners are truncated by a circle, so it still drives hex bores but also pilots in a
# round bearing. Values taken from WCP's own STEP files (which are modelled in inches):
#   WCP-0914  .500" OD x .159" ID  -> hex flats 0.500", corner circle 13.74 mm
#   WCP-0911  .375" OD x .159" ID  -> hex flats 0.375", corner circle 10.24 mm
MM_TO_CM = 0.1

SHAFT_HALF_HEX_FLATS_CM         = 0.500 * IN_TO_CM
SHAFT_HALF_HEX_ROUND_DIA_CM     = 13.74 * MM_TO_CM
SHAFT_THREE_EIGHTH_FLATS_CM     = 0.375 * IN_TO_CM
SHAFT_THREE_EIGHTH_ROUND_DIA_CM = 10.24 * MM_TO_CM
SHAFT_BORE_DIA_CM               = 0.159 * IN_TO_CM

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


def _anchor_point(sketch: adsk.fusion.Sketch,
                  center: adsk.core.Point3D) -> adsk.fusion.SketchPoint:
    """Return a sketch point at `center` that a profile can be constrained against.

    A sketch on the XY plane is centred on the sketch origin, which is already a fixed
    reference. A sketch on a picked face is centred on that face's bounding box, which
    has no natural reference in the sketch, so anchor it with a fixed point.
    """
    if abs(center.x) < 1e-9 and abs(center.y) < 1e-9:
        return sketch.originPoint
    anchor = sketch.sketchPoints.add(adsk.core.Point3D.create(center.x, center.y, 0.0))
    anchor.isFixed = True
    return anchor


def _draw_rounded_hex(sketch: adsk.fusion.Sketch,
                      center: adsk.core.Point3D,
                      flats_cm: float,
                      round_dia_cm: float,
                      constrain: bool = True):
    """Draw a fully-constrained WCP-style rounded hex (six flats joined by six corner
    arcs) centred at `center` in sketch space: a regular hexagon of `flats_cm` across
    the flats, with its corners truncated by a circle of `round_dia_cm`. Flat-side
    up/down, matching the plain hex this replaced.

    Curves created at identical coordinates are *not* merged by Fusion — they come out
    with no constraints at all, so the profile closes only by luck and anything can drag
    it apart. Every point therefore gets stitched and constrained explicitly.

    `constrain=False` draws the geometry only. The constraint pass costs ~400 ms, which
    is far too slow for executePreview; preview skips it and command_execute rebuilds.
    """
    apothem = flats_cm / 2.0
    radius  = round_dia_cm / 2.0
    if radius <= apothem or radius >= apothem / math.cos(math.radians(30)):
        raise ValueError('rounded hex: corner circle must fall between the flats and the corners')

    cx, cy = center.x, center.y
    # Angular half-width of a flat, seen from the centre. Its ends sit on the circle,
    # so every point below is at `radius` from the centre.
    half_flat_ang = math.acos(apothem / radius)

    def _pt(angle_rad):
        return adsk.core.Point3D.create(
            cx + radius * math.cos(angle_rad),
            cy + radius * math.sin(angle_rad),
            0.0,
        )

    lines = sketch.sketchCurves.sketchLines
    arcs  = sketch.sketchCurves.sketchArcs
    flats   = []
    corners = []
    sketch.isComputeDeferred = True
    try:
        for i in range(6):
            phi = math.radians(30 + i * 60)          # outward normal of this flat
            flats.append(lines.addByTwoPoints(
                _pt(phi - half_flat_ang), _pt(phi + half_flat_ang)))
            corners.append(arcs.addByThreePoints(
                _pt(phi + half_flat_ang),                # flat end
                _pt(phi + math.radians(30)),             # tip of the truncated corner
                _pt(phi + math.radians(60) - half_flat_ang)))   # next flat's start
    finally:
        sketch.isComputeDeferred = False

    if not constrain:
        return

    # --- Constrain ----------------------------------------------------------
    # 30 degrees of freedom once the loop is stitched, removed by exactly the 30
    # constraints/dimensions below, so the sketch lands fully constrained.
    gc = sketch.geometricConstraints
    sd = sketch.sketchDimensions

    for i in range(6):                                                  # -24 dof
        gc.addCoincident(flats[i].endSketchPoint, corners[i].startSketchPoint)
        gc.addCoincident(corners[i].endSketchPoint, flats[(i + 1) % 6].startSketchPoint)

    anchor = _anchor_point(sketch, center)
    for arc in corners:                                                 # -12 dof
        gc.addCoincident(arc.centerSketchPoint, anchor)
    for arc in corners[1:]:                                             # -5 dof
        gc.addEqual(corners[0], arc)
    sd.addDiameterDimension(                                            # -1 dof
        corners[0], adsk.core.Point3D.create(cx + 1.2 * radius, cy + 1.2 * radius, 0.0))

    for i, flat in enumerate(flats):                                    # -6 dof
        phi = math.radians(30 + i * 60)
        sd.addOffsetDimension(flat, anchor, adsk.core.Point3D.create(
            cx + 0.55 * apothem * math.cos(phi),
            cy + 0.55 * apothem * math.sin(phi),
            0.0,
        ))

    # Flats 1 and 4 are the horizontal pair; the other four follow from those.
    gc.addHorizontal(flats[1])                                          # -2 dof
    gc.addHorizontal(flats[4])
    gc.addParallel(flats[0], flats[3])                                  # -2 dof
    gc.addParallel(flats[2], flats[5])
    # Text point picks the quadrant an angular dimension measures — keep these outside
    # the profile, on the side of the flat being dimensioned.
    sd.addAngularDimension(flats[1], flats[0],                          # -2 dof
                           adsk.core.Point3D.create(cx + 1.6 * radius, cy + 1.6 * radius, 0.0))
    sd.addAngularDimension(flats[1], flats[2],
                           adsk.core.Point3D.create(cx - 1.6 * radius, cy + 1.6 * radius, 0.0))


def _draw_circle(sketch: adsk.fusion.Sketch,
                 center: adsk.core.Point3D,
                 dia_cm: float,
                 constrain: bool = True) -> adsk.fusion.SketchCircle:
    """Draw a fully-constrained circle of `dia_cm` centred at `center` in sketch space."""
    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(center, dia_cm / 2.0)
    if constrain:
        anchor = _anchor_point(sketch, center)
        sketch.geometricConstraints.addCoincident(circle.centerSketchPoint, anchor)
        sketch.sketchDimensions.addDiameterDimension(circle, adsk.core.Point3D.create(
            center.x + dia_cm, center.y + dia_cm, 0.0))
    return circle


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

def _create_shaft(inputs: adsk.core.CommandInputs, constrain: bool = True):
    shaftTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('shaft_type')
    customOD:       adsk.core.ValueCommandInput     = inputs.itemById('custom_od')
    customID:       adsk.core.ValueCommandInput     = inputs.itemById('custom_id')
    lenTypeInp:     adsk.core.DropDownCommandInput  = inputs.itemById('length_type')
    face1Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face1_selection')
    face2Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face2_selection')
    customLenInp:   adsk.core.ValueCommandInput     = inputs.itemById('custom_length')

    shaft_type = shaftTypeInp.selectedItem.name
    len_type   = lenTypeInp.selectedItem.name

    # Defensive guard — command_validate_input already blocks these values from the
    # dialog's OK button, but executePreview can call this function with a transient
    # or momentarily-invalid value while the user is still typing.
    if shaft_type == SHAFT_CUSTOM:
        od_check = customOD.value
        id_check = customID.value
        if od_check <= 0 or id_check <= 0 or id_check >= od_check:
            futil.log('PartsGen _create_shaft: invalid OD/ID, skipping')
            return

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

    try:
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
            _draw_rounded_hex(sketch, center,
                              SHAFT_HALF_HEX_FLATS_CM, SHAFT_HALF_HEX_ROUND_DIA_CM,
                              constrain=constrain)
        elif shaft_type == SHAFT_THREE_EIGHTH_HEX:
            workingComp.name = 'Shaft_ThreeEighthHex'
            _draw_rounded_hex(sketch, center,
                              SHAFT_THREE_EIGHTH_FLATS_CM, SHAFT_THREE_EIGHTH_ROUND_DIA_CM,
                              constrain=constrain)
        else:
            od_cm = customOD.value
            od_in = od_cm / IN_TO_CM
            workingComp.name = f'Shaft_Custom_{od_in:.4g}in'
            _draw_circle(sketch, center, od_cm, constrain=constrain)

        if sketch.profiles.count < 1:
            futil.popup_error('Parts Gen: could not create a valid outer sketch profile.')
            workingOcc.deleteMe()
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
            bore_dia = SHAFT_BORE_DIA_CM
        else:
            bore_dia = customID.value

        _draw_circle(bore_sketch, center, bore_dia, constrain=constrain)

        if bore_sketch.profiles.count < 1:
            futil.popup_error('Parts Gen: could not create bore profile.')
            workingOcc.deleteMe()
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
    except Exception:
        try:
            workingOcc.deleteMe()
        except Exception:
            pass
        futil.handle_error('PartsGen _create_shaft', show_message_box=True)
