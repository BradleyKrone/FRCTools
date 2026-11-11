# Tab & Slot geometry -- no dialog code, so it can be driven from an MCP test script.
#
# A "joint" is the tab plate's end face (fa) lying flat on the slot plate's face (fb).
# Everything is worked out in world space in a joint frame:
#   d  along the joint (the tab plate's edge)
#   n  normal to the slot plate's face, pointing INTO the slot plate
#   w  = n x d, across the tab plate's thickness
#   origin: on the contact plane, at the start of the shared span (s = 0) and the
#           tab plate's -w face (w = 0). The tab plate spans w in [0, tA].
# Features are built in each body's own (native) component, so world points are mapped
# back through the inverse of the body's occurrence transform before they are sketched.
#
# All lengths are cm (Fusion internal units).

import json
import math
import uuid
from dataclasses import dataclass, field

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil

IN_TO_CM = 2.54

# The dogbone relief is drawn a hair bigger than the bit so the CAM bit actually fits.
DOGBONE_EXTRA = 0.002 * IN_TO_CM

# Small extra depth on cuts so they clear the far face (cuts only touch participantBodies).
CUT_EXTRA = 0.01

_TOL = 1e-4


@dataclass
class Params:
    count: int = 2
    tab_width: float = 1.0 * IN_TO_CM
    margin: float = 0.5 * IN_TO_CM
    bit_dia: float = 0.25 * IN_TO_CM
    clear_w: float = 0.005 * IN_TO_CM      # slot width clearance, per side
    clear_l: float = 0.010 * IN_TO_CM      # slot length clearance, per side
    protrusion: float = 0.0
    corner_round: float = 0.03125 * IN_TO_CM   # fillet on the sharp points beside each dogbone; 0 = off

    @property
    def dogbone_r(self) -> float:
        return self.bit_dia / 2.0 + DOGBONE_EXTRA


@dataclass
class Joint:
    body_a: adsk.fusion.BRepBody          # native tab plate body
    body_b: adsk.fusion.BRepBody          # native slot plate body
    xf_a_inv: adsk.core.Matrix3D          # world -> tab plate's component
    xf_b_inv: adsk.core.Matrix3D          # world -> slot plate's component
    fa: adsk.fusion.BRepFace              # native tab plate end face
    fb: adsk.fusion.BRepFace              # native slot plate contact face
    broad_a: adsk.fusion.BRepFace         # native tab plate face the tab sketch goes on
    broad_w: float                        # w of broad_a (0 or tA)
    origin: adsk.core.Point3D             # world
    d: adsk.core.Vector3D                 # world
    w: adsk.core.Vector3D
    n: adsk.core.Vector3D
    length: float
    t_a: float
    t_b: float
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _native(entity):
    nat = entity.nativeObject
    return nat if nat is not None else entity


def _world_xf(entity) -> adsk.core.Matrix3D:
    """Native-to-world transform of a (proxy) body; identity for a root body. Same pattern as
    PartsGen shaft_gen._world_xform -- a deep proxy's own .geometry can't be trusted."""
    occ = getattr(entity, 'assemblyContext', None)
    return occ.transform2.copy() if occ is not None else adsk.core.Matrix3D.create()


def _xpt(pt: adsk.core.Point3D, xf: adsk.core.Matrix3D) -> adsk.core.Point3D:
    p = pt.copy()
    p.transformBy(xf)
    return p


def _xvec(v: adsk.core.Vector3D, xf: adsk.core.Matrix3D) -> adsk.core.Vector3D:
    out = v.copy()
    out.transformBy(xf)
    out.normalize()
    return out


def _true_normal(face: adsk.fusion.BRepFace) -> adsk.core.Vector3D:
    """A planar face's real outward normal (`Plane.normal` alone ignores isParamReversed)."""
    normal = adsk.core.Plane.cast(face.geometry).normal.copy()
    if face.isParamReversed:
        normal.scaleBy(-1)
    return normal


def _planar(face) -> bool:
    return adsk.core.Plane.cast(face.geometry) is not None


def _dot(a, b) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


def _sub(a: adsk.core.Point3D, b: adsk.core.Point3D) -> adsk.core.Vector3D:
    return adsk.core.Vector3D.create(a.x - b.x, a.y - b.y, a.z - b.z)


def _add(p: adsk.core.Point3D, *terms) -> adsk.core.Point3D:
    """p + sum(scale * vector) for (scale, vector) in terms."""
    x, y, z = p.x, p.y, p.z
    for s, v in terms:
        x += s * v.x
        y += s * v.y
        z += s * v.z
    return adsk.core.Point3D.create(x, y, z)


