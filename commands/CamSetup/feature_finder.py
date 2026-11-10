# Geometry analysis for the FRC CAM Setup command.
#
# Looks at a solid body and works out what has to be machined, without touching
# the design: the machining direction, through holes (sorted into drill / small /
# bolt / bearing / large), non-round through cut-outs, blind pockets and the
# outside profile for a plate; the per-wall holes for a box tube.
#
# Fusion works in centimetres; everything stored on the result objects that a
# person reads (diameters, thickness, radii) is converted to INCHES.

import math
from dataclasses import dataclass, field

import adsk.core
import adsk.fusion

from . import cam_data

IN = 2.54           # cm per inch
PAR_TOL = 0.999     # |dot| above this counts as parallel
LEN_TOL = 0.002     # cm, ~0.0008in -- geometric coincidence


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class Hole:
    faces: list                 # the cylindrical BRepFaces (a hole can be split in two)
    diameter: float             # in
    kind: str                   # 'drill' | 'small' | 'bolt' | 'bearing' | 'large' | 'too_small'
    tool: str = ''              # cam_data.TOOLS key
    edges: list = field(default_factory=list)   # loop edges on the exit face, for 'large'/'bearing' adaptive

@dataclass
class Cutout:
    edges: list                 # closed loop of BRepEdges (on the exit side)
    tool: str
    min_radius: float           # in, smallest concave corner radius (inf = none)
    width: float                # in, rough minimum width (2 * area / perimeter)

@dataclass
class Pocket:
    floor: adsk.fusion.BRepFace
    depth: float                # in, below the top face
    tool: str
    min_radius: float

@dataclass
class PlateFeatures:
    body: adsk.fusion.BRepBody
    top_face: adsk.fusion.BRepFace
    bottom_face: adsk.fusion.BRepFace
    x_edge: object              # straight BRepEdge for the WCS X axis (or None)
    thickness: float            # in
    profile_tool: str
    holes: list = field(default_factory=list)
    cutouts: list = field(default_factory=list)
    pockets: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    kind: str = 'plate'

@dataclass
class TubeSide:
    face: adsk.fusion.BRepFace  # outer wall face; its normal is the setup's +Z
    x_edge: object              # straight edge along the tube, for the WCS X axis
    wall: float                 # in
    holes: list = field(default_factory=list)
    cutouts: list = field(default_factory=list)

@dataclass
class TubeFeatures:
    body: adsk.fusion.BRepBody
    sides: list = field(default_factory=list)     # only the sides that have features
    warnings: list = field(default_factory=list)
    kind: str = 'tube'


# ---------------------------------------------------------------------------
# Small vector helpers (tuples, cm)
# ---------------------------------------------------------------------------
def _v(p):
    return (p.x, p.y, p.z)

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)

def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

def _unit(a):
    m = math.sqrt(_dot(a, a))
    return _mul(a, 1.0 / m) if m > 1e-12 else (0.0, 0.0, 0.0)

def _parallel(a, b):
    return abs(_dot(_unit(a), _unit(b))) > PAR_TOL


def _face_normal(face):
    # Outward normal of a planar face (BRep normals point out of the material).
    ok, n = face.evaluator.getNormalAtPoint(face.pointOnFace)
    return _unit(_v(n))

def _plane_height(face, n):
    return _dot(_v(face.pointOnFace), n)

def _is_plane(face):
    return face.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType

def _is_cylinder(face):
    return face.geometry.surfaceType == adsk.core.SurfaceTypes.CylinderSurfaceType


class _Frame:
    # 2D frame on a plane with normal n: u along x_dir, v = n x u.
    def __init__(self, n, x_dir=None):
        self.n = _unit(n)
        if x_dir is None or _parallel(x_dir, n):
            x_dir = (1.0, 0.0, 0.0) if abs(self.n[0]) < 0.9 else (0.0, 1.0, 0.0)
        x_dir = _sub(x_dir, _mul(self.n, _dot(x_dir, self.n)))
        self.u = _unit(x_dir)
        self.v = _cross(self.n, self.u)

    def to2d(self, p):
        return (_dot(p, self.u), _dot(p, self.v))


