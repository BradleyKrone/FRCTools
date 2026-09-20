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

# Entity tokens for the Between-Two-Faces picks, so right-click Edit can rebuild the shaft
# where it was instead of dropping back to a Custom Length at the world origin.
ATTR_REF_POINT_TOKEN = 'shaft_ref_point_token'
ATTR_FACE2_TOKEN     = 'shaft_face2_token'

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
    edge's centre, or a BRepVertex/SketchPoint/ConstructionPoint's own position."""
    if entity.objectType == adsk.fusion.BRepEdge.classType():
        return entity.geometry.center
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
    directly."""
    if entity.objectType == adsk.fusion.BRepEdge.classType():
        cp_input = workingComp.constructionPoints.createInput()
        cp_input.setByCenter(entity)
        return workingComp.constructionPoints.add(cp_input)
    return entity


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
    # The dialog's CircularEdges filter admits arcs as well as full circles.
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


def _joint_occurrence_chain(entity):
    """Return `[leaf occurrence, ..., top-level child of root]` for a picked entity.

    An as-built joint names two Occurrences, and the pick is usually a proxy several levels
    deep (`Shooter_Base:1+Side_Plate:1`). The leaf is what the shaft should actually joint
    to -- jointing to the top-level group instead would leave the shaft behind whenever a
    sub-assembly pivots -- but the rest of the chain is kept as a fallback. An entity native
    to the root component belongs to no occurrence and yields an empty list.
    """
    chain = []
    occ = getattr(entity, 'assemblyContext', None)
    while occ is not None:
        chain.append(occ)
        occ = occ.assemblyContext
    return chain


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


