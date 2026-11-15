import math
import adsk.core
import adsk.fusion
from ...lib import fusionAddInUtils as futil
from ..PartsGen.shaft_gen import (_insert_bearing, _native, _world_xform, _world_circle,
                                  _world_face_normal, _hide_joint, _occurrence_bodies_recursive,
                                  ATTR_GROUP as PG_ATTR_GROUP, ATTR_HARDWARE_PART)
from ..PartsGen.hardware_gen import (_hole_frame, _place_origin, _joint_origin, _offset_point,
                                     _washer_top_rim, _hardware_folder, _is_hardware_folder)
from . import shaper_data as data

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# FRC Shaper geometry -- no dialog code, so MCP scripts can drive it directly:
#   plan_stack(edge, opts)        -> how a bolt through that hole's plate stack is made up
#   build_stacks(edges, opts)     -> place them all (one timeline group)
#   find_motor_pattern(edge)      -> the motor mount a picked pilot/pattern hole belongs to
#   place_motor(pattern, name, opts)
# Parts go in the "Hardware" folder component (hardware_gen._hardware_folder) and are tagged
# like Parts Gen hardware, so the grip scan of a later run skips them.
# ---------------------------------------------------------------------------

IN = 2.54
ATTR_GROUP   = 'FRCTools_Shaper'
ATTR_GEN_KEY = 'gen_key'      # on a generated bolt/spacer component: what it is, for reuse

EPS_CM = 1e-3


class StackOptions:
    """What a bolt stack is made of. Lengths in inches."""
    def __init__(self, thread=data.THREAD_AUTO, head=data.HEAD_BUTTON, nut=data.NUT_LOCKNUT,
                 washer_head=True, washer_nut=True, spacers=True, max_gap=1.5, flip=False,
                 bolt_motor=True):
        self.thread      = thread
        self.head        = head
        self.nut         = nut
        self.washer_head = washer_head
        self.washer_nut  = washer_nut
        self.spacers     = spacers
        self.max_gap     = max_gap
        self.flip        = flip
        self.bolt_motor  = bolt_motor


# ===========================================================================
# Small vector helpers
# ===========================================================================

def _perp(v: adsk.core.Vector3D) -> adsk.core.Vector3D:
    p = v.crossProduct(adsk.core.Vector3D.create(1, 0, 0))
    if p.length < 1e-6:
        p = v.crossProduct(adsk.core.Vector3D.create(0, 1, 0))
    p.normalize()
    return p


def _along(p: adsk.core.Point3D, d: adsk.core.Vector3D, t: float,
           side: adsk.core.Vector3D = None, r: float = 0.0) -> adsk.core.Point3D:
    q = p.copy()
    q.translateBy(_scaled(d, t))
    if side is not None:
        q.translateBy(_scaled(side, r))
    return q


def _scaled(v: adsk.core.Vector3D, s: float) -> adsk.core.Vector3D:
    w = v.copy()
    w.scaleBy(s)
    return w


def _unit(a: adsk.core.Point3D, b: adsk.core.Point3D) -> adsk.core.Vector3D:
    v = a.vectorTo(b)
    v.normalize()
    return v


def _full_circle(edge):
    """The edge's native Circle3D (or Arc3D), or None."""
    geom = _native(edge).geometry
    return adsk.core.Circle3D.cast(geom) or adsk.core.Arc3D.cast(geom)


def _proxy(native_entity, context):
    return native_entity.createForAssemblyContext(context) if context is not None else native_entity


def _fmt_in(cm: float) -> str:
    return f'{cm / IN:.3f}"'


def _fmt_len(inches: float) -> str:
    """A stock length as a fraction: 1.25 -> 1-1/4"."""
    whole = int(inches + 1e-9)
    frac = inches - whole
    for den in (2, 4, 8, 16):
        num = round(frac * den)
        if abs(frac * den - num) < 1e-6:
            break
    if num == 0:
        return f'{whole}"'
    from math import gcd
    g = gcd(num, den)
    num, den = num // g, den // g
    return f'{num}/{den}"' if whole == 0 else f'{whole}-{num}/{den}"'


# ===========================================================================
# Grip scan: the plate stack under a hole
# ===========================================================================

