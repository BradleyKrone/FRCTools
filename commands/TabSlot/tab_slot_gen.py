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
    if p.clear_w < 0 or p.clear_l < 0 or p.corner_round < 0:
        problems.append('Clearances and corner round cannot be negative.')
    if joint.t_b + p.protrusion <= 10 * _TOL:
        problems.append('A negative Tab Protrusion can\'t be as big as the slot plate\'s thickness.')
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
        self.xf = xf_inv.copy()
        self.xf.invert()
        self.constrain = constrain

    def pt(self, s, wv, nv) -> adsk.core.Point3D:
        world = _add(self.j.origin, (s, self.j.d), (wv, self.j.w), (nv, self.j.n))
        p = self.sketch.modelToSketchSpace(_xpt(world, self.xf_inv))
        return adsk.core.Point3D.create(p.x, p.y, 0)

    def coords(self, sketch_pt):
        """(s, w, n) of a sketch-space point."""
        v = _sub(_xpt(self.sketch.sketchToModelSpace(sketch_pt), self.xf), self.j.origin)
        return _dot(v, self.j.d), _dot(v, self.j.w), _dot(v, self.j.n)

    def line_coords(self, line):
        """((s, w, n) of the start, (s, w, n) of the end) of a sketch line."""
        return self.coords(line.startSketchPoint.geometry), self.coords(line.endSketchPoint.geometry)

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


def _project_lines(sketch, native):
    """Linked projection of one of the sketch's OWN component's edges -> its SketchLines
    (construction, so they don't split profiles), or [] if Fusion refused.
    Never project ANOTHER plate's geometry: Fusion resolves a cross-component projection against
    that plate's FINAL state, not its state at this point in the timeline, which made circular
    dependencies (broken fillets, later features erroring) in a real multi-run design -- even
    one-way, slot plate -> tab plate (checked live). Cross-plate links go through parameters.
    project2 raises InternalValidationError on a compute-deferred sketch, so deferral is lifted."""
    deferred = sketch.isComputeDeferred
    sketch.isComputeDeferred = False
    try:
        ents = sketch.project2([native], True)
    except Exception as ex:
        futil.log(f'Tab & Slot: projection into "{sketch.name}" failed ({ex})')
        return []
    finally:
        sketch.isComputeDeferred = deferred
    lines = []
    for ent in ents:
        ln = adsk.fusion.SketchLine.cast(ent)
        if ln is None:
            continue
        try:
            ln.isConstruction = True
        except Exception:
            pass
        lines.append(ln)
    return lines


def _delete(lines):
    for ln in lines:
        try:
            ln.deleteMe()
        except Exception:
            pass


def _root_axis(sk: _Sk, fa, broad):
    """Projected tab-plate edge where the end face meets the sketch face (the tab roots' line), so
    the tabs stay on the plate's end if its outline moves. None -> use a fixed axis instead."""
    best, best_len = None, 0.0
    for e in fa.edges:
        line = adsk.core.Line3D.cast(e.geometry)
        if line is None or not any(f == broad for f in e.faces):
            continue
        ln = line.startPoint.distanceTo(line.endPoint)
        if ln > best_len:
            best, best_len = e, ln
    if best is None:
        return None
    lines = _project_lines(sk.sketch, best)
    if len(lines) == 1:
        a, b = sk.line_coords(lines[0])
        if all(abs(c[1] - sk.j.broad_w) < 1e-3 and abs(c[2]) < 1e-3 for c in (a, b)):
            return lines[0]
    _delete(lines)
    return None


def _plus(expr, extra_cm):
    """`expr` + a length in cm, as a Fusion expression."""
    if abs(extra_cm) < 1e-9:
        return expr
    return f'{expr} + {extra_cm:.6f} cm' if extra_cm > 0 else f'{expr} - {-extra_cm:.6f} cm'


def _rect_on_axis(sk: _Sk, axis, s_a, s_b, w_base, h_dir_w, h_dir_n, height, height_expr=None,
                  position_dim=True):
    """Rectangle with one side on `axis` from s_a to s_b, `height` tall towards
    (h_dir_w * w + h_dir_n * n). Returns (lines, bottom line).
    height_expr: drive the height by this expression (a parameter) instead of a number, so the
    rectangle follows a thickness change.
    position_dim=False: leave the rectangle free to slide along the axis (_space_along places it)."""
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
        # Parallel FIRST: coincident-then-parallel against a projected (reference) line raises
        # VCS_SKETCH_OVER_CONSTRAINTS (checked live); this order is fine for both axis kinds.
        cons.addParallel(bottom, axis)
        cons.addCoincident(start, axis)
        aligned = adsk.fusion.DimensionOrientations.AlignedDimensionOrientation
        if position_dim:
            dims.addDistanceDimension(axis.startSketchPoint, start, aligned,
                                      _text_pt(axis.startSketchPoint.geometry, p0, -0.3))
        dims.addDistanceDimension(bottom.startSketchPoint, bottom.endSketchPoint, aligned, _text_pt(p0, p1, -0.3))
        h_dim = dims.addDistanceDimension(side_b.startSketchPoint, side_b.endSketchPoint, aligned,
                                          _text_pt(p1, p2, 0.3))
        if height_expr:
            try:
                h_dim.parameter.expression = height_expr
            except Exception as ex:
                futil.log(f'Tab & Slot: could not set "{height_expr}" on {sk.sketch.name} ({ex})')
    return lines, bottom


def _corner_points(lines):
    """Unique SketchPoints of a rectangle's corners."""
    pts = []
    for ln in lines:
        for sp in (ln.startSketchPoint, ln.endSketchPoint):
            if not any(_near(sp.geometry, q.geometry) for q in pts):
                pts.append(sp)
    return pts


def _ends_by_s(sk: _Sk, line):
    """(low-s SketchPoint, high-s SketchPoint) of a line along the joint."""
    a, b = line.startSketchPoint, line.endSketchPoint
    return (a, b) if sk.coords(a.geometry)[0] <= sk.coords(b.geometry)[0] else (b, a)


def _spans_whole_axis(sk: _Sk, axis) -> bool:
    """Does `axis` run exactly over the joint span (s = 0 .. length)? Then the tabs can be spaced
    off its ends and re-space when the plate's length changes."""
    lo, hi = _ends_by_s(sk, axis)
    return abs(sk.coords(lo.geometry)[0]) < 1e-3 and abs(sk.coords(hi.geometry)[0] - sk.j.length) < 1e-3


