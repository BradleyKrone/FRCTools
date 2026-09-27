"""C-C Distance regression test -- a live Fusion script, not a pytest file.

This repo has no automated test framework for Fusion API code (see
commands/AutoHole/dev_scripts/regression_test.py for the established pattern this
follows). It exercises CCLineUtils' math and geometry functions directly --
20DP gears, HTD/GT2 belts, and #25/#35 chains -- plus the create/edit dialogs'
teeth-count validation, without driving the interactive C-C Distance dialog.

How to run:
  - Via the Fusion MCP tools: read this file's contents and pass them as
    `object.script` to `fusion_mcp_execute` with `featureType: "script"`.
  - Or paste this file's contents into a new script in Fusion's Scripts and
    Add-Ins panel (Shift+S) and run it directly.

Either way, make sure the FRCTools add-in is currently running (so the
CCDistance modules are loaded) -- this script finds them via `sys.modules`
and `importlib.reload()`s them first, so it always tests whatever is
currently saved on disk, not a stale cached copy.

The belt/chain formula checks compare CCLineUtils' returned center distance
against the underlying physical equation it was solved from (belt pitch
length / chain link count), evaluated independently right here, rather than
re-deriving the same closed-form quadratic -- so a regression in the
closed-form algebra shows up as a nonzero residual instead of silently
matching a re-typed copy of the same bug.

Add a new `_test_*` function here whenever a new motion type or code path is
added to CCDistance, and re-run this whole file after any change to
CCLineUtils.py/create_cmd.py/edit_cmd.py -- that's the point of keeping it
around instead of re-deriving the math by hand each time.
"""

import adsk.core
import adsk.fusion
import sys
import math
import importlib
import traceback

IN_TO_CM = 2.54
NAME_PREFIX = 'CCTEST_'
TOL = 1e-6


def _get_module(suffix):
    key = next((k for k in sys.modules if k.endswith(suffix)), None)
    if key is None:
        raise RuntimeError(f'{suffix} not found in sys.modules -- is the FRCTools add-in running?')
    module = sys.modules[key]
    importlib.reload(module)
    return module


# ---------------------------------------------------------------------------
# Formula checks -- pure math, no Fusion geometry needed
# ---------------------------------------------------------------------------

def _test_gear_formulas(ccutil, CCLine, sketch):
    ld = CCLine.CCLineData()
    ld.motion = 0
    ld.N1 = 12
    ld.N2 = 36
    ccutil.calcCCLineData(ld)

    exp_pd1, exp_pd2 = 12 / 20, 36 / 20
    exp_cc = (exp_pd1 + exp_pd2) / 2
    exp_od1, exp_od2 = exp_pd1 + 0.1, exp_pd2 + 0.1

    ok = (abs(ld.ccDistIN - exp_cc) < TOL and abs(ld.PD1 - exp_pd1) < TOL
          and abs(ld.PD2 - exp_pd2) < TOL and abs(ld.OD1 - exp_od1) < TOL
          and abs(ld.OD2 - exp_od2) < TOL)
    return ('gear_formulas', ok,
            f'CC={ld.ccDistIN} (exp {exp_cc}) PD=({ld.PD1},{ld.PD2}) OD=({ld.OD1},{ld.OD2})')


def _check_belt(ccutil, CCLine, motion, pitch_mm, N1, N2, teeth):
    ld = CCLine.CCLineData()
    ld.motion = motion
    ld.N1 = N1
    ld.N2 = N2
    ld.Teeth = teeth
    ccutil.calcCCLineData(ld)

    exp_pd1 = N1 * pitch_mm / (25.4 * math.pi)
    exp_pd2 = N2 * pitch_mm / (25.4 * math.pi)
    exp_od1, exp_od2 = exp_pd1 + 0.15, exp_pd2 + 0.15
    pl_in = teeth * pitch_mm / 25.4

    # Independent check: does the returned center distance actually satisfy
    # the belt pitch-length equation it was supposedly solved from?
    C = ld.ccDistIN
    residual = 2 * C + math.pi * (exp_pd1 + exp_pd2) / 2 + (exp_pd1 - exp_pd2) ** 2 / (4 * C) - pl_in

    ok = (abs(residual) < TOL and abs(ld.PD1 - exp_pd1) < TOL and abs(ld.PD2 - exp_pd2) < TOL
          and abs(ld.OD1 - exp_od1) < TOL and abs(ld.OD2 - exp_od2) < TOL and C > 0)
    return ok, f'CC={C} residual={residual} PD=({ld.PD1},{ld.PD2}) OD=({ld.OD1},{ld.OD2})'


def _test_htd_belt_formulas(ccutil, CCLine, sketch):
    ok, detail = _check_belt(ccutil, CCLine, motion=1, pitch_mm=5, N1=15, N2=45, teeth=75)
    return ('htd_belt_formulas', ok, detail)


def _test_gt2_belt_formulas(ccutil, CCLine, sketch):
    ok, detail = _check_belt(ccutil, CCLine, motion=2, pitch_mm=3, N1=20, N2=60, teeth=110)
    return ('gt2_belt_formulas', ok, detail)


