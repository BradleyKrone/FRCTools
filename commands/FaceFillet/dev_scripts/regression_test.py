"""FaceFillet regression test — a live Fusion script, not a pytest file.

This repo has no automated test framework (Fusion API code only runs inside a
live Fusion session), so this exercises FaceFillet's internal geometry
functions directly against real bodies instead of driving the interactive
dialog. It builds its own throwaway bodies, calls the same functions
`entry.py`'s command handlers call (using duck-typed stand-ins for the
CommandInputs object where needed, same idea as CCDistance's dialog-validation
stubs), checks the resulting classification/geometry, then deletes everything
it created (tracked by the ``FFTEST_`` name prefix) — safe to run against a
document that already has other stuff open in it.

How to run:
  - Via the Fusion MCP tools: read this file's contents and pass them as
    `object.script` to `fusion_mcp_execute` with `featureType: "script"`.
  - Or paste this file's contents into a new script in Fusion's Scripts and
    Add-Ins panel (Shift+S) and run it directly.

Either way, make sure the FRCTools add-in is currently running (so
`entry.py`'s module is loaded) — this script finds it via `sys.modules` and
`importlib.reload()`s it first, so it always tests whatever is currently
saved on disk, not a stale cached copy.

Covers:
  - convex (external) classification on a plain box
  - concave (internal) classification on a blind pocket
  - the bounding-box-centroid bug: convexity on an asymmetric "staple" shape
    whose bounding-box center sits in open space between two prongs, far from
    the material's actual mass centroid
  - the 30-degree angle-tolerance boundary
  - the all-at-once fillet succeeding cleanly
  - the all-at-once -> per-edge fallback path when one edge's radius doesn't fit
  - the "nothing to do" case returning cleanly instead of throwing

Add a new `_test_*` function here whenever a new FaceFillet code path is
added, and re-run this whole file after any change to entry.py's geometry
functions -- that's the point of keeping it around instead of re-typing
throwaway scripts each time.
"""

import adsk.core
import adsk.fusion
import sys
import importlib
import math
import traceback

IN_TO_CM = 2.54
NAME_PREFIX = 'FFTEST_'


def _get_entry_module():
    key = next((k for k in sys.modules if k.endswith('FaceFillet.entry')), None)
    if key is None:
        raise RuntimeError('FaceFillet.entry not found in sys.modules -- is the FRCTools add-in running?')
    module = sys.modules[key]
    importlib.reload(module)
    return module


# ---------------------------------------------------------------------------
# Geometry builders
# ---------------------------------------------------------------------------