def _space_along(sk: _Sk, axis, bottoms, edge_driven: bool, margin: float, rect_len: float):
    """Place rectangles that already sit on `axis` (bottom lines `bottoms`, in order along the
    joint): each low-s corner gets a distance from the axis's low-s end.
    edge_driven: the distances are expressions of a driven dimension measuring the axis, so the
    layout re-spaces with the plate edge's length -- first rectangle `margin` from the start, last
    one `margin` from the end, equal steps (one rectangle: centred). Otherwise fixed numbers.
    Every distance is measured from the fixed start corner: spacer lines between neighbours
    (equal constraints, a margin to the far end) flipped to the wrong side of a corner when the
    plate shrank a lot (checked live) -- a distance dimension has no direction."""
    dims = sk.sketch.sketchDimensions
    aligned = adsk.fusion.DimensionOrientations.AlignedDimensionOrientation
    a0, a1 = _ends_by_s(sk, axis)
    corners = [_ends_by_s(sk, b)[0] for b in bottoms]
    n = len(corners)
    length = None
    if edge_driven:
        drv = dims.addDistanceDimension(a0, a1, aligned, _text_pt(a0.geometry, a1.geometry, -0.8), False)
        length = drv.parameter.name
    for k, c in enumerate(corners):
        if c.geometry.distanceTo(a0.geometry) < 1e-6:
            sk.sketch.geometricConstraints.addCoincident(c, a0)
            continue
        dim = dims.addDistanceDimension(a0, c, aligned, _text_pt(a0.geometry, c.geometry, -0.5 - 0.15 * k))
        if length is None:
            continue
        if n == 1:
            expr = f'({length} - {rect_len:.6f} cm) / 2'
        elif k == 0:
            expr = f'{margin:.6f} cm'
        else:
            expr = f'{margin:.6f} cm + {k} * ({length} - {2 * margin + rect_len:.6f} cm) / {n - 1}'
        try:
            dim.parameter.expression = expr
        except Exception as ex:
            futil.log(f'Tab & Slot: could not set "{expr}" on {sk.sketch.name} ({ex})')


def _tool_axis(sk: _Sk, fa, w_base):
    """Construction line along the joint at w = w_base on the slot-tool sketch (on the tab plate's
    end face `fa`), tied to fa's OWN projected edges: parallel to its w = 0 long edge at a
    w_base offset, ends on its two short edges. So it follows the plate's length, and the plate
    wherever a joint moves it. None -> caller falls back to a fixed axis."""
    lines = _project_lines(sk.sketch, fa)
    lo_edge, start_edge, end_edge = None, None, None
    s_min = min((min(sk.coords(ln.startSketchPoint.geometry)[0], sk.coords(ln.endSketchPoint.geometry)[0])
                 for ln in lines), default=None)
    s_max = max((max(sk.coords(ln.startSketchPoint.geometry)[0], sk.coords(ln.endSketchPoint.geometry)[0])
                 for ln in lines), default=None)
    for ln in lines:
        a, b = sk.line_coords(ln)
        if abs(a[1]) < 1e-3 and abs(b[1]) < 1e-3 and abs(a[0] - b[0]) > 1e-3:
            lo_edge = ln
        elif abs(a[0] - b[0]) < 1e-3 and abs(a[0] - s_min) < 1e-3:
            start_edge = ln
        elif abs(a[0] - b[0]) < 1e-3 and abs(a[0] - s_max) < 1e-3:
            end_edge = ln
    if lo_edge is None or start_edge is None or end_edge is None:
        _delete(lines)
        return None
    _delete([ln for ln in lines if ln not in (lo_edge, start_edge, end_edge)])
    cons, dims = sk.sketch.geometricConstraints, sk.sketch.sketchDimensions
    p0, p1 = sk.pt(s_min, w_base, 0), sk.pt(s_max, w_base, 0)
    axis = sk.sketch.sketchCurves.sketchLines.addByTwoPoints(p0, p1)
    axis.isConstruction = True
    if abs(w_base) < 1e-6:
        lo0, lo1 = _ends_by_s(sk, lo_edge)
        cons.addCoincident(axis.startSketchPoint, lo0)
        cons.addCoincident(axis.endSketchPoint, lo1)
        return axis
    cons.addParallel(axis, lo_edge)                 # parallel FIRST (see _rect_on_axis)
    cons.addCoincident(axis.startSketchPoint, start_edge)
    cons.addCoincident(axis.endSketchPoint, end_edge)
    dims.addOffsetDimension(lo_edge, axis, _text_pt(p0, p1, -0.4))
    return axis


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
        # Keyed per component: a multi-plate run makes same-named sketches in several components.
        report.setdefault('fully_constrained', {})[f'{sketch.parentComponent.name}/{sketch.name}'] = ok
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


def _extrude(comp, sketch, body, op, dist, into_world, xf_inv, check, profiles=None, extent=None):
    """One-side extrude of `profiles` (default: every profile in `sketch`) towards `into_world`,
    limited to `body`. `check(before_volume, body, feature)` says whether it went the right way;
    if not, flip once. `extent()` (optional) makes a geometry-driven extent (to a face / through
    all) so the result follows a thickness change; if it fails both ways, fall back to `dist`."""
    into = _xvec(into_world, xf_inv)
    sk_n = sketch.xDirection.crossProduct(sketch.yDirection)
    positive = _dot(sk_n, into) > 0
    extrudes = comp.features.extrudeFeatures
    tries = [(extent, positive), (extent, not positive)] if extent is not None else []
    tries += [(None, positive), (None, not positive)]
    for attempt, (make_extent, pos) in enumerate(tries):
        before = body.volume
        if profiles is None:
            profs = _all_profiles(sketch)
        else:
            profs = adsk.core.ObjectCollection.create()
            for prof in profiles:
                profs.add(prof)
        ext_in = extrudes.createInput(profs, op)
        direction = (adsk.fusion.ExtentDirections.PositiveExtentDirection if pos
                     else adsk.fusion.ExtentDirections.NegativeExtentDirection)
        ext_def = make_extent() if make_extent is not None else \
            adsk.fusion.DistanceExtentDefinition.create(adsk.core.ValueInput.createByReal(dist))
        ext_in.setOneSideExtent(ext_def, direction)
        ext_in.participantBodies = [body]
        try:
            feat = extrudes.add(ext_in)
        except Exception as ex:
            futil.log(f'Tab & Slot: {sketch.name} extrude failed (attempt {attempt + 1}): {ex}')
            continue
        if feat.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState and \
                check(before, body, feat):
            return feat
        futil.log(f'Tab & Slot: {sketch.name} extrude went the wrong way (attempt {attempt + 1}), flipping')
        feat.deleteMe()
    raise RuntimeError(f'Tab & Slot: could not extrude "{sketch.name}" into the plate.')


def _through_all():
    return adsk.fusion.ThroughAllExtentDefinition.create()


def _param_depth(thk_expr, extra):
    """A cut-depth extent maker: plate thickness parameter + `extra`, so the cut still clears the
    plate after a thickness change; through-all when there's no parameter (a through-all tab
    relief failed both ways on a plain test plate, live, so it's only the fallback)."""
    if thk_expr is None:
        return _through_all
    expr = _plus(thk_expr, extra)
    return lambda: adsk.fusion.DistanceExtentDefinition.create(adsk.core.ValueInput.createByString(expr))