def _scan_bodies(design: adsk.fusion.Design):
    """Every visible body in the design, as root-context proxies, skipping Parts Gen /
    Shaper hardware (earlier bolts and spacers sit in these holes too)."""
    root = design.rootComponent
    for body in root.bRepBodies:
        if body.isVisible:
            yield body

    def _walk(occs):
        for occ in occs:
            if not occ.isVisible:
                continue
            if (occ.attributes.itemByName(PG_ATTR_GROUP, ATTR_HARDWARE_PART) is not None
                    or _is_hardware_folder(occ.component)):
                continue
            for body in occ.bRepBodies:
                if body.isVisible:
                    yield body
            yield from _walk(occ.childOccurrences)
    yield from _walk(root.occurrences)


def _segment_hits_box(p, d, t0, t1, box: adsk.core.BoundingBox3D, pad: float) -> bool:
    lo = (box.minPoint.x - pad, box.minPoint.y - pad, box.minPoint.z - pad)
    hi = (box.maxPoint.x + pad, box.maxPoint.y + pad, box.maxPoint.z + pad)
    for pi, di, l, h in zip((p.x, p.y, p.z), (d.x, d.y, d.z), lo, hi):
        if abs(di) < 1e-12:
            if pi < l or pi > h:
                return False
            continue
        a, b = (l - pi) / di, (h - pi) / di
        t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
        if t0 > t1:
            return False
    return True


def _body_layers(body, center, into, hole_r, reach):
    """The stretches of `body`'s material that a bolt down this hole axis passes through:
    [(start t, end t, entry rim proxy, exit rim proxy)], t in cm along `into` from `center`.

    Rims are the body's circles on the axis (any hole size near the picked one, so a
    tapped or counterbored hole still counts). Between two neighbouring rims, a probe just
    outside the widest one says whether that stretch is material or air. Searched on the
    native body with the axis mapped into its frame (LESSONS_LEARNED.md: proxy geometry can
    read native in the live dialog)."""
    native = _native(body)
    to_native = _world_xform(body).copy()
    to_native.invert()
    p = center.copy()
    p.transformBy(to_native)
    d = into.copy()
    d.transformBy(to_native)
    d.normalize()
    if not _segment_hits_box(p, d, -0.05, reach, native.boundingBox, 3 * hole_r):
        return []

    rims = {}   # rounded t -> (t, radius, native edge)
    for edge in native.edges:
        geom = edge.geometry
        circle = adsk.core.Circle3D.cast(geom) or adsk.core.Arc3D.cast(geom)
        if circle is None or abs(circle.normal.dotProduct(d)) < 1 - 1e-4:
            continue
        if not 0.3 * hole_r <= circle.radius <= 3 * hole_r:
            continue
        v = p.vectorTo(circle.center)
        t = v.dotProduct(d)
        v.subtract(_scaled(d, t))
        if v.length > EPS_CM or t < -0.05 or t > reach:
            continue
        key = round(t, 4)
        if key not in rims or circle.radius < rims[key][1]:
            rims[key] = (t, circle.radius, edge)
    if len(rims) < 2:
        return []

    ctx = body.assemblyContext
    ordered = [rims[k] for k in sorted(rims)]
    probe_r = max(r for _, r, _ in ordered) + 0.02
    side = _perp(d)
    layers = []
    for (ta, _, ea), (tb, _, eb) in zip(ordered, ordered[1:]):
        probe = _along(p, d, (ta + tb) / 2, side, probe_r)
        if native.pointContainment(probe) != adsk.fusion.PointContainment.PointInsidePointContainment:
            continue
        if layers and abs(layers[-1][1] - ta) < EPS_CM:
            # Same material carries on past a step (counterbore, chamfer) -- one layer.
            layers[-1] = (layers[-1][0], tb, layers[-1][2], _proxy(eb, ctx))
        else:
            layers.append((ta, tb, _proxy(ea, ctx), _proxy(eb, ctx)))
    return layers