def _make_box(root, extrudes, name, w_in, d_in, h_in, x_off_in, y_off_in=0.0):
    sk = root.sketches.add(root.xYConstructionPlane)
    sk.name = NAME_PREFIX + name + '_sketch'
    p0 = adsk.core.Point3D.create(x_off_in * IN_TO_CM, y_off_in * IN_TO_CM, 0)
    p1 = adsk.core.Point3D.create((x_off_in + w_in) * IN_TO_CM, (y_off_in + d_in) * IN_TO_CM, 0)
    sk.sketchCurves.sketchLines.addTwoPointRectangle(p0, p1)
    prof = sk.profiles.item(0)
    ei = extrudes.createInput(prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ei.setDistanceExtent(False, adsk.core.ValueInput.createByReal(h_in * IN_TO_CM))
    body = extrudes.add(ei).bodies.item(0)
    body.name = NAME_PREFIX + name
    return body


def _make_polygon_prism(root, extrudes, name, points_in, height_in, x_off_in=0.0, y_off_in=0.0):
    """Extrudes a closed 2D polygon (list of (x_in, y_in) tuples, in order)
    straight up along Z. Shared corner coordinates are enough for Fusion's
    sketch engine to treat the segments as one closed, single-profile loop."""
    sk = root.sketches.add(root.xYConstructionPlane)
    sk.name = NAME_PREFIX + name + '_sketch'
    pts = [adsk.core.Point3D.create((x_off_in + x) * IN_TO_CM, (y_off_in + y) * IN_TO_CM, 0)
           for x, y in points_in]
    lines = sk.sketchCurves.sketchLines
    n = len(pts)
    for i in range(n):
        lines.addByTwoPoints(pts[i], pts[(i + 1) % n])
    prof = sk.profiles.item(0)
    ei = extrudes.createInput(prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ei.setDistanceExtent(False, adsk.core.ValueInput.createByReal(height_in * IN_TO_CM))
    body = extrudes.add(ei).bodies.item(0)
    body.name = NAME_PREFIX + name
    return body


def _make_pocket(root, extrudes, body, name, x0_in, y0_in, x1_in, y1_in, top_z_in, depth_in):
    """Cuts a blind rectangular pocket into *body*'s top (z = top_z_in) and
    returns the pocket's floor face."""
    plane_input = root.constructionPlanes.createInput()
    plane_input.setByOffset(root.xYConstructionPlane, adsk.core.ValueInput.createByReal(top_z_in * IN_TO_CM))
    plane = root.constructionPlanes.add(plane_input)
    plane.name = NAME_PREFIX + name + '_pocketplane'

    sk = root.sketches.add(plane)
    sk.name = NAME_PREFIX + name + '_pocket_sketch'
    p0 = adsk.core.Point3D.create(x0_in * IN_TO_CM, y0_in * IN_TO_CM, 0)
    p1 = adsk.core.Point3D.create(x1_in * IN_TO_CM, y1_in * IN_TO_CM, 0)
    sk.sketchCurves.sketchLines.addTwoPointRectangle(p0, p1)
    prof = sk.profiles.item(0)

    ci = extrudes.createInput(prof, adsk.fusion.FeatureOperations.CutFeatureOperation)
    ci.participantBodies = [body]
    ci.setDistanceExtent(False, adsk.core.ValueInput.createByReal(-depth_in * IN_TO_CM))
    extrudes.add(ci)

    floor_z_cm = (top_z_in - depth_in) * IN_TO_CM
    return _planar_face(body, 'z', floor_z_cm)


def _planar_face(body, axis, coord_cm, tol=1e-3):
    """First planar face whose normal is along *axis* ('x'/'y'/'z') and whose
    pointOnFace has that axis's coordinate equal to coord_cm."""
    idx = {'x': 0, 'y': 1, 'z': 2}[axis]
    for f in body.faces:
        if not isinstance(f.geometry, adsk.core.Plane):
            continue
        normal = f.geometry.normal
        n_comp = (normal.x, normal.y, normal.z)[idx]
        if abs(n_comp) < 0.99:
            continue
        p = f.pointOnFace
        p_comp = (p.x, p.y, p.z)[idx]
        if abs(p_comp - coord_cm) < tol:
            return f
    return None


def _edge_dir(edge):
    sp = edge.startVertex.geometry
    ep = edge.endVertex.geometry
    dx, dy, dz = ep.x - sp.x, ep.y - sp.y, ep.z - sp.z
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    return adsk.core.Vector3D.create(dx / length, dy / length, dz / length)


# ---------------------------------------------------------------------------
# Duck-typed stand-ins for entry.py's CommandInputs access, so _apply_fillet
# can be exercised without a real command dialog (same idea as CCDistance's
# _StubValueInput / _StubInputs for its validate-handler tests).
# ---------------------------------------------------------------------------

class _StubSelectedItem:
    def __init__(self, entity):
        self.entity = entity


class _StubSelectionInput:
    def __init__(self, entity):
        self._entity = entity

    def selection(self, index):
        return _StubSelectedItem(self._entity)


class _StubValueInput:
    def __init__(self, value):
        self.value = value


class _StubInputs:
    def __init__(self, mapping):
        self._mapping = mapping

    def itemById(self, key):
        return self._mapping[key]


def _make_fillet_inputs(face, radius_cm, want_ext, want_int):
    return _StubInputs({
        'face_selection': _StubSelectionInput(face),
        'fillet_radius': _StubValueInput(radius_cm),
        'external_edges': _StubValueInput(want_ext),
        'internal_edges': _StubValueInput(want_int),
    })


# ---------------------------------------------------------------------------
# Individual tests -- each returns (name, ok, detail)
# ---------------------------------------------------------------------------

def _test_box_external_only(entry, root, extrudes):
    body = _make_box(root, extrudes, 'Box1', 4, 4, 2, 0, 0)
    top = _planar_face(body, 'z', 2 * IN_TO_CM)
    centroid = entry._body_centroid(body)
    ext_edges = entry._collect_perp_edges(top, True, False, centroid)
    int_edges = entry._collect_perp_edges(top, False, True, centroid)
    ok = len(ext_edges) == 4 and len(int_edges) == 0
    return ('box_external_only', ok,
            f'external={len(ext_edges)} internal={len(int_edges)} expected 4/0')


def _test_pocket_internal_only(entry, root, extrudes):
    body = _make_box(root, extrudes, 'Pocket1', 6, 6, 2, 20, 0)
    floor = _make_pocket(root, extrudes, body, 'Pocket1', 22, 2, 24, 4, 2, 1)
    centroid = entry._body_centroid(body)
    int_edges = entry._collect_perp_edges(floor, False, True, centroid)
    ext_edges = entry._collect_perp_edges(floor, True, False, centroid)
    ok = len(int_edges) == 4 and len(ext_edges) == 0
    return ('pocket_internal_only', ok,
            f'internal={len(int_edges)} external={len(ext_edges)} expected 4/0')


def _test_asymmetric_convexity(entry, root, extrudes):
    """Regression for the bounding-box-centroid convexity bug -- confirmed
    live against a reproduction of the pre-fix heuristic before writing this
    test: a 'staple' shape (a wide short base with two long thin prongs)
    whose bounding-box center sits out in the open gap between the prongs,
    far from where the material's mass centroid actually is.

    The old bbox-center heuristic misclassified the convex corners where the
    base meets each prong as concave (independently confirmed wrong via the
    polygon's own winding direction); the mass-centroid fix gets them right.
    The inner corners of the gap were already classified correctly either
    way and are checked here as a sanity baseline."""
    base_w, base_d = 10.0, 2.0
    prong_len, prong_w, gap = 15.0, 1.0, 1.0
    height = 0.5
    pts = [
        (0, 0), (base_w, 0), (base_w, base_d),
        (base_w - prong_w, base_d), (base_w - prong_w, base_d + prong_len),
        (base_w - prong_w - gap, base_d + prong_len), (base_w - prong_w - gap, base_d),
        (prong_w + gap, base_d), (prong_w + gap, base_d + prong_len),
        (prong_w, base_d + prong_len), (prong_w, base_d),
        (0, base_d),
    ]
    x_off = 40
    body = _make_polygon_prism(root, extrudes, 'Staple', pts, height, x_off_in=x_off)
    centroid = entry._body_centroid(body)

    def _find_vertical_edge(x_in, y_in):
        x_cm = (x_off + x_in) * IN_TO_CM
        y_cm = y_in * IN_TO_CM
        for e in body.edges:
            g = e.geometry
            if not isinstance(g, adsk.core.Line3D):
                continue
            sp, ep = g.startPoint, g.endPoint
            if (abs(sp.x - x_cm) < 1e-4 and abs(sp.y - y_cm) < 1e-4
                    and abs(ep.x - x_cm) < 1e-4 and abs(ep.y - y_cm) < 1e-4
                    and abs(abs(sp.z - ep.z) - height * IN_TO_CM) < 1e-4):
                return e
        return None

    expected = {
        'left_junction': (0.0, base_d, True),                     # bug case: was misclassified concave
        'right_junction': (base_w, base_d, True),                  # bug case: was misclassified concave
        'left_notch': (prong_w, base_d, False),                    # sanity baseline (already correct before the fix)
        'right_notch': (base_w - prong_w, base_d, False),          # sanity baseline (already correct before the fix)
    }

    actual = {}
    ok = True
    for label, (x_in, y_in, want_convex) in expected.items():
        edge = _find_vertical_edge(x_in, y_in)
        convex = entry._edge_is_convex(edge, centroid) if edge else None
        actual[label] = convex
        ok = ok and (edge is not None) and (convex == want_convex)

    want = {k: v[2] for k, v in expected.items()}
    return ('asymmetric_convexity', ok, f'actual={actual} expected={want}')


def _test_angle_tolerance_boundary(entry, root, extrudes):
    """A face whose 'perpendicular' candidate edge is slanted at a known
    angle off vertical: 25 degrees (inside the 30-degree tolerance, must be
    included) and 35 degrees (outside it, must be excluded)."""
    sub_results = []
    for theta_deg, x_off, expect_included in ((25.0, 60, True), (35.0, 65, False)):
        theta = math.radians(theta_deg)
        w_in, h_in, depth_in = 2.0, 3.0, 1.0
        pts = [(0.0, 0.0), (w_in, 0.0), (w_in - h_in * math.tan(theta), h_in), (0.0, h_in)]
        body = _make_polygon_prism(root, extrudes, f'Angle{int(theta_deg)}', pts, depth_in, x_off_in=x_off)
        bottom_face = _planar_face(body, 'y', 0.0)
        centroid = entry._body_centroid(body)
        edges = entry._collect_perp_edges(bottom_face, True, True, centroid)
        found_slant = any(abs(_edge_dir(e).x) > 0.05 for e in edges)
        ok = found_slant == expect_included
        sub_results.append((theta_deg, ok, found_slant, expect_included))

    all_ok = all(ok for _, ok, _, _ in sub_results)
    detail = '; '.join(f'{t}deg found={f} expected={e}' for t, _, f, e in sub_results)
    return ('angle_tolerance_boundary', all_ok, detail)


def _test_fillet_all_at_once(entry, root, extrudes):
    body = _make_box(root, extrudes, 'FilletBox', 4, 4, 2, 80, 0)
    top = _planar_face(body, 'z', 2 * IN_TO_CM)
    radius_in = 0.25
    inputs = _make_fillet_inputs(top, radius_in * IN_TO_CM, True, True)
    success, skipped = entry._apply_fillet(inputs, is_preview=False)
    n_arcs = len([e for e in body.edges
                  if isinstance(e.geometry, adsk.core.Arc3D)
                  and abs(e.geometry.radius / IN_TO_CM - radius_in) < 1e-3])
    ok = success and skipped == 0 and n_arcs == 8
    return ('fillet_all_at_once', ok,
            f'success={success} skipped={skipped} arcs={n_arcs} expected True/0/8')


def _test_fillet_partial_fallback(entry, root, extrudes):
    """A radius too large for a plain box's corners to all fit at once
    reliably fails the all-at-once attempt (confirmed live: deterministic
    across repeated runs) and falls back to per-edge, where 2 of the 4
    corners succeed and 2 are skipped -- exercises the
    all-at-once -> per-edge fallback path end to end."""
    w_in, d_in, height = 2.0, 2.0, 2.0
    x_off = 100
    body = _make_box(root, extrudes, 'FilletFallback', w_in, d_in, height, x_off, 0)
    top = _planar_face(body, 'z', height * IN_TO_CM)
    radius_in = 3.0
    inputs = _make_fillet_inputs(top, radius_in * IN_TO_CM, True, True)
    success, skipped = entry._apply_fillet(inputs, is_preview=False)

    remaining_sharp = [
        e for e in body.edges
        if isinstance(e.geometry, adsk.core.Line3D)
        and abs(e.geometry.startPoint.x - e.geometry.endPoint.x) < 1e-6
        and abs(e.geometry.startPoint.y - e.geometry.endPoint.y) < 1e-6
        and abs(abs(e.geometry.startPoint.z - e.geometry.endPoint.z) - height * IN_TO_CM) < 1e-4
    ]
    ok = success and skipped == 2 and len(remaining_sharp) == 2
    return ('fillet_partial_fallback', ok,
            f'success={success} skipped={skipped} remaining_sharp_edges={len(remaining_sharp)} expected True/2/2')


def _test_no_qualifying_edges(entry, root, extrudes):
    """Internal-only on a plain box (zero concave edges) must fail cleanly
    with skipped=0, not raise."""
    body = _make_box(root, extrudes, 'EmptyBox', 4, 4, 2, 120, 0)
    top = _planar_face(body, 'z', 2 * IN_TO_CM)
    inputs = _make_fillet_inputs(top, 0.25 * IN_TO_CM, False, True)
    success, skipped = entry._apply_fillet(inputs, is_preview=False)
    ok = (success is False) and skipped == 0
    return ('no_qualifying_edges', ok, f'success={success} skipped={skipped} expected False/0')


_TESTS = [
    _test_box_external_only,
    _test_pocket_internal_only,
    _test_asymmetric_convexity,
    _test_angle_tolerance_boundary,
    _test_fillet_all_at_once,
    _test_fillet_partial_fallback,
    _test_no_qualifying_edges,
]


def _cleanup(root):
    # Deleting a FFTEST_ body cascades to the timeline features that solely
    # define it (extrude/cut/fillet) -- same behavior AutoHole's regression
    # test already relies on. Only prefix-tagged entities are ever touched,
    # since a real design could be open in the same document.
    for b in list(root.bRepBodies):
        if b.name.startswith(NAME_PREFIX):
            try:
                b.deleteMe()
            except Exception:
                pass
    for s in list(root.sketches):
        if s.name.startswith(NAME_PREFIX):
            try:
                s.deleteMe()
            except Exception:
                pass
    for cp in list(root.constructionPlanes):
        if cp.name.startswith(NAME_PREFIX):
            try:
                cp.deleteMe()
            except Exception:
                pass


def run(_context: str):
    app = adsk.core.Application.get()
    des = adsk.fusion.Design.cast(app.activeProduct)
    root = des.rootComponent
    extrudes = root.features.extrudeFeatures

    entry = _get_entry_module()

    results = []
    try:
        for test_fn in _TESTS:
            try:
                results.append(test_fn(entry, root, extrudes))
            except Exception:
                results.append((test_fn.__name__, False, traceback.format_exc()))
    finally:
        _cleanup(root)

    passed = sum(1 for _, ok, _ in results if ok)
    print(f'FaceFillet regression: {passed}/{len(results)} passed')
    for name, ok, detail in results:
        print(f'  [{"PASS" if ok else "FAIL"}] {name} -- {detail}')

    if passed != len(results):
        failed = [name for name, ok, _ in results if not ok]
        raise RuntimeError(f'FaceFillet regression FAILED: {failed}')