def _plate_extrude(body, thickness):
    """The one-sided distance extrude that made the plate `body` (thickness `thickness`), or None."""
    try:
        for ext in body.parentComponent.features.extrudeFeatures:
            if ext.operation != adsk.fusion.FeatureOperations.NewBodyFeatureOperation:
                continue
            if not any(b == body for b in ext.bodies):
                continue
            dist = adsk.fusion.DistanceExtentDefinition.cast(ext.extentOne)
            if dist is None or ext.hasTwoExtents or abs(abs(dist.distance.value) - thickness) > 1e-4:
                continue
            return ext
    except Exception as ex:
        futil.log(f'Tab & Slot: could not find the extrude that made {body.name} ({ex})')
    return None


def _thickness_expression(body, thickness):
    """An expression for `body`'s thickness that follows the design: the model parameter of the
    extrude that made the plate (e.g. 'd268', itself = Plywood_3_4), or None. Model parameters can
    be used from any component's sketch (checked live), and -- unlike projecting another
    component's geometry -- they make no geometric cross-component reference."""
    ext = _plate_extrude(body, thickness)
    if ext is None:
        return None
    prm = ext.extentOne.distance
    return prm.name if prm.value > 0 else f'abs({prm.name})'


TAB_LENGTH_PARAM = 'Tab_Length'


def _user_param_expression(body, thickness):
    """The slot plate's thickness as the user wrote it -- 'Plywood_3_4' when its extrude reads
    exactly that -- else its model parameter name, else None."""
    ext = _plate_extrude(body, thickness)
    if ext is None:
        return None
    prm = ext.extentOne.distance
    expr = prm.expression.strip().lstrip('-').strip().strip('()').strip()
    design = adsk.fusion.Design.cast(body.parentComponent.parentDesign)
    if design is not None and design.userParameters.itemByName(expr) is not None:
        return expr
    return _thickness_expression(body, thickness)


def tab_length_expr(j: Joint, p: Params):
    """Tab length as an expression: the slot plate's thickness ('Plywood' when its extrude reads
    exactly that user parameter, else its model parameter) + protrusion, so tabs always reach
    through the slot plate. None -> plain number."""
    thk = _user_param_expression(j.body_b, j.t_b)
    return _plus(thk, p.protrusion) if thk else None


def tab_length_expression(j: Joint, p: Params):
    """LEGACY (make_run_parametric only -- new runs use tab_length_expr).
    Tab length as an expression: the shared `Tab_Length` user parameter (+ protrusion), made
    on first use with the slot plate's thickness parameter as its value (e.g. 'Plywood_3_4').
    If Tab_Length already exists but doesn't match this slot plate's thickness (a thinner back
    panel, say), use that plate's own thickness parameter instead. None -> plain number."""
    design = adsk.fusion.Design.cast(j.body_b.parentComponent.parentDesign)
    if design is None:
        return None
    prm = design.userParameters.itemByName(TAB_LENGTH_PARAM)
    if prm is None:
        default = _user_param_expression(j.body_b, j.t_b)
        try:
            prm = design.userParameters.add(
                TAB_LENGTH_PARAM,
                adsk.core.ValueInput.createByString(default or f'{j.t_b / IN_TO_CM:.5f} in'), 'in',
                'Tab & Slot: how far tabs reach into the slot plate (+ each run\'s Tab Protrusion). '
                'Defaults to the plate thickness.')
        except Exception as ex:
            futil.log(f'Tab & Slot: could not create the {TAB_LENGTH_PARAM} parameter ({ex})')
            prm = None
    if prm is not None and abs(prm.value - j.t_b) < 1e-4:
        return _plus(TAB_LENGTH_PARAM, p.protrusion)
    own = _thickness_expression(j.body_b, j.t_b)
    if own is not None:
        futil.log(f'Tab & Slot: {_joint_name(j)} -- {TAB_LENGTH_PARAM} isn\'t this slot plate\'s '
                  f'thickness, so its tabs follow the plate\'s own thickness parameter ({own})')
        return _plus(own, p.protrusion)
    return None


def _fixed_side(j: Joint):
    """Which tab-plate face stays put when its thickness changes: 'lo' (w = 0) or 'hi' (w = tA) --
    the one on the plate extrude's sketch plane -- or None if that can't be told."""
    ext = _plate_extrude(j.body_a, j.t_a)
    if ext is None:
        return None
    try:
        if adsk.fusion.ProfilePlaneStartDefinition.cast(ext.startExtent) is None:
            return None
        prof = ext.profile
        prof = prof.item(0) if adsk.core.ObjectCollection.cast(prof) is not None else prof
        sketch = _native(prof.parentSketch)     # comes back as an occurrence proxy (checked live)
        xf_a = j.xf_a_inv.copy()
        xf_a.invert()
        wv = _dot(_sub(_xpt(sketch.sketchToModelSpace(adsk.core.Point3D.create(0, 0, 0)), xf_a), j.origin), j.w)
    except Exception as ex:
        futil.log(f'Tab & Slot: could not tell which side of the tab plate is fixed ({ex})')
        return None
    if abs(wv) < 1e-3:
        return 'lo'
    if abs(wv - j.t_a) < 1e-3:
        return 'hi'
    return None


def plate_name(body) -> str:
    """A picked plate's occurrence name ('Battery_Dividers:3'), else the body name."""
    occ = getattr(body, 'assemblyContext', None)
    return occ.name if occ is not None else body.name


def find_joints(tabs, slots):
    """([Joint] for every tab x slot pair that butts, [notes about picks that touch nothing]).
    Each joint remembers its picks in extra['tab'] / extra['slot']."""
    joints, notes = [], []
    used_tabs, used_slots = set(), set()
    for ti, tab in enumerate(tabs):
        for si, slot in enumerate(slots):
            if tab == slot:
                continue
            j = find_joint(tab, slot)
            if j is None:
                continue
            j.extra['tab'], j.extra['slot'] = tab, slot
            joints.append(j)
            used_tabs.add(ti)
            used_slots.add(si)
    if joints:
        for ti, tab in enumerate(tabs):
            if ti not in used_tabs:
                notes.append(f'{plate_name(tab)} doesn\'t sit on any slot plate -- skipped.')
        for si, slot in enumerate(slots):
            if si not in used_slots:
                notes.append(f'No tab plate sits on {plate_name(slot)} -- skipped.')
    return joints, notes


def _joint_name(j: Joint) -> str:
    tab, slot = j.extra.get('tab'), j.extra.get('slot')
    if tab is not None and slot is not None:
        return f'{plate_name(tab)} -> {plate_name(slot)}'
    return j.extra.get('name', 'joint')


def _same_span(a: Joint, b: Joint, xa, xb) -> bool:
    """Do the two joints run along the same native edge span (either way round)?"""
    a0, a1 = _xpt(a.origin, xa), _xpt(_add(a.origin, (a.length, a.d)), xa)
    b0, b1 = _xpt(b.origin, xb), _xpt(_add(b.origin, (b.length, b.d)), xb)
    tol = 1e-3
    return (a0.distanceTo(b0) < tol and a1.distanceTo(b1) < tol) or \
           (a0.distanceTo(b1) < tol and a1.distanceTo(b0) < tol)


