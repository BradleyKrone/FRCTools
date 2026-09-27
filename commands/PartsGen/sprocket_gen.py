"""
sprocket_gen.py  —  Chain Sprocket geometry for PartsGen

ANSI B29.1 roller chain sprocket tooth profiles for #25 (6.35 mm pitch)
and #35 (9.525 mm pitch) chain.  No flanges (chain provides lateral
guidance).

Chain-generated sprockets are smooth disks at the tip OD (no teeth) with a 1/2in hex
bore and the tooth count engraved on both faces, like WCP's. When the chain's C-C
changes they are resized in place (update_sprocket_teeth) so joints made to them survive.
"""

import adsk.core
import adsk.fusion
import math
from ...lib import fusionAddInUtils as futil
from ... import config
from .pulley_gen import (
    _engrave_label_face,
    _offset_xy_plane,
    _draw_hex_bore,
    _bore_radius_cm,
    _feature_sketch,
    _set_circle_diameter,
    _log_unconstrained,
    _warn,
    BORE_HALF_HEX,
    BORE_OFFSET_DEFAULT_IN,
    LABEL_TEXT_HEIGHT_CM,
    LABEL_ENGRAVE_CM,
    LABEL_EDGE_MARGIN_CM,
)
from .shaft_gen import _draw_circle, _hide_joint

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
ATTR_SPROCKET_BORE_OFFSET      = 'sprocket_bore_offset'
ATTR_CUSTOM_NAME               = 'custom_name'

# Smallest label font before it is skipped (a tiny sprocket shrinks it to fit).
LABEL_MIN_TEXT_CM = 0.06 * 2.54

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


def _ansi_od_cm(n_teeth: int, pitch_mm: float) -> float:
    """ANSI sprocket outside diameter, P x (0.6 + cot(180/N)) -- what WCP cut theirs to
    (16T #25 1.406in, 18T #35 2.3517in, matched to 0.001in on their STEP files)."""
    return pitch_mm * (0.6 + 1 / math.tan(math.pi / n_teeth)) / 10


# ---------------------------------------------------------------------------
# Hex bore (sprocket has no flanges — simple straight-through cut)
# ---------------------------------------------------------------------------

def _add_hex_bore_sprocket(comp: adsk.fusion.Component, width_cm: float, tip_od_cm: float,
                           offset_cm: float = BORE_OFFSET_DEFAULT_IN * 2.54,
                           is_preview: bool = False):
    """Cut a 1/2in hex bore (grown by `offset_cm` on every flat) through the sprocket,
    from a fully-constrained sketch on its bottom face (Z=0)."""
    # A small tooth count can make the sprocket body narrower than the fixed 0.5in
    # hex bore, which would otherwise self-intersect the outer profile. Skip the
    # cut rather than risk invalid geometry or a Fusion exception.
    if _bore_radius_cm(BORE_HALF_HEX, offset_cm) >= tip_od_cm / 2:
        _warn(f'PartsGen: sprocket tip diameter ({tip_od_cm * 10:.1f} mm) is too small '
              f'for the 0.5in hex bore — skipping the bore cut. Choose a larger tooth count.',
              is_preview)
        return

    sk = comp.sketches.add(comp.xYConstructionPlane)
    _draw_hex_bore(sk, offset_cm)
    # participantBodies: a cut otherwise also goes through other bodies it overlaps.
    extrudes = comp.features.extrudeFeatures
    ext_in   = extrudes.createInput(sk.profiles.item(0),
                                    adsk.fusion.FeatureOperations.CutFeatureOperation)
    ext_in.setDistanceExtent(False, adsk.core.ValueInput.createByReal(width_cm))
    ext_in.participantBodies = [comp.bRepBodies.item(0)]
    extrudes.add(ext_in)


# ---------------------------------------------------------------------------
# WCP double-hub sprocket body (smooth rim) — measured off WCP's own STEP files:
# #25 WCP-0558/0560/0581/2106 (16-36T) and #35 WCP-0973/0975 (12, 18T). Every size shares
# the hubs: Ø0.750in both sides with a ~0.02in chamfer on their ends and a 0.039in fillet
# into the plate, a 1/2in hex bore, and a solid plate centred in the width whose teeth are
# chamfered on both sides at a 1:4 slope (a 75.96 deg cone) -- 1/32in deep and 1/8in in
# from the tip for #25, so the rim tapers to a thin land. Only the plate's diameter changes
# with tooth count. The plate's OD is the ANSI outside diameter (_ansi_od_cm), like WCP's.
# ---------------------------------------------------------------------------