def _test_belt_too_short(ccutil, CCLine, sketch):
    # N1=20/N2=60 @ HTD 5mm with exactly 40 teeth puts the pitch length right
    # at the point where the quadratic's discriminant goes negative (b == 0
    # algebraically, with a nonzero (PD1-PD2) term) -- must return 0.0, not
    # raise or return a bogus/negative value.
    ld = CCLine.CCLineData()
    ld.motion = 1
    ld.N1 = 20
    ld.N2 = 60
    ld.Teeth = 40
    ccutil.calcCCLineData(ld)
    ok = ld.ccDistIN == 0.0
    return ('belt_too_short_returns_zero', ok, f'ccDistIN={ld.ccDistIN}')


def _check_chain(ccutil, CCLine, motion, pitch_in, roller_in, N1, N2, links):
    ld = CCLine.CCLineData()
    ld.motion = motion
    ld.N1 = N1
    ld.N2 = N2
    ld.Teeth = links
    ccutil.calcCCLineData(ld)

    exp_pd1 = pitch_in / math.sin(math.pi / N1)
    exp_pd2 = pitch_in / math.sin(math.pi / N2)
    exp_od1, exp_od2 = exp_pd1 + roller_in, exp_pd2 + roller_in

    # Independent check against the defining roller-chain link-count equation
    # (center distance expressed in units of chain pitch).
    Cp = ld.ccDistIN / pitch_in
    residual = 2 * Cp + (N1 + N2) / 2 + (N2 - N1) ** 2 / (4 * math.pi ** 2 * Cp) - links

    ok = (abs(residual) < TOL and abs(ld.PD1 - exp_pd1) < TOL and abs(ld.PD2 - exp_pd2) < TOL
          and abs(ld.OD1 - exp_od1) < TOL and abs(ld.OD2 - exp_od2) < TOL and ld.ccDistIN > 0)
    return ok, f'CC={ld.ccDistIN} residual={residual} PD=({ld.PD1},{ld.PD2}) OD=({ld.OD1},{ld.OD2})'


def _test_chain25_formulas(ccutil, CCLine, sketch):
    ok, detail = _check_chain(ccutil, CCLine, motion=3, pitch_in=0.25, roller_in=0.130, N1=13, N2=30, links=50)
    return ('chain25_formulas', ok, detail)


def _test_chain35_formulas(ccutil, CCLine, sketch):
    ok, detail = _check_chain(ccutil, CCLine, motion=4, pitch_in=0.375, roller_in=0.200, N1=17, N2=40, links=55)
    return ('chain35_formulas', ok, detail)


def _test_chain_too_short(ccutil, CCLine, sketch):
    # N1=20/N2=40 with exactly 30 links puts A (= links - (N1+N2)/2) at
    # exactly 0, so the discriminant reduces to -2*(N2-N1)^2/pi^2 -- always
    # negative whenever the two sprockets differ in size. Must return 0.0.
    ld = CCLine.CCLineData()
    ld.motion = 3
    ld.N1 = 20
    ld.N2 = 40
    ld.Teeth = 30
    ccutil.calcCCLineData(ld)
    ok = ld.ccDistIN == 0.0
    return ('chain_too_short_returns_zero', ok, f'ccDistIN={ld.ccDistIN}')


# ---------------------------------------------------------------------------
# End-to-end geometry pipeline -- real sketch, real dimensions
# ---------------------------------------------------------------------------

def _test_geometry_pipeline(ccutil, CCLine, sketch):
    p0 = sketch.sketchPoints.add(adsk.core.Point3D.create(0, 0, 0))
    p1 = sketch.sketchPoints.add(adsk.core.Point3D.create(5 * IN_TO_CM, 0, 0))
    line = ccutil.createCCLine(p0, p1)

    ccLine = CCLine.CCLine()
    ccLine.line = line
    ccLine.data = CCLine.CCLineData()
    ccLine.data.motion = 0
    ccLine.data.N1 = 12
    ccLine.data.N2 = 36
    ccLine.data.ExtraCenterIN = 0.01
    ccutil.calcCCLineData(ccLine.data)
    ccutil.dimAndLabelCCLine(ccLine)
    ccutil.createEndCircles(ccLine)

    ok, detail = _check_pipeline_dims(ccLine)
    return 'geometry_pipeline_create', ok, detail, ccLine


def _test_geometry_pipeline_modify(ccutil, CCLine, sketch, ccLine):
    # Change the gearing and the extra-center backlash, then confirm the
    # existing dimensions get updated to the newly computed values (the edit
    # dialog's code path) rather than left stale or only partially updated.
    ccLine.data.N1 = 18
    ccLine.data.N2 = 54
    ccLine.data.ExtraCenterIN = 0.02
    ccutil.calcCCLineData(ccLine.data)
    ccutil.modifyCCLine(ccLine)

    ok, detail = _check_pipeline_dims(ccLine)
    return 'geometry_pipeline_modify', ok, detail


