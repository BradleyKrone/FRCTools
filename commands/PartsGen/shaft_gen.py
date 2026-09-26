import adsk.core
import adsk.fusion
import math
import uuid
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

SHAFT_HALF_HEX            = '1/2" Hex Shaft'
SHAFT_THREE_EIGHTH_HEX    = '3/8" Hex Shaft'
SHAFT_MAXSPLINE           = 'MAXSpline Shaft'
SHAFT_HALF_HEX_SPACER     = '1/2" Hex Spacer'
SHAFT_THREE_EIGHTH_SPACER = '3/8" Hex Spacer'
SHAFT_CUSTOM              = 'Custom (Round Tube)'

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

# "Hex Spacer" shaft types: the inverse of their same-size hex shaft -- a user-set round OD
# with a hex bore that slips freely over that hex shaft, instead of a hex OD with a round
# bore. The bore is that shaft size's own rounded-hex profile with a radial clearance added
# to both the flats apothem and the corner-circle radius -- not a uniform scale -- so it
# spins freely instead of binding. Not a guess: measured live off Team 1756's existing
# "Hood_Spacer" part (Double Wheel Shooter design) via the Fusion MCP server, where both
# numbers came out offset from the nominal 1/2in hex by exactly the same 0.008in. See
# LESSONS_LEARNED.md.
HEX_SPACER_CLEARANCE_IN = 0.008
HEX_SPACER_CLEARANCE_CM = HEX_SPACER_CLEARANCE_IN * IN_TO_CM


def _hex_spacer_bore_dims_cm(shaft_type: str):
    """Return (flats_cm, round_dia_cm) of a Hex Spacer's clearance bore for `shaft_type`."""
    if shaft_type == SHAFT_THREE_EIGHTH_SPACER:
        flats_cm, round_dia_cm = SHAFT_THREE_EIGHTH_FLATS_CM, SHAFT_THREE_EIGHTH_ROUND_DIA_CM
    else:
        flats_cm, round_dia_cm = SHAFT_HALF_HEX_FLATS_CM, SHAFT_HALF_HEX_ROUND_DIA_CM
    return (flats_cm + 2 * HEX_SPACER_CLEARANCE_CM,
            round_dia_cm + 2 * HEX_SPACER_CLEARANCE_CM)

# REV Robotics MAXSpline shaft (revrobotics.com/MAXSpline-shafts-47in, REV-21-2520): a
# 6-lobe wavy spline. Major/minor OD diameters match REV's drawing (REV-21-2520-DR.pdf);
# neither the lobe shape (each flank is a major-circle arc and a minor-circle arc joined by
# two small blend arcs) nor the bore is on that 2D drawing -- REV's drawing just labels the
# bore "25.4 mm ID", but the real bore is *also* the same 6-lobe wave, offset in from the
# OD by a constant ~1.575 mm wall thickness (25.4 mm is only the bore's minor diameter, at
# the valleys). Both waves were measured directly off REV's own STEP file via a live Fusion
# import+inspection (exact arc radii/centers read off the real B-rep edges) -- reproducing
# a plain round bore looked "close" but was visibly wrong next to the real part.
SHAFT_MAXSPLINE_TEETH             = 6
SHAFT_MAXSPLINE_MAJOR_DIA_CM      = 34.9    * MM_TO_CM
SHAFT_MAXSPLINE_MINOR_DIA_CM      = 28.55   * MM_TO_CM
SHAFT_MAXSPLINE_BLEND_A_RADIUS_CM = 2.0625  * MM_TO_CM
SHAFT_MAXSPLINE_BLEND_B_RADIUS_CM = 2.0375  * MM_TO_CM
# The bore wave: the same profile as the OD, offset inward by a constant wall thickness, so
# its major/minor diameters and blend radii are all exactly (OD value -+ 2x wall thickness)
# -- confirmed against the STEP data, not just assumed. Its minor diameter (25.4 mm) is the
# only bore dimension REV's own drawing calls out.
SHAFT_MAXSPLINE_BORE_MAJOR_DIA_CM      = 31.75  * MM_TO_CM
SHAFT_MAXSPLINE_BORE_MINOR_DIA_CM      = 25.4   * MM_TO_CM
SHAFT_MAXSPLINE_BORE_BLEND_A_RADIUS_CM = 3.6375 * MM_TO_CM
SHAFT_MAXSPLINE_BORE_BLEND_B_RADIUS_CM = 0.4625 * MM_TO_CM
# Junction angles (degrees) of one flank, measured from a valley's (minor arc's) own
# centreline: where the valley arc ends and blend A starts, where blend A ends and blend B
# starts, and where blend B ends and the tip (major arc) starts. The tip's own centreline
# sits at exactly half a tooth pitch (30 deg for 6 teeth); the other flank mirrors these.
# The bore wave shares these same angles -- it's a radial offset of the OD, not an
# independently-angled shape.
SHAFT_MAXSPLINE_VALLEY_END_DEG  = 8.182896124804051
SHAFT_MAXSPLINE_BLEND_A_END_DEG = 15.245720358879575
SHAFT_MAXSPLINE_BLEND_B_END_DEG = 22.643501176981662

ATTR_GROUP      = 'FRCTools_PartsGen'
ATTR_PART_TYPE  = 'part_type'
ATTR_SHAFT_TYPE = 'shaft_type'
ATTR_CUSTOM_OD  = 'custom_od_expr'
ATTR_CUSTOM_ID  = 'custom_id_expr'
ATTR_LEN_EXPR   = 'custom_len_expr'
ATTR_CUSTOM_NAME = 'custom_name'
ATTR_CREATE_JOINT = 'shaft_create_joint'
ATTR_JOINT_TYPE   = 'shaft_joint_type'
ATTR_JOINT_FLIP   = 'shaft_joint_flip'
ATTR_REVERSE_DIR  = 'shaft_reverse_direction'

# Entity tokens for the Between-Two-Faces picks, so right-click Edit can rebuild the shaft
# where it was instead of dropping back to a Custom Length at the world origin. A Custom
# Length shaft built from a Reference Point (no Face 2) stores only the ref-point token.
ATTR_REF_POINT_TOKEN = 'shaft_ref_point_token'
ATTR_FACE2_TOKEN     = 'shaft_face2_token'

# Optional bearing/bushing on both of the shaft's ends, picked from the "Add Bearing"
# dropdown. Each part is inserted as a linked component from Team 1756's own library files
# (Argos CAD > Parts_1 > Parts_Gen), never redrawn. A lineage URN (no ?version=) always
# resolves to the latest version. entry.py builds its dropdown straight from this table.
#   shaft     -- the shaft type the part fits (it's only offered for that type)
#   flange_cm -- axial distance from the flange's seat (the face that sits against the
#                plate) to its outer face -- how far the shaft end sticks out past the
#                plate. Measured live off each file.
BEARING_NONE = 'None'
BEARING_PARTS = {
    'Bearing (WCP-0785)': dict(urn='urn:adsk.wipprod:dm.lineage:d9ZIwsI6QNWLT3Khg8p3Dw',
                               file='Hex_Bearing_WCP-0785',
                               shaft=SHAFT_HALF_HEX, flange_cm=0.1587),
    'Bushing (WCP-0999)': dict(urn='urn:adsk.wipprod:dm.lineage:LPU6SMqwSF-tge9jFnPPTA',
                               file='Hex_Bushing_WCP-0999',
                               shaft=SHAFT_HALF_HEX, flange_cm=0.1588),
    'Bearing (WCP-0784)': dict(urn='urn:adsk.wipprod:dm.lineage:Qh1CTEEoS4CFexctUFifMQ',
                               file='Hex_3_8_WCP-0784',
                               shaft=SHAFT_THREE_EIGHTH_HEX, flange_cm=0.1587),
    'Bearing (TTB-0065)': dict(urn='urn:adsk.wipprod:dm.lineage:ySAXuVQ8QHK9p0QCAX94cw',
                               file='MaxSpline_Bearing_TTB-0065',
                               shaft=SHAFT_MAXSPLINE, flange_cm=0.15),
}
# Values the dropdown stored before it picked a part (it picked which ends instead, and
# the only part was the WCP-0785) -- read back as the WCP-0785 on both ends.
BEARING_LEGACY_ENDS = ('Both Ends', 'Reference End Only', 'Face 2 End Only')
BEARING_LEGACY_PART = 'Bearing (WCP-0785)'


def bearing_part_name(value: str) -> str:
    """Normalise a stored/selected 'Add Bearing' value to a BEARING_PARTS key or BEARING_NONE."""
    if value in BEARING_LEGACY_ENDS:
        return BEARING_LEGACY_PART
    return value if value in BEARING_PARTS else BEARING_NONE


def bearing_choices(shaft_type: str):
    """The 'Add Bearing' dropdown items for a shaft type: None plus every part that fits."""
    return [BEARING_NONE] + [n for n, p in BEARING_PARTS.items() if p['shaft'] == shaft_type]


ATTR_BEARING_ENDS  = 'shaft_bearing_ends'
# A random id stored on the shaft's component and copied onto each of its bearing
# occurrences, so right-click Edit can find and delete the old bearings before rebuilding
# -- deleting the shaft alone only cascades away their joints, leaving the bearings behind.
ATTR_SHAFT_UID     = 'shaft_uid'
ATTR_BEARING_OWNER = 'bearing_owner_shaft_uid'
# Marks the "<shaft>_Group" component a shaft and its bearings are built inside (same uid).
ATTR_SHAFT_GROUP   = 'shaft_group_uid'