def _unit(*terms) -> adsk.core.Vector3D:
    v = adsk.core.Vector3D.create(0, 0, 0)
    for s, u in terms:
        v = adsk.core.Vector3D.create(v.x + s * u.x, v.y + s * u.y, v.z + s * u.z)
    v.normalize()
    return v


def _world_face(face, xf):
    """(world point on face, world true normal) for a native planar face."""
    return _xpt(face.pointOnFace, xf), _xvec(_true_normal(face), xf)


def _ranges(face, xf, base, axes):
    """[min, max] of the face's (world) vertices along each axis, relative to base."""
    out = [[math.inf, -math.inf] for _ in axes]
    for v in face.vertices:
        rel = _sub(_xpt(v.geometry, xf), base)
        for i, ax in enumerate(axes):
            c = _dot(rel, ax)
            out[i][0] = min(out[i][0], c)
            out[i][1] = max(out[i][1], c)
    return out


def _longest_line_dir(face, xf):
    best, best_len = None, 0.0
    for e in face.edges:
        line = adsk.core.Line3D.cast(e.geometry)
        if line is None:
            continue
        vec = _sub(line.endPoint, line.startPoint)
        if vec.length > best_len:
            best_len = vec.length
            best = vec
    return _xvec(best, xf) if best is not None else None


def is_editable(body_proxy) -> bool:
    """False when the body lives in a linked (referenced) component."""
    occ = getattr(body_proxy, 'assemblyContext', None)
    while occ is not None:
        if occ.isReferencedComponent:
            return False
        occ = occ.assemblyContext
    return True


# ---------------------------------------------------------------------------
# Joint detection
# ---------------------------------------------------------------------------

def find_joint(tab_body, slot_body):
    """The best butt joint between the tab plate's end face and the slot plate's face, or None.
    Both arguments are the picks (root-context proxies or root bodies)."""
    body_a, body_b = _native(tab_body), _native(slot_body)
    xf_a, xf_b = _world_xf(tab_body), _world_xf(slot_body)

    faces_a = [(f, *_world_face(f, xf_a)) for f in body_a.faces if _planar(f)]
    faces_b = [(f, *_world_face(f, xf_b)) for f in body_b.faces if _planar(f)]

    best = None
    for fa, pa, na in faces_a:
        for fb, pb, nb in faces_b:
            if _dot(na, nb) > -0.9999:
                continue                                    # not anti-parallel
            if abs(_dot(_sub(pa, pb), nb)) > _TOL:
                continue                                    # not coplanar
            j = _joint_from_pair(fa, fb, pa, na, body_a, body_b, xf_a, xf_b)
            if j is not None and (best is None or j.length > best.length):
                best = j
    return best


def _joint_from_pair(fa, fb, pa, na, body_a, body_b, xf_a, xf_b):
    n = na.copy()                                   # fa's outward normal points into the slot plate
    d = _longest_line_dir(fa, xf_a)
    if d is None:
        return None
    # Make d exactly in-plane.
    d = _unit((1.0, d), (-_dot(d, n), n))
    w = n.crossProduct(d)
    w.normalize()

    (sa0, sa1), (wa0, wa1) = _ranges(fa, xf_a, pa, (d, w))
    (sb0, sb1), (wb0, wb1) = _ranges(fb, xf_b, pa, (d, w))
    s0, s1 = max(sa0, sb0), min(sa1, sb1)
    t_a = wa1 - wa0
    if s1 - s0 < 10 * _TOL or t_a < 10 * _TOL:
        return None
    if min(wa1, wb1) - max(wa0, wb0) < t_a - _TOL:
        return None                                 # tab plate hangs off the slot plate sideways
    if t_a > (sa1 - sa0):
        return None                                 # fa isn't a thin end face

    origin = _add(pa, (s0, d), (wa0, w))

    # Tab plate face the tabs are sketched on: borders fa, normal +-w, bigger than fa.
    broad_a, broad_w = None, 0.0
    for e in fa.edges:
        for f in e.faces:
            if f == fa or not _planar(f):
                continue
            p, nn = _world_face(f, xf_a)
            if abs(abs(_dot(nn, w)) - 1.0) > 1e-4 or f.area <= fa.area:
                continue
            if broad_a is None or f.area > broad_a.area:
                broad_a = f
                broad_w = _dot(_sub(p, origin), w)
    if broad_a is None:
        return None

    # Slot plate thickness: nearest face of B facing +n, beyond the contact plane.
    t_b = None
    for f in body_b.faces:
        if not _planar(f):
            continue
        p, nn = _world_face(f, xf_b)
        if _dot(nn, n) < 0.9999:
            continue
        dist = _dot(_sub(p, origin), n)
        if dist > 10 * _TOL and (t_b is None or dist < t_b):
            t_b = dist
    if t_b is None:
        return None

    xf_a_inv, xf_b_inv = xf_a.copy(), xf_b.copy()
    xf_a_inv.invert()
    xf_b_inv.invert()
    return Joint(body_a=body_a, body_b=body_b, xf_a_inv=xf_a_inv, xf_b_inv=xf_b_inv,
                 fa=fa, fb=fb, broad_a=broad_a, broad_w=round(broad_w / t_a) * t_a,
                 origin=origin, d=d, w=w, n=n, length=s1 - s0, t_a=t_a, t_b=t_b,
                 extra={'b_w_range': (wb0 - wa0, wb1 - wa0)})


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def open_sides(joint: Joint, p: Params):
    """(low-w side open, high-w side open): a slot side closer than a bit diameter to the slot
    plate's edge, which is cut as an open notch instead of leaving an uncuttable web."""
    lo, hi = joint.extra.get('b_w_range', (-math.inf, math.inf))
    return (-p.clear_w - p.bit_dia < lo, joint.t_a + p.clear_w + p.bit_dia > hi)


