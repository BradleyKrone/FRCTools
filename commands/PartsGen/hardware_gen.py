import adsk.core
import adsk.fusion
from ...lib import fusionAddInUtils as futil
from .shaft_gen import (BEARING_PARTS, ATTR_GROUP, BEARING_RADIUS_TOL_CM, _insert_bearing,
                        _occurrence_bodies_recursive, _circle_radii, _true_face_normal,
                        _native, _world_xform, _world_circle, _hide_joint)

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Hardware: library parts (Argos CAD > Parts_1 > Parts_Gen) dropped into picked holes and
# rigidly jointed there -- flange seat face down on the part, body down the hole. Any
# flanged part works: the seat is found by geometry (see `_find_flange_seat_face`), so
# adding one is a line here pointing at a BEARING_PARTS-style entry.
# ---------------------------------------------------------------------------
PART_HARDWARE = 'Hardware'

HARDWARE_PARTS = {
    'Hex Bearing (WCP-0785)': BEARING_PARTS['Bearing (WCP-0785)'],
}

ATTR_HARDWARE_PART = 'hardware_part'


# ===========================================================================
# Dialog
# ===========================================================================

def add_hardware_group(inputs: adsk.core.CommandInputs, visible: bool):
    """Add the "Hardware" group: which part, the hole rims to put it in, and a Flip."""
    group = inputs.addGroupCommandInput('hardware_group', 'Hardware')
    group.isExpanded = True
    group.isVisible  = visible
    children = group.children

    partInp = children.addDropDownCommandInput(
        'hw_part', 'Hardware', adsk.core.DropDownStyles.TextListDropDownStyle)
    for i, name in enumerate(HARDWARE_PARTS):
        partInp.listItems.add(name, i == 0, '')

    holesInp = children.addSelectionInput(
        'hw_holes', 'Holes', 'Click the rim of each hole, on the face the flange sits on')
    holesInp.addSelectionFilter('CircularEdges')
    # No upper limit. entry.py raises the minimum to 1 only while Hardware is the part
    # type, so a hidden, empty pick never blocks OK for the other parts.
    holesInp.setSelectionLimits(1 if visible else 0, 0)
    holesInp.tooltip = ('Click a hole\'s circular edge on the face the flange should sit on. '
                        'Pick as many holes as you like -- one part is added to each and '
                        'rigidly jointed there.')

    flipInp = children.addBoolValueInput('hw_flip', 'Flip', True, '', False)
    flipInp.tooltip = ('Put the flange on the other side of the picked face (e.g. a hole '
                       'rim with no flat face next to it that came out backwards).')


# ===========================================================================
# Geometry
# ===========================================================================

def _find_flange_seat_face(occ: adsk.fusion.Occurrence):
    """The face of a flanged part that sits against the plate -- the underside of the
    flange -- as a root-context proxy, or None.

    The flange is the part's largest circle; two planar faces carry it, its outer face
    (at the part's axial extreme) and the seat. Its true normal points away from the
    flange, into the plate. (WCP-0785: outer face z=0.1587, seat z=0 facing -Z, an annulus
    from the 1.125in body to the flange.)"""
    planar = []
    for body in _occurrence_bodies_recursive(occ):
        for face in body.faces:
            plane = adsk.core.Plane.cast(face.geometry)
            if plane is not None:
                planar.append((face, plane, _circle_radii(face)))
    all_radii = [r for _, _, radii in planar for r in radii]
    if not all_radii:
        return None
    flange_r = max(all_radii)
    flange = [(f, p) for f, p, radii in planar
              if any(abs(r - flange_r) < BEARING_RADIUS_TOL_CM for r in radii)]
    if len(flange) < 2:
        return None

    axis   = flange[0][1].normal
    origin = flange[0][1].origin
    def _along(p: adsk.core.Plane) -> float:
        return origin.vectorTo(p.origin).dotProduct(axis)
    square = [p for _, p, _ in planar if abs(p.normal.dotProduct(axis)) > 1 - 1e-6]
    lo = min(_along(p) for p in square)
    hi = max(_along(p) for p in square)
    seats = [f for f, p in flange
             if abs(_along(p) - lo) > 1e-4 and abs(_along(p) - hi) > 1e-4]
    return seats[0] if len(seats) == 1 else None


def _seat_body_radius(seat_face: adsk.fusion.BRepFace) -> float:
    """Radius of the part's body just under the flange: the seat annulus' inner circle."""
    radii = _circle_radii(seat_face)
    return min(radii) if radii else 0.0