BEARING_RADIUS_TOL_CM = 1e-3

# Must match entry.py's JOINT_REVOLUTE/JOINT_RIGID exactly -- both files independently
# read/write the same dialog list-item names and component attribute values.
JOINT_REVOLUTE = 'Revolute (Spins Freely)'
JOINT_RIGID    = 'Rigid (Fixed)'

# Persistent color marking the reference face (the face the part was extruded from) --
# matches entry.py's transient preview highlight so the color doesn't change when the
# dialog closes and this permanent appearance takes over.
REF_FACE_APPEARANCE_NAME = 'FRCTools_PartsGen_RefFace'
REF_FACE_COLOR           = (120, 190, 255)  # light blue


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


def _draw_maxspline_wave(sketch: adsk.fusion.Sketch,
                         center: adsk.core.Point3D,
                         major_dia_cm: float,
                         minor_dia_cm: float,
                         blend_a_radius_cm: float,
                         blend_b_radius_cm: float,
                         constrain: bool = True):
    """Draw a REV MAXSpline wave centred at `center`: `SHAFT_MAXSPLINE_TEETH` lobes, each
    flank built from a major-circle arc (tip) and a minor-circle arc (valley) joined by two
    small blend arcs, mirrored either side of the tip. Used for both the OD and the bore --
    the bore is the same wave shape at smaller radii (a constant-wall-thickness offset of
    the OD), not a plain circle; see the SHAFT_MAXSPLINE_* comment above.

    Every vertex (and every blend arc's off-axis centre) is placed by a closed-form polar
    formula from the given radii and the module's fixed junction angles, so the loop closes
    exactly without relying on the sketch solver to resolve the blend arcs' tangency itself
    (that turned into two circles chased by one equation, which is where a generic
    `addTangent` pass kept snapping to the wrong branch when tried live in Fusion) --
    instead every point is pinned with `isFixed`, the same trick `_anchor_point` uses for a
    single point, just applied to the whole profile.

    `constrain=False` draws the geometry only (see `_draw_rounded_hex` for why).
    """
    N       = SHAFT_MAXSPLINE_TEETH
    pitch   = 360.0 / N
    R_maj   = major_dia_cm / 2.0
    R_min   = minor_dia_cm / 2.0
    r_a     = blend_a_radius_cm
    r_b     = blend_b_radius_cm
    a1      = SHAFT_MAXSPLINE_VALLEY_END_DEG
    a3      = SHAFT_MAXSPLINE_BLEND_B_END_DEG

    cx, cy = center.x, center.y

    def _pt(radius, angle_deg):
        a = math.radians(angle_deg)
        return adsk.core.Point3D.create(cx + radius * math.cos(a), cy + radius * math.sin(a), 0.0)

    def _blend_center(dist, angle_deg):
        a = math.radians(angle_deg)
        return adsk.core.Point3D.create(cx + dist * math.cos(a), cy + dist * math.sin(a), 0.0)

    def _tangent_point(c_from, r_from, c_to):
        # Point where two externally-tangent circles (centres `c_from`/`c_to`, the first
        # of radius `r_from`) touch.
        vx, vy = c_to.x - c_from.x, c_to.y - c_from.y
        d = math.hypot(vx, vy)
        return adsk.core.Point3D.create(c_from.x + r_from * vx / d, c_from.y + r_from * vy / d, 0.0)

    def _arc_mid(c, r, p_start, p_end):
        # Midpoint of the short arc from p_start to p_end around centre `c`.
        vx = (p_start.x - c.x) + (p_end.x - c.x)
        vy = (p_start.y - c.y) + (p_end.y - c.y)
        n = math.hypot(vx, vy)
        return adsk.core.Point3D.create(c.x + r * vx / n, c.y + r * vy / n, 0.0)

    arcs = sketch.sketchCurves.sketchArcs
    loop = []
    sketch.isComputeDeferred = True
    try:
        for i in range(N):
            off = i * pitch

            v_start = _pt(R_min, off - a1)
            v_end   = _pt(R_min, off + a1)
            loop.append(arcs.addByThreePoints(v_start, _pt(R_min, off), v_end))

            c_a      = _blend_center(R_min + r_a, off + a1)
            c_b      = _blend_center(R_maj - r_b, off + a3)
            junction = _tangent_point(c_a, r_a, c_b)
            maj_start = _pt(R_maj, off + a3)
            loop.append(arcs.addByThreePoints(v_end, _arc_mid(c_a, r_a, v_end, junction), junction))
            loop.append(arcs.addByThreePoints(junction, _arc_mid(c_b, r_b, junction, maj_start), maj_start))

            maj_end = _pt(R_maj, off + pitch - a3)
            loop.append(arcs.addByThreePoints(maj_start, _pt(R_maj, off + pitch / 2.0), maj_end))

            c_b2      = _blend_center(R_maj - r_b, off + pitch - a3)
            c_a2      = _blend_center(R_min + r_a, off + pitch - a1)
            junction2 = _tangent_point(c_b2, r_b, c_a2)
            v_next_start = _pt(R_min, off + pitch - a1)
            loop.append(arcs.addByThreePoints(maj_end, _arc_mid(c_b2, r_b, maj_end, junction2), junction2))
            loop.append(arcs.addByThreePoints(junction2, _arc_mid(c_a2, r_a, junction2, v_next_start), v_next_start))

        if constrain:
            for arc in loop:
                arc.startSketchPoint.isFixed  = True
                arc.endSketchPoint.isFixed    = True
                arc.centerSketchPoint.isFixed = True
    finally:
        sketch.isComputeDeferred = False


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


def _extrude_direction(normal: adsk.core.Vector3D,
                       centroid1: adsk.core.Point3D,
                       centroid2: adsk.core.Point3D):
    """Return the ExtentDirection such that the extrusion goes from centroid1 toward centroid2,
    given the sketch plane's own `normal`."""
    dx = centroid2.x - centroid1.x
    dy = centroid2.y - centroid1.y
    dz = centroid2.z - centroid1.z
    dot = dx * normal.x + dy * normal.y + dz * normal.z
    # Face 2 sitting edge-on to the sketch plane makes the sign a coin flip. That was
    # impossible while the sketch plane was always parallel to Face 2 (`dot` was then just
    # the gap between them), but a shaft built on the hole's axis can tilt far enough to
    # reach it. Keep the positive direction, but say so rather than failing silently.
    if abs(dot) < 1e-7:
        futil.log('PartsGen: Face 2 is nearly edge-on to the shaft; '
                  'extrude direction is ambiguous, using positive')
    return (
        adsk.fusion.ExtentDirections.PositiveExtentDirection
        if dot >= 0
        else adsk.fusion.ExtentDirections.NegativeExtentDirection
    )


def _point_from_entity(entity) -> adsk.core.Point3D:
    """Return the world-space Point3D a Reference Point entity represents -- a circular
    edge's centre, a straight/general edge's midpoint, a face's centroid, or a
    BRepVertex/SketchPoint/ConstructionPoint's own position."""
    if entity.objectType == adsk.fusion.BRepFace.classType():
        return entity.centroid
    if entity.objectType == adsk.fusion.BRepEdge.classType():
        geom = entity.geometry
        for curve_cls in (adsk.core.Circle3D, adsk.core.Arc3D, adsk.core.Ellipse3D):
            curve = curve_cls.cast(geom)
            if curve is not None:
                return curve.center
        evaluator = entity.evaluator
        _, start_param, end_param = evaluator.getParameterExtents()
        _, mid_point = evaluator.getPointAtParameter((start_param + end_param) / 2.0)
        return mid_point
    if hasattr(entity, 'worldGeometry'):
        return entity.worldGeometry
    return entity.geometry


def _selected_entity(sel_input: adsk.core.SelectionCommandInput):
    """Return the first selected entity, or None if nothing is selected yet.

    `selectionCount` can momentarily read >= 1 while `selection(0)` still throws
    `RuntimeError: invalid argument index` -- confirmed live during a live preview tick
    fired mid-click, right as a selection was being made -- so don't trust the count
    alone; treat that failure the same as "nothing selected" rather than letting it
    propagate as a real error.
    """
    if sel_input is None or sel_input.selectionCount < 1:
        return None
    try:
        return sel_input.selection(0).entity
    except RuntimeError:
        return None


def _plane_offset_point(workingComp: adsk.fusion.Component, entity):
    """Return a BRepVertex/SketchPoint/ConstructionPoint usable with
    ConstructionPlaneInput.setByOffsetThroughPoint -- creating a construction point at
    a circular edge's centre first, since setByOffsetThroughPoint doesn't take an edge
    directly. Returns None for a straight/general edge or a face: Fusion has no parametric
    "point at an edge's midpoint" or "point at a face's centroid" construction-point input,
    so those fall back to a numeric offset instead (see _offset_plane_parallel_to_face2)."""
    if entity.objectType == adsk.fusion.BRepEdge.classType():
        if _ref_axis(entity) is None:
            return None
        cp_input = workingComp.constructionPoints.createInput()
        cp_input.setByCenter(entity)
        return workingComp.constructionPoints.add(cp_input)
    if entity.objectType == adsk.fusion.BRepFace.classType():
        return None
    return entity


