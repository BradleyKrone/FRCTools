"""
sprocket_gen.py  —  Chain Sprocket geometry for PartsGen

ANSI B29.1 roller chain sprocket tooth profiles for #25 (6.35 mm pitch)
and #35 (9.525 mm pitch) chain.  No flanges (chain provides lateral
guidance).
"""

import adsk.core
import adsk.fusion
import math
from ...lib import fusionAddInUtils as futil
from ... import config
from .pulley_gen import (
    _group_timeline_features,
    _engrave_label_face,
    HEX_BORE_FLATS_CM,
    LABEL_TEXT_HEIGHT_CM,
    LABEL_ENGRAVE_CM,
)

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Attribute keys
# ---------------------------------------------------------------------------
ATTR_GROUP                  = 'FRCTools_PartsGen'
ATTR_PART_TYPE              = 'part_type'
ATTR_SPROCKET_TOOTH_COUNT   = 'sprocket_tooth_count'
ATTR_SPROCKET_WIDTH         = 'sprocket_width_expr'
ATTR_SPROCKET_SHOW_TEETH    = 'sprocket_show_teeth'
ATTR_SPROCKET_CHAIN_TYPE    = 'sprocket_chain_type'     # '#25 Chain' or '#35 Chain'
# Auto-sprocket link-back attributes (set when created from chain_gen)
ATTR_SPROCKET_CHAIN_COMP_TOKEN = 'sprocket_chain_comp_token'
ATTR_SPROCKET_PITCH_CIRCLE_IDX = 'sprocket_pitch_circle_index'
ATTR_SPROCKET_CHAIN_PITCH      = 'sprocket_chain_pitch_mm'

# ---------------------------------------------------------------------------
# #25 Chain constants (ANSI B29.1)
# ---------------------------------------------------------------------------
CHAIN_25_PITCH_MM       = 6.35                                          # 0.25 in
CHAIN_25_ROLLER_DIAM_MM = 3.302                                         # 0.130 in
CHAIN_25_SEAT_RADIUS_MM = 0.5025 * CHAIN_25_ROLLER_DIAM_MM + 0.0762    # ≈ 1.735 mm

# ---------------------------------------------------------------------------
# #35 Chain constants (ANSI B29.1)
# ---------------------------------------------------------------------------
CHAIN_35_PITCH_MM       = 9.525                                         # 3/8 in
CHAIN_35_ROLLER_DIAM_MM = 5.08                                          # 0.200 in
CHAIN_35_SEAT_RADIUS_MM = 0.5025 * CHAIN_35_ROLLER_DIAM_MM + 0.0762    # ≈ 2.628 mm


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _sprocket_pitch_radius_cm(n_teeth: int, pitch_mm: float = CHAIN_25_PITCH_MM) -> float:
    # Chain pitch diameter: PD = pitch / sin(π/N)
    return (pitch_mm / math.sin(math.pi / n_teeth)) / 20   # mm→cm, diameter→radius


def _sprocket_tip_radius_cm(n_teeth: int, pitch_mm: float = CHAIN_25_PITCH_MM,
                             roller_diam_mm: float = CHAIN_25_ROLLER_DIAM_MM) -> float:
    # OD tip radius = pitch_radius + roller_radius
    pitch_r_mm = (pitch_mm / math.sin(math.pi / n_teeth)) / 2
    return (pitch_r_mm + roller_diam_mm / 2) / 10


# ---------------------------------------------------------------------------
# Hex bore (sprocket has no flanges — simple straight-through cut)
# ---------------------------------------------------------------------------

def _add_hex_bore_sprocket(comp: adsk.fusion.Component, width_cm: float):
    circumradius = (HEX_BORE_FLATS_CM / 2) / math.cos(math.radians(30))

    sk    = comp.sketches.add(comp.xYConstructionPlane)
    lines = sk.sketchCurves.sketchLines
    pts   = [
        adsk.core.Point3D.create(
            circumradius * math.cos(math.radians(30 + i * 60)),
            circumradius * math.sin(math.radians(30 + i * 60)),
            0,
        )
        for i in range(6)
    ]
    for i in range(6):
        lines.addByTwoPoints(pts[i], pts[(i + 1) % 6])

    comp.features.extrudeFeatures.addSimple(
        sk.profiles.item(0),
        adsk.core.ValueInput.createByReal(width_cm),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
    )


# ---------------------------------------------------------------------------
# Label — engrave tooth count on both flat faces
# ---------------------------------------------------------------------------