# ---------------------------------------------------------------------------
# Loop helpers
# ---------------------------------------------------------------------------
def _loop_polygon(edges, frame):
    # Sample a loop into a 2D polygon (cm). Stroke points of each edge, in order
    # of the edge list -- good enough for area / point-in-polygon tests.
    pts = []
    for e in edges:
        ok, strokes = e.evaluator.getStrokes(e.evaluator.getParameterExtents()[1],
                                             e.evaluator.getParameterExtents()[2], 0.01)
        if ok:
            pts.extend(frame.to2d(_v(p)) for p in strokes)
    return _order_polygon(pts)

def _order_polygon(pts):
    # Strokes from separate edges can run either way; sort by angle around the
    # centroid. Fine for the star-shaped loops FRC cut-outs nearly always are,
    # and only used for area / containment estimates.
    if len(pts) < 3:
        return pts
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return sorted(pts, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))

def _poly_area_perimeter(poly):
    a = 0.0
    per = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
        per += math.hypot(x2 - x1, y2 - y1)
    return abs(a) / 2.0, per

def _point_in_poly(pt, poly):
    x, y = pt
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside

def _concave_radius(edges, frame, region_inside):
    # Smallest radius (in) of an arc whose centre lies on the side the tool cuts:
    # inside the loop for a cut-out / pocket floor (region_inside=True), outside
    # it for the outer profile. Those are the inside corners an end mill has to
    # fit into. inf when there are none.
    poly = _loop_polygon(edges, frame)
    best = math.inf
    for e in edges:
        g = e.geometry
        if g.curveType not in (adsk.core.Curve3DTypes.Arc3DCurveType,
                               adsk.core.Curve3DTypes.Circle3DCurveType):
            continue
        c = frame.to2d(_v(g.center))
        if _point_in_poly(c, poly) == region_inside:
            best = min(best, g.radius / IN)
    return best

def _chained_polygon(edges, frame, tol=0.001):
    # The loop as a properly ordered 2D polygon: stroke each edge and append it
    # end-to-start, reversing an edge whose far end is the one that touches.
    strokes = []
    for e in edges:
        ev = e.evaluator
        ok, t0, t1 = ev.getParameterExtents()
        ok, pts = ev.getStrokes(t0, t1, tol)
        strokes.append([frame.to2d(_v(p)) for p in pts])
    poly = list(strokes.pop(0))
    while strokes:
        end = poly[-1]
        best = min(range(len(strokes)), key=lambda i: min(math.dist(end, strokes[i][0]),
                                                          math.dist(end, strokes[i][-1])))
        s = strokes.pop(best)
        if math.dist(end, s[-1]) < math.dist(end, s[0]):
            s = s[::-1]
        poly.extend(s[1:])
    return poly

def material_on_left(edges, up):
    # A CAM ChainSelection from the API cuts on the LEFT of its first edge's
    # direction (seen from +Z = up). True when that side is material, i.e. the
    # chain must be reversed to cut inside the hole / cut-out. Pure edge
    # geometry -- face queries go wrong on parts inside moved components.
    e = edges[0]
    ev = e.evaluator
    ok, t0, t1 = ev.getParameterExtents()
    tm = t0 + 0.25 * (t1 - t0)
    ok, p = ev.getPointAtParameter(tm)
    ok, d = ev.getFirstDerivative(tm)
    left = _unit(_cross(up, _unit((d.x, d.y, d.z))))
    g = e.geometry
    if len(edges) == 1 and g.curveType == adsk.core.Curve3DTypes.Circle3DCurveType:
        # exact for a round hole: left points away from the centre -> material
        return _dot(left, _sub(_v(p), _v(g.center))) > 0
    frame = _Frame(up)
    q = frame.to2d(_add(_v(p), _mul(left, 0.02)))      # 0.2 mm to the left
    return not _point_in_poly(q, _chained_polygon(edges, frame))