def _offset_plane_parallel_to(workingComp: adsk.fusion.Component,
                              base_plane,
                              entity, world_point: adsk.core.Point3D):
    """Build the construction plane the fallback shaft placement sketches onto: parallel to
    `base_plane` (Face 2 in Between-Two-Faces mode, or the root XY plane in Custom Length
    mode when no hole axis is available), passing through the Reference Point.

    Prefers `setByOffsetThroughPoint`, which stays associative to a real parametric point (a
    vertex/sketch/construction point, or a circular edge's centre via a construction point) --
    so the plane keeps following that point if it moves later. A straight/general edge's
    midpoint or a face's centroid has no such parametric point primitive in the Fusion API, so
    those instead get a fixed numeric offset computed from the already-resolved world point:
    still correct at build time, just not associative. `setByOffset`/`setByOffsetThroughPoint`
    both accept a planar BRepFace, ConstructionPlane, or Plane for `base_plane` (confirmed via
    apiDocumentation), so a construction plane works here exactly like Face 2 does.
    """
    plane_input = workingComp.constructionPlanes.createInput()
    plane_point = _plane_offset_point(workingComp, entity)
    if plane_point is not None:
        plane_input.setByOffsetThroughPoint(base_plane, plane_point)
    else:
        base_geom_plane = adsk.core.Plane.cast(base_plane.geometry)
        # setByOffset measures along a face's *true* outward normal, so correct the raw one
        # for isParamReversed -- otherwise a reversed Face 2 puts the plane on its far side.
        base_normal = (_true_face_normal(base_plane)
                       if base_plane.objectType == adsk.fusion.BRepFace.classType()
                       else base_geom_plane.normal)
        offset_dist = base_geom_plane.origin.vectorTo(world_point).dotProduct(base_normal)
        return _offset_plane(workingComp, base_plane, offset_dist)
    plane = workingComp.constructionPlanes.add(plane_input)
    # Pure build scaffolding -- hide it (and the helper point) so it doesn't clutter the view.
    for helper in (plane, plane_point):
        if helper != entity:
            _hide(helper)
    return plane


def _offset_plane(workingComp: adsk.fusion.Component, base_plane, dist_cm: float):
    """A hidden construction plane `dist_cm` from `base_plane`, along its true outward normal."""
    plane_input = workingComp.constructionPlanes.createInput()
    plane_input.setByOffset(base_plane, adsk.core.ValueInput.createByReal(dist_cm))
    plane = workingComp.constructionPlanes.add(plane_input)
    _hide(plane)
    return plane


def _hide(entity):
    try:
        entity.isLightBulbOn = False
    except Exception:
        pass


def _ref_axis(entity):
    """Return the unit world-space axis a Reference Point entity implies, or None.

    A circular edge carries its hole's axis in its own circle normal. Raw B-Rep reads like
    this compose correctly through the *full* proxy chain, unlike `JointGeometry`'s computed
    frame, which reports an entity's local component-native axis once it sits 2+ occurrence
    levels deep (see LESSONS_LEARNED.md). A plain vertex/sketch point/construction point
    carries no axis at all, hence None.
    """
    if entity.objectType != adsk.fusion.BRepEdge.classType():
        return None
    geom = entity.geometry
    # The dialog's Edges filter admits straight edges too, which carry no axis -- only a
    # circle/arc/ellipse does.
    for curve_cls in (adsk.core.Circle3D, adsk.core.Arc3D, adsk.core.Ellipse3D):
        curve = curve_cls.cast(geom)
        if curve is not None:
            return curve.normal
    return None


def _axis_plane_face(entity, axis: adsk.core.Vector3D):
    """Return the planar face the circular edge bounds whose plane is perpendicular to
    `axis` -- the plate or bearing face the hole is drilled through -- or None.

    That face is what makes a coaxial shaft possible at all: `ConstructionPlaneInput` has no
    parametric "plane through a point normal to an axis" constructor (`setByPlane` is
    direct-modeling only, see LESSONS_LEARNED.md), so the hole's own face stands in for one.
    """
    if axis is None:
        return None
    for face in entity.faces:
        plane = adsk.core.Plane.cast(face.geometry)
        # Both vectors are unit, so |dot| ~ 1 means parallel. The test doubles as a reject
        # for the slanted neighbour of a chamfered or filleted rim.
        if plane is not None and abs(plane.normal.dotProduct(axis)) > 1 - 1e-6:
            return face
    return None