def warnings(joint: Joint, p: Params):
    """Non-blocking notes shown in the dialog."""
    notes = []
    if any(open_sides(joint, p)):
        notes.append('Edge joint: the slots are cut as open notches through the slot plate\'s edge '
                     '(no dogbones on the open side).')
    return notes


def layout_tabs(joint: Joint, p: Params):
    """(list of tab centres along d, list of problems). Problems empty = buildable."""
    problems = []
    L, N = joint.length, p.count
    if N < 1:
        problems.append('Tab Count must be at least 1.')
    if p.tab_width <= p.bit_dia:
        problems.append('Tab Width must be bigger than the bit diameter.')
    if p.margin < p.bit_dia:
        problems.append('End Margin must be at least the bit diameter, or a slot can break out of the plate edge.')
    if p.bit_dia <= 0:
        problems.append('Bit diameter must be greater than 0.')
    if p.clear_w < 0 or p.clear_l < 0 or p.protrusion < 0 or p.corner_round < 0:
        problems.append('Clearances, protrusion and corner round cannot be negative.')
    slot_w = joint.t_a + 2.0 * p.clear_w
    if p.bit_dia >= slot_w:
        problems.append(f'The bit ({p.bit_dia / IN_TO_CM:.3f} in) won\'t fit in a {slot_w / IN_TO_CM:.3f} in '
                        f'wide slot -- pick a smaller bit.')
    if problems:
        return [], problems

    usable = L - 2.0 * p.margin
    min_gap = p.bit_dia + 2.0 * p.clear_l           # web left between neighbouring slots
    if N * p.tab_width + (N - 1) * min_gap > usable + _TOL:
        problems.append(f'{N} tabs of {p.tab_width / IN_TO_CM:.3f} in don\'t fit in '
                        f'{usable / IN_TO_CM:.3f} in (joint minus end margins).')
        return [], problems

    if N == 1:
        return [L / 2.0], problems
    step = (usable - p.tab_width) / (N - 1)
    first = p.margin + p.tab_width / 2.0
    return [first + k * step for k in range(N)], problems


# ---------------------------------------------------------------------------
# Sketching helpers
# ---------------------------------------------------------------------------

class _Sk:
    """A sketch plus the mapping from joint coordinates (s, w, n) to its sketch space."""

    def __init__(self, sketch, joint: Joint, xf_inv, constrain: bool):
        self.sketch = sketch
        self.j = joint
        self.xf_inv = xf_inv
        self.constrain = constrain

    def pt(self, s, wv, nv) -> adsk.core.Point3D:
        world = _add(self.j.origin, (s, self.j.d), (wv, self.j.w), (nv, self.j.n))
        p = self.sketch.modelToSketchSpace(_xpt(world, self.xf_inv))
        return adsk.core.Point3D.create(p.x, p.y, 0)

    def axis(self, wv):
        """Fixed construction line along the joint at w = wv (the anchor for every dimension)."""
        line = self.sketch.sketchCurves.sketchLines.addByTwoPoints(
            self.pt(0, wv, 0), self.pt(self.j.length, wv, 0))
        line.isConstruction = True
        # Fix the points: `line.isFixed` alone left both endpoints free (checked live).
        line.startSketchPoint.isFixed = True
        line.endSketchPoint.isFixed = True
        return line


def _near(a: adsk.core.Point3D, b: adsk.core.Point3D) -> bool:
    return a.distanceTo(b) < 1e-5


def _text_pt(a, b, off=0.2):
    return adsk.core.Point3D.create((a.x + b.x) / 2 + off, (a.y + b.y) / 2 + off, 0)


