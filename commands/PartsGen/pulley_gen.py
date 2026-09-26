"""
pulley_gen.py  —  Timing Pulley geometry for PartsGen

Contains the geometry creation functions for HTD and GT2 timing pulleys,
plus the _create_pulley() helper that PartsGen/entry.py calls when the
user selects "Timing Pulley" as the Part Type.
"""

import adsk.core
import adsk.fusion
import math
import uuid
from ...lib import fusionAddInUtils as futil
from ... import config
from .shaft_gen import _insert_bearing, _true_face_normal, _hide_joint, _draw_circle

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Belt type labels (must match the dropdown items in entry.py)
# ---------------------------------------------------------------------------
BELT_HTD = 'HTD 5mm Pitch'
BELT_GT2 = 'GT2 3mm Pitch'

# ---------------------------------------------------------------------------
# Attribute keys (written to the component so the edit command can restore)
# ---------------------------------------------------------------------------
ATTR_GROUP              = 'FRCTools_PartsGen'
ATTR_PART_TYPE          = 'part_type'
ATTR_PULLEY_BELT_TYPE        = 'pulley_belt_type'
ATTR_PULLEY_TOOTH_COUNT     = 'pulley_tooth_count'
ATTR_PULLEY_BELT_WIDTH      = 'pulley_belt_width'
ATTR_PULLEY_SHOW_TEETH      = 'pulley_show_teeth'
ATTR_PULLEY_BELT_COMP_TOKEN  = 'pulley_belt_comp_token'
ATTR_PULLEY_PITCH_CIRCLE_IDX = 'pulley_pitch_circle_index'
ATTR_PULLEY_BORE_TYPE        = 'pulley_bore_type'
ATTR_PULLEY_BORE_OFFSET      = 'pulley_bore_offset'
ATTR_CUSTOM_NAME             = 'custom_name'
ATTR_PULLEY_ADAPTER          = 'pulley_adapter'
# A random id on the pulley's component, repeated on its "<pulley>_Group" component, so
# right-click Edit can tell the group is this pulley's and delete it (adapter included).
ATTR_PULLEY_UID              = 'pulley_uid'
ATTR_PULLEY_GROUP            = 'pulley_group_uid'

# ---------------------------------------------------------------------------
# Bore type labels (must match the dropdown items in entry.py)
# ---------------------------------------------------------------------------
BORE_HALF_HEX  = '1/2" Hex'
BORE_SPLINEXS  = 'SplineXS (WCP)'
BORE_TYPES     = (BORE_HALF_HEX, BORE_SPLINEXS)
# Default bore offset, added to every side of the bore (positive = looser fit).
BORE_OFFSET_DEFAULT_IN = 0.0025

# ---------------------------------------------------------------------------
# Belt width options (must match the 'belt_width' dropdown items in entry.py)
# ---------------------------------------------------------------------------
PULLEY_BELT_WIDTHS_MM   = (9, 15)
PULLEY_BELT_WIDTH_ITEMS = tuple(f'{mm} mm' for mm in PULLEY_BELT_WIDTHS_MM)


def _belt_width_mm(beltWidthInp: adsk.core.DropDownCommandInput) -> int:
    """Belt width in mm from the 'belt_width' dropdown (defaults to the first option)."""
    if beltWidthInp is None or beltWidthInp.selectedItem is None:
        return PULLEY_BELT_WIDTHS_MM[0]
    return PULLEY_BELT_WIDTHS_MM[beltWidthInp.selectedItem.index]

# ---------------------------------------------------------------------------
# Flange dimensions (Fusion 360 uses centimetres internally)
# ---------------------------------------------------------------------------
FLANGE_OD_OFFSET_CM  = 0.196 * 2.54   # flange OD = tooth OD + 0.196 in
FLANGE_THICKNESS_CM  = 0.053 * 2.54   # 0.053 in  →  0.13462 cm
HEX_BORE_FLATS_CM    = 0.5   * 2.54   # 0.5 in across flats →  1.27 cm
LABEL_TEXT_HEIGHT_CM = 0.15 * 2.54    # 0.15 in font height for engraved label
LABEL_ENGRAVE_CM     = 0.02 * 2.54    # 0.02 in engraving depth
LABEL_EDGE_MARGIN_CM = 0.06 * 2.54    # gap between the label's top and the flange edge

# WCP 8mm SplineXS bore (Ø0.313in / 8mm shaft, 15 teeth). WCP only publish the shaft OD,
# so this is measured off the bore in WCP's own CAD (WCP-1021 "SplineXS to 3D Print
# Adapter" STEP): 15 lands on a Ø0.2808in circle, each 8.612° wide, and between them a
# tooth space whose involute flanks are replaced here by straight lines tangent to a
# round bottom (within 0.0006in of WCP's curve everywhere). Offsetting that shape is
# exact: the land and bottom radii grow by the offset and the flanks shift along their
# normal, see _splinexs_bore_points.
SPLINEXS_TEETH              = 15
SPLINEXS_LAND_DIA_CM        = 0.2808 * 2.54
SPLINEXS_LAND_HALF_DEG      = 4.306          # half the land's width, seen from the centre
SPLINEXS_ROOT_CENTER_R_CM   = 0.1552 * 2.54  # space's bottom arc: centre distance ...
SPLINEXS_ROOT_RADIUS_CM     = 0.0105 * 2.54  # ... and radius

# Optional 3D-printed hub adapter pressed into the pulley's bottom flange, one per bore
# type, inserted as a linked component from Team 1756's library (Argos CAD > Parts_1 >
# Parts_Gen) the same way shaft_gen inserts bearings. The pulley is pocketed with a
# Combine Cut (Keep Tools) so the adapter's shape is cut out of it and the adapter stays.
#   radius_cm -- the adapter's outer radius (measured live), to refuse pulleys too small
#   clock_deg -- turn about Z that lines the adapter's bore up with the pulley's: the hex
#                adapter's corners already sit at 30 deg + 60k like _draw_hex_bore's; the
#                SplineXS adapter's lands are centred on 6 deg + 24k, so +12 deg (half a
#                tooth) puts one on +Y like _draw_splinexs_bore's.
ADAPTER_PARTS = {
    BORE_HALF_HEX: dict(urn='urn:adsk.wipprod:dm.lineage:GwLsJoJcQ3Gm1N-zSSlrsQ',
                        file='Hex_3d_print_adapter_WCP-1121',
                        radius_cm=0.5 * 2.54, clock_deg=0.0),
    BORE_SPLINEXS: dict(urn='urn:adsk.wipprod:dm.lineage:3-SH0GSKSUenPJK5KGzDMw',
                        file='SplineXS_3d_print_adapter_WCP-1021',
                        radius_cm=0.7754, clock_deg=12.0),
}
# Pulley material left between the adapter and the bottom of the tooth spaces.
ADAPTER_MIN_WALL_CM = 0.03 * 2.54
# Tooth-space depth below the tooth OD (createHTD/GT2PulleyGeometry's rootHeight), in cm.
TOOTH_ROOT_DEPTH_CM = {5: 0.206, 3: 0.114}


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _warn(message: str, is_preview: bool):
    """Pop `message` up on the committed build; only log it during preview, which reruns
    on every input change and would otherwise stack up message boxes."""
    if is_preview:
        futil.log(message)
    else:
        futil.popup_error(message)


