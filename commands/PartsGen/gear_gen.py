"""
gear_gen.py  —  20DP gears for PartsGen

Builds WCP-style 20DP aluminium hex-bore gears without teeth: a disk at the gear's outside
diameter (0.375in face) with a hub on each side (0.500in overall), a 1/2in hex or SplineXS
bore, and the tooth count engraved on both faces like the pulleys.

Picked from a CCDistance "Gears 20DP" C-C line, both gears are built inside one group
component and revolute-jointed to its pitch circles, their height set by a Z Offset from an
optional face to the gears' top or bottom hub; a commandTerminated hook (the belt/pulley
pattern) then keeps each gear's size and label in step with its pitch circle, editing its
features in place so joints the user made to it survive. With nothing picked, one
standalone gear is built from the Tooth Count input.

Dimensions (docs.wcproducts.com, 20DP gears): PD = N/20in, OD = (N+2)/20in, 0.375in face,
0.500in overall, 1/2in hex hub Ø0.750in. WCP publish no SplineXS gear drawing, so its hub
is taken as Ø0.500in (their 3/8in hex gears' hub).
"""

import adsk.core
import adsk.fusion
import math
import uuid
from ...lib import fusionAddInUtils as futil
from .shaft_gen import _draw_circle, _hide_joint, _world_plane, _world_face_normal
from .pulley_gen import (ATTR_GROUP, ATTR_PART_TYPE, ATTR_CUSTOM_NAME,
                         BORE_HALF_HEX, BORE_SPLINEXS, BORE_TYPES,
                         LABEL_TEXT_HEIGHT_CM, LABEL_ENGRAVE_CM,
                         _bore_radius_cm, _draw_hex_bore, _draw_splinexs_bore,
                         _offset_xy_plane, _engrave_label_face, _set_circle_diameter,
                         _feature_sketch, _warn, _log_unconstrained)
from ..CCDistance.CCLine import getCCLineFromEntity

app = adsk.core.Application.get()

PART_GEAR = 'Gear'

# Which of the gear's hub faces the Z Offset is measured to.
OFFSET_SIDE_BOTTOM = 'Bottom Hub'
OFFSET_SIDE_TOP    = 'Top Hub'

# ---------------------------------------------------------------------------
# Gear dimensions (Fusion works in cm)
# ---------------------------------------------------------------------------
GEAR_DP         = 20
GEAR_FACE_CM    = 0.375  * 2.54     # toothed face width
GEAR_HUB_LEN_CM = 0.0625 * 2.54     # hub proud of each face -> 0.500in overall
GEAR_HUB_DIA_CM = {BORE_HALF_HEX: 0.75 * 2.54, BORE_SPLINEXS: 0.5 * 2.54}
# CCDistance's C-C motion index for 20DP gears (CCDistance/entry.py motionTypes)
MOTION_GEARS_20DP = 0

HUB_ROOT_MARGIN_CM = 0.02  * 2.54   # hub stays this far inside the tooth root
MIN_WALL_CM        = 0.03  * 2.54   # material left between the bore and the hub edge
LABEL_MARGIN_CM    = 0.01  * 2.54   # gap between the label and the band's edges
LABEL_MIN_TEXT_CM  = 0.03  * 2.54   # smaller than this and the label is skipped

# ---------------------------------------------------------------------------
# Attribute keys (on the gear's component)
# ---------------------------------------------------------------------------
ATTR_GEAR_TOOTH_COUNT = 'gear_tooth_count'     # count the geometry is sized for (C-C N)
ATTR_GEAR_LABEL_TEETH = 'gear_label_teeth'     # real count engraved (pinions: PIN1/PIN2)
ATTR_GEAR_BORE_TYPE   = 'gear_bore_type'
ATTR_GEAR_CC_CIRCLE   = 'gear_cc_circle_token'  # the user's C-C pitch circle
ATTR_GEAR_CC_PINION   = 'gear_cc_pinion'        # '1' if its C-C end was a motor pinion
ATTR_GEAR_JOINT_OFFSET = 'gear_joint_offset'    # the Z Offset, as typed
ATTR_GEAR_OFFSET_FACE = 'gear_offset_face_token'  # its "Offset From" face, if any
ATTR_GEAR_OFFSET_SIDE = 'gear_offset_side'      # which hub face it is measured to
ATTR_GEAR_UID         = 'gear_uid'


# ---------------------------------------------------------------------------
# Sizes
# ---------------------------------------------------------------------------

def _pitch_dia_cm(n_teeth: int) -> float:
    return n_teeth / GEAR_DP * 2.54


def _outer_dia_cm(n_teeth: int) -> float:
    return (n_teeth + 2) / GEAR_DP * 2.54


def _root_radius_cm(n_teeth: int) -> float:
    """Tooth-root radius: PD/2 less a 1.25/DP dedendum."""
    return (n_teeth - 2.5) / GEAR_DP * 2.54 / 2


def _hub_radius_cm(n_teeth: int, bore_type: str) -> float:
    """The hub's radius, shrunk inside the tooth root on a small gear."""
    nominal = GEAR_HUB_DIA_CM.get(bore_type, GEAR_HUB_DIA_CM[BORE_HALF_HEX]) / 2
    return min(nominal, _root_radius_cm(n_teeth) - HUB_ROOT_MARGIN_CM)