def scan_stack(edge, flip: bool = False, max_gap_cm: float = 1.5 * IN, reach_cm: float = 6 * IN):
    """The plates a bolt down the picked hole goes through, as a dict:
    center/into/radius (world; `into` points into the stack), `layers` [(t0, t1)], `gaps`
    [(t0, t1, rim the gap starts at)], `grip` (cm, picked face to far face) and `exit_edge`
    (the far face's hole rim), or None when there is no material under the hole.

    The stack carries on through every coaxial plate whose near face is within `max_gap_cm`
    of the last one's far face -- an air gap (box tube, standoff) becomes a spacer."""
    center, into, radius = _hole_frame(edge, flip)
    if center is None:
        return None
    design = adsk.fusion.Design.cast(app.activeProduct)
    layers = []
    for body in _scan_bodies(design):
        try:
            layers.extend(_body_layers(body, center, into, radius, reach_cm))
        except Exception:
            futil.log(f'FRC Shaper: skipped body {body.name} in the grip scan')
    layers.sort(key=lambda layer: layer[0])

    first = [layer for layer in layers if abs(layer[0]) < 2 * EPS_CM]
    if not first:
        return None
    start = max(first, key=lambda layer: layer[1])
    end, exit_edge = start[1], start[3]
    kept, gaps = [(0.0, end)], []
    for t0, t1, entry, exit_rim in layers:
        if t1 <= end + EPS_CM:
            continue
        if t0 < end - EPS_CM:          # overlapping bodies -- just carry on to the far one
            end, exit_edge = t1, exit_rim
            kept[-1] = (kept[-1][0], t1)
            continue
        gap = t0 - end
        if gap > max_gap_cm:
            break
        if gap > 0.01 * IN:
            gaps.append((end, t0, exit_edge))
            kept.append((t0, t1))
        else:
            kept[-1] = (kept[-1][0], t1)
        end, exit_edge = t1, exit_rim
    return dict(center=center, into=into, radius=radius, layers=kept, gaps=gaps,
                grip=end, exit_edge=exit_edge, edge=edge)


# ===========================================================================
# Bolt stacks
# ===========================================================================

def _thread_name(opts: StackOptions, hole_r_cm: float):
    if opts.thread in data.THREADS:
        return opts.thread
    return data.thread_for_hole(2 * hole_r_cm / IN)


def _bolt_length(thread: dict, grip_in: float, last_layer_in: float, opts: StackOptions):
    """(stock length in inches, or None if none is long enough, the minimum it needed)."""
    need = grip_in + (thread['washer']['thickness'] if opts.washer_head else 0.0)
    if opts.nut == data.NUT_LOCKNUT:
        need += (thread['washer']['thickness'] if opts.washer_nut else 0.0)
        need += thread['nut']['height'] + data.MIN_THREADS_PAST_NUT * thread['pitch']
    else:
        # Tapped last plate: thread into it, but don't need more than it has.
        need -= last_layer_in - min(last_layer_in, data.TAPPED_ENGAGEMENT)
    for length in thread['lengths']:
        if length >= need - 1e-6:
            return length, need
    return None, need


def plan_stack(edge, opts: StackOptions):
    """The bolt stack for one hole, as a dict (see `scan_stack`) plus `thread`, `length`,
    `need` and a one-line `text`; `error` is set instead when it can't be made."""
    scan = scan_stack(edge, opts.flip, opts.max_gap * IN)
    if scan is None:
        return dict(error='no plate found under the hole -- pick the rim on the face the bolt '
                          'head sits on')
    thread_name = _thread_name(opts, scan['radius'])
    if thread_name is None:
        scan['error'] = f'no bolt fits a {_fmt_in(2 * scan["radius"])} hole'
        return scan
    thread = data.THREADS[thread_name]
    last = scan['layers'][-1]
    length, need = _bolt_length(thread, scan['grip'] / IN, (last[1] - last[0]) / IN, opts)
    scan.update(thread=thread_name, length=length, need=need)
    if length is None:
        scan['error'] = (f'needs a {thread_name} bolt at least {need:.3f}" long, longer than '
                         f'the longest stock length ({_fmt_len(thread["lengths"][-1])})')
        return scan
    parts = [f'{thread_name} x {_fmt_len(length)} {opts.head.split()[0].lower()}']
    if opts.washer_head:
        parts.append('washer')
    if scan['gaps'] and opts.spacers:
        parts.append(' + '.join(f'{_fmt_in(t1 - t0)} spacer' for t0, t1, _ in scan['gaps']))
    if opts.nut == data.NUT_LOCKNUT:
        parts.append(('washer + ' if opts.washer_nut else '') + 'nylock')
    scan['text'] = ', '.join(parts) + f'  (grip {_fmt_in(scan["grip"])})'
    return scan