def _log_unconstrained(comp: adsk.fusion.Component):
    """Log any sketch in `comp` that isn't fully constrained (every one should be)."""
    for sk in comp.sketches:
        if not sk.isFullyConstrained:
            futil.log(f'PartsGen: {comp.name} {sk.name} is not fully constrained')


def _outer_diameter_cm(belt_pitch_mm: int, n_teeth: int) -> float:
    """Return the tooth outer diameter in cm without creating any sketch geometry."""
    if belt_pitch_mm == 5:  # HTD 5mm
        return (n_teeth * 5.0 / math.pi - 1.74) / 10
    else:  # GT2 3mm
        return (n_teeth * 3.0 / math.pi - 2 * 0.381) / 10


# ---------------------------------------------------------------------------
# Flange helper — adds a disk flange on each side of the pulley body
# ---------------------------------------------------------------------------

def _offset_xy_plane(comp: adsk.fusion.Component, z_cm: float) -> adsk.fusion.ConstructionPlane:
    """A construction plane parallel to the component's XY plane at Z = z_cm."""
    plane_input = comp.constructionPlanes.createInput()
    plane_input.setByOffset(comp.xYConstructionPlane, adsk.core.ValueInput.createByReal(z_cm))
    return comp.constructionPlanes.add(plane_input)


def _add_flanges(comp: adsk.fusion.Component, belt_width_cm: float, tooth_od_cm: float):
    """Extrude a flange disk on each side of the pulley body.

    Flange OD = tooth_od_cm + FLANGE_OD_OFFSET_CM (0.196 in larger than tooth OD).
    Bottom flange: from Z=0 downward by FLANGE_THICKNESS_CM.
    Top flange:    from Z=belt_width_cm upward by FLANGE_THICKNESS_CM.
    Both come from one fully-constrained circle on the XY plane (the top one with an
    offset start) and are joined to the pulley body only.
    """
    extrudes = comp.features.extrudeFeatures
    body     = comp.bRepBodies.item(0)
    sk       = comp.sketches.add(comp.xYConstructionPlane)
    _draw_circle(sk, adsk.core.Point3D.create(0, 0, 0), tooth_od_cm + FLANGE_OD_OFFSET_CM)
    profile  = sk.profiles.item(0)
    thickness = adsk.fusion.DistanceExtentDefinition.create(
        adsk.core.ValueInput.createByReal(FLANGE_THICKNESS_CM))

    for start_cm, direction in (
            (0.0,           adsk.fusion.ExtentDirections.NegativeExtentDirection),
            (belt_width_cm, adsk.fusion.ExtentDirections.PositiveExtentDirection)):
        ext_in = extrudes.createInput(profile, adsk.fusion.FeatureOperations.JoinFeatureOperation)
        ext_in.setOneSideExtent(thickness, direction)
        if start_cm:
            ext_in.startExtent = adsk.fusion.OffsetStartDefinition.create(
                adsk.core.ValueInput.createByReal(start_cm))
        ext_in.participantBodies = [body]
        extrudes.add(ext_in)