def gear_fits(n_teeth: int, bore_type: str) -> bool:
    """Whether the bore fits inside the hub with MIN_WALL_CM to spare."""
    return _bore_radius_cm(bore_type, 0.0) + MIN_WALL_CM < _hub_radius_cm(n_teeth, bore_type)


def min_teeth(bore_type: str) -> int:
    n = 6
    while not gear_fits(n, bore_type) and n < 200:
        n += 1
    return n


def teeth_from_pitch_circle(circle: adsk.fusion.SketchCircle) -> int:
    """Tooth count a 20DP pitch circle is sized for (PD = N/20in)."""
    return int(round(circle.radius * 2 / 2.54 * GEAR_DP))


def gear_teeth_for_circle(circle: adsk.fusion.SketchCircle):
    """(geometry count, engraved count) for a C-C pitch circle.

    The geometry follows the circle itself, so a pitch diameter typed straight into the
    sketch is honoured. An addendum-modified pinion (CCDistance's "9T (10T-CD)") is drawn
    on its centre-distance count but engraved with its real one, read from the C-C line.
    """
    n = teeth_from_pitch_circle(circle)
    label = n
    end = _cc_line_end(circle)
    if end is not None:
        N, PIN = end
        if PIN and N == n:
            label = PIN
    return n, label


def _cc_line_end(circle: adsk.fusion.SketchCircle):
    """(N, PIN) from the C-C line for the end this pitch circle sits on, or None."""
    try:
        cc = getCCLineFromEntity(circle)
        if cc is not None and cc.data is not None and cc.pitchCircle1 is not None:
            # Which end is this? Nearest centre -- entity tokens don't reliably compare.
            c = circle.centerSketchPoint.geometry
            d1 = c.distanceTo(cc.pitchCircle1.centerSketchPoint.geometry)
            d2 = c.distanceTo(cc.pitchCircle2.centerSketchPoint.geometry)
            return ((cc.data.N1, cc.data.PIN1) if d1 <= d2 else (cc.data.N2, cc.data.PIN2))
    except Exception:
        pass
    return None


def is_cc_pinion(circle: adsk.fusion.SketchCircle) -> bool:
    """Whether this pitch circle's C-C end is a motor pinion (CCDistance's "Use Pinion")."""
    end = _cc_line_end(circle)
    return end is not None and bool(end[1])


def default_bore_for_circle(circle: adsk.fusion.SketchCircle) -> str:
    """SplineXS for a C-C motor pinion, else hex."""
    return BORE_SPLINEXS if is_cc_pinion(circle) else BORE_HALF_HEX


def _gear_name(label_teeth: int, bore_type: str) -> str:
    bore = 'SplineXS' if bore_type == BORE_SPLINEXS else 'HalfHex'
    return f'Gear_20DP-{label_teeth}T_{bore}'


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _add_bore(comp: adsk.fusion.Component, bore_type: str):
    """Cut the bore through hubs and face, from a plane on the bottom hub face."""
    plane = _offset_xy_plane(comp, -GEAR_HUB_LEN_CM)
    sk = comp.sketches.add(plane)
    if bore_type == BORE_SPLINEXS:
        _draw_splinexs_bore(sk, 0.0)
    else:
        _draw_hex_bore(sk, 0.0)
    extrudes = comp.features.extrudeFeatures
    ext_in = extrudes.createInput(sk.profiles.item(0),
                                  adsk.fusion.FeatureOperations.CutFeatureOperation)
    ext_in.setDistanceExtent(False, adsk.core.ValueInput.createByReal(
        GEAR_FACE_CM + 2 * GEAR_HUB_LEN_CM))
    # participantBodies: a cut otherwise also goes through other bodies it overlaps.
    ext_in.participantBodies = [comp.bRepBodies.item(0)]
    extrudes.add(ext_in)


def _add_label(comp: adsk.fusion.Component, n_teeth: int, label_teeth: int,
               bore_type: str, is_preview: bool = False):
    """Engrave e.g. "60T" on both sides, in whichever band is wider: the gear face ring
    between the hub and the tooth root, or the hub's own face around the bore (small
    gears, whose ring is too thin). The bottom label is mirrored to read from below."""
    try:
        label  = f'{label_teeth}T'
        hub_r  = _hub_radius_cm(n_teeth, bore_type)
        bands  = [  # (inner r, outer r, top z, bottom z)
            (hub_r, _root_radius_cm(n_teeth), GEAR_FACE_CM, 0.0),
            (_bore_radius_cm(bore_type, 0.0), hub_r,
             GEAR_FACE_CM + GEAR_HUB_LEN_CM, -GEAR_HUB_LEN_CM),
        ]
        r_in, r_out, z_top, z_bot = max(bands, key=lambda b: b[1] - b[0])
        r_in, r_out = r_in + LABEL_MARGIN_CM, r_out - LABEL_MARGIN_CM
        text_h = min(LABEL_TEXT_HEIGHT_CM, (r_out - r_in) / 1.2)
        if text_h < LABEL_MIN_TEXT_CM:
            futil.log(f'PartsGen: {label} gear is too small for its label, skipping it')
            return
        mid = (r_in + r_out) / 2
        y_bot, y_top = mid - 0.6 * text_h, mid + 0.6 * text_h
        for z, direction, mirror in (
                (z_top, adsk.fusion.ExtentDirections.NegativeExtentDirection, False),
                (z_bot, adsk.fusion.ExtentDirections.PositiveExtentDirection, True)):
            _engrave_label_face(comp, label, _offset_xy_plane(comp, z), direction, mirror,
                                y_bot, y_top, is_preview, text_height_cm=text_h)
    except Exception:
        futil.handle_error('PartsGen gear label', show_message_box=not is_preview)