def plan_units(joints):
    """Group the joints into build units -- (tab units, problems).

    Copies of one component share a native body, so a tab added to one copy appears on every
    copy: tabs (and the slot tool body) are built ONCE per (native tab body, end face), and every
    copy using that end face must sit the same way (same span, same slot plate thickness) or the
    shared tabs won't match. Each unit is [first joint, the copies' joints...]; every copy's
    tool body proxy cuts its own slot plate (see _cut_slots)."""
    tab_units, problems = [], []
    for j in joints:
        for unit in tab_units:
            first = unit[0]
            if first.body_a == j.body_a and first.fa == j.fa:
                if not _same_span(first, j, first.xf_a_inv, j.xf_a_inv) or abs(first.t_b - j.t_b) > 1e-4:
                    problems.append(f'{plate_name(j.extra.get("tab"))} is a copy of '
                                    f'{plate_name(first.extra.get("tab"))} but sits differently on its slot '
                                    f'plate, so they can\'t share tabs -- pick them in separate runs.')
                else:
                    unit.append(j)
                break
        else:
            tab_units.append([j])
    return tab_units, problems


def layout_problems(joints, p: Params) -> list:
    """layout_tabs problems for every joint, named when there is more than one."""
    out = []
    for j in joints:
        _, probs = layout_tabs(j, p)
        if len(joints) > 1:
            probs = [f'{_joint_name(j)}: {msg}' for msg in probs]
        for msg in probs:
            if msg not in out:
                out.append(msg)
    return out


def _live_face(face, body, xf_inv, pt_world, n_world):
    """`face` if it is still valid, else the planar face of `body` through `pt_world` with outward
    normal `n_world` -- an earlier unit of the same run may have rebuilt the body's faces."""
    try:
        if face.isValid:
            return face
    except Exception:
        pass
    pt, nn = _xpt(pt_world, xf_inv), _xvec(n_world, xf_inv)
    for f in body.faces:
        if not _planar(f) or _dot(_true_normal(f), nn) < 0.9999:
            continue
        if abs(_dot(_sub(f.pointOnFace, pt), nn)) > _TOL:
            continue
        ok, prm = f.evaluator.getParameterAtPoint(pt)
        if ok and f.evaluator.isParameterOnFace(prm):
            return f
    raise RuntimeError('Tab & Slot: lost track of a plate face while building.')


def build(tab_body, slot_body, p: Params, constrain: bool = True, joint: Joint = None) -> dict:
    """One tab plate, one slot plate (see build_many)."""
    joints = None
    if joint is not None:
        joint.extra.setdefault('tab', tab_body)
        joint.extra.setdefault('slot', slot_body)
        joints = [joint]
    return build_many([tab_body], [slot_body], p, constrain, joints)


def build_many(tabs, slots, p: Params, constrain: bool = True, joints=None) -> dict:
    """Make the tabs, tab-root dogbones and dogboned slots for every tab plate x slot plate pair
    that butts. `joints` = find_joints(tabs, slots)[0], measured before anything changed.
    Returns a report dict ('joints', 'sketches', 'features', 'fully_constrained')."""
    if joints is None:
        joints, _ = find_joints(tabs, slots)
    if not joints:
        raise RuntimeError('Tab & Slot: no tab plate\'s edge sits flat on a slot plate\'s face.')
    tab_units, problems = plan_units(joints)
    problems += layout_problems(joints, p)
    if problems:
        raise RuntimeError('Tab & Slot: ' + ' '.join(problems))

    report = {'joints': joints, 'joint': joints[0], 'sketches': [], 'features': []}
    # Per tab end face: the slot tool body (sketched on that face before the tabs split it), the
    # tabs, then the slot plate cut with the tool. The slots are the TAB plate's own geometry, so
    # they follow it through any parameter change (thickness, length, a joint moving it).
    # Faces measured up front can go stale when a plate is both a tab and a slot plate
    # (_live_face handles that).
    for unit in tab_units:
        tools = _build_slot_tool(unit[0], p, constrain, report)
        _build_tabs(unit[0], p, constrain, report)
        _cut_slots(unit, tools, report)
    return report


def _build_tabs(j: Joint, p: Params, constrain: bool, report: dict):
    centers, _ = layout_tabs(j, p)
    comp_a = j.body_a.parentComponent
    r = p.dogbone_r
    h_tab = j.t_b + p.protrusion
    broad = _live_face(j.broad_a, j.body_a, j.xf_a_inv,
                       _add(j.origin, (j.length / 2.0, j.d), (j.broad_w, j.w), (-0.01, j.n)),
                       j.w if j.broad_w > j.t_a / 2.0 else _unit((-1.0, j.w)))

    # --- Tab plate: ONE sketch on the broad face (before it changes) holding the tab rectangles
    # and their root dogbones; the join takes the tab regions, the cut takes the circle regions.
    tabs_sk = _Sk(_new_sketch(comp_a, broad, 'Tab & Slot - Tabs Sketch'), j, j.xf_a_inv, constrain)
    tab_axis, h_expr = None, None
    if constrain:
        # Follow a plate change: tab roots sit on the plate's own end edge (projected -- same
        # component, so no cross-component reference), and the tab length is the slot plate's
        # thickness parameter (+ protrusion) rather than a number.
        try:
            fa = _live_face(j.fa, j.body_a, j.xf_a_inv,
                            _add(j.origin, (j.length / 2.0, j.d), (j.t_a / 2.0, j.w)), j.n)
            tab_axis = _root_axis(tabs_sk, fa, broad)
        except Exception:
            tab_axis = None
        if tab_axis is None:
            futil.log('Tab & Slot: tab roots fall back to a fixed axis')
            tab_axis = tabs_sk.axis(j.broad_w)
        h_expr = tab_length_expr(j, p)
        if h_expr is None:
            futil.log('Tab & Slot: tab length is a fixed number (no slot plate thickness parameter)')
    tab_spans, circles, bottoms = [], [], []
    k = r / math.sqrt(2.0)
    for c in centers:
        sa, sb = c - p.tab_width / 2.0, c + p.tab_width / 2.0
        _, bottom = _rect_on_axis(tabs_sk, tab_axis, sa, sb, j.broad_w, 0, 1, h_tab, height_expr=h_expr,
                                  position_dim=False)
        bottoms.append(bottom)
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
    if constrain and all(b is not None for b in bottoms):
        _space_along(tabs_sk, tab_axis, bottoms, _spans_whole_axis(tabs_sk, tab_axis), p.margin, p.tab_width)
    _finish(tabs_sk.sketch, constrain, report)
    join_profiles, relief_profiles = _split_tab_profiles(tabs_sk, tab_spans, circles, r, h_tab)
    report['sketches'].append(tabs_sk.sketch)

    into_a = j.w.copy()
    if j.broad_w > j.t_a / 2.0:
        into_a.scaleBy(-1)                              # sketch is on the +w face: go -w
    expected_join = len(centers) * p.tab_width * j.t_a * h_tab

    xf_a = j.xf_a_inv.copy()
    xf_a.invert()

    def inside_plate(feat):
        # A tab extruded the wrong way sits on the broad face, touching the plate only along an
        # edge: same volume, still one lump, so the volume check alone passes it (seen live on a
        # plate whose occurrence is flipped 180 deg). Every tab vertex must stay within w in [0, tA].
        for f in feat.faces:
            for v in f.vertices:
                wv = _dot(_sub(_xpt(v.geometry, xf_a), j.origin), j.w)
                if wv < -10 * _TOL or wv > j.t_a + 10 * _TOL:
                    return False
        return True

    def grew(before, body, feat):
        dv = body.volume - before
        return (abs(dv - expected_join) < 0.01 * expected_join and body.lumps.count == 1
                and inside_plate(feat))

    # Tabs must stay full plate thickness: a distance driven by the plate's own thickness
    # parameter. (A to-face extent also follows, but then Fusion loses the corner-round fillet's
    # edges on every recompute -- checked live.)
    join_extent = None
    thk = _thickness_expression(j.body_a, j.t_a)
    if thk is not None:
        join_extent = lambda: adsk.fusion.DistanceExtentDefinition.create(
            adsk.core.ValueInput.createByString(thk))
    else:
        futil.log('Tab & Slot: tab depth is a fixed number (no tab plate thickness parameter found)')
    tabs = _extrude(comp_a, tabs_sk.sketch, j.body_a, adsk.fusion.FeatureOperations.JoinFeatureOperation,
                    j.t_a, into_a, j.xf_a_inv, grew, join_profiles, extent=join_extent)
    tabs.name = 'Tab & Slot - Tabs (Join)'
    relief = _extrude(comp_a, tabs_sk.sketch, j.body_a, adsk.fusion.FeatureOperations.CutFeatureOperation,
                      j.t_a + CUT_EXTRA, into_a, j.xf_a_inv, _shrank, relief_profiles,
                      extent=_param_depth(thk, CUT_EXTRA))
    relief.name = 'Tab & Slot - Tab Relief (Cut)'
    report['features'] += [tabs, relief]
    # Round the sharp points where each dogbone meets a straight side, so the bit runs smoothly.
    if p.corner_round > 0:
        rnd = _round_cusps(comp_a, relief, into_a, j.xf_a_inv, p.corner_round, 'Tab & Slot - Tab Corner Round')
        if rnd is not None:
            report['features'].append(rnd)
    return tabs