def _joint_geometry_from_entity(entity) -> adsk.fusion.JointGeometry:
    """Build a JointGeometry from a picked entity the way Fusion's own Joint command would
    snap to it by default -- a circle/arc/ellipse edge keys off its centre, a straight/general
    edge off its midpoint, a planar face off its centroid, a spherical/toroidal face off its
    centre, and a cylindrical/conical face off its midpoint -- matching what a user would get
    clicking that same entity in the native Joint dialog without cycling through alternate
    keypoints."""
    if entity.objectType == adsk.fusion.BRepFace.classType():
        geom = entity.geometry
        if adsk.core.Plane.cast(geom) is not None:
            return adsk.fusion.JointGeometry.createByPlanarFace(
                entity, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
        is_centered = (adsk.core.Sphere.cast(geom) is not None
                       or adsk.core.Torus.cast(geom) is not None)
        keypoint = (adsk.fusion.JointKeyPointTypes.CenterKeyPoint if is_centered
                    else adsk.fusion.JointKeyPointTypes.MiddleKeyPoint)
        return adsk.fusion.JointGeometry.createByNonPlanarFace(entity, keypoint)
    if entity.objectType == adsk.fusion.BRepEdge.classType():
        is_curved = any(
            curve_cls.cast(entity.geometry) is not None
            for curve_cls in (adsk.core.Circle3D, adsk.core.Arc3D, adsk.core.Ellipse3D)
        )
        keypoint = (adsk.fusion.JointKeyPointTypes.CenterKeyPoint if is_curved
                    else adsk.fusion.JointKeyPointTypes.MiddleKeyPoint)
        return adsk.fusion.JointGeometry.createByCurve(entity, keypoint)
    return adsk.fusion.JointGeometry.createByPoint(entity)


def _true_face_normal(face: adsk.fusion.BRepFace) -> adsk.core.Vector3D:
    """Return a planar face's actual outward normal, correcting for `isParamReversed` --
    `Plane.cast(face.geometry).normal` alone can silently point the wrong way. Confirmed live:
    a simple block's top and bottom faces both reported the identical raw normal until this
    correction was applied; corrected, they came out opposite as expected."""
    normal = adsk.core.Plane.cast(face.geometry).normal.copy()
    if face.isParamReversed:
        normal.scaleBy(-1)
    return normal


def _natural_joint_axis(entity):
    """Return the reliable, physically-true axis a JointGeometry built from `entity` uses as
    its third axis, or None when it can't be pinned down with confidence (a bare point, a
    straight/general edge, or a non-planar face).

    Used to work out, before creating the joint, which raw `isFlipped` value reproduces the
    shaft's already-built position -- confirmed live against both a planar-face-vs-planar-face
    pair and a planar-face-vs-circular-edge pair (the real PartsGen case, a shaft's end face
    against a bearing bore): `isFlipped=False` reproduces the built position exactly (an
    identity transform, verified to floating-point precision) when the two axes from this
    function point the SAME way, and needs `isFlipped=True` when they're opposed. That means
    the Flip checkbox can mean exactly "flip away from what was just built" instead of an
    arbitrary, geometry-dependent coin flip.
    """
    if entity.objectType == adsk.fusion.BRepFace.classType():
        if adsk.core.Plane.cast(entity.geometry) is not None:
            return _true_face_normal(entity)
        return None
    if entity.objectType == adsk.fusion.BRepEdge.classType():
        geom = entity.geometry
        for curve_cls in (adsk.core.Circle3D, adsk.core.Arc3D, adsk.core.Ellipse3D):
            curve = curve_cls.cast(geom)
            if curve is not None:
                return curve.normal
        return None
    return None


def _extrude_one_side(comp: adsk.fusion.Component,
                      profile: adsk.fusion.Profile,
                      operation: adsk.fusion.FeatureOperations,
                      face2_or_none,
                      custom_len_expr,
                      ext_dir: adsk.fusion.ExtentDirections,
                      start_offset_cm: float = 0.0) -> adsk.fusion.ExtrudeFeature:
    """Extrude `profile` either to face2 (parametric) or by a fixed length. A non-zero
    `start_offset_cm` starts it that far from the sketch plane along the sketch's own z
    (used to reach back past a bearing seat -- see `_bearing_seat_depth`)."""
    extrudes = comp.features.extrudeFeatures
    extInput = extrudes.createInput(profile, operation)
    if start_offset_cm:
        extInput.startExtent = adsk.fusion.OffsetStartDefinition.create(
            adsk.core.ValueInput.createByReal(start_offset_cm))
    if face2_or_none is not None:
        extent = adsk.fusion.ToEntityExtentDefinition.create(face2_or_none, False)
    else:
        extent = adsk.fusion.DistanceExtentDefinition.create(
            adsk.core.ValueInput.createByString(custom_len_expr)
        )
    extInput.setOneSideExtent(extent, ext_dir)
    return extrudes.add(extInput)


def _shaft_side(workingOcc: adsk.fusion.Occurrence, entity, world: bool):
    """Which side of the picked hole the shaft body lies on: the sign of (body centre -
    hole centre) . hole normal, or None when `entity` isn't a circular edge.

    `world=False` reads the hole raw, in whatever frame the shaft was just built in (inside
    the dialog that can be the hole's component-native frame -- see `_world_xform` -- and the
    shaft's sketch shares it), with the body where it was built; `world=True` reads both in
    true world space. A joint that kept the shaft as built gives the same sign for both."""
    if world:
        center, normal = _world_circle(entity)
    else:
        center = normal = None
        curve = (adsk.core.Circle3D.cast(entity.geometry)
                 if entity.objectType == adsk.fusion.BRepEdge.classType() else None)
        if curve is not None:
            center, normal = curve.center, curve.normal
    body = workingOcc.component.bRepBodies.item(0) if workingOcc.component.bRepBodies.count else None
    if center is None or body is None:
        return None
    box = body.boundingBox
    mid = adsk.core.Point3D.create((box.minPoint.x + box.maxPoint.x) / 2,
                                   (box.minPoint.y + box.maxPoint.y) / 2,
                                   (box.minPoint.z + box.maxPoint.z) / 2)
    mid.transformBy(workingOcc.transform2)
    return center.vectorTo(mid).dotProduct(normal) > 0


def _is_unrotated(occ: adsk.fusion.Occurrence) -> bool:
    """True if `occ`'s transform has no rotation (with a tolerance -- a solved joint leaves
    ~1e-16 of float dust, so Matrix3D.isEqualTo's exact compare is useless here)."""
    m = occ.transform2
    err = sum(abs(m.getCell(i, j) - (1.0 if i == j else 0.0))
              for i in range(3) for j in range(3))
    return err < 1e-6


def _hide_joint(joint: adsk.fusion.Joint):
    """Turn a generated joint's browser eye off so its glyph doesn't clutter the canvas.
    Purely cosmetic, so a failure is logged and otherwise ignored."""
    try:
        joint.isLightBulbOn = False
    except Exception:
        futil.log(f'PartsGen: could not hide joint {joint.name}')


def _create_reference_joint(workingOcc: adsk.fusion.Occurrence,
                            ref_face: adsk.fusion.BRepFace,
                            other_entity,
                            joint_type: str,
                            flip: bool = False,
                            shaft_profile: adsk.fusion.Profile = None,
                            profile_axis: adsk.core.Vector3D = None):
    """Regular Joint between the shaft and the part its Reference Point belongs to.

    `shaft_profile` is given when the shaft starts behind its sketch plane (extended past a
    bearing seat), so its start face no longer sits on the Reference Point: the joint then
    keys off the sketch profile, which still does. A profile's joint axis is its sketch's
    normal (`profile_axis`), not the start face's outward normal -- using the face's gave
    the flipped result live. As a safety net the result is checked afterwards; a joint's
    flip can't be edited straight after creation ("Cannot be edited before rolling back"),
    so a wrong one is deleted, the shaft put back, and the joint re-added flipped.

    A **regular** joint, by design -- `rootComp.joints.createInput()` is, per Fusion's own
    docs, "the API equivalent to the Joint command dialog", which is exactly the behaviour
    asked for: pick a joint point the same way the Joint tool lets you, and get a real,
    editable Joint out of it. That means accepting the same tradeoff the native tool has --
    `Joints.add()` repositions/rotates the shaft's occurrence to make the two joint frames
    coincide -- rather than fighting it the way the previous `AsBuiltJoint`-based
    implementation deliberately did (see LESSONS_LEARNED.md for that history).

    The dialog's Flip checkbox does not pick a "recommended" orientation -- the preview already
    built the shaft correctly, so the joint must not silently redo that decision. `Joints.add()`
    picks which of the two valid alignments to use from the raw, arbitrary sign of each side's
    own axis convention, which has nothing to do with which way the shaft was actually built --
    so left alone, `isFlipped=False` reproduces the preview only by coincidence. `flip=False`
    here is defined instead to mean "leave the shaft exactly as built": `_natural_joint_axis`
    reads a reliable, physically-true axis for each side (independent of `JointGeometry`'s own
    axis, which is unreliable 2+ occurrence levels deep -- see LESSONS_LEARNED.md), and the two
    are compared directly to work out which raw `isFlipped` value is a no-op against the
    already-built position. Confirmed live to floating-point precision. `flip=True` then means
    exactly "the other, deliberately flipped orientation" -- never an unexplained coin flip.

    `pulley_gen.py`/`sprocket_gen.py` already use this same `Joints.add()` pattern for a
    different reason (their target is always a freshly-drawn sketch circle); this makes the
    shaft's joint consistent with them.
    """
    design   = adsk.fusion.Design.cast(app.activeProduct)
    rootComp = design.rootComponent

    shaft_face = ref_face.createForAssemblyContext(workingOcc)
    if shaft_profile is not None:
        shaft_geom = adsk.fusion.JointGeometry.createByProfile(
            shaft_profile.createForAssemblyContext(workingOcc), None,
            adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
    else:
        shaft_geom = adsk.fusion.JointGeometry.createByPlanarFace(
            shaft_face, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
    other_geom = _joint_geometry_from_entity(other_entity)

    other_axis = _natural_joint_axis(other_entity)
    if other_axis is not None:
        shaft_axis = (profile_axis if shaft_profile is not None and profile_axis is not None
                      else _true_face_normal(shaft_face))
        preserve_flip = shaft_axis.dotProduct(other_axis) < 0
        actual_flip = preserve_flip != flip
    else:
        # No reliable axis to compare against (a bare point, a straight edge, a non-planar
        # face) -- nothing to correct, so the checkbox is passed through as-is.
        actual_flip = flip

    def _add(is_flipped):
        joint_input = rootComp.joints.createInput(shaft_geom, other_geom)
        if joint_input is None:
            return None
        joint_input.isFlipped = is_flipped
        if joint_type == JOINT_RIGID:
            joint_input.setAsRigidJointMotion()
        else:
            joint_input.setAsRevoluteJointMotion(
                adsk.fusion.JointDirections.ZAxisJointDirection)
        # Documented to return null on failure rather than raising, so check it.
        return rootComp.joints.add(joint_input)

    def _came_out_wrong():
        """Unflipped should keep the shaft on the side of the hole it was built on;
        flipped should put it on the other side."""
        if side_built is not None:
            side_now = _shaft_side(workingOcc, other_entity, world=True)
            if side_now is not None:
                return (side_now == side_built) == flip
        # "Unrotated" only means "as built" when the build frame was world space -- inside
        # the dialog the shaft can be built in the hole's native frame, and the correct
        # joint then rotates it (this fallback once flipped a correct shaft backwards).
        return _is_unrotated(workingOcc) == flip

    try:
        side_built = _shaft_side(workingOcc, other_entity, world=False)
        joint = _add(actual_flip)
        if joint is not None:
            if shaft_profile is not None and _came_out_wrong():
                futil.log('PartsGen: profile joint came out the wrong way; re-adding it flipped')
                joint.deleteMe()
                # deleteMe doesn't restore the occurrence (see LESSONS_LEARNED.md).
                workingOcc.transform2 = adsk.core.Matrix3D.create()
                actual_flip = not actual_flip
                joint = _add(actual_flip)
        if joint is not None:
            joint.name = f'{workingOcc.component.name}_joint'
            _hide_joint(joint)
            futil.log(f'PartsGen: joint for {workingOcc.component.name} '
                      f'({joint_type}, flip={flip}, isFlipped={actual_flip})')
            return joint
    except Exception:
        futil.handle_error(f'PartsGen: joint for {workingOcc.component.name}')

    futil.popup_error(
        f'Parts Gen: could not create a joint for {workingOcc.component.name}.\n\n'
        'The shaft itself is correct -- add the joint by hand if you need one.')
    return None


# ===========================================================================
# Bearings
# ===========================================================================

def _occurrence_bodies_recursive(occ: adsk.fusion.Occurrence):
    """Yield every body under `occ`, read through `Occurrence.bRepBodies` at each level --
    the access path proven to report correct world geometry 2+ occurrence levels deep,
    unlike walking `edge.faces` off a proxy (see LESSONS_LEARNED.md)."""
    for body in occ.bRepBodies:
        yield body
    for child in occ.childOccurrences:
        yield from _occurrence_bodies_recursive(child)


def _native(entity):
    """The native (component-space) object behind a proxy, or the entity itself."""
    native = getattr(entity, 'nativeObject', None)
    return native if native is not None else entity


def _world_xform(entity) -> adsk.core.Matrix3D:
    """Native-to-world transform for a (proxy) B-Rep entity: its assembly context's
    `transform2`, which is the full composed world transform even 2+ occurrence levels deep
    (confirmed live to 0.0 error). Inside the live Parts Gen dialog a proxy's own
    `.geometry` can come back in its component-native frame instead of world (confirmed
    live: the picked hole's centre came back in Hood_Plates' native frame), so anything
    that must be world space is rebuilt as native geometry through this transform."""
    occ = getattr(entity, 'assemblyContext', None)
    return occ.transform2 if occ is not None else adsk.core.Matrix3D.create()


def _world_circle(entity):
    """(world centre, world unit normal) of a circular/arc edge, or (None, None)."""
    if entity is None or entity.objectType != adsk.fusion.BRepEdge.classType():
        return None, None
    geom = _native(entity).geometry
    for curve_cls in (adsk.core.Circle3D, adsk.core.Arc3D):
        curve = curve_cls.cast(geom)
        if curve is not None:
            xf = _world_xform(entity)
            center, normal = curve.center.copy(), curve.normal.copy()
            center.transformBy(xf)
            normal.transformBy(xf)
            normal.normalize()
            return center, normal
    return None, None


def _world_plane(face: adsk.fusion.BRepFace):
    """(world origin, world unit normal) of a planar face's plane (normal sign arbitrary)."""
    plane = adsk.core.Plane.cast(_native(face).geometry)
    xf = _world_xform(face)
    origin, normal = plane.origin.copy(), plane.normal.copy()
    origin.transformBy(xf)
    normal.transformBy(xf)
    normal.normalize()
    return origin, normal


def _world_face_normal(face: adsk.fusion.BRepFace) -> adsk.core.Vector3D:
    """A planar face's true outward normal (see `_true_face_normal`) in world space."""
    normal = _true_face_normal(_native(face))
    normal.transformBy(_world_xform(face))
    normal.normalize()
    return normal


def _bearing_seat_depth(entity, pick_pt: adsk.core.Point3D, outward: adsk.core.Vector3D):
    """How far past `pick_pt`, along `outward` (unit, pointing away from the shaft's other
    end), the outside face of the picked part is -- where a bearing's flange should sit.

    The shaft's hole is found as the body's circular edges coaxial with the shaft. If the
    material around the hole continues outward from the pick (the user picked the plate's
    inner face), the seat is the nearest hole rim beyond it -- the same wall's outer face,
    never a far wall of a tube. Otherwise the pick already is the outside face: 0.
    Returns None when the part has no hole on the shaft's axis (nothing to seat into).

    `pick_pt`/`outward` are world space. The search itself runs on the *native* body, with
    them mapped into its frame through `_world_xform` -- a proxy body's own edge reads can't
    be trusted to be world space inside the live dialog (see `_world_xform`)."""
    native = _native(entity)
    body = getattr(native, 'body', None)
    if body is None:
        return None
    to_native = _world_xform(entity).copy()
    to_native.invert()
    pick_pt = pick_pt.copy()
    pick_pt.transformBy(to_native)
    outward = outward.copy()
    outward.transformBy(to_native)
    outward.normalize()
    rims = []
    for edge in body.edges:
        circle = adsk.core.Circle3D.cast(edge.geometry)
        if circle is None or abs(circle.normal.dotProduct(outward)) < 1 - 1e-4:
            continue
        v = pick_pt.vectorTo(circle.center)
        t = v.dotProduct(outward)
        along = outward.copy()
        along.scaleBy(t)
        v.subtract(along)
        if v.length < 1e-3:
            rims.append((t, circle.radius))

    at_pick = [r for t, r in rims if abs(t) < 1e-4]
    if not at_pick:
        return None

    # Probe just outside the hole wall, a hair outward from the pick.
    perp = outward.crossProduct(adsk.core.Vector3D.create(1, 0, 0))
    if perp.length < 1e-6:
        perp = outward.crossProduct(adsk.core.Vector3D.create(0, 1, 0))
    perp.normalize()
    probe_r = min(at_pick) + 0.01
    probe = adsk.core.Point3D.create(
        pick_pt.x + outward.x * 0.01 + perp.x * probe_r,
        pick_pt.y + outward.y * 0.01 + perp.y * probe_r,
        pick_pt.z + outward.z * 0.01 + perp.z * probe_r)
    if body.pointContainment(probe) != adsk.fusion.PointContainment.PointInsidePointContainment:
        return 0.0
    beyond = [t for t, _ in rims if t > 1e-4]
    return min(beyond) if beyond else 0.0


def _circle_radii(face: adsk.fusion.BRepFace):
    radii = []
    for edge in face.edges:
        circle = adsk.core.Circle3D.cast(edge.geometry)
        if circle is not None:
            radii.append(circle.radius)
    return radii


def _find_bearing_face(bearing_occ: adsk.fusion.Occurrence):
    """Return the bearing/bushing face that gets jointed flush with the shaft's end, as a
    proxy in the root context, or None if the part has no recognisable flange.

    Found by geometry rather than hard-coded radii, face indices or tokens, so it works for
    every part in BEARING_PARTS and survives edits to their files:
      1. the flange is the part's largest circle; the planar faces carrying it are the
         flange's seat and outer face, and the outer one is at the part's axial extreme;
      2. the target is the smallest annulus coplanar with that outer face -- a bearing's
         flange-side inner-race face (the race spins with the shaft), or for a bushing the
         flange face itself (nothing else is coplanar with it).
    """
    planar = []
    for body in _occurrence_bodies_recursive(bearing_occ):
        for face in body.faces:
            plane = adsk.core.Plane.cast(face.geometry)
            if plane is not None:
                planar.append((face, plane, _circle_radii(face)))
    all_radii = [r for _, _, radii in planar for r in radii]
    if not all_radii:
        return None
    flange_r = max(all_radii)
    flange_planes = [p for _, p, radii in planar
                     if any(abs(r - flange_r) < BEARING_RADIUS_TOL_CM for r in radii)]
    if not flange_planes:
        return None

    # Axial position of every face square to the axis, measured along the flange's normal.
    axis   = flange_planes[0].normal
    origin = flange_planes[0].origin
    def _along(p: adsk.core.Plane) -> float:
        return origin.vectorTo(p.origin).dotProduct(axis)
    square = [(f, p) for f, p, _ in planar if abs(p.normal.dotProduct(axis)) > 1 - 1e-6]
    lo = min(_along(p) for _, p in square)
    hi = max(_along(p) for _, p in square)
    outer = [fp for fp in flange_planes
             if abs(_along(fp) - lo) < 1e-4 or abs(_along(fp) - hi) < 1e-4]
    if not outer:
        return None
    outer_pos = _along(outer[0])

    coplanar = [f for f, p in square
                if abs(_along(p) - outer_pos) < 1e-4 and f.loops.count >= 2]
    if not coplanar:
        return None
    return min(coplanar, key=lambda f: f.area)


def _face_circle_center(face: adsk.fusion.BRepFace) -> adsk.core.Point3D:
    for edge in face.edges:
        circle = adsk.core.Circle3D.cast(edge.geometry)
        if circle is not None:
            return circle.center
    return face.centroid


def _insert_bearing(parentOcc: adsk.fusion.Occurrence, part: dict,
                    missing_note: str = 'The shaft was built without bearings.'):
    """Insert a library part (a BEARING_PARTS entry, or anything with 'urn' and 'file'
    keys, e.g. pulley_gen's ADAPTER_PARTS) at the world origin, linked when possible. A
    linked insert fails when the part file is in a different project than this document,
    so fall back to an embedded copy rather than skipping the part. Goes inside `parentOcc`
    (the part's group, placed at identity) when given -- returned as a root-context proxy
    so joints and faces work exactly as for a root-level insert -- else into the root."""
    data_file = app.data.findFileById(part['urn'])
    if data_file is None:
        futil.popup_error(
            f'Parts Gen: could not find the {part["file"]} file '
            f'(Argos CAD > Parts_1 > Parts_Gen > {part["file"]}).\n\n'
            f'{missing_note}')
        return None
    design = adsk.fusion.Design.cast(app.activeProduct)
    occs = (parentOcc.component.occurrences if parentOcc is not None
            else design.rootComponent.occurrences)
    identity = adsk.core.Matrix3D.create()
    occ = None
    try:
        occ = occs.addByInsert(data_file, identity, True)
    except Exception:
        pass
    if occ is None:
        futil.log('PartsGen: linked bearing insert failed; inserting an embedded copy instead')
        occ = occs.addByInsert(data_file, identity, False)
    if occ is not None and parentOcc is not None:
        occ = occ.createForAssemblyContext(parentOcc)
    return occ


def _add_bearing(workingOcc: adsk.fusion.Occurrence,
                 shaft_end_face: adsk.fusion.BRepFace,
                 end_label: str,
                 shaft_uid: str,
                 part: dict,
                 groupOcc: adsk.fusion.Occurrence = None):
    """Insert a BEARING_PARTS part on one end of the shaft and rigidly joint it there,
    matching how Team 1756 mounts them by hand: the face `_find_bearing_face` picks (a
    bearing's flange-side inner-race face, a bushing's flange face) flush with the shaft's
    end face, facing the same way, so the flange sits at the end and the part's body runs
    back along the shaft.

    The bearing is placed there *before* the joint (rotate its face normal onto the shaft
    end's, then translate its centre onto the end's centre), and the joint then uses the
    same "no-op flip" rule as `_create_reference_joint`, so `Joints.add()` has nothing left
    to move -- it only records the relationship. The bore is round, so the bearing's
    clocking about the shaft axis doesn't matter.
    """
    design   = adsk.fusion.Design.cast(app.activeProduct)
    rootComp = design.rootComponent
    shaft_name = workingOcc.component.name

    bearing_occ = _insert_bearing(groupOcc, part)
    if bearing_occ is None:
        return None
    try:
        bearing_face = _find_bearing_face(bearing_occ)
        if bearing_face is None:
            raise RuntimeError(f'{part["file"]} flange face not found')

        shaft_face = shaft_end_face.createForAssemblyContext(workingOcc)
        target_pt  = shaft_face.centroid
        target_n   = _true_face_normal(shaft_face)
        source_pt  = _face_circle_center(bearing_face)
        source_n   = _true_face_normal(bearing_face)

        xform = adsk.core.Matrix3D.create()
        if source_n.dotProduct(target_n) < -1 + 1e-9:
            # Exactly opposed -- the rotation axis is ambiguous, so give it one.
            perp = source_n.crossProduct(adsk.core.Vector3D.create(1, 0, 0))
            if perp.length < 1e-6:
                perp = source_n.crossProduct(adsk.core.Vector3D.create(0, 1, 0))
            xform.setToRotateTo(source_n, target_n, perp)
        else:
            xform.setToRotateTo(source_n, target_n)
        moved = source_pt.copy()
        moved.transformBy(xform)
        xform.translation = moved.vectorTo(target_pt)
        bearing_occ.transform2 = xform

        bearing_occ.attributes.add(ATTR_GROUP, ATTR_BEARING_OWNER, shaft_uid)

        # Re-find the face after the move rather than trusting a proxy taken before it.
        bearing_face = _find_bearing_face(bearing_occ)
        joint_input = rootComp.joints.createInput(
            adsk.fusion.JointGeometry.createByPlanarFace(
                bearing_face, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint),
            adsk.fusion.JointGeometry.createByPlanarFace(
                shaft_face, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint))
        joint_input.isFlipped = _true_face_normal(bearing_face).dotProduct(target_n) < 0
        joint_input.setAsRigidJointMotion()
        joint = rootComp.joints.add(joint_input)
        if joint is None:
            raise RuntimeError('Joints.add returned null')
        joint.name = f'{shaft_name}_bearing_{end_label}'
        _hide_joint(joint)
        futil.log(f'PartsGen: bearing added to {shaft_name} ({end_label} end)')
        return bearing_occ
    except Exception:
        futil.handle_error(f'PartsGen: bearing for {shaft_name} ({end_label} end)')
        futil.popup_error(
            f'Parts Gen: could not add the {end_label}-end bearing to {shaft_name}.\n\n'
            'The shaft itself is correct -- add the bearing by hand if you need one.')
        try:
            bearing_occ.deleteMe()
        except Exception:
            pass
        return None


def _add_bearings(workingOcc: adsk.fusion.Occurrence,
                  outer_feat: adsk.fusion.ExtrudeFeature,
                  part: dict,
                  shaft_uid: str,
                  groupOcc: adsk.fusion.Occurrence = None):
    """Add a BEARING_PARTS part to both ends of the shaft. The reference end is the
    extrude's start face, the Face 2 end its far face, in either length mode."""
    start_face = outer_feat.startFaces.item(0) if outer_feat.startFaces.count > 0 else None
    end_face   = outer_feat.endFaces.item(0)   if outer_feat.endFaces.count   > 0 else None
    if start_face is None:
        futil.log('PartsGen: shaft has no start face; skipping bearings')
        return
    shaft_axis = _true_face_normal(start_face)

    ends = [('ref', start_face)]
    if end_face is None:
        futil.log('PartsGen: shaft has no end face; skipping the Face 2 end bearing')
    elif (adsk.core.Plane.cast(end_face.geometry) is None
            or abs(_true_face_normal(end_face).dotProduct(shaft_axis)) < 1 - 1e-6):
        # Face 2 tilted relative to the hole's axis leaves a slanted end -- no flat,
        # square seat for a flange, so don't pretend there is one.
        futil.popup_error(
            'Parts Gen: the Face 2 end of the shaft is not square to the shaft, '
            'so no bearing was added there.')
    else:
        ends.append(('face2', end_face))

    for label, face in ends:
        _add_bearing(workingOcc, face, label, shaft_uid, part, groupOcc)


def shaft_outer_occurrence(shaft_occ: adsk.fusion.Occurrence) -> adsk.fusion.Occurrence:
    """The occurrence right-click Edit should delete for this shaft: its "<shaft>_Group"
    (which takes the bearings with it) when it was built inside one, else the shaft itself."""
    try:
        parent = shaft_occ.assemblyContext
        uid_attr = shaft_occ.component.attributes.itemByName(ATTR_GROUP, ATTR_SHAFT_UID)
        if parent is not None and uid_attr is not None:
            grp_attr = parent.component.attributes.itemByName(ATTR_GROUP, ATTR_SHAFT_GROUP)
            if grp_attr is not None and grp_attr.value == uid_attr.value:
                return parent
    except Exception:
        pass
    return shaft_occ


def delete_shaft_bearings(shaft_comp: adsk.fusion.Component):
    """Delete the bearing occurrences `_add_bearings` created for this shaft (right-click
    Edit rebuilds the shaft from scratch, and would otherwise leave them behind)."""
    uid_attr = shaft_comp.attributes.itemByName(ATTR_GROUP, ATTR_SHAFT_UID)
    if uid_attr is None:
        return
    design = adsk.fusion.Design.cast(app.activeProduct)
    for occ in list(design.rootComponent.occurrences):
        attr = occ.attributes.itemByName(ATTR_GROUP, ATTR_BEARING_OWNER)
        if attr is not None and attr.value == uid_attr.value:
            try:
                occ.deleteMe()
            except Exception:
                futil.log(f'PartsGen: could not delete old bearing {occ.name}')


# ===========================================================================
# Shaft creation
# ===========================================================================

def _create_shaft(inputs: adsk.core.CommandInputs, constrain: bool = True):
    shaftTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('shaft_type')
    customOD:       adsk.core.ValueCommandInput     = inputs.itemById('custom_od')
    customID:       adsk.core.ValueCommandInput     = inputs.itemById('custom_id')
    lenTypeInp:     adsk.core.DropDownCommandInput  = inputs.itemById('length_type')
    refPointSel:    adsk.core.SelectionCommandInput = inputs.itemById('ref_point_selection')
    face2Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face2_selection')
    customLenInp:   adsk.core.ValueCommandInput     = inputs.itemById('custom_length')
    createJointInp: adsk.core.BoolValueCommandInput = inputs.itemById('create_joint')
    flipJointInp:   adsk.core.BoolValueCommandInput = inputs.itemById('flip_joint')
    reverseDirInp:  adsk.core.BoolValueCommandInput = inputs.itemById('reverse_direction')

    shaft_type = shaftTypeInp.selectedItem.name
    len_type   = lenTypeInp.selectedItem.name

    # Read the Reference Point entity now, before this function starts adding
    # sketches/extrudes/cuts to the timeline. A SelectionCommandInput's live selection
    # on a pre-existing part can otherwise go stale partway through, silently dropping
    # to 0 selections with no error once other document changes happen -- this bit the
    # joint entirely, with no message, before the read was moved this early. It doubles
    # as the joint's target when "Create Joint" is checked -- one selection, two roles.
    ref_point_entity = _selected_entity(refPointSel)
    face2_entity      = _selected_entity(face2Sel)

    # Defensive guard — command_validate_input already blocks these values from the
    # dialog's OK button, but executePreview can call this function with a transient
    # or momentarily-invalid value while the user is still typing.
    if shaft_type == SHAFT_CUSTOM:
        od_check = customOD.value
        id_check = customID.value
        if od_check <= 0 or id_check <= 0 or id_check >= od_check:
            futil.log('PartsGen _create_shaft: invalid OD/ID, skipping')
            return
    elif shaft_type in (SHAFT_HALF_HEX_SPACER, SHAFT_THREE_EIGHTH_SPACER):
        od_check = customOD.value
        _, bore_round_dia_check = _hex_spacer_bore_dims_cm(shaft_type)
        if od_check <= 0 or od_check <= bore_round_dia_check:
            futil.log('PartsGen _create_shaft: OD too small for the hex spacer bore, skipping')
            return

    # Between-Two-Faces mode needs both picks made -- executePreview fires on every
    # selection change, including the moment right after Reference Point is picked but
    # before Face 2 is, so this must not assume both are already there.
    if len_type == LEN_FACES:
        if ref_point_entity is None or face2_entity is None:
            futil.log('PartsGen _create_shaft: reference point/face2 not yet selected, skipping')
            return

    # --- Bearing seats ------------------------------------------------------
    # The "Add Bearing" pick goes on both ends; it's ignored unless it fits this shaft.
    bearingEndsInp = inputs.itemById('bearing_ends')
    bearing_name = bearing_part_name(
        bearingEndsInp.selectedItem.name
        if bearingEndsInp is not None and bearingEndsInp.selectedItem is not None
        else BEARING_NONE)
    bearing_part = BEARING_PARTS.get(bearing_name)
    if bearing_part is not None and bearing_part['shaft'] != shaft_type:
        bearing_name, bearing_part = BEARING_NONE, None
    shaft_uid = uuid.uuid4().hex

    design       = adsk.fusion.Design.cast(app.activeProduct)
    rootComp     = design.rootComponent
    start_marker = design.timeline.markerPosition
    trans        = adsk.core.Matrix3D.create()
    # With bearings, the shaft and its bearings are built inside one "<shaft>_Group"
    # component so they sit together in the browser. They have to be created there from
    # the start: moving them in afterwards (Occurrence.moveToComponent) was confirmed live
    # to drop the joint-solved positions and orphan the bearing joints.
    groupOcc = None
    try:
        if constrain and bearing_part is not None:
            groupOcc = rootComp.occurrences.addNewComponent(trans)
            groupOcc.component.attributes.add(ATTR_GROUP, ATTR_SHAFT_GROUP, shaft_uid)
            workingOcc = groupOcc.component.occurrences.addNewComponent(
                trans).createForAssemblyContext(groupOcc)
        else:
            workingOcc = rootComp.occurrences.addNewComponent(trans)
    except RuntimeError:
        futil.popup_error(
            'Cannot create shaft: this document is in Part Design mode, '
            'which only supports a single component.\n\n'
            'Please open or create an Assembly document and try again.'
        )
        return
    workingComp  = workingOcc.component
    outerOcc     = groupOcc if groupOcc is not None else workingOcc   # what to delete on failure

    try:
        if len_type == LEN_FACES:
            face2: adsk.fusion.BRepFace = face2_entity
            centroid1       = _point_from_entity(ref_point_entity)
            centroid2       = _bbox_center(face2)

            # Sketch straight onto the face the picked hole is drilled through, so the shaft
            # comes out coaxial with that hole -- a shaft through a bearing should follow the
            # bearing's axis, not whatever angle Face 2 happens to sit at. The Reference Point
            # is the hole's centre and so lies exactly on this face, which is why no offset
            # plane is needed (and a zero-distance setByOffsetThroughPoint isn't documented to
            # be legal anyway). Fusion has no parametric "plane through a point normal to an
            # axis", so the hole's own face is the only way to get this orientation.
            ref_axis  = _ref_axis(ref_point_entity)
            axis_face = _axis_plane_face(ref_point_entity, ref_axis)
            if axis_face is not None:
                sketch_plane = axis_face
                futil.log('PartsGen: shaft built coaxial with the reference hole')
            else:
                # Nothing to take an axis from -- a plain vertex/sketch/construction point, a
                # general edge or face, or a rim with no perpendicular planar neighbour. Fall
                # back to the original behaviour: a plane through the Reference Point, parallel
                # to Face 2.
                sketch_plane = _offset_plane_parallel_to(
                    workingComp, face2, ref_point_entity, centroid1)
                futil.log('PartsGen: no hole axis available; shaft built parallel to Face 2')

            face2_target    = face2
            custom_len_expr = None
        else:
            face2_target    = None
            custom_len_expr = customLenInp.expression
            if ref_point_entity is not None:
                # Custom Length with a Reference Point: same placement logic as
                # Between-Two-Faces (coaxial with a picked hole when possible, or directly on
                # a picked flat face), just extruded by a fixed length instead of to Face 2 --
                # this is what lets a joint be built here too, since the joint targets the
                # Reference Point either way.
                centroid1 = _point_from_entity(ref_point_entity)
                ref_axis  = _ref_axis(ref_point_entity)
                axis_face = _axis_plane_face(ref_point_entity, ref_axis)
                if axis_face is not None:
                    sketch_plane = axis_face
                    futil.log('PartsGen: shaft (custom length) built coaxial with the reference hole')
                elif (ref_point_entity.objectType == adsk.fusion.BRepFace.classType()
                        and adsk.core.Plane.cast(ref_point_entity.geometry) is not None):
                    sketch_plane = ref_point_entity
                    futil.log('PartsGen: shaft (custom length) built normal to the reference face')
                else:
                    sketch_plane = _offset_plane_parallel_to(
                        workingComp, rootComp.xYConstructionPlane, ref_point_entity, centroid1)
                    futil.log('PartsGen: no hole axis available; shaft (custom length) '
                              'built parallel to the world XY plane')
            else:
                centroid1    = adsk.core.Point3D.create(0, 0, 0)
                sketch_plane = rootComp.xYConstructionPlane

        sketch: adsk.fusion.Sketch = workingComp.sketches.addWithoutEdges(sketch_plane)
        sketch.name = 'ShaftProfile'

        # Ask the sketch for its own normal rather than reading it off the plane or face:
        # ExtentDirections is measured against the sketch, while a BRepFace's normal flips
        # with isParamReversed, so taking it from the face would be a latent 180-degree bug.
        sk_normal = None
        if len_type == LEN_FACES:
            sk_normal = sketch.xDirection.crossProduct(sketch.yDirection)
            ext_dir   = _extrude_direction(sk_normal, centroid1, centroid2)
        else:
            # No Face 2 to derive a direction from -- the sketch plane's own normal picks a
            # side arbitrarily (whichever way the picked hole/face happens to point), so
            # "Reverse Direction" is a plain manual override, same spirit as the joint's own
            # Flip checkbox.
            ext_dir = (
                adsk.fusion.ExtentDirections.NegativeExtentDirection
                if (reverseDirInp is not None and reverseDirInp.value)
                else adsk.fusion.ExtentDirections.PositiveExtentDirection
            )

        c1_sk  = sketch.modelToSketchSpace(centroid1)
        center = adsk.core.Point3D.create(c1_sk.x, c1_sk.y, 0.0)

        # Between Two Faces: a bearing's flange belongs on the *outside* of the picked part,
        # its body in the part's hole -- whichever face of the part was picked -- and the
        # shaft runs on past the flange to end flush with the bearing's outer face (where
        # `_add_bearings` then seats it). So each bearing end is pushed out to that part's
        # outside face plus the flange thickness: the reference end by starting the extrude
        # behind the sketch plane, the Face 2 end by extruding to a plane offset past it.
        # An end whose part has no hole on the shaft axis is left as picked.
        ref_start_offset_cm = 0.0
        ref_ext_cm = face2_ext_cm = 0.0
        if len_type == LEN_FACES and bearing_part is not None:
            positive = ext_dir == adsk.fusion.ExtentDirections.PositiveExtentDirection
            # Only the extension *lengths* are needed here -- plain scalars, so they can be
            # worked out in true world space no matter which frame the sketch was built in.
            # Every read goes through `_world_*` (native geometry + the occurrence's world
            # transform): inside the live dialog, a pick 2+ occurrence levels deep can
            # report its own geometry in its component-native frame, and mixing that with
            # world-space body edges or Face 2 found no seat at all (confirmed live).
            axis_c, shaft_dir = _world_circle(ref_point_entity)
            if axis_c is None:
                # No hole axis to follow -- the shaft runs along the sketch normal.
                axis_c, shaft_dir = centroid1, sk_normal.copy()
                if not positive:
                    shaft_dir.scaleBy(-1)
            f2_org, f2_nrm = _world_plane(face2_entity)
            denom = shaft_dir.dotProduct(f2_nrm)
            t = (axis_c.vectorTo(f2_org).dotProduct(f2_nrm) / denom
                 if abs(denom) > 1e-6 else None)
            if t is not None and t < 0:
                shaft_dir.scaleBy(-1)   # a hole's normal points either way; aim at Face 2
                t = -t
            shaft_dir.normalize()
            back = shaft_dir.copy()
            back.scaleBy(-1)
            seat = _bearing_seat_depth(ref_point_entity, axis_c, back)
            if seat is not None:
                ref_ext_cm = seat + bearing_part['flange_cm']
                # OffsetStartDefinition is measured along the sketch's own z.
                ref_start_offset_cm = -ref_ext_cm if positive else ref_ext_cm
            if t is not None:
                f2_pt = axis_c.copy()
                step = shaft_dir.copy()
                step.scaleBy(t)
                f2_pt.translateBy(step)
                seat = _bearing_seat_depth(face2_entity, f2_pt, shaft_dir)
                if seat is not None:
                    face2_ext_cm = seat + bearing_part['flange_cm']
                    # setByOffset measures along Face 2's true outward normal; convert
                    # the distance along the shaft into one along that normal.
                    face2_target = _offset_plane(
                        workingComp, face2,
                        face2_ext_cm * shaft_dir.dotProduct(_world_face_normal(face2)))
            futil.log(f'PartsGen: bearing seats extend the shaft {ref_ext_cm / IN_TO_CM:.4g} in '
                      f'(ref end), {face2_ext_cm / IN_TO_CM:.4g} in (Face 2 end)')

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
        elif shaft_type == SHAFT_MAXSPLINE:
            workingComp.name = 'Shaft_MAXSpline'
            _draw_maxspline_wave(sketch, center,
                                 SHAFT_MAXSPLINE_MAJOR_DIA_CM, SHAFT_MAXSPLINE_MINOR_DIA_CM,
                                 SHAFT_MAXSPLINE_BLEND_A_RADIUS_CM, SHAFT_MAXSPLINE_BLEND_B_RADIUS_CM,
                                 constrain=constrain)
        elif shaft_type in (SHAFT_HALF_HEX_SPACER, SHAFT_THREE_EIGHTH_SPACER):
            od_cm = customOD.value
            od_in = od_cm / IN_TO_CM
            size_tag = 'HalfInchHex' if shaft_type == SHAFT_HALF_HEX_SPACER else 'ThreeEighthHex'
            workingComp.name = f'HexSpacer_{size_tag}_{od_in:.4g}in'
            _draw_circle(sketch, center, od_cm, constrain=constrain)
        else:
            od_cm = customOD.value
            od_in = od_cm / IN_TO_CM
            workingComp.name = f'Shaft_Custom_{od_in:.4g}in'
            _draw_circle(sketch, center, od_cm, constrain=constrain)

        customNameInp = inputs.itemById('custom_name')
        custom_name = customNameInp.value.strip() if customNameInp is not None else ''
        if custom_name:
            workingComp.name = custom_name
        if groupOcc is not None:
            groupOcc.component.name = f'{workingComp.name}_Group'

        if sketch.profiles.count < 1:
            futil.popup_error('Parts Gen: could not create a valid outer sketch profile.')
            outerOcc.deleteMe()
            return

        outer_profile = _largest_profile(sketch)

        # --- Extrude outer body -------------------------------------------------
        outer_feat = _extrude_one_side(
            workingComp, outer_profile,
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
            face2_target, custom_len_expr, ext_dir, ref_start_offset_cm
        )
        body = outer_feat.bodies.item(0)

        # --- Cut bore -----------------------------------------------------------
        bore_sketch: adsk.fusion.Sketch = workingComp.sketches.addWithoutEdges(sketch_plane)
        bore_sketch.name = 'BoreProfile'

        if shaft_type == SHAFT_MAXSPLINE:
            _draw_maxspline_wave(bore_sketch, center,
                                 SHAFT_MAXSPLINE_BORE_MAJOR_DIA_CM, SHAFT_MAXSPLINE_BORE_MINOR_DIA_CM,
                                 SHAFT_MAXSPLINE_BORE_BLEND_A_RADIUS_CM, SHAFT_MAXSPLINE_BORE_BLEND_B_RADIUS_CM,
                                 constrain=constrain)
        elif shaft_type in (SHAFT_HALF_HEX_SPACER, SHAFT_THREE_EIGHTH_SPACER):
            bore_flats_cm, bore_round_dia_cm = _hex_spacer_bore_dims_cm(shaft_type)
            _draw_rounded_hex(bore_sketch, center, bore_flats_cm, bore_round_dia_cm,
                              constrain=constrain)
        else:
            if shaft_type in (SHAFT_HALF_HEX, SHAFT_THREE_EIGHTH_HEX):
                bore_dia = SHAFT_BORE_DIA_CM
            else:
                bore_dia = customID.value
            _draw_circle(bore_sketch, center, bore_dia, constrain=constrain)

        if bore_sketch.profiles.count < 1:
            futil.popup_error('Parts Gen: could not create bore profile.')
            outerOcc.deleteMe()
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
        if ref_start_offset_cm:
            bore_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
                adsk.core.ValueInput.createByReal(ref_start_offset_cm))
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
            elif shaftTypeInp.selectedItem.name in (SHAFT_HALF_HEX_SPACER, SHAFT_THREE_EIGHTH_SPACER):
                comp_attrs.add(ATTR_GROUP, ATTR_CUSTOM_OD, customOD.expression)
            if len_type == LEN_FACES:
                # The shaft's centreline length. Start from the perpendicular distance from
                # the Reference Point to Face 2's plane (this used to be the straight-line
                # distance to the face's bounding-box centre, which overstates it whenever
                # Face 2 is wider than the shaft and the pick sits off its centre), then
                # divide by cos(theta) when the shaft runs along the hole's axis rather than
                # perpendicular to Face 2 -- a tilted shaft travels further than the gap.
                # The far end is a slanted ellipse in that case, so "the length" is genuinely
                # ambiguous; the centreline is the one the Custom-Length rebuild on
                # right-click Edit can actually reproduce.
                f2_plane = adsk.core.Plane.cast(face2_entity.geometry)
                f2_org   = f2_plane.origin
                f2_nrm   = f2_plane.normal
                d = abs((centroid1.x - f2_org.x) * f2_nrm.x +
                        (centroid1.y - f2_org.y) * f2_nrm.y +
                        (centroid1.z - f2_org.z) * f2_nrm.z)
                # `sk_normal` rather than the entity-derived axis: a sketch's xDirection and
                # yDirection are documented to be in model space, so their cross product is
                # unambiguously the shaft's world axis, while a proxy's `.geometry` has been
                # seen reporting component-native values while the design is mid-recompute.
                if sk_normal is not None:
                    cos_t = abs(sk_normal.dotProduct(f2_nrm))
                    if cos_t > 1e-6:
                        d /= cos_t
                    else:
                        futil.log('PartsGen: shaft axis is nearly parallel to Face 2; '
                                  'storing the perpendicular distance as its length')
                d += ref_ext_cm + face2_ext_cm   # bearing-seat extensions, if any
                comp_attrs.add(ATTR_GROUP, ATTR_LEN_EXPR, f'{d / IN_TO_CM:.6g} in')
                # Remember both picks so right-click Edit can rebuild the shaft where it
                # stands (and re-create its joint) instead of falling back to a Custom
                # Length at the world origin. `entityToken` can throw on a transient
                # entity, so a failure here must not cost the other attributes.
                try:
                    comp_attrs.add(ATTR_GROUP, ATTR_REF_POINT_TOKEN, ref_point_entity.entityToken)
                    comp_attrs.add(ATTR_GROUP, ATTR_FACE2_TOKEN,     face2_entity.entityToken)
                except Exception:
                    futil.log('PartsGen: could not save shaft reference entity tokens; '
                              'Edit will fall back to Custom Length')
            else:
                comp_attrs.add(ATTR_GROUP, ATTR_LEN_EXPR, custom_len_expr)
                comp_attrs.add(ATTR_GROUP, ATTR_REVERSE_DIR,
                               str(reverseDirInp.value if reverseDirInp is not None else False))
                if ref_point_entity is not None:
                    try:
                        comp_attrs.add(ATTR_GROUP, ATTR_REF_POINT_TOKEN, ref_point_entity.entityToken)
                    except Exception:
                        futil.log('PartsGen: could not save shaft reference point token '
                                  '(custom length); Edit will fall back to world-origin placement')
            if custom_name:
                comp_attrs.add(ATTR_GROUP, ATTR_CUSTOM_NAME, custom_name)
            if bearing_part is not None:
                comp_attrs.add(ATTR_GROUP, ATTR_BEARING_ENDS, bearing_name)
                comp_attrs.add(ATTR_GROUP, ATTR_SHAFT_UID,    shaft_uid)
            if createJointInp is not None and createJointInp.value:
                jointTypeInp = inputs.itemById('joint_type')
                joint_type = jointTypeInp.selectedItem.name if jointTypeInp is not None else JOINT_REVOLUTE
                joint_flip = flipJointInp.value if flipJointInp is not None else False
                comp_attrs.add(ATTR_GROUP, ATTR_CREATE_JOINT, 'True')
                comp_attrs.add(ATTR_GROUP, ATTR_JOINT_TYPE, joint_type)
                comp_attrs.add(ATTR_GROUP, ATTR_JOINT_FLIP, str(joint_flip))
        except Exception:
            futil.log('PartsGen: failed to save shaft attributes')

        # The face the shaft was extruded from -- stays live through the bore
        # cut above, since Fusion keeps an extrude feature's startFaces in
        # sync as later participant-body operations modify the same body.
        ref_face = outer_feat.startFaces.item(0) if outer_feat.startFaces.count > 0 else None

        # Persistently color it so the reference face is still obvious after OK,
        # not just during the dialog's live preview (entry.py handles that part
        # with a transient CustomGraphics overlay). Only on the real, fully
        # constrained build -- not every preview tick -- and only if the user
        # hasn't unchecked "Highlight Reference Face".
        highlightInp = inputs.itemById('highlight_ref_face')
        highlight_enabled = highlightInp is None or highlightInp.value
        if constrain and highlight_enabled and ref_face is not None:
            try:
                appearance = futil.get_or_create_appearance(
                    design, REF_FACE_APPEARANCE_NAME, REF_FACE_COLOR)
                ref_face.appearance = appearance
            except Exception:
                futil.log('PartsGen: failed to color shaft reference face')

        # Optional joint between the shaft's reference face and the Reference Point on
        # the other part -- possible whenever a Reference Point was picked, in either
        # Between-Two-Faces or Custom Length mode (the dialog only allows "Create Joint" to
        # be checked once a Reference Point is selected -- see command_validate_input).
        # Reuses ref_point_entity directly -- the same entity that positioned the shaft is
        # also the joint's target. Gated on `constrain` for the same reason as the coloring
        # above: executePreview reruns this whole function on every tick, and a Joint is a
        # real, undo-tracked design mutation (unlike the transient preview highlight), so it
        # must only be created once, on the final command_execute build. Isolated in its own
        # try/except (mirroring pulley_gen.py's joint block) so a joint failure doesn't take
        # down an otherwise-successful shaft build.
        if (constrain and createJointInp is not None and createJointInp.value
                and ref_face is not None and ref_point_entity is not None):
            jointTypeInp = inputs.itemById('joint_type')
            joint_type = jointTypeInp.selectedItem.name if jointTypeInp is not None else JOINT_REVOLUTE
            joint_flip = flipJointInp.value if flipJointInp is not None else False
            try:
                _create_reference_joint(
                    workingOcc, ref_face, ref_point_entity, joint_type, joint_flip,
                    shaft_profile=outer_profile if ref_start_offset_cm else None,
                    profile_axis=sketch.xDirection.crossProduct(sketch.yDirection))
            except Exception:
                futil.handle_error(
                    f'PartsGen: reference joint for {workingComp.name}', show_message_box=True)

        # Optional bearings -- final build only, like the joint above: inserting a file is
        # a real design mutation, and far too slow to redo on every preview tick. After the
        # reference joint, so they're placed on the shaft where it finally ends up.
        if constrain and bearing_part is not None:
            try:
                _add_bearings(workingOcc, outer_feat, bearing_part, shaft_uid, groupOcc)
            except Exception:
                futil.handle_error(
                    f'PartsGen: bearings for {workingComp.name}', show_message_box=True)

        # Grouped last so the joint above lands inside the shaft's timeline group and is
        # deleted or suppressed along with it, instead of being orphaned outside it.
        futil.group_timeline_features(design, start_marker, workingComp.name)

        return ref_face
    except Exception:
        try:
            outerOcc.deleteMe()
        except Exception:
            pass
        futil.handle_error('PartsGen _create_shaft', show_message_box=True)