class _PartMaker:
    """Adds parts to the Hardware folder: library files by URN (one cloud insert per run,
    then `addExistingComponent` -- LESSONS_LEARNED.md) and generated bolts/spacers (reused
    across runs by their `gen_key` attribute)."""

    def __init__(self, folder: adsk.fusion.Occurrence):
        self.folder = folder
        self.native = folder.nativeObject or folder
        self.comps = {}

    def _existing(self, comp):
        return self.native.component.occurrences.addExistingComponent(
            comp, adsk.core.Matrix3D.create()).createForAssemblyContext(self.folder)

    def library(self, part: dict, label: str):
        comp = self.comps.get(part['urn'])
        if comp is not None and comp.isValid:
            occ = self._existing(comp)
        else:
            occ = _insert_bearing(self.folder, part, 'Nothing more was added.')
            if occ is None:
                return None
            self.comps[part['urn']] = occ.component
        occ.attributes.add(PG_ATTR_GROUP, ATTR_HARDWARE_PART, label)
        return occ

    def generated(self, key: str, name: str, build):
        comp = self.comps.get(key)
        if comp is None or not comp.isValid:
            design = adsk.fusion.Design.cast(app.activeProduct)
            comp = next((c for c in design.allComponents
                         if (a := c.attributes.itemByName(ATTR_GROUP, ATTR_GEN_KEY)) is not None
                         and a.value == key), None)
        if comp is not None and comp.isValid:
            occ = self._existing(comp)
        else:
            occ = self.native.component.occurrences.addNewComponent(
                adsk.core.Matrix3D.create()).createForAssemblyContext(self.folder)
            comp = occ.component
            comp.name = name
            build(comp)
            comp.attributes.add(ATTR_GROUP, ATTR_GEN_KEY, key)
        self.comps[key] = comp
        occ.attributes.add(PG_ATTR_GROUP, ATTR_HARDWARE_PART, name)
        return occ


def _cylinder(comp: adsk.fusion.Component, dia_cm: float, z0: float, z1: float,
              bore_cm: float = 0.0, join: bool = False):
    """Extrude a fully constrained circle (or ring) on the component's XY plane, centred on
    its origin, from z0 to z1 (cm)."""
    sketch = comp.sketches.add(comp.xYConstructionPlane)
    sketch.isComputeDeferred = True
    origin = adsk.core.Point3D.create(0, 0, 0)
    for i, dia in enumerate(d for d in (dia_cm, bore_cm) if d > 0):
        circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(origin, dia / 2)
        sketch.geometricConstraints.addCoincident(circle.centerSketchPoint, sketch.originPoint)
        sketch.sketchDimensions.addDiameterDimension(
            circle, adsk.core.Point3D.create(dia / 2 + 0.2 * (i + 1), 0.2 * (i + 1), 0))
    sketch.isComputeDeferred = False
    if not sketch.isFullyConstrained:
        futil.log(f'FRC Shaper: sketch in {comp.name} is not fully constrained')
    sketch.isVisible = False
    profile = (max(sketch.profiles, key=lambda p: p.profileLoops.count) if bore_cm > 0
               else sketch.profiles.item(0))
    op = (adsk.fusion.FeatureOperations.JoinFeatureOperation if join
          else adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, op)
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(z0))
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(z1 - z0))
    return extrudes.add(ext_input)


def _bolt_part(maker: _PartMaker, thread_name: str, head: str, length: float):
    """The bolt occurrence: the library file when there is one this length, else a
    generated stand-in (head + shank, origin on the seat, +Z up the head)."""
    thread = data.THREADS[thread_name]
    label = f'{thread_name} x {_fmt_len(length)} {head}'
    lib = thread['bolts'].get((head, length))
    if lib is not None:
        return maker.library(lib, label)
    dims = thread['heads'][head]

    def build(comp):
        _cylinder(comp, dims['dia'] * IN, 0.0, dims['height'] * IN)
        _cylinder(comp, thread['major'] * IN, -length * IN, 0.0, join=True)
    key = f'bolt|{thread_name}|{head}|{length}'
    return maker.generated(
        key, f'Bolt_{thread_name}_{_fmt_len(length).rstrip(chr(34))}_{head.split()[0]}', build)


def _spacer_part(maker: _PartMaker, thread_name: str, length_cm: float):
    spec = data.THREADS[thread_name]['spacer']
    length_in = round(length_cm / IN, 3)

    def build(comp):
        _cylinder(comp, spec['od'] * IN, 0.0, length_in * IN, bore_cm=spec['id'] * IN)
    key = f'spacer|{thread_name}|{length_in}'
    return maker.generated(key, f'Spacer_{spec["od"]}OD_{length_in:.3f}in', build)


def _seat(occ, rim, base, out, made: list, name: str):
    """Place `occ` (origin on `base`, +Z along `out`), rigidly joint it to `rim`, and
    remember it so a failed hole can be cleaned up."""
    made.append(occ)
    _place_origin(occ, base, out)
    _joint_origin(occ, rim, f'{name}_joint', base, out)