def _shrank(before, body, feat):
    return body.volume < before - 1e-9


def _build_slot_tool(j: Joint, p: Params, constrain: bool, report: dict):
    """The slots' shape as a tool body (or bodies -- one per slot) in the TAB plate's component:
    sketched on the tab plate's end face from its own projected edges, extruded through the slot
    plate's thickness. _cut_slots cuts the slot plate with it and consumes it. Because the tool is
    the tab plate's own geometry, the slots move, widen and re-space with it after any parameter
    change, without a cross-component sketch reference (see _project_lines).
    Returns the extrude feature that made the tool bodies (the fillet keeps them)."""
    centers, _ = layout_tabs(j, p)
    comp_a = j.body_a.parentComponent
    r = p.dogbone_r
    fa = _live_face(j.fa, j.body_a, j.xf_a_inv,
                    _add(j.origin, (j.length / 2.0, j.d), (j.t_a / 2.0, j.w)), j.n)
    sk = _Sk(_new_sketch(comp_a, fa, 'Tab & Slot - Slot Tool Sketch'), j, j.xf_a_inv, constrain)

    # A slot side within a bit diameter of the slot plate's edge (an L joint) can't keep a web,
    # so that side is cut as an open notch: run it a bit diameter past the edge, and skip its
    # dogbones -- those corners are outside the plate, nothing to relieve.
    open_lo, open_hi = open_sides(j, p)
    edge_lo, edge_hi = j.extra.get('b_w_range', (-math.inf, math.inf))
    w_lo = edge_lo - p.bit_dia if open_lo else -p.clear_w
    w_hi = edge_hi + p.bit_dia if open_hi else j.t_a + p.clear_w
    slot_w = w_hi - w_lo

    axis, width_expr = None, None
    if constrain:
        try:
            axis = _tool_axis(sk, fa, w_lo)
        except Exception as ex:
            futil.log(f'Tab & Slot: slot tool axis projection failed ({ex})')
            axis = None
        if axis is None:
            futil.log('Tab & Slot: slot tool falls back to a fixed axis (it still moves with the tab plate)')
            axis = sk.axis(w_lo)
        # Slot width = tab plate thickness + clearances; the axis already sits on the plate's
        # w = 0 face, so the far side follows the plate whichever way it grows.
        thk_a = _thickness_expression(j.body_a, j.t_a)
        if thk_a is not None:
            width_expr = _plus(thk_a, slot_w - j.t_a)
        else:
            futil.log(f'Tab & Slot: slot width for {_joint_name(j)} is a fixed number (no thickness parameter)')

    half = p.tab_width / 2.0 + p.clear_l
    bottoms = []
    for c in centers:
        lines, bottom = _rect_on_axis(sk, axis, c - half, c + half, w_lo, 1, 0, slot_w,
                                      height_expr=width_expr, position_dim=False)
        bottoms.append(bottom)
        lo_side = (sk.pt(c - half, w_lo, 0), sk.pt(c + half, w_lo, 0))
        for cp in _corner_points(lines):
            g = cp.geometry
            on_lo = any(_near(g, q) for q in lo_side)
            if (on_lo and open_lo) or (not on_lo and open_hi):
                continue
            # The slot isn't square, so aim along the 45 deg corner bisector (built from the
            # corner's two sides -- sketch axes needn't line up with the slot), not at its centre.
            bis, side = _corner_bisector(lines, g)
            center = adsk.core.Point3D.create(g.x + r * bis.x, g.y + r * bis.y, 0)
            _dogbone(sk, center, r, cp if constrain else None, side)
    if constrain and all(b is not None for b in bottoms):
        _space_along(sk, axis, bottoms, _spans_whole_axis(sk, axis), p.margin - p.clear_l, 2.0 * half)
    _finish(sk.sketch, constrain, report)
    report['sketches'].append(sk.sketch)

    # Through the slot plate: its thickness parameter + a hair, so it clears the far face.
    depth_expr = _thickness_expression(j.body_b, j.t_b)
    depth_expr = _plus(depth_expr, CUT_EXTRA) if depth_expr else None
    tool = _tool_extrude(comp_a, sk.sketch, j, depth_expr, j.t_b + CUT_EXTRA)
    tool.name = 'Tab & Slot - Slot Tool'
    report['features'].append(tool)
    # Round the tool's inside corners where a dogbone meets a slot side: they become the
    # rounded points in the slot plate.
    if p.corner_round > 0:
        rnd = _round_cusps(comp_a, tool, j.n, j.xf_a_inv, p.corner_round, 'Tab & Slot - Slot Corner Round')
        if rnd is not None:
            report['features'].append(rnd)
    return tool


