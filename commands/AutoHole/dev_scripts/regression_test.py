"""AutoHole regression test — a live Fusion script, not a pytest file.

This repo has no automated test framework (Fusion API code only runs inside a
live Fusion session), so this exercises AutoHole's internal geometry
functions directly against real bodies instead of driving the interactive
dialog. It builds its own throwaway plates, calls the same functions
`entry.py`'s command handlers call, checks the resulting geometry, then
deletes everything it created (tracked by the ``AHTEST_`` name prefix) — safe
to run against a document that already has other stuff open in it.

How to run:
  - Via the Fusion MCP tools: read this file's contents and pass them as
    `object.script` to `fusion_mcp_execute` with `featureType: "script"`.
  - Or paste this file's contents into a new script in Fusion's Scripts and
    Add-Ins panel (Shift+S) and run it directly.

Either way, make sure the FRCTools add-in is currently running (so
`entry.py`'s module is loaded) — this script finds it via `sys.modules` and
`importlib.reload()`s it first, so it always tests whatever is currently
saved on disk, not a stale cached copy.

Covers every AutoHole cut path:
  - single-edge straight row, Fill and Custom count
  - single-edge arc row, Fill
  - full circular-edge ring, Fill and Custom count (+ custom hole diameter)
  - outer-edge loop trace, Fill and Custom count
  - "Suppress Holes"

Add a new `_test_*` function here whenever a new AutoHole code path is added,
and re-run this whole file after any change to entry.py's cut-generation
functions — that's the point of keeping it around instead of re-typing
throwaway scripts each time.
"""

import adsk.core
import adsk.fusion
import sys
import importlib
import traceback

IN_TO_CM = 2.54
NAME_PREFIX = 'AHTEST_'


def _get_entry_module():
    key = next((k for k in sys.modules if k.endswith('AutoHole.entry')), None)
    if key is None:
        raise RuntimeError('AutoHole.entry not found in sys.modules -- is the FRCTools add-in running?')
    module = sys.modules[key]
    importlib.reload(module)
    return module