def _rect_on_axis(sk: _Sk, axis, s_a, s_b, w_base, h_dir_w, h_dir_n, height):
    """Rectangle with one side on `axis` from s_a to s_b, `height` tall towards
    (h_dir_w * w + h_dir_n * n). Returns (lines, bottom line, corner points dict)."""
    p0 = sk.pt(s_a, w_base, 0)
    p1 = sk.pt(s_b, w_base, 0)
    p2 = sk.pt(s_b, w_base + h_dir_w * height, h_dir_n * height)
    lines = sk.sketch.sketchCurves.sketchLines.addThreePointRectangle(p0, p1, p2)
    lines = [lines.item(i) for i in range(lines.count)]

    bottom = None
    for ln in lines:
        a, b = ln.startSketchPoint.geometry, ln.endSketchPoint.geometry
        if (_near(a, p0) and _near(b, p1)) or (_near(a, p1) and _near(b, p0)):
            bottom = ln
    if sk.constrain and bottom is not None:
        cons = sk.sketch.geometricConstraints
        dims = sk.sketch.sketchDimensions

        def touches(ln, pt):
            return _near(ln.startSketchPoint.geometry, pt) or _near(ln.endSketchPoint.geometry, pt)

        side_b = next(ln for ln in lines if ln != bottom and touches(ln, p1))
        side_a = next(ln for ln in lines if ln != bottom and touches(ln, p0))
        top = next(ln for ln in lines if ln not in (bottom, side_a, side_b))
        # The API's three-point rectangle comes with no constraints at all (checked live).
        # parallel + 2x perpendicular to the bottom -- parallel(left, right) over-constrains later.
        cons.addParallel(top, bottom)
        cons.addPerpendicular(side_a, bottom)
        cons.addPerpendicular(side_b, bottom)
        start = bottom.startSketchPoint if _near(bottom.startSketchPoint.geometry, p0) else bottom.endSketchPoint
        cons.addCoincident(start, axis)
        cons.addParallel(bottom, axis)
        aligned = adsk.fusion.DimensionOrientations.AlignedDimensionOrientation
        dims.addDistanceDimension(axis.startSketchPoint, start, aligned, _text_pt(axis.startSketchPoint.geometry, p0, -0.3))
        dims.addDistanceDimension(bottom.startSketchPoint, bottom.endSketchPoint, aligned, _text_pt(p0, p1, -0.3))
        dims.addDistanceDimension(side_b.startSketchPoint, side_b.endSketchPoint, aligned, _text_pt(p1, p2, 0.3))
    return lines, bottom


def _corner_points(lines):
    """Unique SketchPoints of a rectangle's corners."""
    pts = []
    for ln in lines:
        for sp in (ln.startSketchPoint, ln.endSketchPoint):
            if not any(_near(sp.geometry, q.geometry) for q in pts):
                pts.append(sp)
    return pts


def _dogbone(sk: _Sk, center: adsk.core.Point3D, r: float, corner_pt=None, ref_line=None):
    """Relief circle of radius r at `center` (sketch space). When constraining, it is tied to
    `corner_pt` by a construction line (length + angle to `ref_line`) and given a diameter."""
    circle = sk.sketch.sketchCurves.sketchCircles.addByCenterRadius(center, r)
    if not sk.constrain or corner_pt is None or ref_line is None:
        return circle
    lines = sk.sketch.sketchCurves.sketchLines
    cons = sk.sketch.geometricConstraints
    dims = sk.sketch.sketchDimensions
    cpt = circle.centerSketchPoint
    cl = lines.addByTwoPoints(corner_pt, cpt)
    cl.isConstruction = True
    if cl.startSketchPoint != corner_pt:
        cons.addCoincident(cl.startSketchPoint, corner_pt)
    if cl.endSketchPoint != cpt:
        cons.addCoincident(cl.endSketchPoint, cpt)

    corner = corner_pt.geometry
    dims.addDiameterDimension(circle, adsk.core.Point3D.create(center.x + r, center.y + r, 0))
    dims.addDistanceDimension(cl.startSketchPoint, cl.endSketchPoint,
                              adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
                              _text_pt(corner, center, 0.1))
    # Angle text point: between the diagonal and the reference side, near the corner.
    other = ref_line.endSketchPoint.geometry if _near(ref_line.startSketchPoint.geometry, corner) \
        else ref_line.startSketchPoint.geometry
    u_ref = _sub(other, corner)
    u_ref.normalize()
    u_cl = _sub(center, corner)
    u_cl.normalize()
    tx = adsk.core.Point3D.create(corner.x + (u_ref.x + u_cl.x) * r,
                                  corner.y + (u_ref.y + u_cl.y) * r, 0)
    dims.addAngularDimension(ref_line, cl, tx)
    return circle


def _new_sketch(comp, face, name):
    sketch = comp.sketches.addWithoutEdges(face)
    sketch.name = name
    sketch.isComputeDeferred = True
    return sketch