def _place_bolt(maker, made, thread_name, opts, rim, base, out, length):
    """Washer (optional) and bolt with the head on `rim`'s face, shank down -`out`."""
    thread = data.THREADS[thread_name]
    if opts.washer_head:
        washer = maker.library(thread['washer'], f'{thread_name} Washer')
        if washer is None:
            raise RuntimeError('washer file not found')
        _seat(washer, rim, base, out, made, washer.name)
        rim, base = _washer_top_rim(washer, base, out)
        if rim is None:
            raise RuntimeError('washer top rim not found')
    bolt = _bolt_part(maker, thread_name, opts.head, length)
    if bolt is None:
        raise RuntimeError('bolt file not found')
    _seat(bolt, rim, base, out, made, bolt.name)


def _place_stack(maker: _PartMaker, plan: dict, opts: StackOptions, made: list):
    center, into = plan['center'], plan['into']
    out = _scaled(into, -1)
    thread_name = plan['thread']
    thread = data.THREADS[thread_name]
    _place_bolt(maker, made, thread_name, opts, plan['edge'], center, out, plan['length'])

    if opts.spacers:
        for t0, t1, rim in plan['gaps']:
            spacer = _spacer_part(maker, thread_name, t1 - t0)
            _seat(spacer, rim, _offset_point(center, into, t0), into, made, spacer.name)

    if opts.nut == data.NUT_LOCKNUT:
        rim, base = plan['exit_edge'], _offset_point(center, into, plan['grip'])
        if opts.washer_nut:
            washer = maker.library(thread['washer'], f'{thread_name} Washer')
            if washer is None:
                raise RuntimeError('washer file not found')
            _seat(washer, rim, base, into, made, washer.name)
            rim, base = _washer_top_rim(washer, base, into)
            if rim is None:
                raise RuntimeError('washer top rim not found')
        nut = maker.library(thread['nut'], f'{thread_name} Nylock')
        if nut is None:
            raise RuntimeError('nut file not found')
        _seat(nut, rim, base, into, made, nut.name)


def build_stacks(edges, opts: StackOptions):
    """Plan and place a bolt stack in every picked hole, in one timeline group. Returns
    (number placed, [(pick number, reason)] for the ones that failed)."""
    design = adsk.fusion.Design.cast(app.activeProduct)
    plans = [plan_stack(edge, opts) for edge in edges]   # every scan before anything changes
    start_marker = design.timeline.markerPosition
    folder, created = _hardware_folder(design)
    maker = _PartMaker(folder)
    placed, failed = 0, []
    for i, plan in enumerate(plans):
        if plan.get('error'):
            failed.append((i + 1, plan['error']))
            continue
        made = []
        try:
            _place_stack(maker, plan, opts, made)
            placed += 1
        except Exception as e:
            futil.handle_error(f'FRC Shaper: bolt stack in hole {i + 1}')
            failed.append((i + 1, str(e)))
            for occ in reversed(made):
                try:
                    occ.deleteMe()
                except Exception:
                    pass
    if placed == 0 and created and folder.isValid and folder.childOccurrences.count == 0:
        folder.deleteMe()
    futil.group_timeline_features(design, start_marker, 'FRCShaper_Bolts')
    return placed, failed


# ===========================================================================
# Motors
# ===========================================================================

def _angle_about(u: adsk.core.Vector3D, v: adsk.core.Vector3D, n: adsk.core.Vector3D) -> float:
    return math.atan2(u.crossProduct(v).dotProduct(n), u.dotProduct(v))


def _fits_spacing(center, holes, normal, count) -> bool:
    """Every hole sits on one of `count` evenly spaced positions (relative to the first)."""
    step = 2 * math.pi / count
    u0 = _unit(center, holes[0])
    for h in holes[1:]:
        k = _angle_about(u0, _unit(center, h), normal) / step
        if abs(k - round(k)) * step > math.radians(1.5):
            return False
    return True


def _planar_neighbour(native_edge, normal):
    for face in native_edge.faces:
        plane = adsk.core.Plane.cast(face.geometry)
        if plane is not None and abs(plane.normal.dotProduct(normal)) > 1 - 1e-6:
            return face
    return None