def _make_plate(root, extrudes, name, w_in, h_in, x_off_in, y_off_in=0.0):
    sk = root.sketches.add(root.xYConstructionPlane)
    sk.name = NAME_PREFIX + name + '_sketch'
    p0 = adsk.core.Point3D.create(x_off_in * IN_TO_CM, y_off_in * IN_TO_CM, 0)
    p1 = adsk.core.Point3D.create((x_off_in + w_in) * IN_TO_CM, (y_off_in + h_in) * IN_TO_CM, 0)
    sk.sketchCurves.sketchLines.addTwoPointRectangle(p0, p1)
    prof = sk.profiles.item(0)
    ei = extrudes.createInput(prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ei.setDistanceExtent(False, adsk.core.ValueInput.createByReal(0.25 * IN_TO_CM))
    body = extrudes.add(ei).bodies.item(0)
    body.name = NAME_PREFIX + name
    return body


def _find_top_straight_edge(body, y_in=None):
    """Any straight edge on the top face; if y_in is given, the one at that y."""
    for e in body.edges:
        g = e.geometry
        if isinstance(g, adsk.core.Line3D):
            sp, ep = g.startPoint, g.endPoint
            if abs(sp.z - 0.25 * IN_TO_CM) < 1e-4 and abs(ep.z - 0.25 * IN_TO_CM) < 1e-4:
                if y_in is None or (abs(sp.y - y_in * IN_TO_CM) < 1e-4 and abs(ep.y - y_in * IN_TO_CM) < 1e-4):
                    return e
    return None


def _circle_edges(body, radius_in, tol=1e-3):
    return [e for e in body.edges
            if isinstance(e.geometry, adsk.core.Circle3D) and abs(e.geometry.radius / IN_TO_CM - radius_in) < tol]


def _arc_edges(body):
    return [e for e in body.edges if isinstance(e.geometry, adsk.core.Arc3D)]


def _hole_x_positions(body, radius_in, x_origin_cm=0.0):
    return sorted(set(round((e.geometry.center.x - x_origin_cm) / IN_TO_CM, 3) for e in _circle_edges(body, radius_in)))


def _pattern_snapshot(root):
    """Counts of feature-level patterns before a test runs, so any created
    by *this* test can be identified afterward by what got appended -- never
    delete every pattern in the document, since Fill mode's straight-row and
    loop-trace paths are the only ones that create feature-level patterns,
    and a real design open in the same document could have its own."""
    return (root.features.rectangularPatternFeatures.count, root.features.pathPatternFeatures.count)


def _new_patterns_since(root, snapshot):
    rect_before, path_before = snapshot
    new = []
    for i in range(rect_before, root.features.rectangularPatternFeatures.count):
        new.append(root.features.rectangularPatternFeatures.item(i))
    for i in range(path_before, root.features.pathPatternFeatures.count):
        new.append(root.features.pathPatternFeatures.item(i))
    return new


# ---------------------------------------------------------------------------
# Individual tests -- each returns (name, ok, detail, created_patterns)
# ---------------------------------------------------------------------------

def _test_row_fill_straight(entry, root, extrudes):
    """The bug case: Fill mode must never place a hole past the edge's far
    end. 6in edge, 0.5in offset, 0.4in spacing: usable length is 5.5in, so
    the correct count is floor(5.5/0.4) = 13, not floor(6/0.4) = 15."""
    body = _make_plate(root, extrudes, 'RowFill', 6, 2, 0, 0)
    edge = _find_top_straight_edge(body, y_in=0)
    anchor = edge.startVertex.geometry
    snap = _pattern_snapshot(root)
    entry._process_single_edge_row(edge, anchor, 0.25 * IN_TO_CM, 0.5 * IN_TO_CM, True, 0, 0.4 * IN_TO_CM,
                                    is_preview=False)
    created = _new_patterns_since(root, snap)
    holes = _hole_x_positions(body, 0.125, x_origin_cm=anchor.x)
    notches = _arc_edges(body)
    expected = [round(0.5 + k * 0.4, 3) for k in range(13)]
    ok = (holes == expected) and (len(notches) == 0)
    return ('row_fill_straight', ok,
            f'holes={holes} expected={expected} notch_edges={len(notches)}', created)


def _test_row_custom_straight(entry, root, extrudes):
    body = _make_plate(root, extrudes, 'RowCustom', 6, 2, 0, 8)
    edge = _find_top_straight_edge(body, y_in=8)
    anchor = edge.startVertex.geometry
    entry._process_single_edge_row(edge, anchor, 0.25 * IN_TO_CM, 0.5 * IN_TO_CM, False, 8, 0.6 * IN_TO_CM,
                                    is_preview=False)
    holes = _hole_x_positions(body, 0.125, x_origin_cm=anchor.x)
    expected = [round(0.5 + k * 0.6, 3) for k in range(8)]
    ok = holes == expected
    return ('row_custom_straight', ok, f'holes={holes} expected={expected}', [])


def _test_row_arc_fill(entry, root, extrudes):
    body = _make_plate(root, extrudes, 'ArcRow', 4, 4, 16, 0)
    corner_edge = None
    for e in body.edges:
        g = e.geometry
        if isinstance(g, adsk.core.Line3D):
            sp, ep = g.startPoint, g.endPoint
            if (abs(sp.x - ep.x) < 1e-6 and abs(sp.y - ep.y) < 1e-6
                    and abs(abs(sp.z - ep.z) - 0.25 * IN_TO_CM) < 1e-4
                    and abs(sp.x - 16 * IN_TO_CM) < 1e-4 and abs(sp.y - 4 * IN_TO_CM) < 1e-4):
                corner_edge = e
                break
    ec = adsk.core.ObjectCollection.create()
    ec.add(corner_edge)
    fi = root.features.filletFeatures.createInput()
    fi.addConstantRadiusEdgeSet(ec, adsk.core.ValueInput.createByReal(1.0 * IN_TO_CM), True)
    root.features.filletFeatures.add(fi)

    arc_edge = None
    for e in body.edges:
        if isinstance(e.geometry, adsk.core.Arc3D):
            bb = e.boundingBox
            if abs(bb.minPoint.z - 0.25 * IN_TO_CM) < 1e-4 and abs(bb.maxPoint.z - 0.25 * IN_TO_CM) < 1e-4:
                arc_edge = e
                break
    anchor = arc_edge.startVertex.geometry
    entry._process_single_edge_row(arc_edge, anchor, 0.25 * IN_TO_CM, 0.25 * IN_TO_CM, True, 0, 0.5 * IN_TO_CM,
                                    is_preview=False)
    n = len(_circle_edges(body, 0.125))
    ok = n == 4  # 2 holes x 2 rim edges each
    return ('row_arc_fill', ok, f'circular_edges={n} expected=4', [])


def _make_ring_plate(root, extrudes, name, x_off_in, hole_radius_in=1.2):
    body = _make_plate(root, extrudes, name, 4, 4, x_off_in, 0)
    top = max((f for f in body.faces if isinstance(f.geometry, adsk.core.Plane)), key=lambda f: f.area)
    sk = root.sketches.add(top)
    sk.name = NAME_PREFIX + name + '_ring_sketch'
    center = adsk.core.Point3D.create((x_off_in + 2) * IN_TO_CM, 2 * IN_TO_CM, 0)
    sk.sketchCurves.sketchCircles.addByCenterRadius(center, hole_radius_in * IN_TO_CM)
    prof = sk.profiles.item(0)
    ei = extrudes.createInput(prof, adsk.fusion.FeatureOperations.CutFeatureOperation)
    ei.setOneSideExtent(adsk.fusion.ThroughAllExtentDefinition.create(),
                         adsk.fusion.ExtentDirections.NegativeExtentDirection)
    ei.participantBodies = [body]
    extrudes.add(ei)
    ring_edge = next(e for e in body.edges
                      if isinstance(e.geometry, adsk.core.Circle3D)
                      and abs(e.geometry.radius / IN_TO_CM - hole_radius_in) < 1e-3)
    return body, ring_edge


def _test_ring_fill(entry, root, extrudes):
    body, ring_edge = _make_ring_plate(root, extrudes, 'Ring', 20)
    entry._process_circular_edge(ring_edge, 0.25 * IN_TO_CM, 0.3 * IN_TO_CM, True, 0, 1.0 * IN_TO_CM,
                                  is_preview=False)
    n = len(_circle_edges(body, 0.125))
    ok = n == 10  # 5 holes x 2 rim edges each
    return ('ring_fill', ok, f'circular_edges={n} expected=10', [])


def _test_ring_custom_diam(entry, root, extrudes):
    body, ring_edge = _make_ring_plate(root, extrudes, 'Ring2', 25)
    entry._process_circular_edge(ring_edge, 0.4 * IN_TO_CM, 0.3 * IN_TO_CM, False, 7, 1.0 * IN_TO_CM,
                                  is_preview=False)
    n = len(_circle_edges(body, 0.2))
    ok = n == 14  # 7 holes x 2 rim edges each
    return ('ring_custom_diam', ok, f'circular_edges={n} expected=14', [])


def _test_loop_fill(entry, root, extrudes):
    body = _make_plate(root, extrudes, 'LoopFill', 6, 4, 0, 30)
    edge = _find_top_straight_edge(body)
    anchor = edge.startVertex.geometry
    snap = _pattern_snapshot(root)
    entry._process_loop_from_edge(edge, 0.25 * IN_TO_CM, 0.5 * IN_TO_CM, True, 0, 1.0 * IN_TO_CM,
                                   is_preview=False, anchor_pt_model=anchor)
    created = _new_patterns_since(root, snap)
    n = len(_circle_edges(body, 0.125))
    ok = n == 32  # 16 holes x 2 rim edges each
    return ('loop_fill', ok, f'circular_edges={n} expected=32', created)


def _test_loop_custom(entry, root, extrudes):
    body = _make_plate(root, extrudes, 'LoopCustom', 6, 4, 0, 36)
    edge = _find_top_straight_edge(body)
    anchor = edge.startVertex.geometry
    entry._process_loop_from_edge(edge, 0.25 * IN_TO_CM, 0.5 * IN_TO_CM, False, 10, 1.0 * IN_TO_CM,
                                   is_preview=False, anchor_pt_model=anchor)
    n = len(_circle_edges(body, 0.125))
    ok = n == 20  # 10 holes x 2 rim edges each
    return ('loop_custom', ok, f'circular_edges={n} expected=20', [])


def _test_suppression(entry, root, extrudes):
    body = _make_plate(root, extrudes, 'Suppress', 6, 2, 0, 42)
    edge = _find_top_straight_edge(body, y_in=42)
    anchor = edge.startVertex.geometry
    suppress_key = (round(2.5 * IN_TO_CM + anchor.x, 3), round(42.5 * IN_TO_CM, 3), round(0.25 * IN_TO_CM, 3))
    entry._suppressed_world_keys = {suppress_key}
    entry._last_candidates_world = []
    entry._process_single_edge_row(edge, anchor, 0.25 * IN_TO_CM, 0.5 * IN_TO_CM, False, 5, 1.0 * IN_TO_CM,
                                    is_preview=False)
    entry._suppressed_world_keys = set()
    holes = _hole_x_positions(body, 0.125, x_origin_cm=anchor.x)
    expected = [0.5, 1.5, 3.5, 4.5]  # 2.5 suppressed
    ok = holes == expected
    return ('suppression', ok, f'holes={holes} expected={expected}', [])


_TESTS = [
    _test_row_fill_straight,
    _test_row_custom_straight,
    _test_row_arc_fill,
    _test_ring_fill,
    _test_ring_custom_diam,
    _test_loop_fill,
    _test_loop_custom,
    _test_suppression,
]


def _cleanup(root, created_patterns):
    # Only the specific pattern features each test recorded via
    # _pattern_snapshot/_new_patterns_since get deleted here -- never sweep
    # every RectangularPatternFeature/PathPatternFeature in the document,
    # since a real design open in the same session could have its own that
    # have nothing to do with this test run. A feature-level pattern can
    # block its seed body's delete if left dangling, so these go first.
    for f in created_patterns:
        try:
            f.deleteMe()
        except Exception:
            pass
    for b in list(root.bRepBodies):
        if b.name.startswith(NAME_PREFIX):
            b.deleteMe()
    for s in list(root.sketches):
        if s.name.startswith(NAME_PREFIX):
            s.deleteMe()


def run(_context: str):
    app = adsk.core.Application.get()
    des = adsk.fusion.Design.cast(app.activeProduct)
    root = des.rootComponent
    extrudes = root.features.extrudeFeatures

    entry = _get_entry_module()

    results = []
    created_patterns = []
    try:
        for test_fn in _TESTS:
            try:
                name, ok, detail, patterns = test_fn(entry, root, extrudes)
            except Exception:
                name, ok, detail, patterns = (test_fn.__name__, False, traceback.format_exc(), [])
            results.append((name, ok, detail))
            created_patterns.extend(patterns)
    finally:
        _cleanup(root, created_patterns)

    passed = sum(1 for _, ok, _ in results if ok)
    print(f'AutoHole regression: {passed}/{len(results)} passed')
    for name, ok, detail in results:
        print(f'  [{"PASS" if ok else "FAIL"}] {name} -- {detail}')

    if passed != len(results):
        failed = [name for name, ok, _ in results if not ok]
        raise RuntimeError(f'AutoHole regression FAILED: {failed}')