WCP_HUB_DIA_CM      = 0.750 * 2.54
WCP_HUB_CHAMFER_CM  = 0.020 * 2.54
WCP_HUB_FILLET_CM   = 0.039 * 2.54
WCP_RIM_TAPER_SLOPE = 4.0            # rim chamfer: radial depth = 4 x axial depth
# chain pitch (mm) -> (overall hub-to-hub width, plate thickness, rim chamfer axial depth), cm
WCP_DOUBLE_HUB = {
    CHAIN_25_PITCH_MM: (0.375 * 2.54,   0.110 * 2.54, 0.03125 * 2.54),
    CHAIN_35_PITCH_MM: (0.53125 * 2.54, 0.169 * 2.54, 0.046875 * 2.54),
}
LABEL_ARC_MARGIN_CM = 0.03 * 2.54   # clearance between the label and the hub fillet / tooth root


def wcp_sprocket_width_cm(pitch_mm: float) -> float:
    """WCP's hub-to-hub width for a chain pitch."""
    return WCP_DOUBLE_HUB.get(pitch_mm, WCP_DOUBLE_HUB[CHAIN_25_PITCH_MM])[0]


def _plate_span_cm(width_cm: float, pitch_mm: float):
    """(z of the plate's bottom face, plate thickness): the plate is centred in the width."""
    plate_cm = WCP_DOUBLE_HUB.get(pitch_mm, WCP_DOUBLE_HUB[CHAIN_25_PITCH_MM])[1]
    plate_cm = min(plate_cm, width_cm)
    return (width_cm - plate_cm) / 2, plate_cm


def _circle_edges(body: adsk.fusion.BRepBody, radius_cm: float, z_values) -> list:
    """The body's circular edges on the axis of `radius_cm` at any of the Z levels (local frame)."""
    found = []
    for e in body.edges:
        c = adsk.core.Circle3D.cast(e.geometry)
        if (c is not None and abs(c.radius - radius_cm) < 1e-4
                and abs(c.center.x) < 1e-6 and abs(c.center.y) < 1e-6
                and min(abs(c.center.z - z) for z in z_values) < 1e-4):
            found.append(e)
    return found


def _edge_collection(edges) -> adsk.core.ObjectCollection:
    col = adsk.core.ObjectCollection.create()
    for e in edges:
        col.add(e)
    return col


def _taper_rim(comp: adsk.fusion.Component, tip_r_cm: float, z_values, axial_cm: float):
    """Chamfer the plate's two rim edges like WCP's teeth: `axial_cm` into the face and
    WCP_RIM_TAPER_SLOPE x that in from the tip. Which face a two-distance chamfer's first
    distance goes on can't be read beforehand, so each edge is tried both ways and the
    orientation that removes the right volume is kept (the wrong one asks for more depth
    than the plate has and fails, or cuts a different ring)."""
    radial_cm = WCP_RIM_TAPER_SLOPE * axial_cm
    # Triangle (axial x radial / 2) revolved about the axis at its centroid radius.
    expected = math.pi * axial_cm * radial_cm * (tip_r_cm - radial_cm / 3)
    chamfers = comp.features.chamferFeatures
    for z in z_values:
        done, tries = False, []
        for flip in (False, True):
            body = comp.bRepBodies.item(0)
            edges = _circle_edges(body, tip_r_cm, [z])
            if len(edges) != 1:
                break
            vol = body.volume
            try:
                ch_in = chamfers.createInput2()
                ch_in.chamferEdgeSets.addTwoDistancesChamferEdgeSet(
                    _edge_collection(edges), adsk.core.ValueInput.createByReal(axial_cm),
                    adsk.core.ValueInput.createByReal(radial_cm), flip, False)
                feat = chamfers.add(ch_in)
            except Exception as err:
                tries.append(f'flip={flip}: {err}')
                continue
            removed = vol - comp.bRepBodies.item(0).volume
            if (feat.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
                    and abs(removed - expected) < 0.05 * expected):
                done = True
                break
            tries.append(f'flip={flip}: removed {removed:.5f} cm3, expected {expected:.5f}')
            feat.deleteMe()
        if not done:
            futil.log(f'PartsGen: {comp.name} rim chamfer at z={z:.3f} failed, left square '
                      f'({"; ".join(tries)})')