def _create_reference_joint(workingOcc: adsk.fusion.Occurrence,
                            ref_face: adsk.fusion.BRepFace,
                            other_entity,
                            joint_type: str):
    """As-built joint between the shaft and the part its Reference Point belongs to.

    An **as-built** joint, not a regular one, and that is the whole point. `Joints.add()`
    always repositions its first occurrence to make the two joint frames coincide, so the
    only way to keep a shaft that is already exactly where it belongs was to hunt for an
    `isFlipped`/`angle` pair whose repositioning happened to be a no-op. That hunt is
    unwinnable whenever the picked entity sits 2+ occurrence levels deep, because Fusion then
    reads -- and internally uses -- that entity's local component-native axis instead of the
    world one: confirmed live as a ~48 degree residual on every single candidate. Worse, the
    probe joints' `deleteMe()` did *not* restore the occurrence transform (also confirmed
    live, against a shaft left behind by a failed run), so the shaft kept the last probe's
    180-degree flip -- invisible on a symmetric hex shaft, but real.

    `AsBuiltJoints` adds the same revolute/rigid motion without moving either occurrence,
    which deletes that whole problem rather than tolerating it.

    `pulley_gen.py`/`sprocket_gen.py` deliberately keep the plain `Joints.add()` pattern --
    their joints are driven by a projected sketch circle that has to re-solve as centre
    distance changes -- so don't "fix" them the same way.
    """
    design   = adsk.fusion.Design.cast(app.activeProduct)
    rootComp = design.rootComponent

    chain = _joint_occurrence_chain(other_entity)
    if not chain:
        futil.popup_error(
            f'Parts Gen: no joint was created for {workingOcc.component.name}.\n\n'
            'The Reference Point belongs to the root component rather than to a component '
            'occurrence, and a joint needs two components. The shaft itself is correct -- '
            'move that geometry into its own component, or add the joint by hand.')
        return None

    # A rigid as-built joint takes no geometry at all (the API is explicit that the argument
    # should be null). For revolute, use the shaft's OWN start face rather than the picked
    # hole edge: `workingOcc` is a direct child of root with an identity transform, so it is
    # outside the range of the deep-proxy axis bug above -- and since an as-built joint no
    # longer moves anything, that bug would otherwise hide as a silently wrong rotation axis
    # instead of a visibly wrong position. The face's centre is the Reference Point and its
    # normal is the shaft's axis, so ZAxisJointDirection means "about the shaft".
    if joint_type == JOINT_RIGID:
        geom = None
    else:
        geom = adsk.fusion.JointGeometry.createByPlanarFace(
            ref_face.createForAssemblyContext(workingOcc), None,
            adsk.fusion.JointKeyPointTypes.CenterKeyPoint)

    for occ2 in chain:
        try:
            joint_input = rootComp.asBuiltJoints.createInput(workingOcc, occ2, geom)
            if joint_input is not None:
                if joint_type == JOINT_RIGID:
                    joint_input.setAsRigidJointMotion()
                else:
                    joint_input.setAsRevoluteJointMotion(
                        adsk.fusion.JointDirections.ZAxisJointDirection)
                # Documented to return null on failure rather than raising, so check it.
                joint = rootComp.asBuiltJoints.add(joint_input)
                if joint is not None:
                    joint.name = f'{workingOcc.component.name}_joint'
                    futil.log(f'PartsGen: as-built joint for {workingOcc.component.name} '
                              f'against {occ2.name} ({joint_type})')
                    return joint
        except Exception:
            # Logged, not raised: a deeper occurrence failing is exactly what the walk up
            # the chain exists to absorb, and the final popup below still reports giving up.
            futil.handle_error(f'PartsGen: as-built joint against {occ2.name}')
        futil.log(f'PartsGen: as-built joint against {occ2.name} failed; '
                  'trying its parent occurrence')

    futil.popup_error(
        f'Parts Gen: could not create a joint for {workingOcc.component.name}.\n\n'
        'The shaft itself is correct -- add the joint by hand if you need one.')
    return None


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
                # Nothing to take an axis from -- a plain vertex/sketch/construction point, or
                # a rim with no perpendicular planar neighbour. Fall back to the original
                # behaviour: a plane through the Reference Point, parallel to Face 2.
                plane_point = _plane_offset_point(workingComp, ref_point_entity)
                plane_input = workingComp.constructionPlanes.createInput()
                plane_input.setByOffsetThroughPoint(face2, plane_point)
                sketch_plane = workingComp.constructionPlanes.add(plane_input)
                futil.log('PartsGen: no hole axis available; shaft built parallel to Face 2')

            face2_target    = face2
            custom_len_expr = None
        else:
            face2_target    = None
            centroid1       = adsk.core.Point3D.create(0, 0, 0)
            custom_len_expr = customLenInp.expression
            sketch_plane    = rootComp.xYConstructionPlane

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
            ext_dir   = adsk.fusion.ExtentDirections.PositiveExtentDirection

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
            if custom_name:
                comp_attrs.add(ATTR_GROUP, ATTR_CUSTOM_NAME, custom_name)
            if createJointInp is not None and createJointInp.value:
                jointTypeInp = inputs.itemById('joint_type')
                joint_type = jointTypeInp.selectedItem.name if jointTypeInp is not None else JOINT_REVOLUTE
                comp_attrs.add(ATTR_GROUP, ATTR_CREATE_JOINT, 'True')
                comp_attrs.add(ATTR_GROUP, ATTR_JOINT_TYPE, joint_type)
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
        # the other part -- only possible in Between-Two-Faces mode (only mode with a
        # second part to joint against). Reuses ref_point_entity directly -- the same
        # entity that positioned the shaft is also the joint's target. Gated on
        # `constrain` for the same reason as the coloring above: executePreview reruns
        # this whole function on every tick, and a Joint is a real, undo-tracked design
        # mutation (unlike the transient preview highlight), so it must only be created
        # once, on the final command_execute build. Isolated in its own try/except
        # (mirroring pulley_gen.py's joint block) so a joint failure doesn't take down
        # an otherwise-successful shaft build.
        if (constrain and createJointInp is not None and createJointInp.value
                and len_type == LEN_FACES and ref_face is not None
                and ref_point_entity is not None):
            jointTypeInp = inputs.itemById('joint_type')
            joint_type = jointTypeInp.selectedItem.name if jointTypeInp is not None else JOINT_REVOLUTE
            try:
                _create_reference_joint(workingOcc, ref_face, ref_point_entity, joint_type)
            except Exception:
                futil.handle_error(
                    f'PartsGen: reference joint for {workingComp.name}', show_message_box=True)

        # Grouped last so the joint above lands inside the shaft's timeline group and is
        # deleted or suppressed along with it, instead of being orphaned outside it.
        futil.group_timeline_features(design, start_marker, workingComp.name)

        return ref_face
    except Exception:
        try:
            workingOcc.deleteMe()
        except Exception:
            pass
        futil.handle_error('PartsGen _create_shaft', show_message_box=True)