def _finish(sketch, constrain, report):
    sketch.isComputeDeferred = False
    if constrain:
        ok = sketch.isFullyConstrained
        report.setdefault('fully_constrained', {})[sketch.name] = ok
        if not ok:
            futil.log(f'Tab & Slot: sketch "{sketch.name}" is NOT fully constrained')


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def _all_profiles(sketch):
    profs = adsk.core.ObjectCollection.create()
    for p in sketch.profiles:
        profs.add(p)
    return profs


def _split_tab_profiles(sk, tab_spans, circles, r, h_tab):
    """Sort the combined tabs sketch's profiles by where their centroid (sketch space) falls, in
    joint coordinates (s along the joint, n towards the slot plate): inside a tab rectangle ->
    join; inside a relief circle -> cut. The small rectangle-circle overlaps go in both."""
    j = sk.j
    o = sk.pt(0, j.broad_w, 0)
    ds = _sub(sk.pt(1, j.broad_w, 0), o)
    dn = _sub(sk.pt(0, j.broad_w, 1), o)
    ds.normalize()
    dn.normalize()
    eps = 1e-6
    join, cut = [], []
    for prof in sk.sketch.profiles:
        cen = prof.areaProperties(adsk.fusion.CalculationAccuracy.LowCalculationAccuracy).centroid
        v = _sub(cen, o)
        s, n = _dot(v, ds), _dot(v, dn)
        if any(sa - eps <= s <= sb + eps and -eps <= n <= h_tab + eps for sa, sb in tab_spans):
            join.append(prof)
        if any((s - cs) ** 2 + (n - cn) ** 2 <= r * r + eps for cs, cn in circles):
            cut.append(prof)
    return join, cut


def _extrude(comp, sketch, body, op, dist, into_world, xf_inv, check, profiles=None):
    """One-side extrude of `profiles` (default: every profile in `sketch`) towards `into_world`,
    limited to `body`. `check(before_volume, body, feature)` says whether it went the right way;
    if not, flip once."""
    into = _xvec(into_world, xf_inv)
    sk_n = sketch.xDirection.crossProduct(sketch.yDirection)
    positive = _dot(sk_n, into) > 0
    extrudes = comp.features.extrudeFeatures
    for attempt in range(2):
        before = body.volume
        if profiles is None:
            profs = _all_profiles(sketch)
        else:
            profs = adsk.core.ObjectCollection.create()
            for prof in profiles:
                profs.add(prof)
        ext_in = extrudes.createInput(profs, op)
        direction = (adsk.fusion.ExtentDirections.PositiveExtentDirection if positive
                     else adsk.fusion.ExtentDirections.NegativeExtentDirection)
        ext_in.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(
            adsk.core.ValueInput.createByReal(dist)), direction)
        ext_in.participantBodies = [body]
        feat = extrudes.add(ext_in)
        if feat.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState and \
                check(before, body, feat):
            return feat
        futil.log(f'Tab & Slot: {sketch.name} extrude went the wrong way (attempt {attempt + 1}), flipping')
        feat.deleteMe()
        positive = not positive
    raise RuntimeError(f'Tab & Slot: could not extrude "{sketch.name}" into the plate.')