def _build_double_hub(comp: adsk.fusion.Component, n_teeth: int, pitch_mm: float,
                      roller_diam_mm: float, width_cm: float, bore_offset_cm: float,
                      is_preview: bool = False) -> adsk.fusion.SketchCircle:
    """Build a toothless WCP-style double-hub sprocket in `comp`: the hub cylinder from Z=0 to
    `width_cm` (a new body), the plate at the tip OD joined around its middle, chamfered hub
    ends, the hex bore and the arc label. Returns the hub's sketch circle (at Z=0, on the
    axis) for the joints -- it never changes size, so an in-place update leaves it alone."""
    origin   = adsk.core.Point3D.create(0, 0, 0)
    extrudes = comp.features.extrudeFeatures
    tip_od   = _ansi_od_cm(n_teeth, pitch_mm)

    hub_sk = comp.sketches.add(comp.xYConstructionPlane)
    hub_circle = _draw_circle(hub_sk, origin, WCP_HUB_DIA_CM)
    extrudes.addSimple(hub_sk.profiles.item(0), adsk.core.ValueInput.createByReal(width_cm),
                       adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    body = comp.bRepBodies.item(0)

    # Plate: one circle on XY, extruded with an offset start so it sits mid-width.
    z0, plate_cm = _plate_span_cm(width_cm, pitch_mm)
    plate_sk = comp.sketches.add(comp.xYConstructionPlane)
    _draw_circle(plate_sk, origin, tip_od)
    ext_in = extrudes.createInput(plate_sk.profiles.item(0),
                                  adsk.fusion.FeatureOperations.JoinFeatureOperation)
    ext_in.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(
        adsk.core.ValueInput.createByReal(plate_cm)),
        adsk.fusion.ExtentDirections.PositiveExtentDirection)
    if z0 > 1e-6:
        ext_in.startExtent = adsk.fusion.OffsetStartDefinition.create(
            adsk.core.ValueInput.createByReal(z0))
    ext_in.participantBodies = [body]
    extrudes.add(ext_in)
    plate_z = (z0, z0 + plate_cm)
    hub_r   = WCP_HUB_DIA_CM / 2

    # Hub-to-plate fillets, then the hubs' outer end chamfers (hub radius at Z=0 / width).
    if (z0 > WCP_HUB_FILLET_CM + 2 * WCP_HUB_CHAMFER_CM
            and tip_od / 2 > hub_r + 2 * WCP_HUB_FILLET_CM):
        edges = _circle_edges(body, hub_r, plate_z)
        if edges:
            try:
                fil_in = comp.features.filletFeatures.createInput()
                fil_in.edgeSetInputs.addConstantRadiusEdgeSet(
                    _edge_collection(edges),
                    adsk.core.ValueInput.createByReal(WCP_HUB_FILLET_CM), False)
                comp.features.filletFeatures.add(fil_in)
            except Exception:
                futil.log(f'PartsGen: {comp.name} hub fillet failed, left square')
    if z0 > 2 * WCP_HUB_CHAMFER_CM:
        edges = _circle_edges(comp.bRepBodies.item(0), hub_r, (0.0, width_cm))
        if edges:
            try:
                ch_in = comp.features.chamferFeatures.createInput2()
                ch_in.chamferEdgeSets.addEqualDistanceChamferEdgeSet(
                    _edge_collection(edges),
                    adsk.core.ValueInput.createByReal(WCP_HUB_CHAMFER_CM), False)
                comp.features.chamferFeatures.add(ch_in)
            except Exception:
                futil.log(f'PartsGen: {comp.name} hub chamfer failed, left square')

    # Rim taper, like WCP's chamfered teeth (skipped if it would reach the hub fillet).
    axial_cm = WCP_DOUBLE_HUB.get(pitch_mm, WCP_DOUBLE_HUB[CHAIN_25_PITCH_MM])[2]
    if (2 * axial_cm < plate_cm
            and tip_od / 2 - WCP_RIM_TAPER_SLOPE * axial_cm > hub_r + WCP_HUB_FILLET_CM):
        _taper_rim(comp, tip_od / 2, plate_z, axial_cm)

    _add_hex_bore_sprocket(comp, width_cm, WCP_HUB_DIA_CM, bore_offset_cm, is_preview)
    _add_sprocket_label(comp, n_teeth, pitch_mm, roller_diam_mm, width_cm, is_preview)
    return hub_circle