def _loop_is_circle(loop):
    # (True, diameter_in, centre) when every edge is an arc/circle on one centre.
    centre = None
    radius = None
    for e in loop.edges:
        g = e.geometry
        if g.curveType not in (adsk.core.Curve3DTypes.Arc3DCurveType,
                               adsk.core.Curve3DTypes.Circle3DCurveType):
            return False, 0.0, None
        c = _v(g.center)
        if centre is None:
            centre, radius = c, g.radius
        elif math.dist(c, centre) > LEN_TOL or abs(g.radius - radius) > LEN_TOL:
            return False, 0.0, None
    return True, 2.0 * radius / IN, centre

def _loop_key(loop, frame):
    # Projected 2D bounding box of a loop -- used to match a top loop with the
    # bottom loop of the same through feature.
    pts = []
    for e in loop.edges:
        ok, strokes = e.evaluator.getStrokes(e.evaluator.getParameterExtents()[1],
                                             e.evaluator.getParameterExtents()[2], 0.02)
        if ok:
            pts.extend(frame.to2d(_v(p)) for p in strokes)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))

def _same_key(a, b, tol=0.01):
    return all(abs(x - y) < tol for x, y in zip(a, b))

def _wall_faces(loop, owner):
    # Faces on the other side of each loop edge (the walls of a hole / cut-out).
    out = []
    for e in loop.edges:
        for f in e.faces:
            if f != owner and f not in out:
                out.append(f)
    return out

def part_name(body):
    # Bodies are usually all "Body1"; the component occurrence says which part it is.
    occ = body.assemblyContext
    return occ.name if occ else body.name


def _longest_line(edges):
    best, best_len = None, 0.0
    for e in edges:
        if e.geometry.curveType == adsk.core.Curve3DTypes.Line3DCurveType and e.length > best_len:
            best, best_len = e, e.length
    return best


# ---------------------------------------------------------------------------
# Hole / cut-out classification
# ---------------------------------------------------------------------------
def classify_hole(d):
    # -> (kind, tool) for a round through hole of diameter d (in).
    t14 = cam_data.TOOLS['1/4']['diameter']
    t18 = cam_data.TOOLS['1/8']['diameter']
    for od in cam_data.BEARING_ODS:
        if abs(d - od) <= cam_data.BEARING_TOL:
            return 'bearing', '1/4'
    if abs(d - t18) <= cam_data.DRILL_MATCH_TOL:
        return 'drill', '1/8'
    if d < t18:
        return 'too_small', ''
    if d < cam_data.MIN_BORE_RATIO * t18:
        return 'drill', '1/8'       # barely bigger than the bit: plunge it
    if d < cam_data.SMALL_HOLE_MAX or d < cam_data.MIN_BORE_RATIO * t14:
        return 'small', '1/8'
    if d > cam_data.BORE_MAX:
        return 'large', '1/4'
    return 'bolt', '1/4'

def _cutout_tool(min_radius, width):
    if min_radius < cam_data.SMALL_TOOL_RADIUS or width < cam_data.SMALL_TOOL_WIDTH:
        return '1/8'
    return '1/4'

def _make_hole(loop, owner, d):
    kind, tool = classify_hole(d)
    faces = [f for f in _wall_faces(loop, owner) if _is_cylinder(f)]
    return Hole(faces=faces, diameter=d, kind=kind, tool=tool, edges=list(loop.edges))

def _make_cutout(loop, frame):
    edges = list(loop.edges)
    r = _concave_radius(edges, frame, True)
    area, per = _poly_area_perimeter(_loop_polygon(edges, frame))
    width = (2.0 * area / per) / IN if per > 0 else 0.0
    return Cutout(edges=edges, tool=_cutout_tool(r, width), min_radius=r, width=width)