def _tool_extrude(comp, sketch, j: Joint, depth_expr, depth):
    """New-body extrude of every profile in `sketch` from the contact plane INTO the slot plate
    (+n), `depth_expr` deep (or `depth` cm). Flips once if the bodies came out on the wrong side."""
    xf_a = j.xf_a_inv.copy()
    xf_a.invert()
    into = _xvec(j.n, j.xf_a_inv)
    sk_n = sketch.xDirection.crossProduct(sketch.yDirection)
    positive = _dot(sk_n, into) > 0
    extrudes = comp.features.extrudeFeatures

    def into_slot_plate(feat):
        for b in feat.bodies:
            for v in b.vertices:
                if _dot(_sub(_xpt(v.geometry, xf_a), j.origin), j.n) < -10 * _TOL:
                    return False
        return feat.bodies.count > 0

    for attempt, pos in enumerate((positive, not positive)):
        ext_in = extrudes.createInput(_all_profiles(sketch), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        value = adsk.core.ValueInput.createByString(depth_expr) if depth_expr else \
            adsk.core.ValueInput.createByReal(depth)
        direction = (adsk.fusion.ExtentDirections.PositiveExtentDirection if pos
                     else adsk.fusion.ExtentDirections.NegativeExtentDirection)
        ext_in.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(value), direction)
        try:
            feat = extrudes.add(ext_in)
        except Exception as ex:
            futil.log(f'Tab & Slot: slot tool extrude failed (attempt {attempt + 1}): {ex}')
            continue
        if feat.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState and into_slot_plate(feat):
            return feat
        futil.log(f'Tab & Slot: slot tool went the wrong way (attempt {attempt + 1}), flipping')
        feat.deleteMe()
    raise RuntimeError('Tab & Slot: could not make the slot tool body.')


def _proxy(native, pick):
    """`native` (a body of the pick's component) in the pick's assembly context."""
    occ = getattr(pick, 'assemblyContext', None)
    return native.createForAssemblyContext(occ) if occ is not None else native


def _cut_slots(unit, tool, report: dict):
    """Cut every slot plate in the unit with the tool bodies, placed by each tab-plate copy's
    occurrence: one root-level Combine (cut) per slot plate pick. Every Combine but the last keeps
    the tools; the last consumes them, so the tab plate is back to one body. A Combine across
    components follows both occurrences through the timeline -- unlike a projection (checked live).
    Copies of a slot plate share their native body: a slot landing on the same native spot as one
    already cut is skipped."""
    design = adsk.fusion.Design.cast(unit[0].body_a.parentComponent.parentDesign)
    combines = design.rootComponent.features.combineFeatures
    natives = list(tool.bodies)
    if not natives:
        raise RuntimeError('Tab & Slot: the slot tool made no bodies.')

    groups, done = [], []             # groups: [(slot pick, [joints])]
    for j in unit:
        if any(o.body_b == j.body_b and _same_span(o, j, o.xf_b_inv, j.xf_b_inv) for o in done):
            continue
        done.append(j)
        slot = j.extra.get('slot')
        for pick, js in groups:
            if pick == slot:
                js.append(j)
                break
        else:
            groups.append((slot, [j]))

    for gi, (slot, js) in enumerate(groups):
        target = _proxy(js[0].body_b, slot)
        tools = adsk.core.ObjectCollection.create()
        for j in js:
            for b in natives:
                tools.add(_proxy(b, j.extra.get('tab')))
        cin = combines.createInput(target, tools)
        cin.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
        cin.isKeepToolBodies = gi < len(groups) - 1
        before = target.volume
        comb = combines.add(cin)
        comb.name = 'Tab & Slot - Slots (Cut)'
        report['features'].append(comb)
        after = _proxy(js[0].body_b, slot).volume
        if after > before - 1e-9:
            futil.log(f'Tab & Slot: the slot cut into {plate_name(slot)} removed nothing', force_console=True)


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


def run_items(report: dict):
    """Every sketch and feature a `build` made."""
    return list(report.get('sketches', [])) + list(report.get('features', []))


def tokens(data: dict, role: str) -> list:
    """A run's tab ('tab') or slot ('slot') plate tokens. Runs saved before multi-select stored
    one 'tab_token' / 'slot_token'."""
    toks = data.get(f'{role}_tokens')
    if toks:
        return list(toks)
    tok = data.get(f'{role}_token')
    return [tok] if tok else []


def stored_joints(data: dict) -> list:
    """The joint measurements saved with a run (a list of dicts; old runs saved one 'joint')."""
    return list(data.get('joints') or ([data['joint']] if data.get('joint') else []))


