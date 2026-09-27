import json
import os
import pathlib
import re
import adsk.core
import adsk.fusion
from ...lib import fusionAddInUtils as futil
from .shaft_gen import (BEARING_PARTS, ATTR_GROUP, BEARING_RADIUS_TOL_CM, _insert_bearing,
                        _occurrence_bodies_recursive, _circle_radii, _true_face_normal,
                        _native, _world_xform, _world_circle, _hide_joint)
from .hardware_thumbs import ensure_thumbnails, preview_file

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Hardware: library parts (Argos CAD > 1 Parts > Standard Parts > Parts_Gen > Bearing /
# Bolts / Washers / Nuts) dropped into picked holes and rigidly jointed there. Two mounts:
#   flange (default) -- bearings/bushings: flange seat face down on the part, body down the
#       hole. The seat is found by geometry (see `_find_flange_seat_face`), so any flanged
#       part works.
#   origin -- bolts, washers, nuts: the file is modelled with its component origin on
#       the seat and +Z pointing away from the picked face, so the origin is jointed straight
#       to the hole rim. A bolt's `washer` names the washer "Add washer" stacks under it.
# Adding a part is a line in HARDWARE_CATEGORIES.
#
# The parts are filed in a "Hardware" component (see `_hardware_folder`) inside the active
# component -- or its parent, when the active one is empty (activated by mistake).
# ---------------------------------------------------------------------------
PART_HARDWARE = 'Hardware'

MOUNT_FLANGE = 'flange'
MOUNT_ORIGIN = 'origin'

HARDWARE_CATEGORIES = {
    'Bearings': {
        'Hex Bearing (WCP-0785)':       BEARING_PARTS['Bearing (WCP-0785)'],
        'MaxSpline Bearing (TTB-0065)': BEARING_PARTS['Bearing (TTB-0065)'],
        'Hex Bushing (WCP-0999)':       BEARING_PARTS['Bushing (WCP-0999)'],
    },
    'Bolts': {
        '10-32 x 5/8 Button Head': dict(urn='urn:adsk.wipprod:dm.lineage:otYK9gfvS66I0T6nr3rX0w',
                                        file='Bolt_10-32_5/8_Button', folder='Bolts',
                                        mount=MOUNT_ORIGIN, washer='10-32 Washer'),
        '10-32 x 5/8 Socket Head': dict(urn='urn:adsk.wipprod:dm.lineage:Fq3ohf5mTeaOyTTEtD3ezw',
                                        file='Bolt_10-32_5/8_Cap', folder='Bolts',
                                        mount=MOUNT_ORIGIN, washer='10-32 Washer'),
        # Countersunk -- sits flush in its chamfer, so no washer.
        '10-32 x 5/8 Chamfer Head': dict(urn='urn:adsk.wipprod:dm.lineage:OkHlcH-9Q8iyUXFGw5j6LQ',
                                         file='Bolt_10-32_5/8_Chamfer', folder='Bolts',
                                         mount=MOUNT_ORIGIN),
        '1/4-20 x 1/4 Button Head': dict(urn='urn:adsk.wipprod:dm.lineage:bib30h7LQIq-9xvsvidVMg',
                                         file='Bolt_1/4-20_1/4_Button', folder='Bolts',
                                         mount=MOUNT_ORIGIN, washer='1/4-20 Washer'),
    },
    'Washers': {
        '10-32 Washer':  dict(urn='urn:adsk.wipprod:dm.lineage:IXITY8uKStGJSSwDSlLsNQ',
                              file='10-32_washer', folder='Washers', mount=MOUNT_ORIGIN),
        '1/4-20 Washer': dict(urn='urn:adsk.wipprod:dm.lineage:rGGAeZYcT-mEOsAdfXK8CQ',
                              file='1/4-20_washer', folder='Washers', mount=MOUNT_ORIGIN),
    },
    'Nuts': {
        '10-32 Rivnut': dict(urn='urn:adsk.wipprod:dm.lineage:b1vZ232ZRT6s6maFO71Sag',
                             file='Rivenut_10-32', folder='Nuts', mount=MOUNT_ORIGIN),
        '10-32 Locknut':  dict(urn='urn:adsk.wipprod:dm.lineage:ZWF-hmswQMa_67Uz92GEFQ',
                               file='10-32_Locknut', folder='Nuts', mount=MOUNT_ORIGIN),
        '1/4-20 Locknut': dict(urn='urn:adsk.wipprod:dm.lineage:KOBQlzjKQiOWoU8gTEtcEA',
                               file='1/4-20_Locknut', folder='Nuts', mount=MOUNT_ORIGIN),
    },
}
HARDWARE_PARTS = {name: part for parts in HARDWARE_CATEGORIES.values()
                  for name, part in parts.items()}

ATTR_HARDWARE_PART   = 'hardware_part'
ATTR_HARDWARE_FOLDER = 'hardware_folder'
HARDWARE_FOLDER_NAME = 'Hardware'

# The thumbnail grid that replaces the part dropdown (a BrowserCommandInput page).
PICKER_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources')
PICKER_HTML = os.path.join(PICKER_DIR, 'hardware_picker.html')


# ===========================================================================
# Dialog
# ===========================================================================

def _fill_parts(partInp: adsk.core.DropDownCommandInput, category: str):
    """Refill the part dropdown with one category's parts, the first one selected."""
    partInp.listItems.clear()
    for i, name in enumerate(HARDWARE_CATEGORIES[category]):
        partInp.listItems.add(name, i == 0, '')


def _picker_data(inputs: adsk.core.CommandInputs) -> str:
    """The thumbnail picker's contents (see hardware_picker.html) for the current category:
    each part's name and thumbnail path relative to the page, and which one is selected."""
    category = inputs.itemById('hw_category').selectedItem.name
    parts = [dict(name=name, img=os.path.relpath(preview_file(part), PICKER_DIR).replace('\\', '/'))
             for name, part in HARDWARE_CATEGORIES[category].items()]
    return json.dumps(dict(parts=parts, selected=_selected_part_name(inputs)))


def _update_picker(inputs: adsk.core.CommandInputs):
    pickerInp = inputs.itemById('hw_picker')
    if pickerInp is not None:
        pickerInp.sendInfoToHTML('parts', _picker_data(inputs))


def handle_hardware_html(args: adsk.core.HTMLEventArgs):
    """The thumbnail picker's messages: 'ready' is answered with its contents, and 'select'
    (a tile was clicked) selects that part in the hidden part dropdown."""
    if args.browserCommandInput is None or args.browserCommandInput.id != 'hw_picker':
        return
    inputs = args.browserCommandInput.parentCommand.commandInputs
    if args.action == 'ready':
        args.returnData = _picker_data(inputs)
    elif args.action == 'select':
        partInp = inputs.itemById('hw_part')
        for i in range(partInp.listItems.count):
            item = partInp.listItems.item(i)
            if item.name == args.data:
                item.isSelected = True
        handle_hardware_input_changed(inputs, 'hw_part')
        args.returnData = 'OK'


def _selected_part_name(inputs: adsk.core.CommandInputs):
    partInp = inputs.itemById('hw_part')
    item = partInp.selectedItem if partInp is not None else None
    return item.name if item is not None else None


def add_hardware_group(inputs: adsk.core.CommandInputs, visible: bool):
    """Add the "Hardware" group: a category, which part, the hole rims to put it in, an
    optional washer under a bolt, and a Flip. The part is picked by clicking its thumbnail
    in a grid (hardware_picker.html); the hidden `hw_part` dropdown holds the pick."""
    ensure_thumbnails(HARDWARE_PARTS.values())
    group = inputs.addGroupCommandInput('hardware_group', 'Hardware')
    group.isExpanded = True
    group.isVisible  = visible
    children = group.children

    categoryInp = children.addDropDownCommandInput(
        'hw_category', 'Category', adsk.core.DropDownStyles.TextListDropDownStyle)
    for i, name in enumerate(HARDWARE_CATEGORIES):
        categoryInp.listItems.add(name, i == 0, '')

    partInp = children.addDropDownCommandInput(
        'hw_part', 'Hardware', adsk.core.DropDownStyles.TextListDropDownStyle)
    _fill_parts(partInp, categoryInp.selectedItem.name)
    partInp.isVisible = False

    # Empty name: centred, full dialog width. The page asks for its contents once loaded.
    # A real file:/// URL -- a bare Windows path's backslashes come through as %5C.
    children.addBrowserCommandInput('hw_picker', '', pathlib.Path(PICKER_HTML).as_uri(), 150, 300)

    holesInp = children.addSelectionInput(
        'hw_holes', 'Holes', 'Click the rim of each hole, on the face the flange sits on')
    holesInp.addSelectionFilter('CircularEdges')
    # No upper limit. entry.py raises the minimum to 1 only while Hardware is the part
    # type, so a hidden, empty pick never blocks OK for the other parts.
    holesInp.setSelectionLimits(1 if visible else 0, 0)
    holesInp.tooltip = ('Click a hole\'s circular edge on the face the flange should sit on. '
                        'Pick as many holes as you like -- one part is added to each and '
                        'rigidly jointed there.')

    washerInp = children.addBoolValueInput('hw_washer', 'Add Washer', True, '', False)
    washerInp.tooltip = ('Put the matching washer on the face first and sit the bolt on top '
                         'of it (the bolt is jointed to the washer).')
    washerInp.isVisible = False

    offsetInp = children.addValueInput('hw_offset', 'Z Offset', 'in', futil.inchValue(0))
    offsetInp.tooltip = ('Lift the part off the picked face by this much (negative sinks it '
                         'in). Kept as the joint\'s offset, so it can be edited later in the '
                         'joint. With Add Washer, the washer is lifted and the bolt stays on it.')

    flipInp = children.addBoolValueInput('hw_flip', 'Flip', True, '', False)
    flipInp.tooltip = ('Put the part on the other side of the picked face (e.g. a hole '
                       'rim with no flat face next to it that came out backwards).')


def handle_hardware_input_changed(inputs: adsk.core.CommandInputs, changed_id: str):
    """Keep the part list and the thumbnail grid in step with the category, and show "Add
    Washer" only for a part that has one. `inputs` must be the command's top-level inputs."""
    partInp = inputs.itemById('hw_part')
    if changed_id == 'hw_category' and partInp is not None:
        _fill_parts(partInp, inputs.itemById('hw_category').selectedItem.name)
        _update_picker(inputs)
    washerInp = inputs.itemById('hw_washer')
    if washerInp is not None:
        part = HARDWARE_PARTS.get(_selected_part_name(inputs))
        washerInp.isVisible = part is not None and 'washer' in part


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


def _rigid_joint(occ: adsk.fusion.Occurrence, part_geometry, edge: adsk.fusion.BRepEdge,
                 name: str, moved, offset_cm: float = 0.0):
    """Rigidly joint `part_geometry()` (a fresh JointGeometry on the part) to the centre of
    the hole rim `edge`, `offset_cm` off it, without moving the part off the placement it
    was given (already offset). Neither the `isFlipped` nor the offset sign that keeps it
    can be trusted from a deep pick's joint axes, so add it, and while `moved()` says it
    shifted, delete it, put the part back and try the next combination."""
    rootComp = adsk.fusion.Design.cast(app.activeProduct).rootComponent
    placed = occ.transform2

    def _add(is_flipped: bool, offset: float):
        joint_input = rootComp.joints.createInput(
            part_geometry(),
            adsk.fusion.JointGeometry.createByCurve(
                edge, adsk.fusion.JointKeyPointTypes.CenterKeyPoint))
        joint_input.isFlipped = is_flipped
        joint_input.setAsRigidJointMotion()
        if offset:
            joint_input.offset = adsk.core.ValueInput.createByReal(offset)
        return rootComp.joints.add(joint_input)

    signs = (1, -1) if offset_cm else (1,)
    tries = [(flip, sign * offset_cm) for flip in (False, True) for sign in signs]
    joint = None
    for n, (is_flipped, offset) in enumerate(tries):
        if joint is not None:
            joint.deleteMe()
            occ.transform2 = placed
        joint = _add(is_flipped, offset)
        if joint is not None and not moved():
            break
        if n == len(tries) - 1:
            futil.log(f'PartsGen: {name} joint moved the part every way; kept the last one')
    if joint is None:
        raise RuntimeError('Joints.add returned null')
    joint.name = name
    _hide_joint(joint)
    return joint


def _joint(occ: adsk.fusion.Occurrence, edge: adsk.fusion.BRepEdge, name: str,
           center: adsk.core.Point3D, into: adsk.core.Vector3D, offset_cm: float = 0.0):
    """Rigidly joint a flanged part's seat face to the hole rim (see `_rigid_joint`).
    `center` is where the seat was placed (already offset)."""
    return _rigid_joint(
        occ,
        lambda: adsk.fusion.JointGeometry.createByPlanarFace(
            _find_flange_seat_face(occ), None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint),
        edge, name, lambda: _moved(occ, center, into), offset_cm)


# --- Origin mount (bolts, washers, nuts) -----------------------------------

def _place_origin(occ: adsk.fusion.Occurrence, center: adsk.core.Point3D,
                  out: adsk.core.Vector3D):
    """Move `occ` so its component origin lands on `center` with its +Z along `out`."""
    x = out.crossProduct(adsk.core.Vector3D.create(1, 0, 0))
    if x.length < 1e-6:
        x = out.crossProduct(adsk.core.Vector3D.create(0, 1, 0))
    x.normalize()
    y = out.crossProduct(x)
    xform = adsk.core.Matrix3D.create()
    xform.setWithCoordinateSystem(center, x, y, out)
    occ.transform2 = xform


def _origin_moved(occ: adsk.fusion.Occurrence, center: adsk.core.Point3D,
                  out: adsk.core.Vector3D) -> bool:
    """True if a joint moved the part's origin off `center` or turned its +Z off `out`.
    A spin about Z doesn't count (the joint re-clocks round parts to the edge's X axis)."""
    origin, _, _, z = occ.transform2.getAsCoordinateSystem()
    z.normalize()
    return origin.distanceTo(center) > 1e-4 or z.dotProduct(out) < 1 - 1e-6


def _joint_origin(occ: adsk.fusion.Occurrence, edge: adsk.fusion.BRepEdge, name: str,
                  center: adsk.core.Point3D, out: adsk.core.Vector3D, offset_cm: float = 0.0):
    """Rigidly joint the part's component origin to the hole rim (see `_rigid_joint`).
    `center` is where the origin was placed (already offset)."""
    return _rigid_joint(
        occ,
        lambda: adsk.fusion.JointGeometry.createByPoint(
            occ.component.originConstructionPoint.createForAssemblyContext(occ)),
        edge, name, lambda: _origin_moved(occ, center, out), offset_cm)


def _offset_point(point: adsk.core.Point3D, direction: adsk.core.Vector3D,
                  distance: float) -> adsk.core.Point3D:
    moved = point.copy()
    step = direction.copy()
    step.scaleBy(distance)
    moved.translateBy(step)
    return moved


def _washer_top_rim(washer: adsk.fusion.Occurrence, center: adsk.core.Point3D,
                    out: adsk.core.Vector3D):
    """(bore rim edge on the washer's top face, its world centre) for a washer placed at
    `center` facing `out`, or (None, None): of the circular edges on the washer's axis,
    the ones furthest along `out`, then the smallest."""
    best = None
    for body in _occurrence_bodies_recursive(washer):
        for edge in body.edges:
            c, n = _world_circle(edge)
            if c is None or abs(n.dotProduct(out)) < 1 - 1e-6:
                continue
            offset = center.vectorTo(c)
            along  = offset.dotProduct(out)
            radial = offset.copy()
            axial  = out.copy()
            axial.scaleBy(along)
            radial.subtract(axial)
            if radial.length > 1e-4:
                continue
            geom = _native(edge).geometry
            circle = adsk.core.Circle3D.cast(geom) or adsk.core.Arc3D.cast(geom)
            key = (-round(along, 5), circle.radius)
            if best is None or key < best[0]:
                best = (key, edge, c)
    return (best[1], best[2]) if best is not None else (None, None)


# ===========================================================================
# Creation
# ===========================================================================

def _is_hardware_folder(comp: adsk.fusion.Component) -> bool:
    """A component made by `_hardware_folder`, or one the user named "Hardware" by hand.
    Component names are unique design-wide, so Fusion renames the second one "Hardware (1)"."""
    return (comp.attributes.itemByName(ATTR_GROUP, ATTR_HARDWARE_FOLDER) is not None
            or re.fullmatch(rf'{HARDWARE_FOLDER_NAME}( \(\d+\))?', comp.name) is not None)


def _hardware_folder(design: adsk.fusion.Design):
    """(root-context occurrence of the "Hardware" component to put new parts in, True if it
    was just created).

    It lives in the active component -- or in that one's parent when the active component
    is empty (no bodies, no sub-components: most likely activated by mistake). An existing
    one there is reused; else a new one is added at identity. An active Hardware component
    is used as is."""
    active = design.activeOccurrence
    if active is not None and _is_hardware_folder(active.component):
        return active, False
    if (active is not None and active.component.bRepBodies.count == 0
            and active.component.occurrences.count == 0):
        active = active.assemblyContext
    parent_comp = active.component if active is not None else design.rootComponent

    folder = None
    for occ in parent_comp.occurrences:
        if _is_hardware_folder(occ.component):
            folder = occ
            break
    created = folder is None
    if created:
        folder = parent_comp.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        folder.component.name = HARDWARE_FOLDER_NAME
        folder.component.attributes.add(ATTR_GROUP, ATTR_HARDWARE_FOLDER, '1')
    if active is not None:
        folder = folder.createForAssemblyContext(active)
    return folder, created


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
    holesInp  = inputs.itemById('hw_holes')
    flipInp   = inputs.itemById('hw_flip')
    washerInp = inputs.itemById('hw_washer')
    offsetInp = inputs.itemById('hw_offset')
    offset_cm = offsetInp.value if offsetInp is not None else 0.0
    part_name = _selected_part_name(inputs)
    part  = HARDWARE_PARTS[part_name]
    flip  = flipInp is not None and flipInp.value
    washer_name = part.get('washer') if (washerInp is not None and washerInp.isVisible
                                         and washerInp.value) else None
    edges = _selected_edges(holesInp)
    if not edges:
        return False

    design   = adsk.fusion.Design.cast(app.activeProduct)
    start_marker = design.timeline.markerPosition
    hw_occ, hw_created = _hardware_folder(design)
    hw_native = hw_occ.nativeObject or hw_occ

    # One cloud insert per part; every other hole gets another occurrence of the same
    # component.
    comps = {}
    def _new_occ(name: str):
        comp = comps.get(name)
        if comp is not None and comp.isValid:
            return hw_native.component.occurrences.addExistingComponent(
                comp, adsk.core.Matrix3D.create()).createForAssemblyContext(hw_occ)
        occ = _insert_bearing(hw_occ, HARDWARE_PARTS[name], 'No hardware was added.')
        if occ is not None:
            comps[name] = occ.component
        return occ

    failed = []
    added  = 0
    for i, edge in enumerate(edges):
        made = []
        try:
            center, into, hole_r = _hole_frame(edge, flip)
            if center is None:
                raise RuntimeError('picked edge is not a circle')
            if part.get('mount', MOUNT_FLANGE) == MOUNT_ORIGIN:
                out = into.copy()
                out.scaleBy(-1)
                # The offset goes on whatever sits on the face; a bolt on a washer rides it.
                rim, base, part_offset = edge, _offset_point(center, out, offset_cm), offset_cm
                if washer_name is not None:
                    washer = _new_occ(washer_name)
                    if washer is None:
                        break
                    made.append(washer)
                    _place_origin(washer, base, out)
                    washer.attributes.add(ATTR_GROUP, ATTR_HARDWARE_PART, washer_name)
                    _joint_origin(washer, edge, f'{washer.name}_joint', base, out, offset_cm)
                    rim, base = _washer_top_rim(washer, base, out)
                    part_offset = 0.0
                    if rim is None:
                        raise RuntimeError(f'{washer_name} top rim not found')
                occ = _new_occ(part_name)
                if occ is None:
                    for washer in made:
                        washer.deleteMe()
                    break
                made.append(occ)
                _place_origin(occ, base, out)
                occ.attributes.add(ATTR_GROUP, ATTR_HARDWARE_PART, part_name)
                _joint_origin(occ, rim, f'{occ.name}_joint', base, out, part_offset)
            else:
                occ = _new_occ(part_name)
                if occ is None:
                    break
                made.append(occ)
                seat = _find_flange_seat_face(occ)
                if seat is None:
                    raise RuntimeError(f'{part["file"]} flange seat face not found')
                body_r = _seat_body_radius(seat)
                if hole_r + BEARING_RADIUS_TOL_CM < body_r:
                    futil.log(f'PartsGen: hole {i + 1} is O{2 * hole_r / 2.54:.3f} in, smaller '
                              f'than the {part["file"]} body (O{2 * body_r / 2.54:.3f} in)')
                seat_center = _offset_point(center, into, -offset_cm)
                _place(occ, seat, seat_center, into)
                occ.attributes.add(ATTR_GROUP, ATTR_HARDWARE_PART, part_name)
                _joint(occ, edge, f'{occ.name}_joint', seat_center, into, offset_cm)
            added += 1
        except Exception:
            futil.handle_error(f'PartsGen: {part_name} in hole {i + 1}')
            failed.append(i + 1)
            for occ in reversed(made):
                try:
                    occ.deleteMe()
                except Exception:
                    pass

    if added == 0 and hw_created and hw_occ.isValid and hw_occ.childOccurrences.count == 0:
        hw_occ.deleteMe()
    group_name = f'Hardware_{part["file"]}' + ('+washer' if washer_name else '')
    futil.group_timeline_features(design, start_marker, group_name)
    futil.log(f'PartsGen: added {added} x {part_name}')
    if failed:
        futil.popup_error(
            f'Parts Gen: could not add the {part_name} to hole(s) '
            f'{", ".join(str(n) for n in failed)} (in pick order). See the Text Command '
            'window for details.')
    return added > 0