def _add_pulley_body(comp: adsk.fusion.Component, sketch: adsk.fusion.Sketch,
                     belt_pitch_mm: int, n_teeth: int, show_teeth: bool,
                     belt_width_cm: float, is_preview: bool = False):
    """Draw the tooth profile (or, without teeth, a smooth circle at the tooth OD) in
    `sketch` and extrude it into the pulley's body.

    Returns (tooth_od_cm, joint_curve) -- a sketch curve centred on the pulley's axis, for
    the belt's revolute joint -- or None if the tooth profile didn't close.
    """
    if show_teeth:
        geometry_fn = createHTDPulleyGeometry if belt_pitch_mm == 5 else createGT2PulleyGeometry
        tooth_od_cm = geometry_fn(sketch, belt_pitch_mm, n_teeth)
        if sketch.profiles.count != 1:
            _warn(f'Parts Gen: Timing Pulley sketch has {sketch.profiles.count} profiles '
                  f'(expected 1, {n_teeth}T, {belt_pitch_mm}mm pitch). The tooth geometry '
                  'may not have closed correctly.', is_preview)
            return None
        # A joint needs a closed curve for CenterKeyPoint, so add a construction circle
        # at the tooth OD (it doesn't affect the profile).
        joint_curve = _draw_circle(sketch, adsk.core.Point3D.create(0, 0, 0), tooth_od_cm)
        joint_curve.isConstruction = True
    else:
        tooth_od_cm = _outer_diameter_cm(belt_pitch_mm, n_teeth)
        joint_curve = _draw_circle(sketch, adsk.core.Point3D.create(0, 0, 0), tooth_od_cm)
    comp.features.extrudeFeatures.addSimple(
        sketch.profiles.item(0), adsk.core.ValueInput.createByReal(belt_width_cm),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    return tooth_od_cm, joint_curve


def _polar(radius: float, angle_deg: float) -> adsk.core.Point3D:
    a = math.radians(angle_deg)
    return adsk.core.Point3D.create(radius * math.cos(a), radius * math.sin(a), 0)


def _bore_radius_cm(bore_type: str, offset_cm: float) -> float:
    """Return the bore's outermost radius (hex corner / spline tooth-space bottom), in cm."""
    if bore_type == BORE_SPLINEXS:
        return SPLINEXS_ROOT_CENTER_R_CM + SPLINEXS_ROOT_RADIUS_CM + offset_cm
    return (HEX_BORE_FLATS_CM / 2 + offset_cm) / math.cos(math.radians(30))


def _draw_hex_bore(sk: adsk.fusion.Sketch, offset_cm: float):
    """Draw a sharp 1/2in hex, grown by `offset_cm` on every flat, corners at 30°, 90°, ...
    (a corner points up). Fully constrained: the lines share their end points and every
    point is fixed."""
    circumradius = _bore_radius_cm(BORE_HALF_HEX, offset_cm)
    lines = sk.sketchCurves.sketchLines
    pts   = [_polar(circumradius, 30 + i * 60) for i in range(6)]
    first = lines.addByTwoPoints(pts[0], pts[1])
    prev  = first
    for i in range(2, 6):
        prev = lines.addByTwoPoints(prev.endSketchPoint, pts[i])
    lines.addByTwoPoints(prev.endSketchPoint, first.startSketchPoint)
    # The lines share their corners, so each line's start point covers all six.
    for line in sk.sketchCurves.sketchLines:
        line.startSketchPoint.isFixed = True


def _line_circle_hit(p: adsk.core.Point3D, d, center, radius: float):
    """Return the point where the line p + t*d meets the circle of `radius` about
    `center` (an (x, y) tuple), taking the hit nearest p (d is a unit vector)."""
    px, py = p.x - center[0], p.y - center[1]
    b    = px * d[0] + py * d[1]
    c    = px * px + py * py - radius * radius
    disc = b * b - c
    if disc < 0:
        raise ValueError('SplineXS bore: flank misses the land circle (offset too large)')
    roots = (-b - math.sqrt(disc), -b + math.sqrt(disc))
    t = min(roots, key=abs)
    return adsk.core.Point3D.create(p.x + t * d[0], p.y + t * d[1], 0)


def _splinexs_bore_points(offset_cm: float):
    """Return (r_land, corner, tangent, r_bottom) for the SplineXS bore grown by
    `offset_cm`, in the frame of one tooth space centred on 0°, for its lower (-Y) half:
    the land circle's radius, the land/flank corner, the flank/bottom-arc tangent point,
    and the bottom arc's radius (its centre stays at SPLINEXS_ROOT_CENTER_R_CM on the X
    axis). The upper half is the mirror image in X."""
    half_space = 180.0 / SPLINEXS_TEETH - SPLINEXS_LAND_HALF_DEG
    r_land   = SPLINEXS_LAND_DIA_CM / 2
    center   = (SPLINEXS_ROOT_CENTER_R_CM, 0.0)
    r_bottom = SPLINEXS_ROOT_RADIUS_CM
    if r_bottom + offset_cm <= 0:
        raise ValueError('SplineXS bore: offset too negative, the tooth space vanishes')

    # Nominal flank: from the land corner, tangent to the bottom arc (the lower tangent).
    q     = _polar(r_land, -half_space)
    vx, vy = center[0] - q.x, center[1] - q.y
    dist  = math.hypot(vx, vy)
    ang   = math.atan2(vy, vx) - math.asin(r_bottom / dist)
    d     = (math.cos(ang), math.sin(ang))
    # Unit normal pointing away from the bottom arc's centre, i.e. into the land.
    n     = (d[1], -d[0])
    if n[0] * vx + n[1] * vy > 0:
        n = (-n[0], -n[1])

    # Offset flank: shift along n; it stays tangent to the grown bottom arc.
    p       = adsk.core.Point3D.create(q.x + offset_cm * n[0], q.y + offset_cm * n[1], 0)
    corner  = _line_circle_hit(p, d, (0.0, 0.0), r_land + offset_cm)
    t       = (center[0] - p.x) * d[0] + (center[1] - p.y) * d[1]
    tangent = adsk.core.Point3D.create(p.x + t * d[0], p.y + t * d[1], 0)
    if math.degrees(math.atan2(-corner.y, corner.x)) >= 180.0 / SPLINEXS_TEETH:
        raise ValueError('SplineXS bore: offset too large, the lands vanish')
    return r_land + offset_cm, corner, tangent, r_bottom + offset_cm


def _draw_splinexs_bore(sk: adsk.fusion.Sketch, offset_cm: float):
    """Draw the WCP SplineXS bore (a land centred on +Y). Every point is placed from
    closed-form maths and fixed, the same approach as shaft_gen._draw_maxspline_wave."""
    r_land, corner, tangent, r_bottom = _splinexs_bore_points(offset_cm)
    pitch = 360.0 / SPLINEXS_TEETH
    lines = sk.sketchCurves.sketchLines
    arcs  = sk.sketchCurves.sketchArcs

    def _rot(x, y, deg):
        a = math.radians(deg)
        return adsk.core.Point3D.create(x * math.cos(a) - y * math.sin(a),
                                        x * math.sin(a) + y * math.cos(a), 0)

    a_corner = math.degrees(math.atan2(-corner.y, corner.x))   # half the space at the land
    bottom   = SPLINEXS_ROOT_CENTER_R_CM + r_bottom
    sk.isComputeDeferred = True
    try:
        for k in range(SPLINEXS_TEETH):
            s = 90.0 + pitch / 2 + k * pitch                    # this tooth space's centre
            land_end = s - a_corner
            arcs.addByThreePoints(_polar(r_land, land_end - (pitch - 2 * a_corner)),
                                  _polar(r_land, land_end - (pitch / 2 - a_corner)),
                                  _polar(r_land, land_end))
            lines.addByTwoPoints(_rot(corner.x, corner.y, s), _rot(tangent.x, tangent.y, s))
            arcs.addByThreePoints(_rot(tangent.x, tangent.y, s), _rot(bottom, 0.0, s),
                                  _rot(tangent.x, -tangent.y, s))
            lines.addByTwoPoints(_rot(tangent.x, -tangent.y, s), _rot(corner.x, -corner.y, s))

        for line in sk.sketchCurves.sketchLines:
            line.startSketchPoint.isFixed = True
            line.endSketchPoint.isFixed   = True
        for arc in sk.sketchCurves.sketchArcs:
            arc.startSketchPoint.isFixed  = True
            arc.endSketchPoint.isFixed    = True
            arc.centerSketchPoint.isFixed = True
    finally:
        sk.isComputeDeferred = False


def _add_bore(comp: adsk.fusion.Component, belt_width_cm: float, tooth_od_cm: float,
              bore_type: str = BORE_HALF_HEX,
              offset_cm: float = BORE_OFFSET_DEFAULT_IN * 2.54, is_preview: bool = False):
    """Cut the centre bore (1/2in hex or WCP SplineXS, grown by `offset_cm` on every side)
    through the entire pulley (flanges + belt body). Returns the bore's sketch plane (on
    the bottom flange face, which the bottom label reuses), or None if the cut was skipped."""
    bore_radius = _bore_radius_cm(bore_type, offset_cm)

    # A small tooth count can make the tooth body narrower than the bore, which would
    # otherwise self-intersect the outer profile. Skip the cut rather than risk an
    # invalid/self-intersecting geometry or a Fusion exception.
    if bore_radius >= tooth_od_cm / 2:
        _warn(f'PartsGen: pulley tooth diameter ({tooth_od_cm * 10:.1f} mm) is too small '
              f'for the {bore_type} bore — skipping the bore cut. Choose a larger tooth count.',
              is_preview)
        return None

    # Sketch on a plane at the bottom of the lower flange
    bot_plane = _offset_xy_plane(comp, -FLANGE_THICKNESS_CM)
    sk = comp.sketches.add(bot_plane)
    if bore_type == BORE_SPLINEXS:
        _draw_splinexs_bore(sk, offset_cm)
    else:
        _draw_hex_bore(sk, offset_cm)

    # Cut through bottom flange + belt body + top flange
    # participantBodies: a cut otherwise also goes through any other body it overlaps —
    # e.g. a shaft or another pulley sitting at the origin while this one is built.
    total_cm = 2 * FLANGE_THICKNESS_CM + belt_width_cm
    extrudes = comp.features.extrudeFeatures
    ext_in   = extrudes.createInput(sk.profiles.item(0),
                                    adsk.fusion.FeatureOperations.CutFeatureOperation)
    ext_in.setDistanceExtent(False, adsk.core.ValueInput.createByReal(total_cm))
    ext_in.participantBodies = [comp.bRepBodies.item(0)]
    extrudes.add(ext_in)
    return bot_plane


def _engrave_label_face(comp: adsk.fusion.Component, label: str,
                        plane: adsk.fusion.ConstructionPlane, cut_direction, mirror: bool,
                        y_bot: float, y_top: float, is_preview: bool = False):
    """Engrave *label* on one flange face, in the band y_bot..y_top above the centre.

    Args:
        plane:         Construction plane on the flange face to engrave.
        cut_direction: ExtentDirections constant — Negative cuts into the body
                       from the top face; Positive cuts in from the bottom face.
        mirror:        When True the text is flipped horizontally so the label
                       reads correctly when viewed from the outside of the bottom
                       flange (i.e. from the -Z direction).
    """
    sk = comp.sketches.add(plane)

    corner1 = adsk.core.Point3D.create(-1.5, y_bot, 0)
    corner2 = adsk.core.Point3D.create( 1.5, y_top, 0)

    text_input = sk.sketchTexts.createInput2(label, LABEL_TEXT_HEIGHT_CM)
    text_input.setAsMultiLine(
        corner1, corner2,
        adsk.core.HorizontalAlignments.CenterHorizontalAlignment,
        adsk.core.VerticalAlignments.MiddleVerticalAlignment,
        0
    )
    # isHorizontalFlip makes the text readable from the outside of the bottom face (-Z).
    if mirror:
        text_input.isHorizontalFlip = True
    text = sk.sketchTexts.add(text_input)
    # Keep the text whole and pin its box, which fully constrains the sketch. Exploding
    # it would leave ~60 loose curves that each need pinning.
    for line in text.definition.rectangleLines:
        line.startSketchPoint.isFixed = True

    body     = comp.bRepBodies.item(0)
    extrudes = comp.features.extrudeFeatures
    depth    = adsk.fusion.DistanceExtentDefinition.create(
        adsk.core.ValueInput.createByReal(LABEL_ENGRAVE_CM))
    face     = 'bottom' if mirror else 'top'

    # One cut for the whole label.
    try:
        ext_in = extrudes.createInput(text, adsk.fusion.FeatureOperations.CutFeatureOperation)
        ext_in.setOneSideExtent(depth, cut_direction)
        ext_in.participantBodies = [body]
        feature = extrudes.add(ext_in)
        if feature.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState:
            return
        futil.log(f'PartsGen label: {face} cut unhealthy ({feature.errorOrWarningMessage}), '
                  'engraving per character')
        feature.deleteMe()
    except Exception as err:
        futil.log(f'PartsGen label: {face} cut failed ({err}), engraving per character')

    # Fallback: engrave each character profile on its own, so a profile that falls off
    # the body is skipped rather than aborting the whole label.
    text.explode()
    n_profiles = sk.profiles.count
    if n_profiles == 0:
        _warn(f'PartsGen label: sketch text "{label}" generated 0 profiles — '
              f'cannot engrave. (text height = {LABEL_TEXT_HEIGHT_CM:.3f} cm)', is_preview)
        return
    engraved = 0
    for i in range(n_profiles):
        try:
            ext_in = extrudes.createInput(sk.profiles.item(i),
                                          adsk.fusion.FeatureOperations.CutFeatureOperation)
            ext_in.setOneSideExtent(depth, cut_direction)
            ext_in.participantBodies = [body]
            extrudes.add(ext_in)
            engraved += 1
        except Exception:
            pass  # profile does not intersect any body — skip it

    if engraved == 0:
        _warn(f'PartsGen label: {n_profiles} profile(s) found but none could be '
              f'engraved on the {face} face.', is_preview)


def _add_label(comp: adsk.fusion.Component, belt_width_cm: float, n_teeth: int,
               tooth_od_cm: float, bore_radius_cm: float,
               bottom_plane: adsk.fusion.ConstructionPlane = None, is_preview: bool = False):
    """Engrave tooth count on both flange faces (e.g. \"18T\"), out near the flange edge.

    Top face: normal orientation, readable from above (+Z).
    Bottom face: mirrored so the label reads correctly from below (-Z). `bottom_plane`
    is the bore's sketch plane, which already sits on that face; made here if None.
    """
    try:
        label = f'{n_teeth}T'
        # Sit the label just inside the flange edge; on a pulley too small for that band
        # to clear the bore, fall back to just above the bore.
        flange_radius = (tooth_od_cm + FLANGE_OD_OFFSET_CM) / 2
        text_band     = LABEL_TEXT_HEIGHT_CM * 1.2
        y_top = flange_radius - LABEL_EDGE_MARGIN_CM
        y_bot = y_top - text_band
        if y_bot < bore_radius_cm + 0.02:
            y_bot = bore_radius_cm + 0.02
            y_top = y_bot + text_band
        if y_top > flange_radius:
            futil.log(f'PartsGen: {label} pulley is too small for its label, skipping it')
            return
        if bottom_plane is None:
            bottom_plane = _offset_xy_plane(comp, -FLANGE_THICKNESS_CM)
        _engrave_label_face(
            comp, label,
            plane         = _offset_xy_plane(comp, belt_width_cm + FLANGE_THICKNESS_CM),
            cut_direction = adsk.fusion.ExtentDirections.NegativeExtentDirection,
            mirror        = False,
            y_bot         = y_bot,
            y_top         = y_top,
            is_preview    = is_preview,
        )
        _engrave_label_face(
            comp, label,
            plane         = bottom_plane,
            cut_direction = adsk.fusion.ExtentDirections.PositiveExtentDirection,
            mirror        = True,
            y_bot         = y_bot,
            y_top         = y_top,
            is_preview    = is_preview,
        )
    except Exception:
        futil.handle_error('PartsGen _add_label', show_message_box=not is_preview)


# ---------------------------------------------------------------------------
# 3D-printed hub adapter
# ---------------------------------------------------------------------------

def _adapter_fits(part: dict, belt_pitch_mm: int, tooth_od_cm: float) -> bool:
    """True if the adapter clears the bottom of the tooth spaces by ADAPTER_MIN_WALL_CM."""
    root_r = tooth_od_cm / 2 - TOOTH_ROOT_DEPTH_CM[belt_pitch_mm]
    return part['radius_cm'] + ADAPTER_MIN_WALL_CM <= root_r


def _bottom_flange_face(occ: adsk.fusion.Occurrence) -> adsk.fusion.BRepFace:
    """The pulley's bottom flange face (planar, facing -Z, at z = -FLANGE_THICKNESS_CM),
    as a proxy through `occ`. `occ` is at identity, so its faces read world values."""
    faces = [f for f in occ.bRepBodies.item(0).faces
             if adsk.core.Plane.cast(f.geometry) is not None
             and abs(f.pointOnFace.z + FLANGE_THICKNESS_CM) < 1e-4
             and _true_face_normal(f).z < -1 + 1e-6]
    return max(faces, key=lambda f: f.area) if faces else None


def _adapter_bottom_face(adapter_occ: adsk.fusion.Occurrence) -> adsk.fusion.BRepFace:
    """The adapter's lowest flat face that carries its bore (a planar face with an outer
    and a bore loop), as a proxy through `adapter_occ`."""
    faces = [f for body in adapter_occ.bRepBodies for f in body.faces
             if adsk.core.Plane.cast(f.geometry) is not None and f.loops.count >= 2]
    return min(faces, key=lambda f: f.pointOnFace.z) if faces else None


def _add_adapter(pulleyOcc: adsk.fusion.Occurrence, groupOcc: adsk.fusion.Occurrence,
                 part: dict, pulley_uid: str):
    """Insert a 3D-printed hub adapter into the pulley's bottom, the way Team 1756 does it
    by hand: rigid-joint its flat face flush with the bottom flange face (so it sits all
    the way inside the pulley), then Combine-Cut it out of the pulley with Keep Tools so
    the pulley is pocketed and the adapter stays.

    The adapter is placed *before* the joint, turned `clock_deg` so its bore lines up with
    the pulley's. A planar-face joint on its own aligns both faces' X axes and would undo
    that turn, so the joint gets the same angle -- negated, because the joint's axis is
    the bottom face's normal (-Z) while the turn is about +Z (checked live).
    """
    pulley_name = pulleyOcc.component.name
    adapter_occ = _insert_bearing(groupOcc, part,
                                  'The pulley was built without the 3D print adapter.')
    if adapter_occ is None:
        return None
    try:
        pulley_face = _bottom_flange_face(pulleyOcc)
        if pulley_face is None:
            raise RuntimeError('pulley bottom flange face not found')

        # Turn it about Z, then drop it so its lowest point sits on the bottom flange face.
        clock = math.radians(part['clock_deg'])
        xform = adsk.core.Matrix3D.create()
        xform.setToRotation(clock, adsk.core.Vector3D.create(0, 0, 1),
                            adsk.core.Point3D.create(0, 0, 0))
        low_z = min(b.boundingBox.minPoint.z for b in adapter_occ.bRepBodies)
        xform.translation = adsk.core.Vector3D.create(0, 0, -FLANGE_THICKNESS_CM - low_z)
        adapter_occ.transform2 = xform

        adapter_face = _adapter_bottom_face(adapter_occ)
        if adapter_face is None:
            raise RuntimeError(f'{part["file"]} flat face not found')

        rootComp    = adsk.fusion.Design.cast(app.activeProduct).rootComponent
        joint_input = rootComp.joints.createInput(
            adsk.fusion.JointGeometry.createByPlanarFace(
                adapter_face, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint),
            adsk.fusion.JointGeometry.createByPlanarFace(
                pulley_face, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint))
        target_n = _true_face_normal(pulley_face)
        joint_input.isFlipped = _true_face_normal(adapter_face).dotProduct(target_n) < 0
        joint_input.angle = adsk.core.ValueInput.createByReal(clock * target_n.z)
        joint_input.setAsRigidJointMotion()
        joint = rootComp.joints.add(joint_input)
        if joint is None:
            raise RuntimeError('Joints.add returned null')
        joint.name = f'{pulley_name}_adapter'
        _hide_joint(joint)

        # Pocket the pulley: cut the adapter's shape out of it, keeping the adapter.
        tools = adsk.core.ObjectCollection.create()
        for body in adapter_occ.bRepBodies:
            tools.add(body)
        combines   = pulleyOcc.component.features.combineFeatures
        comb_input = combines.createInput(pulleyOcc.bRepBodies.item(0), tools)
        comb_input.operation        = adsk.fusion.FeatureOperations.CutFeatureOperation
        comb_input.isKeepToolBodies = True
        combine = combines.add(comb_input)
        if combine.healthState != adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState:
            raise RuntimeError(f'adapter cut failed: {combine.errorOrWarningMessage}')

        adapter_occ.attributes.add(ATTR_GROUP, ATTR_PULLEY_UID, pulley_uid)
        futil.log(f'PartsGen: {part["file"]} added to {pulley_name}')
        return adapter_occ
    except Exception:
        futil.handle_error(f'PartsGen: adapter for {pulley_name}')
        futil.popup_error(
            f'Parts Gen: could not add the {part["file"]} adapter to {pulley_name}.\n\n'
            'The pulley itself is correct -- add the adapter by hand if you need one.')
        try:
            adapter_occ.deleteMe()
        except Exception:
            pass
        return None


def pulley_outer_occurrence(pulley_occ: adsk.fusion.Occurrence) -> adsk.fusion.Occurrence:
    """The occurrence right-click Edit should delete for this pulley: its "<pulley>_Group"
    (which takes the adapter with it) when it was built inside one, else the pulley itself."""
    try:
        parent   = pulley_occ.assemblyContext
        uid_attr = pulley_occ.component.attributes.itemByName(ATTR_GROUP, ATTR_PULLEY_UID)
        if parent is not None and uid_attr is not None:
            grp_attr = parent.component.attributes.itemByName(ATTR_GROUP, ATTR_PULLEY_GROUP)
            if grp_attr is not None and grp_attr.value == uid_attr.value:
                return parent
    except Exception:
        pass
    return pulley_occ


# ---------------------------------------------------------------------------
# Public creation entry point — called by PartsGen/entry.py
# ---------------------------------------------------------------------------

def _create_pulley(inputs: adsk.core.CommandInputs, is_preview: bool = False):
    """Create a timing pulley component from the PartsGen dialog inputs. Returns False if
    nothing was built (bad input or an error, already reported), else True.

    With "3D Print Adapter" on, the pulley and its adapter are built inside one
    "<pulley>_Group" component. The preview skips both (inserting a cloud part on every
    preview tick is slow), so entry.py makes command_execute rebuild it with them.
    """

    beltType:      adsk.core.DropDownCommandInput  = inputs.itemById('belt_type')
    toothCount:    adsk.core.ValueCommandInput     = inputs.itemById('tooth_count')
    beltWidth:     adsk.core.DropDownCommandInput  = inputs.itemById('belt_width')
    showTeethInp:  adsk.core.BoolValueCommandInput = inputs.itemById('pulley_show_teeth')

    width_mm       = _belt_width_mm(beltWidth)
    belt_width_cm  = width_mm / 10.0

    show_teeth = showTeethInp.value if showTeethInp is not None else False

    boreTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('pulley_bore_type')
    boreOffsetInp: adsk.core.ValueCommandInput     = inputs.itemById('pulley_bore_offset')
    bore_type = (boreTypeInp.selectedItem.name
                 if boreTypeInp is not None and boreTypeInp.selectedItem is not None
                 else BORE_HALF_HEX)
    bore_offset_cm = (boreOffsetInp.value if boreOffsetInp is not None
                      else BORE_OFFSET_DEFAULT_IN * 2.54)

    adapterInp: adsk.core.BoolValueCommandInput = inputs.itemById('pulley_adapter')
    use_adapter  = adapterInp is not None and adapterInp.value
    adapter_part = ADAPTER_PARTS.get(bore_type) if use_adapter else None

    n_teeth = int(toothCount.value)
    # Defensive guard — command_validate_input already blocks tooth counts below 8
    # from the dialog's OK button, but executePreview can call this function with a
    # transient/momentarily-invalid value while the user is still typing.
    if n_teeth < 3:
        futil.log('PartsGen _create_pulley: invalid tooth count, skipping')
        return False

    if adapter_part is not None:
        pitch_mm = 5 if beltType.selectedItem.index == 0 else 3
        if not _adapter_fits(adapter_part, pitch_mm, _outer_diameter_cm(pitch_mm, n_teeth)):
            if not is_preview:
                futil.popup_error(
                    f'Parts Gen: a {n_teeth}T pulley is too small for the '
                    f'{adapter_part["file"]} adapter (it would cut into the teeth), so the '
                    'pulley was built without it. Choose a larger tooth count.')
            adapter_part = None

    design    = adsk.fusion.Design.cast(app.activeProduct)
    rootComp  = design.rootComponent
    start_marker = design.timeline.markerPosition
    trans     = adsk.core.Matrix3D.create()
    pulley_uid = uuid.uuid4().hex
    # With the adapter, build the pulley inside its group from the start -- moving parts
    # in afterwards (moveToComponent) drops joint-solved positions (LESSONS_LEARNED.md).
    groupOcc = None
    try:
        if adapter_part is not None and not is_preview:
            groupOcc = rootComp.occurrences.addNewComponent(trans)
            groupOcc.component.attributes.add(ATTR_GROUP, ATTR_PULLEY_GROUP, pulley_uid)
            workingOcc = groupOcc.component.occurrences.addNewComponent(
                trans).createForAssemblyContext(groupOcc)
        else:
            workingOcc = rootComp.occurrences.addNewComponent(trans)
    except RuntimeError:
        _warn('Cannot create pulley: this document is in Part Design mode, '
              'which only supports a single component.\n\n'
              'Please open or create an Assembly document and try again.', is_preview)
        return False
    workingComp = workingOcc.component
    outerOcc    = groupOcc if groupOcc is not None else workingOcc   # what to delete on failure

    try:
        if beltType.selectedItem.index == 0:
            comp_name   = f'Pulley_HTD_5mm-{n_teeth}Tx{width_mm}mm'
            belt_pitch  = 5
        else:
            comp_name   = f'Pulley_GT2_3mm-{n_teeth}Tx{width_mm}mm'
            belt_pitch  = 3

        workingComp.name = comp_name

        customNameInp = inputs.itemById('custom_name')
        custom_name = customNameInp.value.strip() if customNameInp is not None else ''
        if custom_name:
            workingComp.name = comp_name = custom_name

        sketch = workingComp.sketches.add(rootComp.xYConstructionPlane, workingOcc)
        body = _add_pulley_body(workingComp, sketch, belt_pitch, n_teeth, show_teeth,
                                belt_width_cm, is_preview)
        if body is None:
            outerOcc.deleteMe()
            return False
        outer_diameter_cm = body[0]

        _add_flanges(workingComp, belt_width_cm, outer_diameter_cm)
        bot_plane = _add_bore(workingComp, belt_width_cm, outer_diameter_cm, bore_type,
                              bore_offset_cm, is_preview)
        # Keep the label's fallback spot (just above the bore) clear of the adapter.
        label_floor_cm = _bore_radius_cm(bore_type, bore_offset_cm)
        if groupOcc is not None:
            label_floor_cm = max(label_floor_cm, adapter_part['radius_cm'])
        _add_label(workingComp, belt_width_cm, n_teeth, outer_diameter_cm, label_floor_cm,
                   bot_plane, is_preview)
        _log_unconstrained(workingComp)

        if groupOcc is not None:
            groupOcc.component.name = f'{comp_name}_Group'
            _add_adapter(workingOcc, groupOcc, adapter_part, pulley_uid)

        # Save attributes so the right-click Edit command can restore the dialog
        try:
            attrs = workingComp.attributes
            attrs.add(ATTR_GROUP, ATTR_PART_TYPE,           'Timing Pulley')
            attrs.add(ATTR_GROUP, ATTR_PULLEY_ADAPTER,      str(use_adapter))
            attrs.add(ATTR_GROUP, ATTR_PULLEY_UID,          pulley_uid)
            attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_TYPE,    beltType.selectedItem.name)
            attrs.add(ATTR_GROUP, ATTR_PULLEY_TOOTH_COUNT,  str(n_teeth))
            attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_WIDTH,   f'{width_mm} mm')
            attrs.add(ATTR_GROUP, ATTR_PULLEY_SHOW_TEETH,   str(show_teeth))
            attrs.add(ATTR_GROUP, ATTR_PULLEY_BORE_TYPE,    bore_type)
            if boreOffsetInp is not None:
                attrs.add(ATTR_GROUP, ATTR_PULLEY_BORE_OFFSET, boreOffsetInp.expression)
            if custom_name:
                attrs.add(ATTR_GROUP, ATTR_CUSTOM_NAME,     custom_name)
        except Exception:
            futil.log('PartsGen: failed to save pulley attributes')

        futil.group_timeline_features(design, start_marker, comp_name)
        return True
    except Exception:
        try:
            outerOcc.deleteMe()
        except Exception:
            pass
        futil.handle_error('PartsGen _create_pulley', show_message_box=not is_preview)
        return False