def build(tab_body, slot_body, p: Params, constrain: bool = True, joint: Joint = None) -> dict:
    """Make the tabs, tab-root dogbones and dogboned slots. Returns a report dict."""
    j = joint or find_joint(tab_body, slot_body)
    if j is None:
        raise RuntimeError('Tab & Slot: the tab plate\'s edge doesn\'t sit flat on the slot plate\'s face.')
    centers, problems = layout_tabs(j, p)
    if problems:
        raise RuntimeError('Tab & Slot: ' + ' '.join(problems))

    comp_a, comp_b = j.body_a.parentComponent, j.body_b.parentComponent
    r = p.dogbone_r
    h_tab = j.t_b + p.protrusion
    report = {'centers': centers, 'joint': j}

    # --- Tab plate: ONE sketch on the broad face (before it changes) holding the tab rectangles
    # and their root dogbones; the join takes the tab regions, the cut takes the circle regions.
    tabs_sk = _Sk(_new_sketch(comp_a, j.broad_a, 'Tab & Slot - Tabs Sketch'), j, j.xf_a_inv, constrain)
    tab_axis = tabs_sk.axis(j.broad_w) if constrain else None
    tab_spans, circles = [], []
    k = r / math.sqrt(2.0)
    for c in centers:
        sa, sb = c - p.tab_width / 2.0, c + p.tab_width / 2.0
        _, bottom = _rect_on_axis(tabs_sk, tab_axis, sa, sb, j.broad_w, 0, 1, h_tab)
        tab_spans.append((sa, sb))
        for s_corner, out in ((sa, -1.0), (sb, 1.0)):
            # Open quadrant at the root: away from the tab along d, towards the slot plate along n.
            circles.append((s_corner + out * k, k))
            corner = tabs_sk.pt(s_corner, j.broad_w, 0)
            corner_pt = None
            if constrain and bottom is not None:
                corner_pt = bottom.startSketchPoint if _near(bottom.startSketchPoint.geometry, corner) \
                    else bottom.endSketchPoint
            _dogbone(tabs_sk, tabs_sk.pt(s_corner + out * k, j.broad_w, k), r, corner_pt, bottom)
    _finish(tabs_sk.sketch, constrain, report)
    join_profiles, relief_profiles = _split_tab_profiles(tabs_sk, tab_spans, circles, r, h_tab)

    # --- Slot plate: slots with a dogbone in every inside corner, sketched on the contact face.
    # A slot side within a bit diameter of the slot plate's edge (an L joint) can't keep a web,
    # so that side is cut as an open notch: run it a bit diameter past the edge, and skip its
    # dogbones -- those corners are outside the plate, nothing to relieve.
    slot_sk = _Sk(_new_sketch(comp_b, j.fb, 'Tab & Slot - Slots Sketch'), j, j.xf_b_inv, constrain)
    open_lo, open_hi = open_sides(j, p)
    edge_lo, edge_hi = j.extra.get('b_w_range', (-math.inf, math.inf))
    w_lo = edge_lo - p.bit_dia if open_lo else -p.clear_w
    w_hi = edge_hi + p.bit_dia if open_hi else j.t_a + p.clear_w
    slot_w = w_hi - w_lo
    slot_axis = slot_sk.axis(w_lo) if constrain else None
    for c in centers:
        half = p.tab_width / 2.0 + p.clear_l
        lines, bottom = _rect_on_axis(slot_sk, slot_axis, c - half, c + half, w_lo, 1, 0, slot_w)
        lo_side = (slot_sk.pt(c - half, w_lo, 0), slot_sk.pt(c + half, w_lo, 0))
        for cp in _corner_points(lines):
            g = cp.geometry
            on_lo = any(_near(g, q) for q in lo_side)
            if (on_lo and open_lo) or (not on_lo and open_hi):
                continue
            # The slot isn't square, so aim along the 45 deg corner bisector (built from the
            # corner's two sides -- sketch axes needn't line up with the slot), not at its centre.
            bis, side = _corner_bisector(lines, g)
            center = adsk.core.Point3D.create(g.x + r * bis.x, g.y + r * bis.y, 0)
            _dogbone(slot_sk, center, r, cp if constrain else None, side)
    _finish(slot_sk.sketch, constrain, report)
    report['sketches'] = [tabs_sk.sketch, slot_sk.sketch]

    # --- Features.
    into_a = j.w.copy()
    if j.broad_w > j.t_a / 2.0:
        into_a.scaleBy(-1)                              # sketch is on the +w face: go -w
    expected_join = len(centers) * p.tab_width * j.t_a * h_tab

    def grew(before, body, feat):
        dv = body.volume - before
        return abs(dv - expected_join) < 0.01 * expected_join and body.lumps.count == 1

    def shrank(before, body, feat):
        return body.volume < before - 1e-9

    report['tabs'] = _extrude(comp_a, tabs_sk.sketch, j.body_a,
                              adsk.fusion.FeatureOperations.JoinFeatureOperation,
                              j.t_a, into_a, j.xf_a_inv, grew, join_profiles)
    report['tabs'].name = 'Tab & Slot - Tabs (Join)'
    report['relief'] = _extrude(comp_a, tabs_sk.sketch, j.body_a,
                                adsk.fusion.FeatureOperations.CutFeatureOperation,
                                j.t_a + CUT_EXTRA, into_a, j.xf_a_inv, shrank, relief_profiles)
    report['relief'].name = 'Tab & Slot - Tab Relief (Cut)'
    report['slots'] = _extrude(comp_b, slot_sk.sketch, j.body_b,
                               adsk.fusion.FeatureOperations.CutFeatureOperation,
                               j.t_b + CUT_EXTRA, j.n, j.xf_b_inv, shrank)
    report['slots'].name = 'Tab & Slot - Slots (Cut)'

    # Round the sharp points where each dogbone meets a straight side, so the bit runs smoothly.
    if p.corner_round > 0:
        report['tab_round'] = _round_cusps(comp_a, report['relief'], into_a, j.xf_a_inv,
                                           p.corner_round, 'Tab & Slot - Tab Corner Round')
        report['slot_round'] = _round_cusps(comp_b, report['slots'], j.n, j.xf_b_inv,
                                            p.corner_round, 'Tab & Slot - Slot Corner Round')
    return report