def _check_pipeline_dims(ccLine):
    expected_len_cm = (ccLine.data.ccDistIN + ccLine.data.ExtraCenterIN) * IN_TO_CM
    actual_len_cm = ccLine.line.startSketchPoint.geometry.distanceTo(ccLine.line.endSketchPoint.geometry)

    ok = (abs(ccLine.lengthDim.value - expected_len_cm) < TOL
          and abs(actual_len_cm - expected_len_cm) < 1e-4
          and abs(ccLine.PD1Dim.value - ccLine.data.PD1 * IN_TO_CM) < TOL
          and abs(ccLine.PD2Dim.value - ccLine.data.PD2 * IN_TO_CM) < TOL
          and abs(ccLine.OD1Dim.value - ccLine.data.OD1 * IN_TO_CM) < TOL
          and abs(ccLine.OD2Dim.value - ccLine.data.OD2 * IN_TO_CM) < TOL)
    detail = (f'lengthDim={ccLine.lengthDim.value} expected={expected_len_cm} '
              f'actual_sketch_len={actual_len_cm}')
    return ok, detail


# ---------------------------------------------------------------------------
# Dialog validation -- duck-typed stand-ins for Fusion's CommandInput objects
# ---------------------------------------------------------------------------

class _StubValueInput:
    def __init__(self, value):
        self.value = value


class _StubSelectedItem:
    def __init__(self, index):
        self.index = index


class _StubDropdown:
    def __init__(self, index):
        self.selectedItem = _StubSelectedItem(index)


class _StubStatus:
    def __init__(self):
        self.formattedText = ''


class _StubInputs:
    def __init__(self, mapping):
        self._mapping = mapping

    def itemById(self, key):
        return self._mapping[key]


class _StubArgs:
    def __init__(self, inputs):
        self.inputs = inputs
        self.areInputsValid = True


def _make_validate_args(cog1, cog2):
    mapping = {
        'motion_type': _StubDropdown(0),  # Gears -- skips the belt/chain length check entirely
        'cog1_teeth': _StubValueInput(cog1),
        'cog2_teeth': _StubValueInput(cog2),
        'belt_teeth': _StubValueInput(70),
        'chain_links': _StubValueInput(60),
        'status_msg': _StubStatus(),
    }
    return _StubArgs(_StubInputs(mapping))


def _test_validate_cog2_upper_bound(create_cmd, edit_cmd):
    # Regression for a copy-paste bug where the range check tested
    # cog1Teeth.value < 100 twice instead of checking cog2Teeth.value's upper
    # bound, silently accepting any cog2 tooth count.
    results = []
    for label, fn in (('create', create_cmd.command_validate_input),
                       ('edit', edit_cmd.edit_command_validate_input)):
        args_over = _make_validate_args(cog1=24, cog2=150)
        fn(args_over)
        rejected = args_over.areInputsValid is False

        args_ok = _make_validate_args(cog1=24, cog2=99)
        fn(args_ok)
        accepted = args_ok.areInputsValid is True

        results.append((label, rejected, accepted))

    ok = all(rejected and accepted for _, rejected, accepted in results)
    detail = ', '.join(f'{label}: cog2=150 rejected={rejected} cog2=99 accepted={accepted}'
                        for label, rejected, accepted in results)
    return ('validate_cog2_upper_bound', ok, detail)


# ---------------------------------------------------------------------------

def run(_context: str = ''):
    app = adsk.core.Application.get()
    des = adsk.fusion.Design.cast(app.activeProduct)
    root = des.rootComponent

    CCLine = _get_module('CCDistance.CCLine')
    ccutil = _get_module('CCDistance.CCLineUtils')
    create_cmd = _get_module('CCDistance.create_cmd')
    edit_cmd = _get_module('CCDistance.edit_cmd')

    sketch = root.sketches.add(root.xYConstructionPlane)
    sketch.name = NAME_PREFIX + 'sketch'

    results = []
    try:
        for test_fn in (_test_gear_formulas, _test_htd_belt_formulas, _test_gt2_belt_formulas,
                         _test_belt_too_short, _test_chain25_formulas, _test_chain35_formulas,
                         _test_chain_too_short):
            try:
                results.append(test_fn(ccutil, CCLine, sketch))
            except Exception:
                results.append((test_fn.__name__, False, traceback.format_exc()))

        try:
            name, ok, detail, ccLine = _test_geometry_pipeline(ccutil, CCLine, sketch)
            results.append((name, ok, detail))
            results.append(_test_geometry_pipeline_modify(ccutil, CCLine, sketch, ccLine))
        except Exception:
            results.append(('geometry_pipeline', False, traceback.format_exc()))

        try:
            results.append(_test_validate_cog2_upper_bound(create_cmd, edit_cmd))
        except Exception:
            results.append(('validate_cog2_upper_bound', False, traceback.format_exc()))
    finally:
        try:
            sketch.deleteMe()
        except Exception:
            pass

    passed = sum(1 for _, ok, _ in results if ok)
    print(f'CCDistance regression: {passed}/{len(results)} passed')
    for name, ok, detail in results:
        print(f'  [{"PASS" if ok else "FAIL"}] {name} -- {detail}')

    if passed != len(results):
        failed = [name for name, ok, _ in results if not ok]
        raise RuntimeError(f'CCDistance regression FAILED: {failed}')