# ---------------------------------------------------------------------------
# Label — "22T - #25" engraved along the rim on both plate faces, like WCP's
# ---------------------------------------------------------------------------

def _plate_faces(comp: adsk.fusion.Component, width_cm: float, pitch_mm: float):
    """The plate's two annular faces (bottom, top) on the component's own body, in its
    local frame (LESSONS_LEARNED.md: never search world space for a nested part's faces)."""
    z0, plate_cm = _plate_span_cm(width_cm, pitch_mm)
    found = []
    for z in (z0, z0 + plate_cm):
        faces = [f for f in comp.bRepBodies.item(0).faces
                 if adsk.core.Plane.cast(f.geometry) is not None
                 and abs(f.pointOnFace.z - z) < 1e-4]
        found.append(max(faces, key=lambda f: f.area) if faces else None)
    return found


def _sketch_arc_label(comp: adsk.fusion.Component, face: adsk.fusion.BRepFace, label: str,
                      r_path_cm: float, text_h_cm: float):
    """Sketch `label` along a circular path of `r_path_cm` about the sprocket's axis on
    `face`, reading upright from outside the face. Returns (sketch, SketchText).

    The sketch is on the face itself, so its normal points out of the body and the text
    reads correctly from that side on both plate faces (no mirroring), and a Negative cut
    goes into the plate. SketchArcs always run counter-clockwise, and text runs along the
    path with "above" to its left, so the path is drawn across the bottom of the sketch
    (centred on -Y) and the text sits inside it, tops towards the centre. Fixing the arc's
    three points fully constrains the sketch."""
    sk = comp.sketches.add(face)
    c  = sk.modelToSketchSpace(adsk.core.Point3D.create(0, 0, face.pointOnFace.z))
    # Path length ~ the text's own length (with some slack) keeps it centred on -Y.
    span = min(math.radians(300), 0.85 * text_h_cm * len(label) / r_path_cm + 0.3)

    def _pt(a):
        return adsk.core.Point3D.create(c.x + r_path_cm * math.cos(a),
                                        c.y + r_path_cm * math.sin(a), 0)
    down = -math.pi / 2
    path = sk.sketchCurves.sketchArcs.addByThreePoints(_pt(down - span / 2), _pt(down),
                                                       _pt(down + span / 2))
    path.isConstruction = True
    path.startSketchPoint.isFixed  = True
    path.endSketchPoint.isFixed    = True
    path.centerSketchPoint.isFixed = True
    text_input = sk.sketchTexts.createInput2(label, text_h_cm)
    text_input.setAsAlongPath(path, True,
                              adsk.core.HorizontalAlignments.CenterHorizontalAlignment, 0)
    return sk, sk.sketchTexts.add(text_input)


def _cut_label(comp: adsk.fusion.Component, sk: adsk.fusion.Sketch, text: adsk.fusion.SketchText):
    """Engrave a face-sketch label: the face sketch's normal points out of the body, so a
    Negative cut goes into it."""
    extrudes = comp.features.extrudeFeatures
    ext_in = extrudes.createInput(text, adsk.fusion.FeatureOperations.CutFeatureOperation)
    ext_in.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(
        adsk.core.ValueInput.createByReal(LABEL_ENGRAVE_CM)),
        adsk.fusion.ExtentDirections.NegativeExtentDirection)
    ext_in.participantBodies = [comp.bRepBodies.item(0)]
    feature = extrudes.add(ext_in)
    if feature.healthState != adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState:
        futil.log(f'PartsGen: sprocket label cut unhealthy ({feature.errorOrWarningMessage}), '
                  'removing it')
        feature.deleteMe()
        sk.deleteMe()