def create_pulley_for_belt(belt_pitch_mm: int, n_teeth: int, belt_width_cm: float,
                           belt_occ: adsk.fusion.Occurrence = None,
                           proj_circle: adsk.fusion.SketchCircle = None,
                           show_teeth: bool = False,
                           circle_index: int = 0,
                           parent_comp: adsk.fusion.Component = None,
                           bore_type: str = BORE_HALF_HEX,
                           bore_offset_cm: float = BORE_OFFSET_DEFAULT_IN * 2.54,
                           use_adapter: bool = False,
                           is_preview: bool = False):
    """Create a timing pulley component from raw parameters.

    Called by belt_gen._create_belt() to auto-generate matched pulleys when
    a belt is created.  Uses the same geometry functions as _create_pulley()
    but accepts numeric values instead of CommandInputs.

    A parametric revolute Joint is created between the pulley and belt_occ
    using proj_circle.centerSketchPoint as the live anchor, so the pulley
    automatically repositions when the belt's C-C distance is edited.

    With `use_adapter` (skipped in preview, like _create_pulley) the pulley and its
    3D print adapter are built inside a "<pulley>_Group" component in `parent_comp`.
    """
    design    = adsk.fusion.Design.cast(app.activeProduct)
    rootComp  = design.rootComponent
    parent    = parent_comp if parent_comp is not None else rootComp
    start_marker = design.timeline.markerPosition

    adapter_part = ADAPTER_PARTS.get(bore_type) if use_adapter and not is_preview else None
    if adapter_part is not None and not _adapter_fits(
            adapter_part, belt_pitch_mm, _outer_diameter_cm(belt_pitch_mm, n_teeth)):
        futil.popup_error(
            f'Parts Gen: a {n_teeth}T pulley is too small for the '
            f'{adapter_part["file"]} adapter (it would cut into the teeth), so the '
            'pulley was built without it.')
        adapter_part = None

    pulley_uid = uuid.uuid4().hex
    groupOcc   = None       # native occurrence of the group, in `parent`
    if adapter_part is not None:
        # Build at identity: _add_adapter reads the pulley's faces in world space and
        # places the adapter at the origin. The revolute joint below then moves the
        # pulley (and the rigidly-jointed adapter with it) onto the pitch circle.
        groupOcc = parent.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        groupOcc.component.attributes.add(ATTR_GROUP, ATTR_PULLEY_GROUP, pulley_uid)
        workingOcc = groupOcc.component.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    else:
        # Pre-position the occurrence at the pitch-circle centre so the two pulleys
        # do not overlap during geometry creation (label engraving in particular).
        # Without this, both pulleys start at the origin and the label cut from one
        # pulley visually overlaps the other, making the tooth count hard to read.
        # The parametric Joint created below locks the position parametrically so
        # the pulley follows automatically when the C-C distance is edited.
        trans = adsk.core.Matrix3D.create()
        if proj_circle is not None:
            c = proj_circle.centerSketchPoint.geometry
            trans.translation = adsk.core.Vector3D.create(c.x, c.y, 0.0)
        workingOcc = parent.occurrences.addNewComponent(trans)
    workingComp = workingOcc.component

    width_mm = int(round(belt_width_cm * 10))   # cm → mm

    if belt_pitch_mm == 5:
        comp_name      = f'Pulley_HTD_5mm-{n_teeth}Tx{width_mm}mm'
        belt_type_name = BELT_HTD
    else:
        comp_name      = f'Pulley_GT2_3mm-{n_teeth}Tx{width_mm}mm'
        belt_type_name = BELT_GT2

    workingComp.name = comp_name

    try:
        # Local XY plane — sketch geometry is in the component's local space and
        # is transformed to the pitch-circle centre via the occurrence transform above.
        sketch = workingComp.sketches.add(workingComp.xYConstructionPlane)
        body = _add_pulley_body(workingComp, sketch, belt_pitch_mm, n_teeth, show_teeth,
                                belt_width_cm, is_preview)
        if body is None:
            (groupOcc or workingOcc).deleteMe()
            return
        outer_diameter_cm, joint_circle = body

        _add_flanges(workingComp, belt_width_cm, outer_diameter_cm)
        bot_plane = _add_bore(workingComp, belt_width_cm, outer_diameter_cm, bore_type,
                              bore_offset_cm, is_preview)
        label_floor_cm = _bore_radius_cm(bore_type, bore_offset_cm)
        if adapter_part is not None:
            label_floor_cm = max(label_floor_cm, adapter_part['radius_cm'])
        _add_label(workingComp, belt_width_cm, n_teeth, outer_diameter_cm, label_floor_cm,
                   bot_plane, is_preview)
        _log_unconstrained(workingComp)
    except Exception:
        try:
            (groupOcc or workingOcc).deleteMe()
        except Exception:
            pass
        futil.handle_error(f'PartsGen: auto-pulley {comp_name}',
                           show_message_box=not is_preview)
        return

    # Pulley occurrence as seen from `parent` (where the revolute joint lives).
    joint_occ = workingOcc
    if groupOcc is not None:
        groupOcc.component.name = f'{comp_name}_Group'
        joint_occ = workingOcc.createForAssemblyContext(groupOcc)
        # _add_adapter wants root-context proxies (it joints in the root component).
        group_ctx = (groupOcc.createForAssemblyContext(belt_occ)
                     if parent_comp is not None and belt_occ is not None else groupOcc)
        _add_adapter(workingOcc.createForAssemblyContext(group_ctx), group_ctx,
                     adapter_part, pulley_uid)

    try:
        attrs = workingComp.attributes
        attrs.add(ATTR_GROUP, ATTR_PART_TYPE,           'Timing Pulley')
        attrs.add(ATTR_GROUP, ATTR_PULLEY_ADAPTER,      str(use_adapter))
        attrs.add(ATTR_GROUP, ATTR_PULLEY_UID,          pulley_uid)
        attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_TYPE,    belt_type_name)
        attrs.add(ATTR_GROUP, ATTR_PULLEY_TOOTH_COUNT,  str(n_teeth))
        attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_WIDTH,   f'{width_mm} mm')
        attrs.add(ATTR_GROUP, ATTR_PULLEY_SHOW_TEETH,   str(show_teeth))
        attrs.add(ATTR_GROUP, ATTR_PULLEY_BORE_TYPE,    bore_type)
        attrs.add(ATTR_GROUP, ATTR_PULLEY_BORE_OFFSET,  f'{bore_offset_cm / 2.54:.4f} in')
        if belt_occ is not None:
            attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_COMP_TOKEN,  belt_occ.component.entityToken)
            attrs.add(ATTR_GROUP, ATTR_PULLEY_PITCH_CIRCLE_IDX, str(circle_index))
    except Exception:
        futil.log('PartsGen: failed to save auto-pulley attributes')

    # Parametric revolute Joint: pulley centre <-> projected pitch-circle centre in
    # the belt sketch.  When CCDistance edits the belt the projected point moves,
    # Fusion re-solves the joint, and the pulley repositions automatically.
    if proj_circle is not None and belt_occ is not None:
        try:
            # Use createForAssemblyContext so the joint targets this specific
            # occurrence rather than the component prototype.
            outer_circle = joint_circle.createForAssemblyContext(joint_occ)
            pulley_geom = adsk.fusion.JointGeometry.createByCurve(
                outer_circle,
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            belt_geom   = adsk.fusion.JointGeometry.createByCurve(
                proj_circle,
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            joint_input = parent.joints.createInput(pulley_geom, belt_geom)
            joint_input.setAsRevoluteJointMotion(
                adsk.fusion.JointDirections.ZAxisJointDirection)
            joint = parent.joints.add(joint_input)
            joint.name = f'{comp_name}_revolute'
        except Exception:
            futil.handle_error(f'PartsGen: joint for {comp_name}', show_message_box=not is_preview)

    futil.group_timeline_features(design, start_marker, comp_name)


# ---------------------------------------------------------------------------
# Tooth profile geometry
#
# Every point of one tooth is placed from closed-form maths and fixed, then a circular
# pattern constraint makes the rest -- the same approach as _draw_splinexs_bore. The
# profiles used to be built by the sketch solver from rough guesses (tangents + dimension
# values), which failed outright for GT2 36-60T and HTD 64T (over-constrained / failed to
# solve) because the solver had to pull the rough geometry too far.
#
# Maths is in mm, in a frame where +x runs along the centre of the tooth space to the right
# of the tooth on +Y (at 90 - 180/n degrees) and +y points back towards that tooth. Only the
# right half of the tooth is computed; the left half is its mirror image in the Y axis.
# ---------------------------------------------------------------------------

# HTD 5mm: pitch-line offset (belt thickness), tip fillet, root arc radius, root depth and
# tooth-space width at the tip fillet.
HTD_BELT_THICKNESS_MM = 1.74
HTD_TOP_RADIUS_MM     = 0.43
HTD_ROOT_RADIUS_MM    = 1.49
HTD_ROOT_HEIGHT_MM    = 2.06
HTD_ROOT_WIDTH_MM     = 3.05

# GT2 3mm: pitch-line offset, tip fillet, root arc radius, root depth, and the transition
# arc that joins them (its radius and its centre's offset past the tooth-space centreline).
GT2_PITCH_LINE_OFFSET_MM = 0.381
GT2_TOP_RADIUS_MM        = 0.25
GT2_ROOT_RADIUS_MM       = 0.85
GT2_ROOT_HEIGHT_MM       = 1.14
GT2_TRANSITION_RADIUS_MM = 1.52
GT2_TRANSITION_OFFSET_MM = 0.61


def _v_add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _v_scale(a, s):
    return (a[0] * s, a[1] * s)


def _v_unit(a):
    n = math.hypot(a[0], a[1])
    return (a[0] / n, a[1] / n)


def _htd_tooth_curves(n_teeth: int):
    """Right half of one HTD tooth, as ('arc', centre, start, end) / ('line', start, end)
    tuples in the tooth frame (mm): tip arc, tip fillet, flank line, root arc."""
    R    = (n_teeth * 5.0 / math.pi - HTD_BELT_THICKNESS_MM) / 2
    phi  = math.pi / n_teeth
    rt   = HTD_TOP_RADIUS_MM
    rr   = HTD_ROOT_RADIUS_MM
    c_r  = (R - HTD_ROOT_HEIGHT_MM + rr, 0.0)          # root arc centre, on the centreline

    def _flank(beta):
        # Tip fillet centre at angle `beta`, internally tangent to the tip circle, and the
        # flank line tangent to it and to the root arc (circles on opposite sides). None
        # while the fillet still overlaps the root arc (beta too close to the centreline).
        c_t  = ((R - rt) * math.cos(beta), (R - rt) * math.sin(beta))
        d    = _v_sub(c_r, c_t)
        dist = math.hypot(d[0], d[1])
        if dist <= rr + rt:
            return None
        ang  = math.atan2(d[1], d[0]) + math.acos((rr + rt) / dist)
        m    = (math.cos(ang), math.sin(ang))
        return c_t, _v_add(c_t, _v_scale(m, rt)), _v_sub(c_r, _v_scale(m, rr))

    def _miss(beta):
        # How far the flank's top sits past half the root width from the centreline;
        # grows with beta.
        flank = _flank(beta)
        return -1.0 if flank is None else flank[1][1] - HTD_ROOT_WIDTH_MM / 2

    lo, hi = 0.0, phi
    if _miss(hi) <= 0:
        raise ValueError(f'HTD {n_teeth}T: no tooth fits between the tooth spaces')
    for _ in range(60):
        mid = (lo + hi) / 2
        if _miss(mid) < 0:
            lo = mid
        else:
            hi = mid
    c_t, flank_top, flank_bot = _flank(hi)

    top  = (R * math.cos(phi), R * math.sin(phi))
    tip  = _v_scale(c_t, R / (R - rt))
    root = (R - HTD_ROOT_HEIGHT_MM, 0.0)
    return [('arc', (0.0, 0.0), top, tip),
            ('arc', c_t, tip, flank_top),
            ('line', flank_top, flank_bot),
            ('arc', c_r, flank_bot, root)]


def _gt2_tooth_curves(n_teeth: int):
    """Right half of one GT2 tooth in the tooth frame (mm): tip arc, tip fillet,
    transition arc, root arc (see _htd_tooth_curves)."""
    R   = n_teeth * 3.0 / math.pi / 2 - GT2_PITCH_LINE_OFFSET_MM
    phi = math.pi / n_teeth
    rt  = GT2_TOP_RADIUS_MM
    rr  = GT2_ROOT_RADIUS_MM
    r_x = GT2_TRANSITION_RADIUS_MM
    off = GT2_TRANSITION_OFFSET_MM

    c_r = (R - GT2_ROOT_HEIGHT_MM + rr, 0.0)
    # Transition arc: its centre sits `off` past the centreline, with the root arc
    # internally tangent to it.
    c_x = (c_r[0] + math.sqrt((r_x - rr) ** 2 - off ** 2), -off)

    # Tip fillet centre: internally tangent to the tip circle (|c_t| = R - rt) and
    # externally tangent to the transition arc (|c_t - c_x| = r_x + rt), tooth side.
    r1, r2 = R - rt, r_x + rt
    d = math.hypot(c_x[0], c_x[1])
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h2 = r1 * r1 - a * a
    if h2 <= 0:
        raise ValueError(f'GT2 {n_teeth}T: tip fillet cannot reach the transition arc')
    h = math.sqrt(h2)
    ux, uy = c_x[0] / d, c_x[1] / d
    cands = [(a * ux - s * h * uy, a * uy + s * h * ux) for s in (1, -1)]
    c_t = max(cands, key=lambda p: p[1])
    if math.atan2(c_t[1], c_t[0]) >= phi:
        raise ValueError(f'GT2 {n_teeth}T: no tooth fits between the tooth spaces')

    top  = (R * math.cos(phi), R * math.sin(phi))
    tip  = _v_scale(c_t, R / r1)
    j1   = _v_add(c_t, _v_scale(_v_unit(_v_sub(c_x, c_t)), rt))
    j2   = _v_add(c_x, _v_scale(_v_unit(_v_sub(c_r, c_x)), r_x))
    root = (R - GT2_ROOT_HEIGHT_MM, 0.0)
    return [('arc', (0.0, 0.0), top, tip),
            ('arc', c_t, tip, j1),
            ('arc', c_x, j1, j2),
            ('arc', c_r, j2, root)]


def _draw_tooth_profile(sketch: adsk.fusion.Sketch, n_teeth: int, half_tooth):
    """Draw one tooth on +Y from `half_tooth` (its right half, tooth frame, mm) plus its
    mirror image, fix every point, and circular-pattern it about the sketch origin.

    The first curve must be the tip arc (from the tooth's top to its fillet). It is drawn
    as one arc across both halves, since a small tooth's tip is only ~0.01 mm wide."""
    phi = math.pi / n_teeth
    ux, uy = math.sin(phi), math.cos(phi)        # centreline direction, at 90 - phi deg

    def _world(p, mirror):
        x = (p[0] * ux - p[1] * uy) / 10         # mm -> cm
        y = (p[0] * uy + p[1] * ux) / 10
        return adsk.core.Point3D.create(-x if mirror else x, y, 0)

    def _arc_mid(c, s, e):
        # Midpoint of the short arc from s to e around c.
        r  = math.hypot(s[0] - c[0], s[1] - c[1])
        vm = _v_unit(_v_add(_v_unit(_v_sub(s, c)), _v_unit(_v_sub(e, c))))
        return _v_add(c, _v_scale(vm, r))

    lines = sketch.sketchCurves.sketchLines
    arcs  = sketch.sketchCurves.sketchArcs
    tip = half_tooth[0][3]
    sketch.isComputeDeferred = True
    try:
        # Centre + sweep, not three points: on HTD 8T the tip is ~0.008 mm wide, and
        # addByThreePoints rejects three points that close together.
        tip_sweep = 2 * (phi - math.atan2(tip[1], tip[0]))
        tooth = [arcs.addByCenterStartSweep(adsk.core.Point3D.create(0, 0, 0),
                                            _world(tip, False), tip_sweep)]
        for mirror in (False, True):
            for curve in half_tooth[1:]:
                if curve[0] == 'line':
                    tooth.append(lines.addByTwoPoints(_world(curve[1], mirror),
                                                      _world(curve[2], mirror)))
                else:
                    _, c, s, e = curve
                    tooth.append(arcs.addByThreePoints(_world(s, mirror),
                                                       _world(_arc_mid(c, s, e), mirror),
                                                       _world(e, mirror)))
        for curve in tooth:
            curve.startSketchPoint.isFixed = True
            curve.endSketchPoint.isFixed   = True
            arc = adsk.fusion.SketchArc.cast(curve)
            if arc is not None:
                arc.centerSketchPoint.isFixed = True
    finally:
        sketch.isComputeDeferred = False

    pattern = sketch.geometricConstraints.createCircularPatternInput(tooth, sketch.originPoint)
    pattern.quantity = adsk.core.ValueInput.createByReal(n_teeth)
    sketch.geometricConstraints.addCircularPattern(pattern)


def createHTDPulleyGeometry(sketch: adsk.fusion.Sketch, beltPitchMM: float, toothCount: int):
    """Draw the fully-constrained HTD 5mm tooth profile; returns the tooth OD in cm."""
    _draw_tooth_profile(sketch, toothCount, _htd_tooth_curves(toothCount))
    return _outer_diameter_cm(5, toothCount)


def createGT2PulleyGeometry(sketch: adsk.fusion.Sketch, beltPitchMM: float, toothCount: int):
    """Draw the fully-constrained GT2 3mm tooth profile; returns the tooth OD in cm."""
    _draw_tooth_profile(sketch, toothCount, _gt2_tooth_curves(toothCount))
    return _outer_diameter_cm(3, toothCount)