def find_motor_pattern(edge, flip: bool = False):
    """The motor mount the picked rim (its pilot hole or one of its bolt holes) belongs to,
    as a dict -- `center`, `into` (world), `pilot_edge`, `holes` [(world centre, rim
    proxy)], `motors` (names that fit, best first) -- or None.

    The mount is a pilot circle on the picked face with holes around it at one motor's
    bolt-circle radius, every one on that motor's evenly spaced positions. Of the motors
    that fit, ones with a model come first, then the one with the fewest positions (the
    most specific: six holes at 60 deg is a Falcon 500 before it's a Kraken X60)."""
    circle = _full_circle(edge)
    if circle is None:
        return None
    native = _native(edge)
    face = _planar_neighbour(native, circle.normal)
    if face is None:
        return None
    xf = _world_xform(edge)
    ctx = getattr(edge, 'assemblyContext', None)
    normal = circle.normal.copy()
    normal.transformBy(xf)
    normal.normalize()

    circles = []
    for e in face.edges:
        c = adsk.core.Circle3D.cast(e.geometry)
        if c is None:
            continue
        w = c.center.copy()
        w.transformBy(xf)
        circles.append((w, 2 * c.radius / IN, _proxy(e, ctx)))
    picked_center, _ = _world_circle(edge)
    lo, hi = data.MOTOR_HOLE_DIA_RANGE
    holes = [(w, e) for w, dia, e in circles if lo <= dia <= hi]

    groups = []    # (pilot edge, [(rank, motor name, holes on its bolt circle)])
    for pc, pdia, pedge in circles:
        fits = []
        for name, motor in data.MOTORS.items():
            if abs(pdia - motor['pilot']) > data.MOTOR_DIA_TOL:
                continue
            radius = motor['pattern'] / 2
            on = [(w, e) for w, e in holes
                  if abs(pc.distanceTo(w) / IN - radius) <= data.MOTOR_HOLE_TOL]
            if len(on) < 2:
                continue
            if (picked_center.distanceTo(pc) > EPS_CM
                    and all(picked_center.distanceTo(w) > EPS_CM for w, _ in on)):
                continue   # a different motor's mount on the same face
            if not _fits_spacing(pc, [w for w, _ in on], normal, motor['count']):
                continue
            fits.append(((motor['urn'] is None, -len(on), motor['count']), name, on))
        if fits:
            groups.append((pedge, sorted(fits, key=lambda f: f[0])))
    if not groups:
        return None
    pilot_edge, fits = max(groups, key=lambda g: len(g[1][0][2]))
    center, into, _ = _hole_frame(pilot_edge, flip)
    return dict(center=center, into=into, pilot_edge=pilot_edge,
                motors=[name for _, name, _ in fits],
                holes_by_motor={name: on for _, name, on in fits})


def _circumcenter(a, b, c):
    """Centre of the circle through three world points, or None if they're in a line."""
    ab, ac = a.vectorTo(b), a.vectorTo(c)
    n = ab.crossProduct(ac)
    if n.length < 1e-9:
        return None
    t1 = n.crossProduct(ab)
    t1.scaleBy(ac.dotProduct(ac))
    t2 = ac.crossProduct(n)
    t2.scaleBy(ab.dotProduct(ab))
    t1.add(t2)
    t1.scaleBy(1 / (2 * n.dotProduct(n)))
    center = a.copy()
    center.translateBy(t1)
    return center


def _motor_mount(occ: adsk.fusion.Occurrence, motor: dict):
    """(world pattern centre, world outward normal, [(hole centre, hole rim proxy)]) of the
    motor model's mounting face, found by geometry so any vendor file works: a planar face
    with 3+ equal small holes on the motor's bolt circle. When several faces qualify (front
    and back of a flange), the one in front of the others along its own normal wins."""
    candidates = []
    for body in _occurrence_bodies_recursive(occ):
        for face in body.faces:
            if adsk.core.Plane.cast(_native(face).geometry) is None:
                continue
            by_dia = {}
            for e in face.edges:
                c = adsk.core.Circle3D.cast(_native(e).geometry)
                if c is None:
                    continue
                dia = 2 * c.radius / IN
                if 0.12 <= dia <= 0.26:
                    w, _ = _world_circle(e)
                    by_dia.setdefault(round(dia, 3), []).append((w, e))
            for group in by_dia.values():
                if len(group) < 3:
                    continue
                pts = [w for w, _ in group]
                center = None
                for i in range(1, len(pts) - 1):
                    center = _circumcenter(pts[0], pts[i], pts[i + 1])
                    if center is not None:
                        break
                if center is None:
                    continue
                radius = motor['pattern'] / 2 * IN
                if any(abs(center.distanceTo(p) - radius) > data.MOTOR_HOLE_TOL * IN for p in pts):
                    continue
                candidates.append((center, _world_face_normal(face), group))
    if not candidates:
        return None

    def _in_front(cand):
        c, n, _ = cand
        return sum(1 for o, _, _ in candidates if c.vectorTo(o).dotProduct(n) <= 1e-4)
    return max(candidates, key=lambda cand: (_in_front(cand), len(cand[2])))