def _add_sprocket_label(comp: adsk.fusion.Component, width_cm: float, n_teeth: int):
    try:
        label = f'{n_teeth}T'
        _engrave_label_face(
            comp, label,
            z_offset_cm  = width_cm,
            cut_direction = adsk.fusion.ExtentDirections.NegativeExtentDirection,
            mirror        = False,
        )
        _engrave_label_face(
            comp, label,
            z_offset_cm  = 0.0,
            cut_direction = adsk.fusion.ExtentDirections.PositiveExtentDirection,
            mirror        = True,
        )
    except Exception:
        futil.handle_error('PartsGen _add_sprocket_label', show_message_box=True)


# ---------------------------------------------------------------------------
# Tooth profile sketch — generic ANSI roller chain sprocket
# ---------------------------------------------------------------------------

def _createSprocketGeometry(sketch: adsk.fusion.Sketch, n_teeth: int,
                             pitch_mm: float, roller_diam_mm: float,
                             seat_radius_mm: float) -> float:
    """Build a fully-constrained sprocket tooth profile for any ANSI roller chain.

    Returns the tip circle outer diameter in cm.
    """
    Rs_cm      = seat_radius_mm / 10
    pitch_r_cm = _sprocket_pitch_radius_cm(n_teeth, pitch_mm)
    tip_r_cm   = _sprocket_tip_radius_cm(n_teeth, pitch_mm, roller_diam_mm)

    gc     = sketch.geometricConstraints
    dims   = sketch.sketchDimensions
    curves = sketch.sketchCurves
    origin = adsk.core.Point3D.create(0, 0, 0)

    # --- Pitch circle (construction) — roller seats lie on this circle -----
    pitchCircle = curves.sketchCircles.addByCenterRadius(origin, pitch_r_cm)
    pitchCircle.isConstruction = True
    gc.addCoincident(pitchCircle.centerSketchPoint, sketch.originPoint)
    dims.addDiameterDimension(
        pitchCircle,
        adsk.core.Point3D.create(-pitch_r_cm / 4, 0, 0),
    ).value = pitch_r_cm * 2

    # --- Tip circle (construction) — tooth peaks lie on this circle --------
    tipCircle = curves.sketchCircles.addByCenterRadius(origin, tip_r_cm)
    tipCircle.isConstruction = True
    gc.addCoincident(tipCircle.centerSketchPoint, sketch.originPoint)
    dims.addDiameterDimension(
        tipCircle,
        adsk.core.Point3D.create(-tip_r_cm / 4, 0, 0),
    ).value = tip_r_cm * 2

    # --- Vertical construction line: center → tooth peak (up) --------------
    vertEnd  = adsk.core.Point3D.create(0, tip_r_cm * 1.1, 0)
    vertLine = curves.sketchLines.addByTwoPoints(sketch.originPoint, vertEnd)
    vertLine.isConstruction = True
    gc.addCoincident(vertLine.endSketchPoint, tipCircle)
    gc.addVertical(vertLine)

    # --- Pie construction line: center → valley center (at angle π/N) ------
    half_angle = math.pi / n_teeth
    pieEnd   = adsk.core.Point3D.create(
        pitch_r_cm * 1.1 * math.sin(half_angle),
        pitch_r_cm * 1.1 * math.cos(half_angle),
        0,
    )
    pieLine = curves.sketchLines.addByTwoPoints(sketch.originPoint, pieEnd)
    pieLine.isConstruction = True
    gc.addCoincident(pieLine.endSketchPoint, pitchCircle)
    dims.addAngularDimension(
        vertLine, pieLine,
        adsk.core.Point3D.create(tip_r_cm / 8, tip_r_cm / 8, 0),
    ).value = half_angle

    # --- Tip arc (right half of tooth tip, on tipCircle) -------------------
    tipArc = curves.sketchArcs.addByCenterStartSweep(
        sketch.originPoint, vertLine.endSketchPoint, -half_angle / 4)
    gc.addConcentric(tipArc, tipCircle)

    # Mirror of tipArc (left half)
    tipArcMirror = curves.sketchArcs.addByCenterStartSweep(
        sketch.originPoint, vertLine.endSketchPoint, half_angle / 4)
    gc.addConcentric(tipArcMirror, tipCircle)
    gc.addSymmetry(tipArc.startSketchPoint, tipArcMirror.endSketchPoint, vertLine)

    # --- Seating arc (concave, right half — centered at pieLine.endPoint) --
    seat_sweep = -(math.radians(35) - math.radians(60) / n_teeth)
    seat_start_approx = adsk.core.Point3D.create(
        pieLine.endSketchPoint.geometry.x + Rs_cm * math.cos(math.pi + half_angle + 0.5),
        pieLine.endSketchPoint.geometry.y + Rs_cm * math.sin(math.pi + half_angle + 0.5),
        0,
    )
    seatArc = curves.sketchArcs.addByCenterStartSweep(
        pieLine.endSketchPoint, seat_start_approx, seat_sweep)
    gc.addCoincident(seatArc.centerSketchPoint, pieLine)
    gc.addCoincident(seatArc.centerSketchPoint, pitchCircle)
    dims.addRadialDimension(
        seatArc,
        futil.offsetPoint3D(pieLine.endSketchPoint.geometry, -Rs_cm / 2, -Rs_cm / 2, 0),
    ).value = Rs_cm
    gc.addCoincident(seatArc.endSketchPoint, pieLine)

    # Mirror of seatArc (left half)
    seat_center_m = seatArc.centerSketchPoint.geometry
    seatArcMirror = curves.sketchArcs.addByCenterStartSweep(
        adsk.core.Point3D.create(-seat_center_m.x, seat_center_m.y, 0),
        seatArc.endSketchPoint,
        -seat_sweep,
    )
    gc.addSymmetry(seatArc.startSketchPoint, seatArcMirror.endSketchPoint, pieLine)

    # --- Flank line (straight, right side — tooth face) --------------------
    flank_end_approx = futil.offsetPoint3D(tipArc.startSketchPoint.geometry,
                                            Rs_cm * 0.5, -Rs_cm, 0)
    flankLine = curves.sketchLines.addByTwoPoints(
        tipArc.startSketchPoint, flank_end_approx)
    gc.addTangent(flankLine, tipArc)
    gc.addTangent(flankLine, seatArc)

    # Mirror of flankLine (left side)
    flank_end_m_approx = futil.offsetPoint3D(tipArcMirror.endSketchPoint.geometry,
                                              -Rs_cm * 0.5, -Rs_cm, 0)
    flankLineMirror = curves.sketchLines.addByTwoPoints(
        tipArcMirror.endSketchPoint, flank_end_m_approx)
    gc.addTangent(flankLineMirror, tipArcMirror)
    gc.addTangent(flankLineMirror, seatArcMirror)
    gc.addSymmetry(flankLine.endSketchPoint, flankLineMirror.endSketchPoint, vertLine)

    # --- Circular pattern (replicate one full tooth N times) ---------------
    tooth_entities = [
        seatArcMirror, flankLineMirror, tipArcMirror,
        tipArc, flankLine, seatArc,
    ]
    pattern_input = gc.createCircularPatternInput(tooth_entities, sketch.originPoint)
    pattern_input.quantity = adsk.core.ValueInput.createByReal(n_teeth)
    gc.addCircularPattern(pattern_input)

    return tip_r_cm * 2  # OD in cm