def _add_sprocket_label(comp: adsk.fusion.Component, n_teeth: int,
                        pitch_mm: float, roller_diam_mm: float, width_cm: float,
                        is_preview: bool = False):
    """Engrave e.g. "22T - #25" along the rim on both plate faces, in the band between the
    hub and the tooth root circle. Skipped (logged) on a sprocket too small for that band."""
    try:
        chain = '35' if abs(pitch_mm - CHAIN_35_PITCH_MM) < 0.01 else '25'
        label  = f'{n_teeth}T - #{chain}'
        r_in   = WCP_HUB_DIA_CM / 2 + WCP_HUB_FILLET_CM + LABEL_ARC_MARGIN_CM / 3
        r_out  = (_sprocket_pitch_radius_cm(n_teeth, pitch_mm) - roller_diam_mm / 20
                  - LABEL_ARC_MARGIN_CM)
        text_h = min(LABEL_TEXT_HEIGHT_CM, (r_out - r_in) / 1.1)
        if text_h < LABEL_MIN_TEXT_CM:
            futil.log(f'PartsGen: {n_teeth}T sprocket is too small for its label, skipping it')
            return
        # The text sits inside its path, so a path on the band's outer edge puts it out near
        # the rim, where WCP engrave theirs.
        r_path = r_out
        # Sketch both faces before cutting either: a cut remakes the body's faces.
        labels = [_sketch_arc_label(comp, face, label, r_path, text_h)
                  for face in _plate_faces(comp, width_cm, pitch_mm) if face is not None]
        for sk, text in labels:
            _cut_label(comp, sk, text)
    except Exception:
        futil.handle_error('PartsGen _add_sprocket_label', show_message_box=not is_preview)


def _add_flat_sprocket_label(comp: adsk.fusion.Component, width_cm: float, n_teeth: int,
                             pitch_mm: float = CHAIN_25_PITCH_MM,
                             roller_diam_mm: float = CHAIN_25_ROLLER_DIAM_MM,
                             bore_offset_cm: float = BORE_OFFSET_DEFAULT_IN * 2.54,
                             is_preview: bool = False):
    """Label for the standalone *toothed* sprocket (a full-width plate): e.g. "18T" on both
    faces, out near the rim just inside the tooth root circle; on a sprocket too small for
    that band to clear the bore, it goes between the bore and the tip circle, shrunk to fit.
    The bottom label is mirrored to read from below."""
    try:
        label  = f'{n_teeth}T'
        r_in   = _bore_radius_cm(BORE_HALF_HEX, bore_offset_cm) + 0.02
        tip_r  = _sprocket_tip_radius_cm(n_teeth, pitch_mm, roller_diam_mm)
        root_r = _sprocket_pitch_radius_cm(n_teeth, pitch_mm) - roller_diam_mm / 20
        r_out  = root_r - LABEL_EDGE_MARGIN_CM
        if r_out - r_in < 1.2 * LABEL_TEXT_HEIGHT_CM:
            r_out = tip_r - LABEL_EDGE_MARGIN_CM
        text_h = min(LABEL_TEXT_HEIGHT_CM, (r_out - r_in) / 1.2)
        if text_h < LABEL_MIN_TEXT_CM:
            futil.log(f'PartsGen: {label} sprocket is too small for its label, skipping it')
            return
        y_top, y_bot = r_out, r_out - 1.2 * text_h
        for plane, direction, mirror in (
                (_offset_xy_plane(comp, width_cm),
                 adsk.fusion.ExtentDirections.NegativeExtentDirection, False),
                (comp.xYConstructionPlane,
                 adsk.fusion.ExtentDirections.PositiveExtentDirection, True)):
            _engrave_label_face(comp, label, plane, direction, mirror, y_bot, y_top,
                                is_preview, text_height_cm=text_h)
    except Exception:
        futil.handle_error('PartsGen _add_sprocket_label', show_message_box=not is_preview)


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

    # Defensive guard — command_validate_input already blocks tooth counts below 9
    # from the dialog's OK button, but executePreview can call this function with a
    # transient/momentarily-invalid value while the user is still typing.
    if n_teeth < 3:
        futil.log('PartsGen _create_sprocket: invalid tooth count, skipping')
        return

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

    try:
        width_mm    = round(width_cm * 10)
        comp_name   = f'{chain_prefix}-{n_teeth}Tx{width_mm}mm'
        workingComp.name = comp_name

        customNameInp = inputs.itemById('custom_name')
        custom_name = customNameInp.value.strip() if customNameInp is not None else ''
        if custom_name:
            workingComp.name = comp_name = custom_name

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
            _add_hex_bore_sprocket(workingComp, width_cm, outer_diameter_cm)
            _add_flat_sprocket_label(workingComp, width_cm, n_teeth, pitch_mm, roller_diam_mm)
        else:
            # Without teeth: the same WCP double-hub shape as the chain's sprockets.
            _build_double_hub(workingComp, n_teeth, pitch_mm, roller_diam_mm, width_cm,
                              BORE_OFFSET_DEFAULT_IN * 2.54)

        try:
            attrs = workingComp.attributes
            attrs.add(ATTR_GROUP, ATTR_PART_TYPE,            'Sprocket')
            attrs.add(ATTR_GROUP, ATTR_SPROCKET_TOOTH_COUNT, str(n_teeth))
            attrs.add(ATTR_GROUP, ATTR_SPROCKET_WIDTH,       sprocket_width.expression)
            attrs.add(ATTR_GROUP, ATTR_SPROCKET_SHOW_TEETH,  str(show_teeth))
            attrs.add(ATTR_GROUP, ATTR_SPROCKET_CHAIN_TYPE,  chain_type_str)
            if custom_name:
                attrs.add(ATTR_GROUP, ATTR_CUSTOM_NAME,      custom_name)
        except Exception:
            futil.log('PartsGen: failed to save sprocket attributes')

        futil.group_timeline_features(design, start_marker, comp_name)
    except Exception:
        try:
            workingOcc.deleteMe()
        except Exception:
            pass
        futil.handle_error('PartsGen _create_sprocket', show_message_box=True)