def _frame_xform(center, x, z):
    m = adsk.core.Matrix3D.create()
    y = z.crossProduct(x)
    y.normalize()
    m.setWithCoordinateSystem(center, x, y, z)
    return m


def _align(src_center, src_x, src_z, dst_center, dst_x, dst_z) -> adsk.core.Matrix3D:
    """World transform taking the source frame onto the destination frame."""
    src = _frame_xform(src_center, src_x, src_z)
    src.invert()
    dst = _frame_xform(dst_center, dst_x, dst_z)
    src.transformBy(dst)      # dst * src^-1
    return src


def _joint_keep(occ, part_edge, target_edge, name: str):
    """Rigidly joint the centre of `part_edge` (on the part) to the centre of `target_edge`
    without moving the part off its placement -- position, axis *and* clocking. A rigid
    joint snaps the part's spin to the edges' own X axes, so after the first try the spin
    it lost is measured and put back with `JointInput.angle` (LESSONS_LEARNED.md)."""
    root = adsk.fusion.Design.cast(app.activeProduct).rootComponent
    placed = occ.transform2
    o0, x0, _, z0 = placed.getAsCoordinateSystem()
    x0.normalize()
    z0.normalize()

    def _add(flip, angle):
        ji = root.joints.createInput(
            adsk.fusion.JointGeometry.createByCurve(part_edge, adsk.fusion.JointKeyPointTypes.CenterKeyPoint),
            adsk.fusion.JointGeometry.createByCurve(target_edge, adsk.fusion.JointKeyPointTypes.CenterKeyPoint))
        ji.isFlipped = flip
        ji.setAsRigidJointMotion()
        if angle:
            ji.angle = adsk.core.ValueInput.createByReal(angle)
        return root.joints.add(ji)

    joint = None
    for flip in (False, True):
        tries = [0.0]
        i = 0
        while i < len(tries):
            if joint is not None:
                joint.deleteMe()
                occ.transform2 = placed
            joint = _add(flip, tries[i])
            o, x, _, z = occ.transform2.getAsCoordinateSystem()
            x.normalize()
            z.normalize()
            if z.dotProduct(z0) > 1 - 1e-6:
                if x.dotProduct(x0) > 1 - 1e-6 and o.distanceTo(o0) < 1e-3:
                    joint.name = name
                    _hide_joint(joint)
                    return joint
                # The joint sits on a bolt hole, not the part's origin, so a lost spin
                # moves the origin too -- measure the spin alone and put it back.
                if i == 0:
                    lost = _angle_about(x, x0, z0)
                    tries += [lost, -lost]
            i += 1
    futil.log(f'FRC Shaper: {name} moved the motor every way; kept the last try')
    joint.name = name
    _hide_joint(joint)
    return joint


def _as_built_rigid(design: adsk.fusion.Design, occ, other_occ, name: str):
    """Lock `occ` to `other_occ` where it sits now. A joint on the motor's own hole edges
    breaks for vendor models whose holes live in a sub-component of the linked file (the
    Falcon 500: the whole motor jumped ~9 in, whatever the flip/angle). The placement made
    by `transform2` must be captured in a snapshot first, or the as-built joint locks the
    part back at its insert position (LESSONS_LEARNED.md)."""
    if design.snapshots.hasPendingSnapshot:
        design.snapshots.add()
    joints = design.rootComponent.asBuiltJoints
    ji = joints.createInput(occ, other_occ, None)
    ji.setAsRigidJointMotion()
    joint = joints.add(ji)
    joint.name = name
    _hide_joint(joint)
    return joint


def _motor_parent(design: adsk.fusion.Design):
    """Where a motor goes: the active component (its parent when it's empty), or None for
    the root -- the same place the Hardware folder lives."""
    active = design.activeOccurrence
    if (active is not None and active.component.bRepBodies.count == 0
            and active.component.occurrences.count == 0):
        active = active.assemblyContext
    if active is not None and _is_hardware_folder(active.component):
        active = active.assemblyContext
    return active