# ---------------------------------------------------------------------------
# Plates
# ---------------------------------------------------------------------------
def analyze_plate(body, prefer_dir=(0.0, 0.0, 1.0)):
    planes = [f for f in body.faces if _is_plane(f)]
    if not planes:
        raise ValueError(f'{part_name(body)}: no flat faces -- not a plate')

    # The plate's big faces: the largest planar face and the farthest planar
    # face facing the other way.
    big = max(planes, key=lambda f: f.area)
    n = _face_normal(big)
    opposite = [f for f in planes if _dot(_face_normal(f), n) < -PAR_TOL]
    if not opposite:
        raise ValueError(f'{part_name(body)}: no face opposite the largest face -- not a plate')
    other = min(opposite, key=lambda f: _plane_height(f, n))

    # Which side is up: the side the blind pocket floors face, else whichever
    # big face points most along prefer_dir (world +Z by default).
    def floors_facing(normal, top):
        h_top = _plane_height(top, normal)
        return [f for f in planes if f != top and _dot(_face_normal(f), normal) > PAR_TOL
                and _plane_height(f, normal) < h_top - LEN_TOL]
    up_a = floors_facing(n, big)
    up_b = floors_facing(_mul(n, -1), other)
    if up_a and not up_b:
        top, bottom = big, other
    elif up_b and not up_a:
        top, bottom = other, big
    else:
        top, bottom = (big, other) if _dot(n, prefer_dir) >= _dot(_mul(n, -1), prefer_dir) else (other, big)

    up = _face_normal(top)
    thickness = abs(_dot(_sub(_v(top.pointOnFace), _v(bottom.pointOnFace)), up)) / IN

    outer_top = next(l for l in top.loops if l.isOuter)
    x_edge = _longest_line(outer_top.edges)
    frame = _Frame(up, _sub(_v(x_edge.endVertex.geometry), _v(x_edge.startVertex.geometry)) if x_edge else None)

    profile_r = _concave_radius(list(outer_top.edges), frame, False)
    feats = PlateFeatures(body=body, top_face=top, bottom_face=bottom, x_edge=x_edge,
                          thickness=thickness, profile_tool=_cutout_tool(profile_r, math.inf))

    # Through features: an inner loop on the bottom face with a matching loop
    # on the top face. Machine from the bottom loop (it sits at the exit depth).
    top_keys = [(_loop_key(l, frame), l) for l in top.loops if not l.isOuter]
    matched_top = set()
    for loop in bottom.loops:
        if loop.isOuter:
            continue
        key = _loop_key(loop, frame)
        hit = next((i for i, (k, _) in enumerate(top_keys) if _same_key(k, key)), None)
        if hit is None:
            feats.warnings.append(f'{part_name(body)}: a feature open only on the bottom face was skipped '
                                  '(flip the part and run again)')
            continue
        matched_top.add(hit)
        is_circle, d, _ = _loop_is_circle(loop)
        if is_circle:
            hole = _make_hole(loop, bottom, d)
            if hole.kind == 'too_small':
                feats.warnings.append(f'{part_name(body)}: {d:.3f}" hole is smaller than the 1/8" bit -- skipped')
                continue
            feats.holes.append(hole)
        else:
            feats.cutouts.append(_make_cutout(loop, frame))

    # Blind pockets: planar floors facing up, below the top face.
    h_top = _plane_height(top, up)
    for f in planes:
        if f == top or _dot(_face_normal(f), up) < PAR_TOL:
            continue
        depth = (h_top - _plane_height(f, up)) / IN
        if depth <= LEN_TOL / IN:
            continue
        outer = next(l for l in f.loops if l.isOuter)
        r = _concave_radius(list(outer.edges), frame, True)
        area, per = _poly_area_perimeter(_loop_polygon(list(outer.edges), frame))
        width = (2.0 * area / per) / IN if per > 0 else 0.0
        feats.pockets.append(Pocket(floor=f, depth=depth, tool=_cutout_tool(r, width), min_radius=r))

    return feats


# ---------------------------------------------------------------------------
# Box tube
# ---------------------------------------------------------------------------
def _tube_axis(body):
    # A box tube has two planar end faces, each with a hole in it (the bore of
    # the tube), facing opposite ways and far apart. -> (axis, end_face) or None.
    ends = [f for f in body.faces if _is_plane(f) and f.loops.count >= 2]
    for a in ends:
        na = _face_normal(a)
        for b in ends:
            if b == a or _dot(na, _face_normal(b)) > -PAR_TOL:
                continue
            length = abs(_dot(_sub(_v(a.pointOnFace), _v(b.pointOnFace)), na))
            bb = a.boundingBox
            size = max(bb.maxPoint.x - bb.minPoint.x, bb.maxPoint.y - bb.minPoint.y,
                       bb.maxPoint.z - bb.minPoint.z)
            if length > 2.0 * size:
                return na, a
    return None