# ---------------------------------------------------------------------------
# Auto-sprocket creation — called by chain_gen._create_chain()
# ---------------------------------------------------------------------------

def _chain_standard(chain_pitch_mm: float):
    """(pitch_mm, roller_diam_mm, name prefix, chain type label) for a chain pitch."""
    if abs(chain_pitch_mm - CHAIN_35_PITCH_MM) < 0.01:
        return CHAIN_35_PITCH_MM, CHAIN_35_ROLLER_DIAM_MM, 'Sprocket_35Chain', '#35 Chain'
    return CHAIN_25_PITCH_MM, CHAIN_25_ROLLER_DIAM_MM, 'Sprocket_25Chain', '#25 Chain'


def _sprocket_name(prefix: str, n_teeth: int, width_cm: float) -> str:
    return f'{prefix}-{n_teeth}Tx{round(width_cm * 10)}mm'


def create_sprocket_for_chain(n_teeth: int, width_cm: float, chain_pitch_mm: float,
                               chain_occ: adsk.fusion.Occurrence = None,
                               proj_circle: adsk.fusion.SketchCircle = None,
                               circle_index: int = 0,
                               parent_comp: adsk.fusion.Component = None,
                               bore_offset_cm: float = BORE_OFFSET_DEFAULT_IN * 2.54,
                               is_preview: bool = False, group_timeline: bool = True):
    """Create a toothless WCP-style double-hub sprocket (see _build_double_hub) and
    revolute-joint it to the chain's projected pitch circle.

    Mirrors create_pulley_for_belt() in pulley_gen.py.  chain_pitch_mm selects
    the chain standard: 6.35 → #25, 9.525 → #35. The hubs run from Z=0 to `width_cm`
    with the plate centred between them; the hub's sketch circle is the joint circle.

    Returns (sprocket occurrence as seen from `parent_comp`, its joint circle) so the
    caller can add the C-C joint, or None if the sprocket wasn't built. `group_timeline`
    False leaves its features ungrouped for a caller that groups a wider range (Fusion
    can't nest timeline groups, so a group inside the chain's made that one fail).
    """
    pitch_mm, roller_diam_mm, chain_prefix, chain_type_str = _chain_standard(chain_pitch_mm)

    design   = adsk.fusion.Design.cast(app.activeProduct)
    parent   = parent_comp if parent_comp is not None else design.rootComponent
    start_marker = design.timeline.markerPosition

    # Pre-position the occurrence at the pitch-circle centre so the two sprockets'
    # label cuts don't overlap while they are built; the joint then holds it there.
    trans = adsk.core.Matrix3D.create()
    if proj_circle is not None:
        c = proj_circle.centerSketchPoint.geometry
        trans.translation = adsk.core.Vector3D.create(c.x, c.y, 0.0)
    workingOcc  = parent.occurrences.addNewComponent(trans)
    workingComp = workingOcc.component

    comp_name = _sprocket_name(chain_prefix, n_teeth, width_cm)
    workingComp.name = comp_name

    try:
        joint_circle = _build_double_hub(workingComp, n_teeth, pitch_mm, roller_diam_mm,
                                         width_cm, bore_offset_cm, is_preview)
        if not is_preview:
            _log_unconstrained(workingComp)
    except Exception:
        try:
            workingOcc.deleteMe()
        except Exception:
            pass
        futil.handle_error(f'PartsGen: auto-sprocket {comp_name}',
                           show_message_box=not is_preview)
        return None

    try:
        attrs = workingComp.attributes
        attrs.add(ATTR_GROUP, ATTR_PART_TYPE,            'Sprocket')
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_TOOTH_COUNT, str(n_teeth))
        # Exact, not the rounded mm of the name: a rebuild reads it back.
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_WIDTH,       f'{width_cm / 2.54:.4f} in')
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_SHOW_TEETH,  'False')
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_CHAIN_TYPE,  chain_type_str)
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_CHAIN_PITCH, str(pitch_mm))
        attrs.add(ATTR_GROUP, ATTR_SPROCKET_BORE_OFFSET, f'{bore_offset_cm / 2.54:.4f} in')
        if chain_occ is not None:
            attrs.add(ATTR_GROUP, ATTR_SPROCKET_CHAIN_COMP_TOKEN, chain_occ.component.entityToken)
            attrs.add(ATTR_GROUP, ATTR_SPROCKET_PITCH_CIRCLE_IDX, str(circle_index))
    except Exception:
        futil.log('PartsGen: failed to save auto-sprocket attributes')

    # Parametric revolute Joint: sprocket centre <-> projected pitch-circle centre in the
    # chain sketch, so the sprocket follows when the C-C distance is edited.
    if proj_circle is not None and chain_occ is not None:
        try:
            sprocket_geom = adsk.fusion.JointGeometry.createByCurve(
                joint_circle.createForAssemblyContext(workingOcc),
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            chain_geom = adsk.fusion.JointGeometry.createByCurve(
                proj_circle,
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            joint_input = parent.joints.createInput(sprocket_geom, chain_geom)
            joint_input.setAsRevoluteJointMotion(
                adsk.fusion.JointDirections.ZAxisJointDirection)
            joint = parent.joints.add(joint_input)
            joint.name = f'{comp_name}_revolute'
            _hide_joint(joint)
        except Exception:
            futil.handle_error(f'PartsGen: joint for {comp_name}', show_message_box=not is_preview)

    if group_timeline:
        futil.group_timeline_features(design, start_marker, comp_name)
    return workingOcc, joint_circle


# ---------------------------------------------------------------------------
# In-place tooth-count update (chain C-C edits)
# ---------------------------------------------------------------------------

def _sprocket_features(comp: adsk.fusion.Component):
    """(plate extrude, bore cut or None, [label cuts]) of a generated double-hub sprocket
    (_build_double_hub), told apart by operation and depth; None if the component doesn't
    look like one (e.g. a plain disk from before the double-hub shape -- it gets rebuilt)."""
    hub_ext, plate_ext, bore_ext, label_exts = None, None, None, []
    for f in comp.features.extrudeFeatures:
        op = f.operation
        if op == adsk.fusion.FeatureOperations.NewBodyFeatureOperation:
            if hub_ext is not None:
                return None
            hub_ext = f
        elif op == adsk.fusion.FeatureOperations.JoinFeatureOperation:
            if plate_ext is not None:
                return None
            plate_ext = f
        elif op == adsk.fusion.FeatureOperations.CutFeatureOperation:
            extent = adsk.fusion.DistanceExtentDefinition.cast(f.extentOne)
            # (a Negative-direction extent reads back as a negative distance)
            if extent is not None and abs(abs(extent.distance.value) - LABEL_ENGRAVE_CM) < 1e-6:
                label_exts.append(f)
            elif bore_ext is None:
                bore_ext = f
            else:
                return None
        else:
            return None
    if hub_ext is None or plate_ext is None:
        return None
    return plate_ext, bore_ext, label_exts


def _delete_cut(feature: adsk.fusion.ExtrudeFeature):
    """Delete a cut plus the sketch and construction plane it was made from."""
    sk = _feature_sketch(feature)
    try:
        plane = adsk.fusion.ConstructionPlane.cast(sk.referencePlane)
    except RuntimeError:
        plane = None    # a sketch on a BRepFace: reading it raises unless rolled back
    feature.deleteMe()
    sk.deleteMe()
    if plane is not None:
        try:
            plane.deleteMe()
        except Exception:
            pass    # an origin plane


def _set_attr(comp: adsk.fusion.Component, name: str, value: str):
    old = comp.attributes.itemByName(ATTR_GROUP, name)
    if old is not None:
        old.deleteMe()
    comp.attributes.add(ATTR_GROUP, name, value)


def update_sprocket_teeth(sprocket_occ: adsk.fusion.Occurrence, n_teeth: int,
                          design: adsk.fusion.Design) -> bool:
    """Change a chain sprocket's tooth count by editing its features in place: drive the
    plate circle's diameter to the new tip OD and re-engrave the label. The occurrence,
    hub faces and joint circle (on the hub) survive, so joints made to the sprocket stay valid.

    No timeline roll-back is needed (the plate profile is a plain circle); the new label
    lands at the end of the timeline. The bore doesn't depend on the tooth count. Returns
    False, changing nothing, when the sprocket can't be updated this way (built with
    teeth, unrecognised features, the bore wouldn't fit); the caller then rebuilds it."""
    comp  = sprocket_occ.component
    attrs = comp.attributes

    def _attr(name, default=None):
        a = attrs.itemByName(ATTR_GROUP, name)
        return a.value if a is not None else default

    if _attr(ATTR_SPROCKET_SHOW_TEETH, 'False').lower() == 'true':
        return False
    pitch_mm, roller_diam_mm, prefix, _ = _chain_standard(
        float(_attr(ATTR_SPROCKET_CHAIN_PITCH, CHAIN_25_PITCH_MM)))
    units = design.unitsManager
    try:
        width_cm = units.evaluateExpression(_attr(ATTR_SPROCKET_WIDTH, '0.375 in'), 'in')
    except Exception:
        return False
    tip_od_cm = _ansi_od_cm(n_teeth, pitch_mm)

    feats = _sprocket_features(comp)
    if feats is None:
        futil.log(f'PartsGen: {comp.name} features not recognised, rebuilding it instead')
        return False
    plate_ext, bore_ext, label_exts = feats
    if bore_ext is None:
        return False    # built without a bore (too small); a rebuild adds it back

    old_name = comp.name
    new_name = _sprocket_name(prefix, n_teeth, width_cm)
    timeline = design.timeline
    try:
        plate_sk = _feature_sketch(plate_ext)
        circles = list(plate_sk.sketchCurves.sketchCircles)
        if len(circles) != 1:
            return False
        _set_circle_diameter(plate_sk, circles[0], tip_od_cm)

        start = timeline.markerPosition
        for f in label_exts:
            _delete_cut(f)
        _add_sprocket_label(comp, n_teeth, pitch_mm, roller_diam_mm, width_cm)
        _log_unconstrained(comp)
        for sk in comp.sketches:
            sk.isLightBulbOn = False
    except Exception:
        futil.handle_error(f'PartsGen: in-place update of {old_name}', show_message_box=False)
        return False

    try:
        _set_attr(comp, ATTR_SPROCKET_TOOTH_COUNT, str(n_teeth))
        if attrs.itemByName(ATTR_GROUP, ATTR_CUSTOM_NAME) is None:
            comp.name = new_name
            # The generated joints are named after the sprocket. Match them by the part
            # they hold, not by name (both sprockets of a chain may share a base name).
            owners = [design.rootComponent]
            ctx = sprocket_occ.assemblyContext
            if ctx is not None:
                owners.append(ctx.component)
            for owner in owners:
                for joint in owner.joints:
                    # Longest first: '_cc_revolute' also contains '_revolute'.
                    suffix = next((s for s in ('_cc_revolute', '_cc_cylindrical', '_revolute')
                                   if s in joint.name), None)
                    try:
                        occs = (joint.occurrenceOne, joint.occurrenceTwo)
                    except Exception:
                        continue    # a grounded side raises (LESSONS_LEARNED.md)
                    if suffix and any(o is not None and o.component == comp for o in occs):
                        joint.name = new_name + suffix
        futil.group_timeline_features(design, start, f'{new_name} update')
    except Exception:
        futil.log(f'PartsGen: renaming {old_name} after its tooth-count update failed')
    futil.log(f'PartsGen: {old_name} updated in place to {n_teeth}T')
    return True