def _round_cusps(comp, cut_feature, axis_world, xf_inv, radius, name):
    """Fillet the sharp points a dogbone cut leaves: the straight edges, running along the cut
    direction, on the dogbone cylinders `cut_feature` made. Each one is a convex point of
    material (where the circle crosses a straight side, or two overlapping circles meet).
    If the radius doesn't fit everywhere, halve it a couple of times; else skip with a log."""
    axis = _xvec(axis_world, xf_inv)
    edges, seen = [], set()
    for face in cut_feature.faces:
        if adsk.core.Cylinder.cast(face.geometry) is None:
            continue
        for e in face.edges:
            line = adsk.core.Line3D.cast(e.geometry)
            if line is None or e.tempId in seen:
                continue
            v = _sub(line.endPoint, line.startPoint)
            v.normalize()
            if abs(abs(_dot(v, axis)) - 1.0) < 1e-4:
                seen.add(e.tempId)
                edges.append(e)
    if not edges:
        return None
    fillets = comp.features.filletFeatures
    for attempt in range(3):
        coll = adsk.core.ObjectCollection.create()
        for e in edges:
            coll.add(e)
        fin = fillets.createInput()
        fin.addConstantRadiusEdgeSet(coll, adsk.core.ValueInput.createByReal(radius), False)
        try:
            feat = fillets.add(fin)
            feat.name = name
            return feat
        except Exception as ex:
            futil.log(f'Tab & Slot: {name} at {radius / IN_TO_CM:.4f} in failed ({ex}); trying smaller')
            radius /= 2.0
    futil.log(f'Tab & Slot: {name} skipped -- no radius fit', force_console=True)
    return None


def _corner_bisector(lines, g):
    """(unit vector in sketch space from corner `g` into the rectangle along the 45 deg
    bisector, one of the corner's side lines)."""
    dirs, side = [], None
    for ln in lines:
        a, b = ln.startSketchPoint.geometry, ln.endSketchPoint.geometry
        if _near(a, g):
            v = _sub(b, a)
        elif _near(b, g):
            v = _sub(a, b)
        else:
            continue
        v.normalize()
        dirs.append(v)
        side = side or ln
    v = adsk.core.Vector3D.create(dirs[0].x + dirs[1].x, dirs[0].y + dirs[1].y, 0)
    v.normalize()
    return v, side


# ---------------------------------------------------------------------------
# Edit support: every run tags its sketches/features with one id + its settings, so a
# right-click Edit can find the run, roll the timeline back to it and rebuild it in place.
# ---------------------------------------------------------------------------

ATTR_GROUP = 'FRCTools_TabSlot'
ATTR_ID = 'id'
ATTR_DATA = 'data'


def run_items(report: dict) -> list:
    """Every sketch and feature a `build` made."""
    items = list(report.get('sketches', []))
    for key in ('tabs', 'relief', 'slots', 'tab_round', 'slot_round'):
        if report.get(key) is not None:
            items.append(report[key])
    return items


def make_data(tab_body, slot_body, p: Params, joint: Joint, bit_label: str, group_name: str) -> dict:
    """Everything an Edit needs: the settings, the two plates (entity tokens) and the joint's
    measurements -- the edit dialog validates against these, since the live plates already
    carry this run's tabs and slots."""
    lo, hi = joint.extra.get('b_w_range', (-1e9, 1e9))
    return {
        'params': {k: getattr(p, k) for k in ('count', 'tab_width', 'margin', 'bit_dia',
                                              'clear_w', 'clear_l', 'protrusion', 'corner_round')},
        'bit_label': bit_label,
        'tab_token': tab_body.entityToken,
        'slot_token': slot_body.entityToken,
        'joint': {'length': joint.length, 't_a': joint.t_a, 't_b': joint.t_b, 'b_w_range': [lo, hi]},
        'group_name': group_name,
    }


def tag_run(report: dict, data: dict) -> str:
    """Tag every item of a run with a new id and the run's data. Returns the id."""
    run_id = uuid.uuid4().hex
    text = json.dumps(data)
    for item in run_items(report):
        item.attributes.add(ATTR_GROUP, ATTR_ID, run_id)
        item.attributes.add(ATTR_GROUP, ATTR_DATA, text)
    return run_id