def is_tube(body):
    return _tube_axis(body) is not None

def analyze_tube(body):
    found = _tube_axis(body)
    if not found:
        raise ValueError(f'{part_name(body)}: not a box tube (no open end faces found)')
    axis, end_face = found
    feats = TubeFeatures(body=body)

    # Wall thickness per direction, from the end face's outer vs inner loop.
    def wall_along(d):
        outer = next(l for l in end_face.loops if l.isOuter)
        inner = next(l for l in end_face.loops if not l.isOuter)
        def extent(loop):
            vals = []
            for e in loop.edges:
                ok, pts = e.evaluator.getStrokes(e.evaluator.getParameterExtents()[1],
                                                 e.evaluator.getParameterExtents()[2], 0.02)
                vals.extend(_dot(_v(p), d) for p in pts)
            return max(vals)
        return (extent(outer) - extent(inner)) / IN

    # Outer side faces: planar, perpendicular to the axis, outermost along their normal.
    sides = {}
    for f in body.faces:
        if not _is_plane(f):
            continue
        nf = _face_normal(f)
        if abs(_dot(nf, axis)) > 1.0 - PAR_TOL:
            continue
        key = tuple(round(c, 3) for c in nf)
        h = _plane_height(f, nf)
        if key not in sides or h > sides[key][1]:
            sides[key] = (f, h)

    for key, (face, _) in sides.items():
        nf = _face_normal(face)
        side = TubeSide(face=face, x_edge=None, wall=wall_along(nf))
        lines = [e for e in face.edges
                 if e.geometry.curveType == adsk.core.Curve3DTypes.Line3DCurveType
                 and _parallel(_sub(_v(e.endVertex.geometry), _v(e.startVertex.geometry)), axis)]
        side.x_edge = max(lines, key=lambda e: e.length) if lines else None
        frame = _Frame(nf, axis)
        for loop in face.loops:
            if loop.isOuter:
                continue
            is_circle, d, _ = _loop_is_circle(loop)
            if is_circle:
                hole = _make_hole(loop, face, d)
                if hole.kind == 'too_small':
                    feats.warnings.append(f'{part_name(body)}: {d:.3f}" hole is smaller than the 1/8" bit -- skipped')
                    continue
                side.holes.append(hole)
            else:
                side.cutouts.append(_make_cutout(loop, frame))
        if side.holes or side.cutouts:
            feats.sides.append(side)
    return feats


def analyze(body, part_type='Auto'):
    # part_type: 'Auto' | 'Plate' | 'Tube'
    if part_type == 'Tube' or (part_type == 'Auto' and is_tube(body)):
        return analyze_tube(body)
    return analyze_plate(body)


def summarize(feats):
    # One line for the dialog, e.g. "Plate 0.250in: 3 bolt, 1 bearing, 2 cut-outs, 1 pocket, profile".
    def hole_counts(holes):
        names = {'drill': 'drilled', 'small': 'small', 'bolt': 'bolt', 'bearing': 'bearing', 'large': 'large'}
        counts = {}
        for h in holes:
            counts[names[h.kind]] = counts.get(names[h.kind], 0) + 1
        return [f'{c} {k}' for k, c in counts.items()]

    if feats.kind == 'tube':
        parts = []
        for s in feats.sides:
            bits = hole_counts(s.holes)
            if s.cutouts:
                bits.append(f'{len(s.cutouts)} cut-out')
            parts.append(', '.join(bits))
        return f'{part_name(feats.body)}: tube, {len(feats.sides)} face(s) -- ' + ' | '.join(parts)

    bits = hole_counts(feats.holes)
    if feats.cutouts:
        bits.append(f'{len(feats.cutouts)} cut-out')
    if feats.pockets:
        bits.append(f'{len(feats.pockets)} pocket')
    bits.append('profile')
    return f'{part_name(feats.body)}: plate {feats.thickness:.3f}" -- ' + ', '.join(bits)