def make_data(tabs, slots, p: Params, joints, bit_label: str, group_name: str) -> dict:
    """Everything an Edit needs: the settings, the plates (entity tokens) and the joints'
    measurements -- the edit dialog validates against these, since the live plates already
    carry this run's tabs and slots."""
    def jd(j):
        lo, hi = j.extra.get('b_w_range', (-1e9, 1e9))
        return {'length': j.length, 't_a': j.t_a, 't_b': j.t_b, 'b_w_range': [lo, hi],
                'name': _joint_name(j)}
    return {
        'params': {k: getattr(p, k) for k in ('count', 'tab_width', 'margin', 'bit_dia',
                                              'clear_w', 'clear_l', 'protrusion', 'corner_round')},
        'bit_label': bit_label,
        'tab_tokens': [b.entityToken for b in tabs],
        'slot_tokens': [b.entityToken for b in slots],
        'joints': [jd(j) for j in joints],
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


def rebuild_run(design, run_id, tab_tokens, slot_tokens, p: Params, data: dict) -> dict:
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
    tabs = [_resolve(design, t) for t in tab_tokens]
    slots = [_resolve(design, t) for t in slot_tokens]
    if not tabs or not slots or any(b is None for b in tabs + slots):
        raise RuntimeError('Tab & Slot: could not find the original plates to rebuild on.')
    report = build_many(tabs, slots, p, constrain=True)
    end = timeline.markerPosition - 1
    if end > start:
        new_group = timeline.timelineGroups.add(start, end)
        new_group.name = data.get('group_name', 'Tab & Slot')
        new_group.isCollapsed = True
    data = {k: v for k, v in data.items() if k not in ('tab_token', 'slot_token', 'joint')}
    data.update(make_data(tabs, slots, p, report['joints'], data.get('bit_label', ''),
                          data.get('group_name', 'Tab & Slot')))
    tag_run(report, data)
    timeline.moveToEnd()
    return report


def _signed(expr, value):
    return expr if value >= 0 else f'-( {expr} )'


def _line_dim(sketch, value):
    """[(dimension, line)] for the linear dimensions in `sketch` worth `value` that measure the
    length of one solid (non-construction) line -- end point to end point."""
    out = []
    for dim in sketch.sketchDimensions:
        lin = adsk.fusion.SketchLinearDimension.cast(dim)
        if lin is None or abs(lin.parameter.value - value) > 1e-4:
            continue
        a, b = adsk.fusion.SketchPoint.cast(lin.entityOne), adsk.fusion.SketchPoint.cast(lin.entityTwo)
        if a is None or b is None:
            continue
        for ent in a.connectedEntities:
            ln = adsk.fusion.SketchLine.cast(ent)
            if ln is not None and not ln.isConstruction and \
                    {ln.startSketchPoint.entityToken, ln.endSketchPoint.entityToken} == {a.entityToken, b.entityToken}:
                out.append((lin, ln))
                break
    return out


def _other_line(pt, not_line):
    """The solid line other than `not_line` that ends at sketch point `pt`."""
    for ent in pt.connectedEntities:
        ln = adsk.fusion.SketchLine.cast(ent)
        if ln is not None and not ln.isConstruction and ln != not_line:
            return ln
    return None


def _reanchor_slot(sketch, side, bottom_pt, top_pt):
    """Old slots hang off a fixed axis on their low-w side. When the tab plate grows towards -w,
    the slot must grow that way too: fix the high-w side instead and free the low side (drop its
    axis constraints, the axis offset and the now-redundant length dimension)."""
    bottom, top = _other_line(bottom_pt, side), _other_line(top_pt, side)
    if bottom is None or top is None:
        return False
    bottom_pts = {bottom.startSketchPoint.entityToken, bottom.endSketchPoint.entityToken}
    axis = None
    for pt in (bottom.startSketchPoint, bottom.endSketchPoint):
        for c in list(pt.geometricConstraints):
            co = adsk.fusion.CoincidentConstraint.cast(c)
            ln = adsk.fusion.SketchLine.cast(co.entity) if co is not None else None
            if ln is not None and ln.isConstruction and ln.startSketchPoint.isFixed:
                axis = ln
                co.deleteMe()
    if axis is None:
        return False
    for c in list(bottom.geometricConstraints):
        par = adsk.fusion.ParallelConstraint.cast(c)
        if par is not None and axis in (par.lineOne, par.lineTwo):
            par.deleteMe()
    for dim in list(sketch.sketchDimensions):
        lin = adsk.fusion.SketchLinearDimension.cast(dim)
        if lin is None:
            continue
        ends = set()
        for ent in (lin.entityOne, lin.entityTwo):
            sp = adsk.fusion.SketchPoint.cast(ent)
            if sp is not None:
                ends.add(sp.entityToken)
        if ends == bottom_pts or (axis.startSketchPoint.entityToken in ends and ends & bottom_pts):
            lin.deleteMe()
    top.startSketchPoint.isFixed = True
    top.endSketchPoint.isFixed = True
    return True


def make_run_parametric(design, run_id, data: dict) -> dict:
    """Upgrade a run built before Tab_Length IN PLACE -- no rebuild, so nothing moves today and
    nothing later in the timeline loses its references (rebuilding runs mid-timeline did, live):
    tab length -> Tab_Length (+ protrusion), tab join / relief depth -> the tab plate's thickness
    parameter, slot cut depth -> the slot plate's, slot width -> tab plate thickness + clearance
    (re-anchored when the tab plate grows towards -w). Returns {'changed': [...], 'skipped': [...]}."""
    out = {'changed': [], 'skipped': []}
    p = Params(**data['params'])
    tabs = [_resolve(design, t) for t in tokens(data, 'tab')]
    slots = [_resolve(design, t) for t in tokens(data, 'slot')]
    joints, _ = find_joints([b for b in tabs if b is not None], [b for b in slots if b is not None])
    if not joints:
        out['skipped'].append('no joints found')
        return out
    # Thicknesses as they were when the run was made: features later in the timeline (a pocket in
    # the back panel, say) change what a measurement today gives (seen live).
    # Matched case-blind: an occurrence renamed 'top:1' -> 'Top:1' since (seen live).
    stored = {str(s.get('name')).lower(): s for s in stored_joints(data)}
    for j in joints:
        s = stored.get(_joint_name(j).lower())
        if s:
            j.t_a, j.t_b = s['t_a'], s['t_b']
            j.extra['b_w_range'] = tuple(s['b_w_range'])
    for obj in _run_timeline_objects(design, run_id):
        ent = obj.entity
        sk = adsk.fusion.Sketch.cast(ent)
        ext = adsk.fusion.ExtrudeFeature.cast(ent)
        if sk is not None and sk.name.startswith('Tab & Slot - Tabs Sketch'):
            js = [j for j in joints if j.body_a.parentComponent == sk.parentComponent]
            expr = tab_length_expression(js[0], p) if js else None
            hits = _line_dim(sk, js[0].t_b + p.protrusion) if js else []
            if not expr or not hits:
                out['skipped'].append(f'{sk.parentComponent.name}/{sk.name}: tab length')
                continue
            for dim, _ in hits:
                dim.parameter.expression = expr
            out['changed'].append(f'{sk.parentComponent.name}/{sk.name}: {len(hits)} tab length(s) = {expr}')
        elif sk is not None and sk.name.startswith('Tab & Slot - Slots Sketch'):
            done = set()
            for j in [j for j in joints if j.body_b.parentComponent == sk.parentComponent]:
                open_lo, open_hi = open_sides(j, p)
                edge_lo, edge_hi = j.extra.get('b_w_range', (-math.inf, math.inf))
                w_lo = edge_lo - p.bit_dia if open_lo else -p.clear_w
                w_hi = edge_hi + p.bit_dia if open_hi else j.t_a + p.clear_w
                thk_a, fixed = _thickness_expression(j.body_a, j.t_a), _fixed_side(j)
                if thk_a is None or fixed is None:
                    out['skipped'].append(f'{_joint_name(j)}: slot width (no parameter / fixed side)')
                    continue
                expr = _plus(thk_a, (w_hi - w_lo) - j.t_a)
                frame = _Sk(sk, j, j.xf_b_inv, True)
                n_done = 0
                for dim, ln in _line_dim(sk, w_hi - w_lo):
                    a, b = frame.coords(ln.startSketchPoint.geometry), frame.coords(ln.endSketchPoint.geometry)
                    # This joint's slot sides run across it (along w) from w_lo to w_hi. (Joint
                    # length isn't usable here: measured on today's tabbed end face it's partial.)
                    if dim.entityToken in done or abs(a[0] - b[0]) > 1e-3 or \
                            abs(min(a[1], b[1]) - w_lo) > 1e-3 or abs(max(a[1], b[1]) - w_hi) > 1e-3 or \
                            abs(a[2]) > 1e-3:
                        continue                                # another joint's slot
                    done.add(dim.entityToken)
                    if fixed == 'hi':
                        lo_pt, hi_pt = (ln.startSketchPoint, ln.endSketchPoint) if a[1] < b[1] else \
                            (ln.endSketchPoint, ln.startSketchPoint)
                        if not _reanchor_slot(sk, ln, lo_pt, hi_pt):
                            out['skipped'].append(f'{_joint_name(j)}: could not re-anchor a slot')
                            continue
                    dim.parameter.expression = expr
                    n_done += 1
                out['changed'].append(f'{_joint_name(j)}: {n_done} slot width(s) = {expr} (fixed {fixed})')
            if not sk.isFullyConstrained:
                out['skipped'].append(f'{sk.parentComponent.name}/{sk.name}: NOT fully constrained after upgrade')
        elif ext is not None and ext.name.startswith('Tab & Slot'):
            dist = adsk.fusion.DistanceExtentDefinition.cast(ext.extentOne)
            # `participantBodies` raises "Didn't roll editing feature back" here; `bodies` doesn't.
            if dist is None or ext.bodies.count != 1:
                continue
            body, val = _native(ext.bodies.item(0)), dist.distance.value
            extra = 0.0 if '(Join)' in ext.name else CUT_EXTRA
            thk = _thickness_expression(body, abs(val) - extra)
            if thk is None:
                out['skipped'].append(f'{ext.name}: depth (no thickness parameter)')
                continue
            dist.distance.expression = _signed(_plus(thk, extra), val)
            out['changed'].append(f'{body.parentComponent.name}/{ext.name}: depth = {dist.distance.expression}')
    return out


def _rebind_axis(sk: _Sk, fa, broad) -> bool:
    """Old tab sketches hang every tab off a construction axis with FIXED end points, so when the
    plate's outline changes (its height = something - 2 x plywood, say) the tabs float off its
    end. Tie that axis to the plate's own end edge instead (projected, same component): parallel
    + on the edge, start held at its current distance from the nearest edge end, length kept."""
    sketch = sk.sketch
    axis = next((ln for ln in sketch.sketchCurves.sketchLines
                 if ln.isConstruction and not ln.isReference
                 and ln.startSketchPoint.isFixed and ln.endSketchPoint.isFixed), None)
    if axis is None:
        return False
    ref = _root_axis(sk, fa, broad)
    if ref is None:
        return False
    cons, dims = sketch.geometricConstraints, sketch.sketchDimensions
    aligned = adsk.fusion.DimensionOrientations.AlignedDimensionOrientation
    a0, a1 = axis.startSketchPoint.geometry, axis.endSketchPoint.geometry
    axis.startSketchPoint.isFixed = False
    axis.endSketchPoint.isFixed = False
    cons.addParallel(axis, ref)                 # parallel FIRST (see _rect_on_axis)
    near = min((ref.startSketchPoint, ref.endSketchPoint), key=lambda q: q.geometry.distanceTo(a0))
    if near.geometry.distanceTo(a0) < 1e-5:
        cons.addCoincident(axis.startSketchPoint, near)
    else:
        cons.addCoincident(axis.startSketchPoint, ref)
        dims.addDistanceDimension(near, axis.startSketchPoint, aligned, _text_pt(near.geometry, a0, -0.4))
    dims.addDistanceDimension(axis.startSketchPoint, axis.endSketchPoint, aligned, _text_pt(a0, a1, -0.6))
    return True


def anchor_tab_axes(design, run_id, data: dict) -> dict:
    """Re-tie every tab sketch of an older run to its plate's own end edge (see _rebind_axis).
    Each sketch is edited with the timeline rolled to just after it -- the end edge it projects
    only exists there (the tab join splits it) -- then the timeline goes back to the end."""
    out = {'changed': [], 'skipped': []}
    timeline = design.timeline
    tabs = [_resolve(design, t) for t in tokens(data, 'tab')]
    slots = [_resolve(design, t) for t in tokens(data, 'slot')]
    sketches = [o.entity for o in _run_timeline_objects(design, run_id)
                if adsk.fusion.Sketch.cast(o.entity) is not None and o.entity.name.startswith('Tab & Slot - Tabs Sketch')]
    try:
        for sketch in sketches:
            label = f'{sketch.parentComponent.name}/{sketch.name}'
            sketch.timelineObject.rollTo(False)         # marker just after the sketch
            joints, _ = find_joints([b for b in tabs if b is not None], [b for b in slots if b is not None])
            hit = None
            for j in joints:
                if j.body_a.parentComponent != sketch.parentComponent:
                    continue
                frame = _Sk(sketch, j, j.xf_a_inv, True)
                axis = next((ln for ln in sketch.sketchCurves.sketchLines
                             if ln.isConstruction and ln.startSketchPoint.isFixed and ln.endSketchPoint.isFixed), None)
                if axis is None:
                    break
                a, b = frame.line_coords(axis)
                # Either broad face: with the timeline rolled back, joint detection can pick the
                # other one than the sketch sits on (seen live) -- the sketch's own face decides.
                wv = a[1]
                if abs(a[2]) < 1e-3 and abs(b[2]) < 1e-3 and abs(a[1] - b[1]) < 1e-3 and \
                        (abs(wv) < 1e-3 or abs(wv - j.t_a) < 1e-3):
                    j.broad_w = 0.0 if abs(wv) < 1e-3 else j.t_a
                    hit = (frame, j)
                    break
            if hit is None:
                out['skipped'].append(f'{label}: no fixed axis / matching joint')
                continue
            frame, j = hit
            # The sketch's face: beside the end face, on the side the axis is on. (Reading
            # sketch.referencePlane needs a different rollback -- it raised here.)
            xf_a = j.xf_a_inv.copy()
            xf_a.invert()
            broad = None
            for e in j.fa.edges:
                for f in e.faces:
                    if f == j.fa or not _planar(f):
                        continue
                    p, nn = _world_face(f, xf_a)
                    if abs(abs(_dot(nn, j.w)) - 1.0) < 1e-4 and abs(_dot(_sub(p, j.origin), j.w) - j.broad_w) < 1e-3:
                        broad = f if broad is None or f.area > broad.area else broad
            if _rebind_axis(frame, j.fa, broad or j.broad_a):
                ok = sketch.isFullyConstrained
                out['changed' if ok else 'skipped'].append(
                    f'{label}: axis tied to the plate edge' + ('' if ok else ' -- NOT fully constrained'))
            else:
                out['skipped'].append(f'{label}: could not project the plate edge')
    finally:
        timeline.moveToEnd()
    return out


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


def runs_for_body(design, body) -> list:
    """[(run id, data)] of the runs that used `body` as their tab or slot plate, newest first."""
    try:
        token = body.entityToken
    except Exception:
        return []
    out = []
    for run_id, data in list_runs(design):
        toks = tokens(data, 'tab') + tokens(data, 'slot')
        if token in toks:
            out.append((run_id, data))
            continue
        # Tokens can be spelled differently for the same body -- compare the entities too.
        for tok in toks:
            ent = _resolve(design, tok)
            if ent is not None and ent == body:
                out.append((run_id, data))
                break
    return out