def read_run(entity):
    """(run id, data dict) for a tagged sketch/feature, a timeline group holding one, or a
    timeline object; else (None, None)."""
    if entity is None:
        return None, None
    # A timeline item may come back typed as a plain TimelineObject even when it's a group.
    if getattr(entity, 'isGroup', False) and adsk.fusion.TimelineGroup.cast(entity) is None:
        try:
            tl = adsk.fusion.Design.cast(entity.parentProduct if hasattr(entity, 'parentProduct')
                                         else adsk.core.Application.get().activeProduct).timeline
            for g in tl.timelineGroups:
                if g.name == entity.name:
                    entity = g
                    break
        except Exception:
            pass
    group = adsk.fusion.TimelineGroup.cast(entity)
    if group is not None:
        for k in range(group.count):
            found = read_run(group.item(k).entity)
            if found[0]:
                return found
        return None, None
    tl_obj = adsk.fusion.TimelineObject.cast(entity)
    if tl_obj is not None:
        return read_run(tl_obj.entity)
    attrs = getattr(entity, 'attributes', None)
    if attrs is None:
        return None, None
    a_id, a_data = attrs.itemByName(ATTR_GROUP, ATTR_ID), attrs.itemByName(ATTR_GROUP, ATTR_DATA)
    if a_id is None or a_data is None:
        return None, None
    return a_id.value, json.loads(a_data.value)


def read_run_for_body(design, body):
    """The most recent run that used `body` as its tab or slot plate, or (None, None)."""
    try:
        token = body.entityToken
    except Exception:
        return None, None
    best = (None, None, -1)
    for attr in design.findAttributes(ATTR_GROUP, ATTR_DATA):
        try:
            data = json.loads(attr.value)
            if token not in (data.get('tab_token'), data.get('slot_token')):
                continue
            parent = attr.parent
            tlo = parent.timelineObject
            try:
                idx = tlo.index
            except Exception:
                idx = tlo.parentGroup.index             # inside a collapsed group
            if idx > best[2]:
                run_id = parent.attributes.itemByName(ATTR_GROUP, ATTR_ID).value
                best = (run_id, data, idx)
        except Exception:
            continue
    return best[0], best[1]


def _run_timeline_objects(design, run_id):
    objs = []
    for attr in design.findAttributes(ATTR_GROUP, ATTR_ID):
        if attr.value != run_id:
            continue
        try:
            objs.append(attr.parent.timelineObject)
        except Exception:
            pass
    return objs


def _resolve(design, token):
    try:
        found = design.findEntityByToken(token)
        return found[0] if found else None
    except Exception:
        return None


def rebuild_run(design, run_id, tab_token, slot_token, p: Params, data: dict) -> dict:
    """Replace a run in place: roll back to it, delete its group/items, rebuild with `p` at the
    same timeline position, regroup + re-tag, and roll forward. Raises on failure (a command's
    executeFailed then undoes everything, so the original run comes back)."""
    timeline = design.timeline
    objs = _run_timeline_objects(design, run_id)
    if not objs:
        raise RuntimeError('Tab & Slot: could not find the features of the joint being edited.')
    group = objs[0].parentGroup
    if group is not None:
        group.isCollapsed = False             # items in a collapsed group have no index
    start = min(o.index for o in objs)
    # `TimelineGroup.deleteMe` (True or False) raised InternalValidationError live, so delete
    # the items themselves, newest-first (features before their sketches).
    for o in sorted(objs, key=lambda o: o.index, reverse=True):
        o.entity.deleteMe()
    if group is not None and group.isValid:
        try:
            group.deleteMe(False)
        except Exception:
            pass

    timeline.markerPosition = start
    tab, slot = _resolve(design, tab_token), _resolve(design, slot_token)
    if tab is None or slot is None:
        raise RuntimeError('Tab & Slot: could not find the original plates to rebuild on.')
    report = build(tab, slot, p, constrain=True)
    end = timeline.markerPosition - 1
    if end > start:
        new_group = timeline.timelineGroups.add(start, end)
        new_group.name = data.get('group_name', 'Tab & Slot')
        new_group.isCollapsed = True
    data = dict(data)
    data.update(make_data(tab, slot, p, report['joint'], data.get('bit_label', ''), data.get('group_name', 'Tab & Slot')))
    tag_run(report, data)
    timeline.moveToEnd()
    return report


def list_runs(design) -> list:
    """[(run id, data)] for every Tab & Slot run in the design, newest (latest in timeline) first."""
    runs = {}
    for attr in design.findAttributes(ATTR_GROUP, ATTR_DATA):
        try:
            parent = attr.parent
            run_id = parent.attributes.itemByName(ATTR_GROUP, ATTR_ID).value
            tlo = parent.timelineObject
            try:
                idx = tlo.index
            except Exception:
                idx = tlo.parentGroup.index             # inside a collapsed group
            if run_id not in runs or idx > runs[run_id][0]:
                runs[run_id] = (idx, json.loads(attr.value))
        except Exception:
            continue
    ordered = sorted(runs.items(), key=lambda kv: kv[1][0], reverse=True)
    return [(run_id, data) for run_id, (_, data) in ordered]