def _hole_frame(edge: adsk.fusion.BRepEdge, flip: bool):
    """(world centre, world unit 'into the part' direction, radius) for a picked hole rim,
    or (None, None, None).

    Into the part = against the true outward normal of the rim's flat neighbour face (the
    face the flange sits on). A rim with no flat neighbour falls back to probing the body
    either side of the rim, just outside the hole. Read natively and mapped through
    `_world_xform` -- a deep pick's own `.geometry` can come back native in the live dialog
    (see LESSONS_LEARNED.md)."""
    center, normal = _world_circle(edge)
    if center is None:
        return None, None, None
    native = _native(edge)
    circle = adsk.core.Circle3D.cast(native.geometry) or adsk.core.Arc3D.cast(native.geometry)
    radius = circle.radius
    xf = _world_xform(edge)

    into = None
    for face in native.faces:
        plane = adsk.core.Plane.cast(face.geometry)
        if plane is None or abs(plane.normal.dotProduct(circle.normal)) < 1 - 1e-6:
            continue
        into = _true_face_normal(face)
        into.scaleBy(-1)
        break

    if into is None:
        body = native.body
        perp = circle.normal.crossProduct(adsk.core.Vector3D.create(1, 0, 0))
        if perp.length < 1e-6:
            perp = circle.normal.crossProduct(adsk.core.Vector3D.create(0, 1, 0))
        perp.normalize()
        for sign in (1, -1):
            c = circle.center
            probe = adsk.core.Point3D.create(
                c.x + perp.x * (radius + 0.02) + circle.normal.x * 0.02 * sign,
                c.y + perp.y * (radius + 0.02) + circle.normal.y * 0.02 * sign,
                c.z + perp.z * (radius + 0.02) + circle.normal.z * 0.02 * sign)
            if body.pointContainment(probe) == adsk.fusion.PointContainment.PointInsidePointContainment:
                into = circle.normal.copy()
                into.scaleBy(sign)
                break
    if into is None:
        into = circle.normal.copy()

    into.transformBy(xf)
    into.normalize()
    if flip:
        into.scaleBy(-1)
    return center, into, radius


def _place(occ: adsk.fusion.Occurrence, seat_face: adsk.fusion.BRepFace,
           center: adsk.core.Point3D, into: adsk.core.Vector3D):
    """Move `occ` (at identity, so its seat face reads world) so the seat's centre lands on
    the hole centre and its true normal points into the part."""
    source_n  = _true_face_normal(seat_face)
    source_pt = next((adsk.core.Circle3D.cast(e.geometry).center for e in seat_face.edges
                      if adsk.core.Circle3D.cast(e.geometry) is not None), seat_face.centroid)
    xform = adsk.core.Matrix3D.create()
    if source_n.dotProduct(into) < -1 + 1e-9:
        # Exactly opposed -- the rotation axis is ambiguous, so give it one.
        perp = source_n.crossProduct(adsk.core.Vector3D.create(1, 0, 0))
        if perp.length < 1e-6:
            perp = source_n.crossProduct(adsk.core.Vector3D.create(0, 1, 0))
        xform.setToRotateTo(source_n, into, perp)
    else:
        xform.setToRotateTo(source_n, into)
    moved = source_pt.copy()
    moved.transformBy(xform)
    xform.translation = moved.vectorTo(center)
    occ.transform2 = xform


def _seat_frame(occ: adsk.fusion.Occurrence):
    """(world centre, world true normal) of the part's flange seat as it sits now."""
    seat = _find_flange_seat_face(occ)
    center = next((adsk.core.Circle3D.cast(e.geometry).center for e in seat.edges
                   if adsk.core.Circle3D.cast(e.geometry) is not None), seat.centroid)
    return center, _true_face_normal(seat)


def _moved(occ: adsk.fusion.Occurrence, center: adsk.core.Point3D,
           into: adsk.core.Vector3D) -> bool:
    """True if a joint moved the part's seat off the hole or turned it over. A spin about
    the hole's axis doesn't count -- the joint is free to re-clock a round part (it
    snaps it to the hole edge's own X axis)."""
    now_c, now_n = _seat_frame(occ)
    return now_c.distanceTo(center) > 1e-4 or now_n.dotProduct(into) < 1 - 1e-6