def place_motor(pattern: dict, motor_name: str, opts: StackOptions):
    """Insert the motor's model, lined up on the mount (mounting face on the picked face,
    shaft through the pilot, bolt holes clocked onto the plate's), joint it, and optionally
    bolt it in from the far side. Returns (motor occurrence or None, message list)."""
    motor = data.MOTORS[motor_name]
    plate_holes = pattern['holes_by_motor'].get(motor_name) or next(iter(pattern['holes_by_motor'].values()))
    notes = []
    if not motor.get('urn'):
        return None, [f'There is no {motor_name} model in {data.MOTORS_PATH} yet.']
    try:
        found = app.data.findFileById(motor['urn']) is not None
    except RuntimeError:     # raises "file not found" rather than returning None
        found = False
    if not found:
        return None, [f'Could not find the {motor["file"]} file ({data.MOTORS_PATH}) -- check '
                      'that Fusion is online and you can open that project.']
    design = adsk.fusion.Design.cast(app.activeProduct)
    start_marker = design.timeline.markerPosition
    occ = _insert_bearing(_motor_parent(design), dict(motor, folder=None), '')
    if occ is None:
        return None, [f'Could not insert the {motor_name}.']
    occ.attributes.add(ATTR_GROUP, 'motor', motor_name)
    try:
        mount = _motor_mount(occ, motor)
        if mount is None:
            raise RuntimeError(f'no {motor["pattern"]}" bolt circle found on the {motor["file"]} model')
        m_center, m_normal, m_holes = mount
        m_x = _unit(m_center, m_holes[0][0])
        best = None
        for p_center, p_edge in plate_holes:
            xform = _align(m_center, m_x, m_normal, pattern['center'],
                           _unit(pattern['center'], p_center), pattern['into'])
            hits = 0
            for w, _ in m_holes:
                q = w.copy()
                q.transformBy(xform)
                if any(q.distanceTo(p) < data.MOTOR_HOLE_TOL * IN for p, _ in plate_holes):
                    hits += 1
            if best is None or hits > best[0]:
                best = (hits, xform, p_edge)
        hits, xform, p_edge = best
        moved = occ.transform2.copy()
        moved.transformBy(xform)
        occ.transform2 = moved
        if hits < len(plate_holes):
            notes.append(f'{len(plate_holes) - hits} plate hole(s) don\'t line up with a '
                         f'{motor_name} hole.')
        plate_occ = getattr(p_edge, 'assemblyContext', None)
        if plate_occ is not None:
            _as_built_rigid(design, occ, plate_occ, f'{motor_name}_joint')
        else:
            # Plate is a root body: no occurrence to as-build to, so joint hole to hole.
            m_edge = min(m_holes, key=lambda h: _world_circle(h[1])[0].distanceTo(
                _world_circle(p_edge)[0]))[1]
            _joint_keep(occ, m_edge, p_edge, f'{motor_name}_joint')

        if opts.bolt_motor:
            notes.extend(_bolt_motor(plate_holes, opts))
    except Exception as e:
        futil.handle_error(f'FRC Shaper: place {motor_name}')
        try:
            occ.deleteMe()
        except Exception:
            pass
        return None, [f'Could not place the {motor_name}: {e}']
    futil.group_timeline_features(design, start_marker, f'FRCShaper_{motor_name.replace(" ", "")}')
    return occ, notes


def _bolt_motor(plate_holes, opts: StackOptions):
    """A bolt (and washer) up through each plate hole into the motor's tapped holes, the
    head on the plate's far face. Length: through the touching plates + thread engagement."""
    notes = []
    design = adsk.fusion.Design.cast(app.activeProduct)
    folder, created = _hardware_folder(design)
    maker = _PartMaker(folder)
    placed = 0
    for _, edge in plate_holes:
        made = []
        try:
            scan = scan_stack(edge, False, 0.0)
            if scan is None:
                raise RuntimeError('no plate under the hole')
            thread_name = _thread_name(opts, scan['radius']) or '10-32'
            thread = data.THREADS[thread_name]
            need = (scan['grip'] / IN + data.TAPPED_ENGAGEMENT
                    + (thread['washer']['thickness'] if opts.washer_head else 0.0))
            length = next((n for n in thread['lengths'] if n >= need - 1e-6), None)
            if length is None:
                raise RuntimeError(f'needs a bolt {need:.3f}" long')
            base = _offset_point(scan['center'], scan['into'], scan['grip'])
            _place_bolt(maker, made, thread_name, opts, scan['exit_edge'], base,
                        scan['into'], length)
            placed += 1
        except Exception as e:
            futil.handle_error('FRC Shaper: motor bolt')
            notes.append(f'A motor bolt was skipped: {e}')
            for occ in reversed(made):
                try:
                    occ.deleteMe()
                except Exception:
                    pass
    if placed == 0 and created and folder.isValid and folder.childOccurrences.count == 0:
        folder.deleteMe()
    return notes