def createSprocket25ChainGeometry(sketch: adsk.fusion.Sketch, n_teeth: int) -> float:
    """Build #25 chain sprocket tooth profile. Returns tip circle OD in cm."""
    return _createSprocketGeometry(sketch, n_teeth,
                                    CHAIN_25_PITCH_MM, CHAIN_25_ROLLER_DIAM_MM,
                                    CHAIN_25_SEAT_RADIUS_MM)


def createSprocket35ChainGeometry(sketch: adsk.fusion.Sketch, n_teeth: int) -> float:
    """Build #35 chain sprocket tooth profile. Returns tip circle OD in cm."""
    return _createSprocketGeometry(sketch, n_teeth,
                                    CHAIN_35_PITCH_MM, CHAIN_35_ROLLER_DIAM_MM,
                                    CHAIN_35_SEAT_RADIUS_MM)


# ---------------------------------------------------------------------------
# Public entry point — called by PartsGen/entry.py (standalone sprocket)
# ---------------------------------------------------------------------------

def _create_sprocket(inputs: adsk.core.CommandInputs):
    sprocket_tooth_count: adsk.core.ValueCommandInput     = inputs.itemById('sprocket_tooth_count')
    sprocket_width:       adsk.core.ValueCommandInput     = inputs.itemById('sprocket_width')
    sprocket_show_teeth:  adsk.core.BoolValueCommandInput = inputs.itemById('sprocket_show_teeth')
    chain_type_inp:       adsk.core.DropDownCommandInput  = inputs.itemById('sprocket_chain_type')

    n_teeth    = int(sprocket_tooth_count.value)
    width_cm   = sprocket_width.value
    show_teeth = sprocket_show_teeth.value if sprocket_show_teeth is not None else False

    # Determine chain type from dropdown (default #25 if input absent)
    is_35 = (chain_type_inp is not None and '#35' in chain_type_inp.selectedItem.name)
    if is_35:
        pitch_mm       = CHAIN_35_PITCH_MM
        roller_diam_mm = CHAIN_35_ROLLER_DIAM_MM
        seat_radius_mm = CHAIN_35_SEAT_RADIUS_MM
        chain_prefix   = 'Sprocket_35Chain'
        chain_type_str = '#35 Chain'
    else:
        pitch_mm       = CHAIN_25_PITCH_MM
        roller_diam_mm = CHAIN_25_ROLLER_DIAM_MM
        seat_radius_mm = CHAIN_25_SEAT_RADIUS_MM
        chain_prefix   = 'Sprocket_25Chain'
        chain_type_str = '#25 Chain'

    design   = adsk.fusion.Design.cast(app.activeProduct)
    rootComp = design.rootComponent
    start_marker = design.timeline.markerPosition
    trans    = adsk.core.Matrix3D.create()

    try:
        workingOcc = rootComp.occurrences.addNewComponent(trans)
    except RuntimeError:
        futil.popup_error(
            'Cannot create sprocket: this document is in Part Design mode, '
            'which only supports a single component.\n\n'
            'Please open or create an Assembly document and try again.'
        )
        return

    workingComp = workingOcc.component
    width_mm    = round(width_cm * 10)
    comp_name   = f'{chain_prefix}-{n_teeth}Tx{width_mm}mm'
    workingComp.name = comp_name

    extrudes   = workingComp.features.extrudeFeatures
    widthValue = adsk.core.ValueInput.createByReal(width_cm)

    if show_teeth:
        sketch = workingComp.sketches.add(rootComp.xYConstructionPlane, workingOcc)
        outer_diameter_cm = _createSprocketGeometry(sketch, n_teeth, pitch_mm,
                                                     roller_diam_mm, seat_radius_mm)

        if sketch.profiles.count != 1:
            futil.popup_error(
                f'PartsGen: Sprocket sketch has {sketch.profiles.count} profiles '
                f'(expected 1). The tooth geometry may not have closed correctly.'
            )
            workingOcc.deleteMe()
            return

        extrudes.addSimple(
            sketch.profiles.item(0),
            widthValue,
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        )
    else:
        tip_r_cm = _sprocket_tip_radius_cm(n_teeth, pitch_mm, roller_diam_mm)
        outer_diameter_cm = tip_r_cm * 2
        sk_cyl = workingComp.sketches.add(rootComp.xYConstructionPlane, workingOcc)
        sk_cyl.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(0, 0, 0), tip_r_cm
        )
        extrudes.addSimple(
            sk_cyl.profiles.item(0),
            widthValue,
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        )

    _add_hex_bore_sprocket(workingComp, width_cm)
    _add_sprocket_label(workingComp, width_cm, n_teeth)

    try:
        attrs = workingComp.attributes
        attrs.add(ATTR_GROUP, ATTR_PART_TYPE,            'Sprocket')
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_TOOTH_COUNT, str(n_teeth))
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_WIDTH,       sprocket_width.expression)
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_SHOW_TEETH,  str(show_teeth))
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_CHAIN_TYPE,  chain_type_str)
    except Exception:
        futil.log('PartsGen: failed to save sprocket attributes')

    _group_timeline_features(design, start_marker, comp_name)