def _joint(occ: adsk.fusion.Occurrence, edge: adsk.fusion.BRepEdge, name: str,
           center: adsk.core.Point3D, into: adsk.core.Vector3D):
    """Rigidly joint the part's seat face to the hole rim without moving it off the
    placement `_place` gave it. Which `isFlipped` keeps it can't be trusted from a deep
    pick's joint axes, so add it, and if the seat moved, delete it, put the part back and
    add it flipped."""
    rootComp = adsk.fusion.Design.cast(app.activeProduct).rootComponent
    placed = occ.transform2

    def _add(is_flipped: bool):
        seat = _find_flange_seat_face(occ)
        joint_input = rootComp.joints.createInput(
            adsk.fusion.JointGeometry.createByPlanarFace(
                seat, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint),
            adsk.fusion.JointGeometry.createByCurve(
                edge, adsk.fusion.JointKeyPointTypes.CenterKeyPoint))
        joint_input.isFlipped = is_flipped
        joint_input.setAsRigidJointMotion()
        return rootComp.joints.add(joint_input)

    joint = _add(False)
    if joint is None or _moved(occ, center, into):
        if joint is not None:
            joint.deleteMe()
        occ.transform2 = placed
        joint = _add(True)
        if joint is None or _moved(occ, center, into):
            futil.log(f'PartsGen: {name} joint moved the part either way; kept the flipped one')
    if joint is None:
        raise RuntimeError('Joints.add returned null')
    joint.name = name
    _hide_joint(joint)
    return joint


# ===========================================================================
# Creation
# ===========================================================================

def _selected_edges(sel: adsk.core.SelectionCommandInput):
    edges = []
    for i in range(sel.selectionCount):
        try:
            edges.append(sel.selection(i).entity)
        except RuntimeError:
            pass
    return edges


def create_hardware(inputs: adsk.core.CommandInputs, is_preview: bool = False) -> bool:
    """Insert the chosen part into every picked hole and joint it. The preview builds
    nothing (a cloud insert per tick is too slow); OK does the real work."""
    if is_preview:
        return True
    partInp  = inputs.itemById('hw_part')
    holesInp = inputs.itemById('hw_holes')
    flipInp  = inputs.itemById('hw_flip')
    part_name = partInp.selectedItem.name
    part  = HARDWARE_PARTS[part_name]
    flip  = flipInp is not None and flipInp.value
    edges = _selected_edges(holesInp)
    if not edges:
        return False

    design   = adsk.fusion.Design.cast(app.activeProduct)
    rootComp = design.rootComponent
    start_marker = design.timeline.markerPosition

    # One cloud insert; every other hole gets another occurrence of the same component.
    comp   = None
    failed = []
    added  = 0
    for i, edge in enumerate(edges):
        if comp is not None and comp.isValid:
            occ = rootComp.occurrences.addExistingComponent(comp, adsk.core.Matrix3D.create())
        else:
            occ = _insert_bearing(None, part, 'No hardware was added.')
            if occ is None:
                return added > 0
            comp = occ.component
        try:
            center, into, hole_r = _hole_frame(edge, flip)
            if center is None:
                raise RuntimeError('picked edge is not a circle')
            seat = _find_flange_seat_face(occ)
            if seat is None:
                raise RuntimeError(f'{part["file"]} flange seat face not found')
            body_r = _seat_body_radius(seat)
            if hole_r + BEARING_RADIUS_TOL_CM < body_r:
                futil.log(f'PartsGen: hole {i + 1} is O{2 * hole_r / 2.54:.3f} in, smaller than '
                          f'the {part["file"]} body (O{2 * body_r / 2.54:.3f} in)')
            _place(occ, seat, center, into)
            occ.attributes.add(ATTR_GROUP, ATTR_HARDWARE_PART, part_name)
            _joint(occ, edge, f'{occ.name}_joint', center, into)
            added += 1
        except Exception:
            futil.handle_error(f'PartsGen: {part_name} in hole {i + 1}')
            failed.append(i + 1)
            try:
                occ.deleteMe()
            except Exception:
                pass

    futil.group_timeline_features(design, start_marker, f'Hardware_{part["file"]}')
    futil.log(f'PartsGen: added {added} x {part_name}')
    if failed:
        futil.popup_error(
            f'Parts Gen: could not add the {part_name} to hole(s) '
            f'{", ".join(str(n) for n in failed)} (in pick order). See the Text Command '
            'window for details.')
    return added > 0