def _build_gear(comp: adsk.fusion.Component, n_teeth: int, label_teeth: int,
                bore_type: str, is_preview: bool = False):
    """Build the gear in `comp` (face from Z=0 to GEAR_FACE_CM, hubs either side).
    Returns its joint circle: a construction circle at the pitch diameter."""
    origin = adsk.core.Point3D.create(0, 0, 0)
    extrudes = comp.features.extrudeFeatures

    # Body: a disk at the OD, plus the pitch circle the joints key off.
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.isComputeDeferred = True
    try:
        _draw_circle(sk, origin, _outer_dia_cm(n_teeth))
        joint_circle = _draw_circle(sk, origin, _pitch_dia_cm(n_teeth))
        joint_circle.isConstruction = True
    finally:
        sk.isComputeDeferred = False
    extrudes.addSimple(sk.profiles.item(0), adsk.core.ValueInput.createByReal(GEAR_FACE_CM),
                       adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    body = comp.bRepBodies.item(0)

    # Hubs: one circle, a Join down from Z=0 and one up from the top face.
    hub_sk = comp.sketches.add(comp.xYConstructionPlane)
    _draw_circle(hub_sk, origin, 2 * _hub_radius_cm(n_teeth, bore_type))
    hub_len = adsk.fusion.DistanceExtentDefinition.create(
        adsk.core.ValueInput.createByReal(GEAR_HUB_LEN_CM))
    for start_cm, direction in (
            (0.0,          adsk.fusion.ExtentDirections.NegativeExtentDirection),
            (GEAR_FACE_CM, adsk.fusion.ExtentDirections.PositiveExtentDirection)):
        ext_in = extrudes.createInput(hub_sk.profiles.item(0),
                                      adsk.fusion.FeatureOperations.JoinFeatureOperation)
        ext_in.setOneSideExtent(hub_len, direction)
        if start_cm:
            ext_in.startExtent = adsk.fusion.OffsetStartDefinition.create(
                adsk.core.ValueInput.createByReal(start_cm))
        ext_in.participantBodies = [body]
        extrudes.add(ext_in)

    _add_bore(comp, bore_type)
    _add_label(comp, n_teeth, label_teeth, bore_type, is_preview)
    if not is_preview:
        _log_unconstrained(comp)
    return joint_circle


def _save_attributes(comp: adsk.fusion.Component, n_teeth: int, label_teeth: int,
                     bore_type: str, custom_name: str, cc_circle=None,
                     joint_offset_expr: str = None, offset_face=None,
                     offset_side: str = None):
    try:
        attrs = comp.attributes
        attrs.add(ATTR_GROUP, ATTR_PART_TYPE,        PART_GEAR)
        attrs.add(ATTR_GROUP, ATTR_GEAR_UID,         uuid.uuid4().hex)
        attrs.add(ATTR_GROUP, ATTR_GEAR_TOOTH_COUNT, str(n_teeth))
        attrs.add(ATTR_GROUP, ATTR_GEAR_LABEL_TEETH, str(label_teeth))
        attrs.add(ATTR_GROUP, ATTR_GEAR_BORE_TYPE,   bore_type)
        if custom_name:
            attrs.add(ATTR_GROUP, ATTR_CUSTOM_NAME, custom_name)
        if cc_circle is not None:
            attrs.add(ATTR_GROUP, ATTR_GEAR_CC_CIRCLE, cc_circle.entityToken)
            attrs.add(ATTR_GROUP, ATTR_GEAR_CC_PINION, '1' if is_cc_pinion(cc_circle) else '0')
        if joint_offset_expr is not None:
            attrs.add(ATTR_GROUP, ATTR_GEAR_JOINT_OFFSET, joint_offset_expr)
        if offset_side is not None:
            attrs.add(ATTR_GROUP, ATTR_GEAR_OFFSET_SIDE, offset_side)
        if offset_face is not None:
            attrs.add(ATTR_GROUP, ATTR_GEAR_OFFSET_FACE, offset_face.entityToken)
    except Exception:
        futil.log(f'PartsGen: failed to save gear attributes on {comp.name}')


# ---------------------------------------------------------------------------
# C-C joint
# ---------------------------------------------------------------------------

def _circle_world_frame(circle: adsk.fusion.SketchCircle):
    """World (centre, unit sketch normal) of a -- possibly proxied -- sketch circle."""
    native = circle.nativeObject or circle
    sk = native.parentSketch
    center = sk.sketchToModelSpace(native.centerSketchPoint.geometry)
    normal = sk.xDirection.crossProduct(sk.yDirection)
    ctx = circle.assemblyContext
    if ctx is not None:
        center.transformBy(ctx.transform2)
        normal.transformBy(ctx.transform2)
    normal.normalize()
    return center, normal


def _placement(center: adsk.core.Point3D, normal: adsk.core.Vector3D) -> adsk.core.Matrix3D:
    """A transform putting a gear's origin at `center` with its Z along `normal`."""
    ref = adsk.core.Vector3D.create(1, 0, 0)
    if abs(ref.dotProduct(normal)) > 0.9:
        ref = adsk.core.Vector3D.create(0, 1, 0)
    y = normal.crossProduct(ref)
    y.normalize()
    x = y.crossProduct(normal)
    x.normalize()
    m = adsk.core.Matrix3D.create()
    m.setWithCoordinateSystem(center, x, y, normal)
    return m


def _gear_joint_offset_cm(cc_circle: adsk.fusion.SketchCircle, face, dist_cm: float,
                          side: str = OFFSET_SIDE_BOTTOM):
    """Offset for a gear's C-C joint that puts its `side` hub face (OFFSET_SIDE_BOTTOM /
    _TOP) `dist_cm` from `face`, positive = out along the face's outward normal. With no
    face the distance is measured from the C-C sketch plane, positive along its normal.
    Returns None when the face isn't parallel to the C-C sketch.

    The joint offset moves the gear's joint circle (Z=0, the bottom of its face) along the
    sketch normal; the bottom hub face is GEAR_HUB_LEN_CM below it, the top one a face
    width plus a hub above it."""
    center, normal = _circle_world_frame(cc_circle)
    side_z = (GEAR_FACE_CM + GEAR_HUB_LEN_CM if side == OFFSET_SIDE_TOP
              else -GEAR_HUB_LEN_CM)
    if face is None:
        return dist_cm - side_z
    origin, face_n = _world_plane(face)
    if abs(face_n.dotProduct(normal)) < 0.999:
        return None
    sign = 1.0 if _world_face_normal(face).dotProduct(normal) > 0 else -1.0
    s_face   = normal.dotProduct(origin.asVector())
    s_sketch = normal.dotProduct(center.asVector())
    return s_face + sign * dist_cm - s_sketch - side_z


def _add_gear_cc_joint(occ: adsk.fusion.Occurrence, joint_circle: adsk.fusion.SketchCircle,
                       cc_circle: adsk.fusion.SketchCircle, offset_cm: float = 0.0,
                       is_preview: bool = False):
    """Revolute-joint a gear's pitch circle to the user's C-C pitch circle (root joint).

    The gear was pre-placed on the circle with its Z along the C-C sketch's normal. As with
    belt_gen._add_cc_joint, the JointGeometry frames are component-native, so the flip and
    offset sign are checked on the result: the gear must keep pointing the same way and
    end up `offset_cm` along the sketch normal."""
    name = occ.component.name
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        root   = design.rootComponent
        center, normal = _circle_world_frame(cc_circle)

        def _add(flip: bool):
            gear_geom = adsk.fusion.JointGeometry.createByCurve(
                joint_circle.createForAssemblyContext(occ),
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            cc_geom = adsk.fusion.JointGeometry.createByCurve(
                cc_circle, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            joint_input = root.joints.createInput(gear_geom, cc_geom)
            joint_input.isFlipped = flip
            joint_input.offset = adsk.core.ValueInput.createByReal(offset_cm)
            joint_input.setAsRevoluteJointMotion(adsk.fusion.JointDirections.ZAxisJointDirection)
            joint = root.joints.add(joint_input)
            if joint is None:
                raise RuntimeError('Joints.add returned null')
            return joint

        # `occ` is the gear's root-context proxy; its native occurrence's (group-local)
        # transform is the one that can be put back.
        native = occ.nativeObject or occ
        before = native.transform2
        joint = _add(False)
        if occ.transform2.getAsCoordinateSystem()[3].dotProduct(normal) < 0.999:
            joint.deleteMe()                 # doesn't put the gear back (LESSONS_LEARNED.md)
            native.transform2 = before
            joint = _add(True)
        if abs(offset_cm) > 1e-9:
            moved = normal.dotProduct(center.vectorTo(occ.transform2.translation.asPoint()))
            if abs(moved + offset_cm) < abs(moved - offset_cm):
                joint.offset.value = -joint.offset.value   # went the wrong way
        joint.name = f'{name}_cc_revolute'
        _hide_joint(joint)
        return joint
    except Exception:
        futil.handle_error(f'PartsGen: C-C joint for {name}', show_message_box=not is_preview)
    return None


# ---------------------------------------------------------------------------
# Dialog entry points — called by PartsGen/entry.py
# ---------------------------------------------------------------------------

def _offset_face(inputs: adsk.core.CommandInputs):
    """The picked Offset From face, or None."""
    sel: adsk.core.SelectionCommandInput = inputs.itemById('gear_offset_face')
    if sel is None or sel.selectionCount == 0:
        return None
    try:
        return sel.selection(0).entity
    except RuntimeError:
        return None     # the count can run ahead of the indexer mid-click (LESSONS_LEARNED.md)


def _offset_side(inputs: adsk.core.CommandInputs) -> str:
    inp: adsk.core.DropDownCommandInput = inputs.itemById('gear_offset_side')
    return (inp.selectedItem.name if inp is not None and inp.selectedItem is not None
            else OFFSET_SIDE_BOTTOM)


def _bore_type(inputs: adsk.core.CommandInputs, i: int) -> str:
    inp: adsk.core.DropDownCommandInput = inputs.itemById(f'gear_bore_type_{i}')
    return (inp.selectedItem.name if inp is not None and inp.selectedItem is not None
            else BORE_HALF_HEX)


def _select_bore_type(inputs: adsk.core.CommandInputs, i: int, bore_type: str):
    inp: adsk.core.DropDownCommandInput = inputs.itemById(f'gear_bore_type_{i}')
    if inp is None:
        return
    for j in range(inp.listItems.count):
        item = inp.listItems.item(j)
        if item.name == bore_type:
            item.isSelected = True


def _custom_name(inputs: adsk.core.CommandInputs) -> str:
    inp = inputs.itemById('custom_name')
    return inp.value.strip() if inp is not None else ''


def selected_pitch_circles(inputs: adsk.core.CommandInputs):
    """The two picked pitch circles, or None."""
    sel: adsk.core.SelectionCommandInput = inputs.itemById('gear_pitch_circles')
    if sel is None or sel.selectionCount < 2:
        return None
    try:
        circles = [sel.selection(i).entity for i in range(2)]
    except RuntimeError:
        return None     # the count can run ahead of the indexer mid-click (LESSONS_LEARNED.md)
    if any(adsk.fusion.SketchCircle.cast(c) is None for c in circles):
        return None
    return circles


def create_gears(inputs: adsk.core.CommandInputs, is_preview: bool = False) -> bool:
    """Build a C-C gear pair if two pitch circles are picked, else one standalone gear."""
    if selected_pitch_circles(inputs) is not None:
        return _create_gear_pair(inputs, is_preview)
    return _create_gear(inputs, is_preview)


def _new_occurrence(world_transform: adsk.core.Matrix3D, is_preview: bool,
                    parent_occ: adsk.fusion.Occurrence = None):
    """A root-context occurrence in `parent_occ`, or else the active component."""
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        return futil.add_occurrence_in_active(design, world_transform, parent_occ)
    except RuntimeError:
        _warn('Cannot create gear: this document is in Part Design mode, which only '
              'supports a single component.\n\nPlease open or create an Assembly document '
              'and try again.', is_preview)
        return None


def _create_gear(inputs: adsk.core.CommandInputs, is_preview: bool = False) -> bool:
    """One gear at the origin from the Tooth Count input (no joints)."""
    toothInp: adsk.core.ValueCommandInput = inputs.itemById('gear_tooth_count')
    n_teeth = int(round(toothInp.value)) if toothInp is not None else 0
    bore_type = _bore_type(inputs, 1)
    if not gear_fits(n_teeth, bore_type):
        _warn(f'Parts Gen: a {n_teeth}T gear is too small for a {bore_type} bore '
              f'(needs {min_teeth(bore_type)}T or more).', is_preview)
        return False

    design = adsk.fusion.Design.cast(app.activeProduct)
    start_marker = design.timeline.markerPosition
    occ = _new_occurrence(None, is_preview)
    if occ is None:
        return False
    comp = occ.component
    try:
        custom_name = _custom_name(inputs)
        comp.name = comp_name = custom_name or _gear_name(n_teeth, bore_type)
        _build_gear(comp, n_teeth, n_teeth, bore_type, is_preview)
        _save_attributes(comp, n_teeth, n_teeth, bore_type, custom_name)
        futil.group_timeline_features(design, start_marker, comp_name)
        return True
    except Exception:
        try:
            occ.deleteMe()
        except Exception:
            pass
        futil.handle_error('PartsGen _create_gear', show_message_box=not is_preview)
        return False


def _create_gear_pair(inputs: adsk.core.CommandInputs, is_preview: bool = False) -> bool:
    """Both gears of a C-C line, built inside one group component and each
    revolute-jointed to its pitch circle."""
    circles = selected_pitch_circles(inputs)
    jointOffInp: adsk.core.ValueCommandInput = inputs.itemById('gear_joint_offset')
    offset_dist_cm    = jointOffInp.value if jointOffInp is not None else 0.0
    joint_offset_expr = jointOffInp.expression if jointOffInp is not None else '0 in'
    offset_face = _offset_face(inputs)
    offset_side = _offset_side(inputs)
    custom_name = _custom_name(inputs)

    specs = []
    for i, circle in enumerate(circles):
        n, label = gear_teeth_for_circle(circle)
        bore_type = _bore_type(inputs, i + 1)
        if not gear_fits(n, bore_type):
            _warn(f'Parts Gen: Gear {i + 1} ({n}T) is too small for a {bore_type} bore '
                  f'(needs {min_teeth(bore_type)}T or more).', is_preview)
            return False
        joint_offset_cm = _gear_joint_offset_cm(circle, offset_face, offset_dist_cm,
                                                offset_side)
        if joint_offset_cm is None:
            _warn('Parts Gen: the "Offset From" face must be parallel to the C-C sketch.',
                  is_preview)
            return False
        specs.append((circle, n, label, bore_type, joint_offset_cm))

    n1, n2 = specs[0][2], specs[1][2]
    group_name = custom_name or f'Gears_20DP-{n1}T-{n2}T'
    design = adsk.fusion.Design.cast(app.activeProduct)
    start_marker = design.timeline.markerPosition
    group_occ = _new_occurrence(None, is_preview)
    if group_occ is None:
        return False
    try:
        group_occ.component.name = group_name
        for i, (circle, n, label, bore_type, joint_offset_cm) in enumerate(specs):
            center, normal = _circle_world_frame(circle)
            occ = _new_occurrence(_placement(center, normal), is_preview,
                                  parent_occ=group_occ)
            if occ is None:
                raise RuntimeError('could not add the gear occurrence')
            comp = occ.component
            name = (f'{custom_name}_{i + 1}' if custom_name
                    else _gear_name(label, bore_type))
            comp.name = name
            joint_circle = _build_gear(comp, n, label, bore_type, is_preview)
            _save_attributes(comp, n, label, bore_type, name if custom_name else '', circle,
                             joint_offset_expr, offset_face, offset_side)
            # The joint lives in the root (the C-C circle is outside the group); `occ` is
            # already the gear's root-context proxy through the group.
            _add_gear_cc_joint(occ, joint_circle, circle, joint_offset_cm, is_preview)
        futil.group_timeline_features(design, start_marker, group_name)
        return True
    except Exception:
        try:
            group_occ.deleteMe()
        except Exception:
            pass
        futil.handle_error('PartsGen _create_gear_pair', show_message_box=not is_preview)
        return False


def handle_gear_selection_changed(inputs: adsk.core.CommandInputs):
    """React to the gear_pitch_circles selection: any member of a C-C line (the line, a
    circle, its label) selects that line's two pitch circles; a non-gear C-C line is
    refused. Keeps the read-only Gear 1 / Gear 2 summary up to date."""
    sel: adsk.core.SelectionCommandInput = inputs.itemById('gear_pitch_circles')
    if sel is None:
        return
    try:
        count = sel.selectionCount
        if count in (1, 3):
            entity = sel.selection(count - 1).entity
            cc = getCCLineFromEntity(entity)
            sel.clearSelection()
            if cc is not None and cc.data is not None and cc.data.motion == MOTION_GEARS_20DP:
                sel.addSelection(cc.pitchCircle1)
                sel.addSelection(cc.pitchCircle2)
                for i, circle in enumerate((cc.pitchCircle1, cc.pitchCircle2)):
                    _select_bore_type(inputs, i + 1, default_bore_for_circle(circle))
                faceInp = inputs.itemById('gear_offset_face')
                if faceInp is not None and faceInp.selectionCount == 0:
                    faceInp.hasFocus = True     # next click picks the Offset From face
            elif cc is not None:
                futil.popup_error('Parts Gen: the selected C-C Line is not a gear C-C.\n'
                                  'Please select a "Gears 20DP" C-C Distance sketch.')
            else:
                futil.popup_error('Parts Gen: please select a "Gears 20DP" C-C Line '
                                  '(made with the C-C Distance tool).')
    except Exception:
        futil.handle_error('PartsGen gear selection', show_message_box=False)
    update_gear_dialog(inputs)


def update_gear_dialog(inputs: adsk.core.CommandInputs):
    """Show the picked pair's tooth counts, and which inputs apply (pair vs standalone)."""
    circles = selected_pitch_circles(inputs)
    info: adsk.core.TextBoxCommandInput = inputs.itemById('gear_cc_info')
    toothInp = inputs.itemById('gear_tooth_count')
    if circles is not None:
        counts = [gear_teeth_for_circle(c)[1] for c in circles]
        text = f'Gear 1: {counts[0]}T    Gear 2: {counts[1]}T'
    else:
        text = 'No C-C Line: one standalone gear is built.'
    pair = circles is not None
    if info is not None:
        info.text = text
    if toothInp is not None:
        toothInp.isEnabled = not pair
    for input_id in ('gear_bore_type_2', 'gear_offset_face', 'gear_joint_offset',
                     'gear_offset_side'):
        inp = inputs.itemById(input_id)
        if inp is not None:
            inp.isEnabled = pair


# ---------------------------------------------------------------------------
# In-place update (C-C sync and right-click Edit)
# ---------------------------------------------------------------------------

def _gear_features(comp: adsk.fusion.Component):
    """(body extrude, [hub extrudes], bore cut or None, [label cuts]), or None if the
    component doesn't look like a generated gear."""
    body_ext, hub_exts, bore_ext, label_exts = None, [], None, []
    for f in comp.features.extrudeFeatures:
        op = f.operation
        if op == adsk.fusion.FeatureOperations.NewBodyFeatureOperation:
            if body_ext is not None:
                return None
            body_ext = f
        elif op == adsk.fusion.FeatureOperations.JoinFeatureOperation:
            hub_exts.append(f)
        elif op == adsk.fusion.FeatureOperations.CutFeatureOperation:
            extent = adsk.fusion.DistanceExtentDefinition.cast(f.extentOne)
            if extent is not None and abs(abs(extent.distance.value) - LABEL_ENGRAVE_CM) < 1e-6:
                label_exts.append(f)
            elif bore_ext is None:
                bore_ext = f
            else:
                return None
    if body_ext is None or len(hub_exts) != 2:
        return None
    return body_ext, hub_exts, bore_ext, label_exts


def _delete_cut(feature: adsk.fusion.ExtrudeFeature):
    """Delete a cut plus the sketch and construction plane it was made from."""
    sk = _feature_sketch(feature)
    plane = adsk.fusion.ConstructionPlane.cast(sk.referencePlane)
    feature.deleteMe()
    sk.deleteMe()
    if plane is not None:
        try:
            plane.deleteMe()
        except Exception:
            pass    # an origin plane


def _attr(comp: adsk.fusion.Component, name: str, default=None):
    a = comp.attributes.itemByName(ATTR_GROUP, name)
    return a.value if a is not None else default


def _set_attr(comp: adsk.fusion.Component, name: str, value: str):
    old = comp.attributes.itemByName(ATTR_GROUP, name)
    if old is not None:
        old.deleteMe()
    comp.attributes.add(ATTR_GROUP, name, value)


def update_gear(occ: adsk.fusion.Occurrence, n_teeth: int, label_teeth: int,
                bore_type: str, design: adsk.fusion.Design,
                custom_name: str = None) -> bool:
    """Change a generated gear by editing its features in place: re-dimension the OD,
    pitch and hub circles, re-cut the bore if its type changed, re-engrave the
    label. The occurrence, body and hub faces survive, so joints to them stay valid.

    No timeline roll-back is needed (there are no teeth to swap): circle diameters are
    driven through their dimension parameters, and the new bore/label land at the end of
    the timeline. `custom_name` None keeps the stored one. Returns False, changing nothing,
    when the gear can't be updated this way (the bore wouldn't fit, unknown features)."""
    comp = occ.component
    if not gear_fits(n_teeth, bore_type):
        futil.log(f'PartsGen: {comp.name} not updated -- a {n_teeth}T gear is too small '
                  f'for a {bore_type} bore')
        return False
    feats = _gear_features(comp)
    if feats is None:
        futil.log(f'PartsGen: {comp.name} features not recognised, not updated')
        return False
    body_ext, hub_exts, bore_ext, label_exts = feats
    old_name = comp.name
    old_bore = _attr(comp, ATTR_GEAR_BORE_TYPE, BORE_HALF_HEX)
    timeline = design.timeline
    try:
        body_sk = _feature_sketch(body_ext)
        circles = sorted(body_sk.sketchCurves.sketchCircles, key=lambda c: c.isConstruction)
        if len(circles) != 2:
            return False
        od_circle, pd_circle = circles          # the construction circle sorts last
        _set_circle_diameter(body_sk, od_circle, _outer_dia_cm(n_teeth))
        _set_circle_diameter(body_sk, pd_circle, _pitch_dia_cm(n_teeth))
        hub_sk = _feature_sketch(hub_exts[0])
        _set_circle_diameter(hub_sk, hub_sk.sketchCurves.sketchCircles.item(0),
                             2 * _hub_radius_cm(n_teeth, bore_type))

        start = timeline.markerPosition
        if bore_ext is None or old_bore != bore_type:
            if bore_ext is not None:
                _delete_cut(bore_ext)
            _add_bore(comp, bore_type)
        for f in label_exts:
            _delete_cut(f)
        _add_label(comp, n_teeth, label_teeth, bore_type)
        _log_unconstrained(comp)
        for sk in comp.sketches:
            sk.isLightBulbOn = False
    except Exception:
        futil.handle_error(f'PartsGen: in-place update of {old_name}', show_message_box=False)
        return False

    try:
        _set_attr(comp, ATTR_GEAR_TOOTH_COUNT, str(n_teeth))
        _set_attr(comp, ATTR_GEAR_LABEL_TEETH, str(label_teeth))
        _set_attr(comp, ATTR_GEAR_BORE_TYPE, bore_type)
        cc_circle = _find_cc_circle(design, comp)
        if cc_circle is not None:
            _set_attr(comp, ATTR_GEAR_CC_PINION, '1' if is_cc_pinion(cc_circle) else '0')
        if custom_name is not None:
            old = comp.attributes.itemByName(ATTR_GROUP, ATTR_CUSTOM_NAME)
            if old is not None:
                old.deleteMe()
            if custom_name:
                comp.attributes.add(ATTR_GROUP, ATTR_CUSTOM_NAME, custom_name)
        name = _attr(comp, ATTR_CUSTOM_NAME) or _gear_name(label_teeth, bore_type)
        comp.name = name
        for joint in design.rootComponent.joints:
            occs = (joint.occurrenceOne, joint.occurrenceTwo)
            if ('_cc_revolute' in joint.name
                    and any(o is not None and o.component == comp for o in occs)):
                joint.name = f'{name}_cc_revolute'
        _rename_gear_group(occ)
        futil.group_timeline_features(design, start, f'{name} update')
    except Exception:
        futil.log(f'PartsGen: renaming {old_name} after its update failed')
    futil.log(f'PartsGen: {old_name} updated in place to {label_teeth}T {bore_type}')
    return True


def _rename_gear_group(occ: adsk.fusion.Occurrence):
    """Keep a C-C pair's group named for its gears' tooth counts, unless the user named
    it (a custom-named pair's group isn't called "Gears_20DP-...")."""
    group_occ = occ.assemblyContext
    if group_occ is None or not group_occ.component.name.startswith('Gears_20DP-'):
        return
    counts = [_attr(o.component, ATTR_GEAR_LABEL_TEETH) for o in group_occ.childOccurrences]
    if len(counts) == 2 and all(counts):
        group_occ.component.name = f'Gears_20DP-{counts[0]}T-{counts[1]}T'


def update_gear_from_dialog(occ: adsk.fusion.Occurrence, inputs: adsk.core.CommandInputs) -> bool:
    """Right-click Edit of a C-C gear: new bore/name, tooth count from its circle."""
    design = adsk.fusion.Design.cast(app.activeProduct)
    comp = occ.component
    n = int(_attr(comp, ATTR_GEAR_TOOTH_COUNT, '0'))
    label = int(_attr(comp, ATTR_GEAR_LABEL_TEETH, str(n)))
    circle = _find_cc_circle(design, comp)
    if circle is not None:
        n, label = gear_teeth_for_circle(circle)
    ok = update_gear(occ, n, label, _bore_type(inputs, 1), design, _custom_name(inputs))
    if not ok:
        futil.popup_error(f'Parts Gen: could not update {comp.name} in place -- see the '
                          'Text Command window. Is the bore too big for this gear?')
    return ok


def is_cc_gear(comp: adsk.fusion.Component) -> bool:
    return comp.attributes.itemByName(ATTR_GROUP, ATTR_GEAR_CC_CIRCLE) is not None


def _find_cc_circle(design: adsk.fusion.Design, comp: adsk.fusion.Component):
    token = _attr(comp, ATTR_GEAR_CC_CIRCLE)
    if not token:
        return None
    try:
        found = design.findEntityByToken(token)
        if found and len(found) > 0:
            return adsk.fusion.SketchCircle.cast(found[0])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# C-C sync — commandTerminated hook (same guards as belt_gen's)
# ---------------------------------------------------------------------------

_gear_sync_handlers = []
_gear_sync_registered = False
_gear_sync_running = False


def register_gear_sync():
    """Hook commandTerminated so C-C gears follow their pitch circles after any edit."""
    global _gear_sync_registered
    if _gear_sync_registered:
        return
    try:
        futil.add_handler(app.userInterface.commandTerminated, _on_command_terminated,
                          local_handlers=_gear_sync_handlers)
        _gear_sync_registered = True
    except Exception:
        futil.handle_error('PartsGen register_gear_sync', show_message_box=False)


def unregister_gear_sync():
    global _gear_sync_registered
    _gear_sync_handlers.clear()
    _gear_sync_registered = False


def _on_command_terminated(args: adsk.core.ApplicationCommandEventArgs):
    global _gear_sync_running
    if _gear_sync_running:
        return  # a command fired by the sync's own edits
    from .belt_gen import should_run_part_sync
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not should_run_part_sync(args, design):
            return
        _gear_sync_running = True
        _scan_and_update_gears(design)
    except Exception:
        pass  # must never interrupt normal Fusion operation
    finally:
        _gear_sync_running = False


def _scan_and_update_gears(design: adsk.fusion.Design):
    """Update every C-C gear whose pitch circle now calls for a different gear.
    Collects first, then edits (LESSONS_LEARNED.md: a change mid-walk ends the scan)."""
    gears = []
    root = design.rootComponent
    for occ in root.allOccurrences:
        try:
            comp = occ.component
            if is_cc_gear(comp) and all(g.component != comp for g in gears):
                gears.append(occ)
        except Exception:
            pass
    for occ in gears:
        try:
            if not occ.isValid:
                continue
            comp = occ.component
            circle = _find_cc_circle(design, comp)
            if circle is None:
                continue
            n, label = gear_teeth_for_circle(circle)
            if (str(n) == _attr(comp, ATTR_GEAR_TOOTH_COUNT)
                    and str(label) == _attr(comp, ATTR_GEAR_LABEL_TEETH)):
                continue
            bore_type = _sync_bore_type(comp, n, label, circle)
            if bore_type is not None:
                update_gear(occ, n, label, bore_type, design)
        except Exception:
            futil.handle_error('PartsGen gear sync', show_message_box=False)


# (gear uid, geometry count, engraved count) already warned about, so a pitch circle no
# bore fits pops up once instead of after every later command.
_gear_sync_warned = set()


def _sync_bore_type(comp: adsk.fusion.Component, n: int, label: int,
                    circle: adsk.fusion.SketchCircle):
    """The bore a synced gear should get. If its C-C end switched between motor pinion and
    plain gear, the new default (SplineXS / hex) when it fits; otherwise its own if it
    still fits; else the first BORE_TYPES bore that does (a C-C gear turned into a 12T
    motor pinion outgrows the 1/2in hex), or None -- after one warning -- when none fits."""
    bore_type = _attr(comp, ATTR_GEAR_BORE_TYPE, BORE_HALF_HEX)
    # Gears made before the pinion flag existed: a SplineXS bore means it was the pinion.
    was_pinion = _attr(comp, ATTR_GEAR_CC_PINION,
                       '1' if bore_type == BORE_SPLINEXS else '0') == '1'
    if is_cc_pinion(circle) != was_pinion:
        default = default_bore_for_circle(circle)
        if gear_fits(n, default):
            return default
    if gear_fits(n, bore_type):
        return bore_type
    key = (_attr(comp, ATTR_GEAR_UID) or comp.name, n, label)
    fallback = next((b for b in BORE_TYPES if gear_fits(n, b)), None)
    if key not in _gear_sync_warned:
        _gear_sync_warned.add(key)
        if fallback is not None:
            futil.popup_error(f'Parts Gen: a {label}T gear is too small for a {bore_type} '
                              f'bore, so {comp.name} was given a {fallback} bore. Change it '
                              'with right-click Edit if the part uses another bore.')
        else:
            futil.popup_error(f'Parts Gen: {comp.name} was not updated -- a {label}T gear '
                              f'(drawn at {n}T) is too small for any bore Parts Gen makes '
                              f'(min {min(min_teeth(b) for b in BORE_TYPES)}T).')
    return fallback