# ---------------------------------------------------------------------------
# Auto-sprocket creation — called by chain_gen._create_chain()
# ---------------------------------------------------------------------------

def create_sprocket_for_chain(n_teeth: int, width_cm: float, chain_pitch_mm: float,
                               chain_occ: adsk.fusion.Occurrence = None,
                               proj_circle: adsk.fusion.SketchCircle = None,
                               show_teeth: bool = False,
                               circle_index: int = 0,
                               parent_comp: adsk.fusion.Component = None):
    """Create a sprocket component from raw parameters and attach it to a chain component.

    Mirrors create_pulley_for_belt() in pulley_gen.py.  chain_pitch_mm selects
    the chain standard: 6.35 → #25, 9.525 → #35.

    A revolute Joint is created between the sprocket and chain_occ so the
    sprocket repositions automatically when the C-C distance is edited.
    """
    is_35 = abs(chain_pitch_mm - CHAIN_35_PITCH_MM) < 0.01
    if is_35:
        pitch_mm       = CHAIN_35_PITCH_MM
        roller_diam_mm = CHAIN_35_ROLLER_DIAM_MM
        seat_radius_mm = CHAIN_35_SEAT_RADIUS_MM
        chain_prefix   = 'Sprocket_35Chain'
        chain_type_str = '#35 Chain'
    else:
        pitch_mm       = CHAIN_25_PITCH_MM
        roller_diam_mm = CHAIN_25_ROLLER_DIAM_MM
        seat_radius_mm = CHAIN_25_SEAT_RADIUS_MM
        chain_prefix   = 'Sprocket_25Chain'
        chain_type_str = '#25 Chain'

    design   = adsk.fusion.Design.cast(app.activeProduct)
    parent   = parent_comp if parent_comp is not None else design.rootComponent
    start_marker = design.timeline.markerPosition

    # Pre-position the occurrence at the pitch-circle centre
    trans = adsk.core.Matrix3D.create()
    if proj_circle is not None:
        c = proj_circle.centerSketchPoint.geometry
        trans.translation = adsk.core.Vector3D.create(c.x, c.y, 0.0)
    workingOcc  = parent.occurrences.addNewComponent(trans)
    workingComp = workingOcc.component

    width_mm  = round(width_cm * 10)
    comp_name = f'{chain_prefix}-{n_teeth}Tx{width_mm}mm'
    workingComp.name = comp_name

    extrudes   = workingComp.features.extrudeFeatures
    widthValue = adsk.core.ValueInput.createByReal(width_cm)

    if show_teeth:
        sketch = workingComp.sketches.add(workingComp.xYConstructionPlane)
        outer_diameter_cm = _createSprocketGeometry(sketch, n_teeth, pitch_mm,
                                                     roller_diam_mm, seat_radius_mm)
        if sketch.profiles.count != 1:
            futil.popup_error(
                f'PartsGen: auto-sprocket sketch has {sketch.profiles.count} profiles '
                f'(expected 1, n_teeth={n_teeth}, pitch={pitch_mm}mm) — aborting.'
            )
            workingOcc.deleteMe()
            return
        extrudes.addSimple(sketch.profiles.item(0), widthValue,
                           adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        joint_circle = sketch.sketchCurves.sketchCircles.item(0)
    else:
        tip_r_cm = _sprocket_tip_radius_cm(n_teeth, pitch_mm, roller_diam_mm)
        outer_diameter_cm = tip_r_cm * 2
        sk_cyl = workingComp.sketches.add(workingComp.xYConstructionPlane)
        sk_cyl.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(0, 0, 0), tip_r_cm)
        extrudes.addSimple(sk_cyl.profiles.item(0), widthValue,
                           adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        joint_circle = sk_cyl.sketchCurves.sketchCircles.item(0)

    _add_hex_bore_sprocket(workingComp, width_cm)
    _add_sprocket_label(workingComp, width_cm, n_teeth)

    try:
        attrs = workingComp.attributes
        attrs.add(ATTR_GROUP, ATTR_PART_TYPE,            'Sprocket')
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_TOOTH_COUNT, str(n_teeth))
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_WIDTH,       f'{width_mm} mm')
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_SHOW_TEETH,  str(show_teeth))
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_CHAIN_TYPE,  chain_type_str)
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_CHAIN_PITCH, str(pitch_mm))
        if chain_occ is not None:
            attrs.add(ATTR_GROUP, ATTR_SPROCKET_CHAIN_COMP_TOKEN, chain_occ.component.entityToken)
            attrs.add(ATTR_GROUP, ATTR_SPROCKET_PITCH_CIRCLE_IDX, str(circle_index))
    except Exception:
        futil.log('PartsGen: failed to save auto-sprocket attributes')

    # Parametric revolute Joint: sprocket centre <-> projected pitch-circle centre
    if proj_circle is not None and chain_occ is not None:
        try:
            outer_circle = joint_circle.createForAssemblyContext(workingOcc)
            sprocket_geom = adsk.fusion.JointGeometry.createByCurve(
                outer_circle,
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            chain_geom = adsk.fusion.JointGeometry.createByCurve(
                proj_circle,
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            joint_input = parent.joints.createInput(sprocket_geom, chain_geom)
            joint_input.setAsRevoluteJointMotion(
                adsk.fusion.JointDirections.ZAxisJointDirection)
            joint = parent.joints.add(joint_input)
            joint.name = f'{comp_name}_revolute'
        except Exception:
            futil.handle_error(f'PartsGen: joint for {comp_name}', show_message_box=True)

    _group_timeline_features(design, start_marker, comp_name)
