# Builds the Manufacture-workspace data for the FRC CAM Setup command:
# tools in the document tool library, one setup per plate (one per machined face
# for a box tube), the operations for every feature feature_finder found, and
# then generates the toolpaths.
#
# Every number comes from cam_data.py (inches / IPM); parameters are written as
# unit-carrying expressions ("0.25 in", "27 in/min") so Fusion does the converting.

import json
import time
import uuid

import adsk.core
import adsk.fusion
import adsk.cam

from . import cam_data
from . import feature_finder as ff

ATTR_GROUP = 'FRCTools_CamSetup'


def _noop_log(msg):
    pass


# ---------------------------------------------------------------------------
# Parameter helpers
# ---------------------------------------------------------------------------
def _set(params, name, expr, log=_noop_log):
    # Write one CAM parameter by expression. A missing / rejected parameter is
    # logged, not fatal -- Fusion renames parameters between releases.
    try:
        p = params.itemByName(name)
        if p is None:
            log(f'CAM parameter not found: {name}')
            return False
        p.expression = expr
        return True
    except Exception as e:
        log(f'CAM parameter {name} = {expr!r} failed: {e}')
        return False

def _set_geometry(params, name, entities, log=_noop_log):
    try:
        params.itemByName(name).value.value = list(entities)
        return True
    except Exception as e:
        log(f'CAM geometry {name} failed: {e}')
        return False

def _q(s):
    return f"'{s}'"

def _inch(v):
    return f'{v:.5f} in'

def _ipm(v):
    return f'{v:.3f} in/min'


# ---------------------------------------------------------------------------
# CAM product / machine / tools
# ---------------------------------------------------------------------------
def get_cam(doc=None):
    # The CAM product only exists once the Manufacture workspace has been opened
    # for the document (itemByProductType raises until then), so switch to it first.
    app = adsk.core.Application.get()
    doc = doc or app.activeDocument
    try:
        cam = doc.products.itemByProductType('CAMProductType')
    except RuntimeError:
        cam = None
    if cam is None:
        app.userInterface.workspaces.itemById('CAMEnvironment').activate()
        cam = doc.products.itemByProductType('CAMProductType')
    return adsk.cam.CAM.cast(cam)


def find_machine(machine_name, log=_noop_log):
    # Look through the local + cloud machine libraries for a machine matching
    # the cam_data entry. None if the user hasn't added one (that's fine: the
    # setup simply has no machine, which only matters when posting).
    match = cam_data.MACHINES[machine_name]['library_match']
    try:
        ml = adsk.cam.CAMManager.get().libraryManager.machineLibrary
    except Exception as e:
        log(f'machine library unavailable: {e}')
        return None
    for loc in (adsk.cam.LibraryLocations.LocalLibraryLocation,
                adsk.cam.LibraryLocations.CloudLibraryLocation,
                adsk.cam.LibraryLocations.Fusion360LibraryLocation):
        try:
            for m in ml.createQuery(loc).execute():
                text = f'{m.vendor} {m.model} {m.description}'.lower()
                if any(k in text for k in match):
                    return m
        except Exception as e:
            log(f'machine query {loc} failed: {e}')
    log(f'no "{machine_name}" machine in the machine library; setup created without one')
    return None


def _tool_json(key, material, machine_name):
    t = cam_data.TOOLS[key]
    feeds_key = cam_data.MACHINES[machine_name]['feeds']
    rows = cam_data.FEEDS[feeds_key][material][key]
    presets = []
    for strategy, row in rows.items():
        presets.append({
            'name': f'{material} - {strategy}',
            'description': f'FRCTools {material} {strategy}',
            'guid': str(uuid.uuid4()),
            'n': row['rpm'], 'n_ramp': row['rpm'],
            'v_f': row['feed'], 'v_f_leadIn': row['feed'], 'v_f_leadOut': row['feed'],
            'v_f_transition': row['feed'],
            'v_f_plunge': row['plunge'], 'v_f_ramp': row['plunge'],
            'f_z': row['feed'] / (row['rpm'] * t['flutes']),
            'f_n': row['feed'] / row['rpm'],
            'ramp-angle': 2,
            'stepdown': row['doc'], 'use-stepdown': True,
            'stepover': row.get('woc', t['diameter']), 'use-stepover': True,
            'tool-coolant': cam_data.COOLANT,
            'material': {'category': 'all', 'query': '', 'use-hardness': False},
        })
    return json.dumps({
        'BMC': 'carbide',
        'GRADE': 'Mill Generic',
        'description': t['description'],
        'guid': str(uuid.uuid4()),
        'type': 'flat end mill',
        'unit': 'inches',
        'vendor': 'FRCTools',
        'product-id': '',
        'product-link': '',
        'geometry': {
            'CSP': False, 'DC': t['diameter'], 'HAND': True,
            'LB': t['body_length'], 'LCF': t['flute_length'], 'NOF': t['flutes'], 'NT': 1,
            'OAL': t['overall_length'], 'RE': 0, 'SFDM': t['diameter'], 'TA': 0, 'TP': 0,
            'shoulder-diameter': t['diameter'], 'shoulder-length': t['shoulder_length'],
            'tip-diameter': t['diameter'], 'tip-length': 0, 'tip-offset': 0,
            'thread-profile-angle': 60,
        },
        'post-process': {
            'break-control': False, 'comment': t['description'], 'diameter-offset': t['number'],
            'length-offset': t['number'], 'live': True, 'manual-tool-change': True,
            'number': t['number'], 'turret': 0,
        },
        'start-values': {'presets': presets},
    })


def make_tools(material, machine_name):
    # -> {'1/4': Tool, '1/8': Tool}. The operations each get the tool object;
    # Fusion files it in the document tool library on its own.
    return {key: adsk.cam.Tool.createFromJson(_tool_json(key, material, machine_name))
            for key in cam_data.TOOLS}


# ---------------------------------------------------------------------------
# Setups
# ---------------------------------------------------------------------------
def _new_setup(cam, bodies, name, z_face, x_edge, box_point, side_offset, machine, log):
    # x_edge None -> the WCS X follows the model's X (the unselected-axis default).
    si = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
    si.models = list(bodies)
    si.name = name
    if machine is not None:
        try:
            si.machine = machine
        except Exception as e:
            log(f'could not assign machine: {e}')
    p = si.parameters
    _set(p, 'wcs_orientation_mode', _q('axesZX'), log)
    _set_geometry(p, 'wcs_orientation_axisZ', [z_face], log)
    if x_edge is not None:
        _set_geometry(p, 'wcs_orientation_axisX', [x_edge], log)
    _set(p, 'wcs_origin_mode', _q('stockPoint'), log)
    _set(p, 'wcs_origin_boxPoint', _q(box_point), log)
    _set(p, 'job_stockMode', _q('default'), log)          # relative size box
    _set(p, 'job_stockOffsetMode', _q('simple'), log)
    _set(p, 'job_stockOffsetSides', _inch(side_offset), log)
    _set(p, 'job_stockOffsetTop', _inch(0), log)
    _set(p, 'job_stockOffsetBottom', _inch(0), log)
    return cam.setups.add(si)


def _orientation_ok(setup, z_face):
    # The WCS Z must come out along the face's outward normal (out of the part).
    try:
        m = setup.workCoordinateSystem
        z = m.getAsCoordinateSystem()[3]
        n = ff._face_normal(z_face)
        return z.x * n[0] + z.y * n[1] + z.z * n[2] > 0
    except Exception:
        return True


def _fix_orientation(setup, z_face, log):
    if not _orientation_ok(setup, z_face):
        _set(setup.parameters, 'wcs_orientation_flipZ', 'true', log)
        log(f'{setup.name}: flipped WCS Z to point out of the top face')


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def _setup_z(setup):
    z = setup.workCoordinateSystem.getAsCoordinateSystem()[3]
    return ff._unit((z.x, z.y, z.z))



def _new_op(setup, strategy, name, tool, log):
    oi = setup.operations.createInput(strategy)
    oi.displayName = name
    oi.tool = tool
    return oi


def _apply_feeds(params, row, log):
    _set(params, 'tool_spindleSpeed', f"{row['rpm']} rpm", log)
    _set(params, 'tool_feedCutting', _ipm(row['feed']), log)
    _set(params, 'tool_feedEntry', _ipm(row['feed']), log)
    _set(params, 'tool_feedExit', _ipm(row['feed']), log)
    _set(params, 'tool_feedTransition', _ipm(row['feed']), log)
    _set(params, 'tool_feedPlunge', _ipm(row['plunge']), log)
    _set(params, 'tool_feedRamp', _ipm(row['plunge']), log)
    _set(params, 'tool_coolant', _q(cam_data.COOLANT), log)


def _through_bottom(params, log):
    _set(params, 'bottomHeight_mode', _q('from stock bottom'), log)
    _set(params, 'bottomHeight_offset', _inch(-cam_data.THROUGH_OVERCUT), log)


class _Ctx:
    def __init__(self, material, machine_name, tools, log):
        self.material = material
        self.tools = tools
        self.log = log
        self.feeds = cam_data.FEEDS[cam_data.MACHINES[machine_name]['feeds']][material]

    def row(self, tool, strategy):
        return self.feeds[tool][strategy]


def _tool_label(key):
    return f'{key} O-flute'


def add_drill(setup, holes, ctx):
    # Plunge holes that are the bit's own size.
    if not holes:
        return []
    key = holes[0].tool
    row = ctx.row(key, 'slot')
    d = holes[0].diameter
    oi = _new_op(setup, 'drill', f'Drill {len(holes)}x Ø{d:.3f} - {_tool_label(key)}', ctx.tools[key], ctx.log)
    p = oi.parameters
    _set(p, 'holeMode', _q('selection-faces'), ctx.log)
    _set_geometry(p, 'holeFaces', [f for h in holes for f in h.faces], ctx.log)
    _set(p, 'cycleType', _q('chip-breaking'), ctx.log)
    _set(p, 'peckingDepth', _inch(row['doc']), ctx.log)
    _set(p, 'bottomHeight_mode', _q('from hole bottom'), ctx.log)
    _set(p, 'bottomHeight_offset', _inch(-cam_data.THROUGH_OVERCUT), ctx.log)
    _apply_feeds(p, row, ctx.log)
    _set(p, 'tool_feedCutting', _ipm(row['plunge']), ctx.log)
    return [setup.operations.add(oi)]


def add_bore(setup, holes, ctx, name, pitch=None, row=None, stock_to_leave=None,
             hole_bottom=False, clear=True):
    # Helical bore for round holes. pitch = depth per revolution (in): the
    # slot depth-of-cut by default. No repeat (spring) pass -- it doubles the time.
    if not holes:
        return []
    key = holes[0].tool
    row = row or ctx.row(key, 'slot')
    oi = _new_op(setup, 'bore', name, ctx.tools[key], ctx.log)
    p = oi.parameters
    _set(p, 'holeMode', _q('selection-faces'), ctx.log)
    _set_geometry(p, 'circularFaces', [f for h in holes for f in h.faces], ctx.log)
    # 'Use Ramp Angle' defaults on (2 deg) and silently overrides the pitch.
    _set(p, 'useAngle', 'false', ctx.log)
    _set(p, 'threadPitch', _inch(pitch if pitch is not None else row['doc']), ctx.log)
    _set(p, 'direction', _q('climb'), ctx.log)
    if hole_bottom:
        _set(p, 'bottomHeight_mode', _q('from hole bottom'), ctx.log)
        _set(p, 'bottomHeight_offset', _inch(-cam_data.THROUGH_OVERCUT), ctx.log)
    else:
        _through_bottom(p, ctx.log)
    # Holes much bigger than the bit need extra radial passes -- unless an
    # earlier op already cleared them (clear=False: one helix at final size).
    tool_d = cam_data.TOOLS[key]['diameter']
    if clear and max(h.diameter for h in holes) > 2.0 * tool_d:
        _set(p, 'doMultiplePasses', 'true', ctx.log)
        _set(p, 'stepover', _inch(tool_d * 0.4), ctx.log)
    _set(p, 'nullPass', 'false', ctx.log)
    if stock_to_leave is not None:
        _set(p, 'useStockToLeave', 'true', ctx.log)
        _set(p, 'stockToLeave', _inch(stock_to_leave), ctx.log)
    _apply_feeds(p, row, ctx.log)
    return [setup.operations.add(oi)]


def add_adaptive(setup, name, key, ctx, chains=None, floors=None, through=True,
                 stock_to_leave=0.0):
    # 2D adaptive clearing inside closed chains (through cut-outs, big holes)
    # or down to blind pocket floors (face contour selections).
    row = ctx.row(key, 'adaptive')
    oi = _new_op(setup, 'adaptive2d', name, ctx.tools[key], ctx.log)
    op = setup.operations.add(oi)
    p = op.parameters
    try:
        pv = adsk.cam.CadContours2dParameterValue.cast(p.itemByName('pockets').value)
        sels = pv.getCurveSelections()
        up = _setup_z(setup)
        for loop_edges in (chains or []):
            s = sels.createNewChainSelection()
            s.inputGeometry = [loop_edges[0]]
            # The API cuts on the LEFT of the first edge (seen from +Z) -- it
            # doesn't detect the side itself. Reverse when material is there.
            s.isReverted = ff.material_on_left(loop_edges, up)
        for face in (floors or []):
            s = sels.createNewFaceContourSelection()
            s.inputGeometry = [face]
        pv.applyCurveSelections(sels)
    except Exception as e:
        ctx.log(f'{name}: geometry selection failed: {e}')
    if through:
        _through_bottom(p, ctx.log)
    _set(p, 'optimalLoad', _inch(row['woc']), ctx.log)
    _set(p, 'doMultipleDepths', 'true', ctx.log)
    _set(p, 'maximumStepdown', _inch(row['doc']), ctx.log)
    _set(p, 'useEvenStepdowns', 'true', ctx.log)
    _set(p, 'useStockToLeave', 'true' if stock_to_leave else 'false', ctx.log)
    if stock_to_leave:
        _set(p, 'stockToLeave', _inch(stock_to_leave), ctx.log)
        _set(p, 'verticalStockToLeave', _inch(0), ctx.log)
    _apply_feeds(p, row, ctx.log)
    return [op]


def add_profile(setup, bodies, key, ctx, tabs):
    # Outside profile of each part through the stock, multiple depths at the
    # slot DOC, tabs on every part.
    row = ctx.row(key, 'slot')
    count = f'{len(bodies)}x ' if len(bodies) > 1 else ''
    oi = _new_op(setup, 'contour2d', f'Profile {count}- {_tool_label(key)}', ctx.tools[key], ctx.log)
    op = setup.operations.add(oi)
    p = op.parameters
    try:
        pv = adsk.cam.CadContours2dParameterValue.cast(p.itemByName('contours').value)
        sels = pv.getCurveSelections()
        for body in bodies:
            s = sels.createNewSilhouetteSelection()
            s.inputGeometry = [body]
            # Default is AllLoops: every hole gets profiled too. Outer edge only, tool outside.
            s.loopType = adsk.cam.LoopTypes.OnlyOutsideLoops
            s.sideType = adsk.cam.SideTypes.AlwaysOutsideSideType
        pv.applyCurveSelections(sels)
    except Exception as e:
        ctx.log(f'profile geometry selection failed: {e}')
    _through_bottom(p, ctx.log)
    _set(p, 'doMultipleDepths', 'true', ctx.log)
    _set(p, 'maximumStepdown', _inch(row['doc']), ctx.log)
    _set(p, 'useEvenStepdowns', 'true', ctx.log)
    _set(p, 'doRamp', 'true', ctx.log)
    _set(p, 'rampAngle', '2 deg', ctx.log)
    # API-made contours default this to maximumStepdown, so every layer spirals
    # down from the ramp clearance height one stepdown per lap at ramp feed.
    _set(p, 'maximumRampZStepdown', 'tool_fluteLength', ctx.log)
    # The default lead-out arcs off the contour into uncut stock -- a full-width
    # bite. Without it the tool lifts straight up out of its own slot.
    _set(p, 'doLeadOut', 'false', ctx.log)
    if tabs:
        _set(p, 'group_tabs', 'true', ctx.log)
        _set(p, 'tabShape', _q('triangular'), ctx.log)
        _set(p, 'tabPositioning', _q('tabCount'), ctx.log)
        _set(p, 'tabsPerContour', str(cam_data.TAB_COUNT), ctx.log)
        _set(p, 'tabWidth', _inch(cam_data.TAB_WIDTH), ctx.log)
        _set(p, 'tabHeight', _inch(cam_data.TAB_HEIGHT), ctx.log)
    _apply_feeds(p, row, ctx.log)
    return [op]


def _group(items, key_fn):
    out = {}
    for it in items:
        out.setdefault(key_fn(it), []).append(it)
    return out


# ---------------------------------------------------------------------------
# Plate / tube builders
# ---------------------------------------------------------------------------
def _wall_bottom(ops, wall, ctx):
    # Tube walls: an adaptive on a chain has no depth of its own (its bottom
    # defaults to the selected contour, which sits on the outer face), so cut
    # from that contour down through the wall.
    for op in ops:
        _set(op.parameters, 'bottomHeight_mode', _q('from contour'), ctx.log)
        _set(op.parameters, 'bottomHeight_offset',
             _inch(-(wall + cam_data.THROUGH_OVERCUT)), ctx.log)
    return ops


def _hole_ops(setup, holes, ctx, tube_wall=False, wall=0.0):
    # wall: tube wall thickness (in); only used when tube_wall is True.
    ops = []
    by_kind = _group(holes, lambda h: h.kind)
    for kind in ('drill', 'small', 'bolt'):
        for d, group in sorted(_group(by_kind.get(kind, []), lambda h: round(h.diameter, 4)).items()):
            if kind == 'drill':
                ops += add_drill(setup, group, ctx)
                continue
            key = group[0].tool
            name = f'Bore {len(group)}x Ø{d:.3f} - {_tool_label(key)}'
            if tube_wall:
                if ctx.material == 'Aluminum' and key == '1/8':
                    r = cam_data.TUBE_BORE_ALUMINUM
                    ops += add_bore(setup, group, ctx, name, pitch=r['pitch'], row=r, hole_bottom=True)
                else:
                    ops += add_bore(setup, group, ctx, name, hole_bottom=True)
            else:
                ops += add_bore(setup, group, ctx, name)
    # Bearing holes: 2D adaptive rough leaving a little, then a clean-up bore at
    # final size with the fit offset (edit its "Radial Stock to Leave" to tune the
    # fit). The clean-up only skims the rough's stock, so it helixes down steeply.
    # Grouped by nominal bearing OD, so a 1.126 and a 1.125 hole share the same two ops.
    def nominal(h):
        return min(cam_data.BEARING_ODS, key=lambda od: abs(od - h.diameter))
    for od, group in sorted(_group(by_kind.get('bearing', []), nominal).items()):
        rough = add_adaptive(setup, f'Bearing {len(group)}x Ø{od:.3f} rough - {_tool_label("1/4")}', '1/4', ctx,
                             chains=[h.edges for h in group], through=not tube_wall,
                             stock_to_leave=cam_data.BEARING_ROUGH_STOCK)
        ops += _wall_bottom(rough, wall, ctx) if tube_wall else rough
        ops += add_bore(setup, group, ctx, f'Bearing {len(group)}x Ø{od:.3f} clean-up - {_tool_label("1/4")}',
                        pitch=cam_data.BEARING_CLEANUP_PITCH, stock_to_leave=cam_data.BEARING_FIT_OFFSET,
                        hole_bottom=tube_wall, clear=False)
    large = by_kind.get('large', [])
    if large:
        clear = add_adaptive(setup, f'Large holes {len(large)}x - {_tool_label("1/4")}', '1/4', ctx,
                             chains=[h.edges for h in large], through=not tube_wall)
        ops += _wall_bottom(clear, wall, ctx) if tube_wall else clear
    return ops


def build_plate(cam, sheet, ctx, machine, tabs, setup_name):
    # sheet: PlateFeatures of parts lying on one sheet of stock (same up
    # direction, thickness and bottom height) -> ONE setup machining all of
    # them, each operation covering that feature on every part.
    first = sheet[0]
    # A lone part squares the WCS to its longest edge; a sheet keeps the model's X.
    x_edge = first.x_edge if len(sheet) == 1 else None
    setup = _new_setup(cam, [f.body for f in sheet], setup_name, first.top_face, x_edge,
                       cam_data.PLATE_WCS_POINT, cam_data.STOCK_SIDE_OFFSET, machine, ctx.log)
    _fix_orientation(setup, first.top_face, ctx.log)
    ops = _hole_ops(setup, [h for f in sheet for h in f.holes], ctx)
    cutouts = [c for f in sheet for c in f.cutouts]
    for key, group in sorted(_group(cutouts, lambda c: c.tool).items(), reverse=True):
        ops += add_adaptive(setup, f'Cut-outs {len(group)}x - {_tool_label(key)}', key, ctx,
                            chains=[c.edges for c in group])
    pockets = [pk for f in sheet for pk in f.pockets]
    for key, group in sorted(_group(pockets, lambda pk: pk.tool).items(), reverse=True):
        ops += add_adaptive(setup, f'Pockets {len(group)}x - {_tool_label(key)}', key, ctx,
                            floors=[pk.floor for pk in group], through=False)
    for key, group in sorted(_group(sheet, lambda f: f.profile_tool).items(), reverse=True):
        ops += add_profile(setup, [f.body for f in group], key, ctx, tabs)
    return [setup]


def _sheet_key(feats):
    # Plates that can share one setup: same up direction and bottom height, i.e.
    # laid out side by side on the same sheet. Thickness is deliberately not part
    # of it -- parts modelled at 0.125 and 0.1225 come off the same 1/8" sheet;
    # the stock takes the thickest and through cuts go past the stock bottom.
    up = ff._face_normal(feats.top_face)
    bottom = ff._dot(ff._v(feats.bottom_face.pointOnFace), up) / ff.IN
    return (tuple(round(c, 3) + 0.0 for c in up), round(bottom, 2) + 0.0)


def build_tube(cam, feats, ctx, machine, setup_name):
    setups = []
    for i, side in enumerate(feats.sides):
        name = f'{setup_name} - face {i + 1}'
        setup = _new_setup(cam, [feats.body], name, side.face, side.x_edge,
                           cam_data.TUBE_WCS_POINT, 0.0, machine, ctx.log)
        _fix_orientation(setup, side.face, ctx.log)
        _hole_ops(setup, side.holes, ctx, tube_wall=True, wall=side.wall)
        for key, group in sorted(_group(side.cutouts, lambda c: c.tool).items(), reverse=True):
            ops = add_adaptive(setup, f'Cut-outs {len(group)}x - {_tool_label(key)}', key, ctx,
                               chains=[c.edges for c in group], through=False)
            _wall_bottom(ops, side.wall, ctx)
        setups.append(setup)
    return setups


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_all(bodies, machine_name, material, part_type='Auto', tabs=True, generate=True,
              log=_noop_log, progress=None):
    # bodies: BRepBodies (proxies are fine). Returns a result dict:
    #   setups, warnings, failed_ops (names), summary lines.
    cam = get_cam()
    machine = find_machine(machine_name, log)
    tools = make_tools(material, machine_name)
    ctx = _Ctx(material, machine_name, tools, log)
    result = {'setups': [], 'warnings': [], 'failed_ops': [], 'summary': []}

    sheets = {}
    for i, body in enumerate(bodies):
        name = ff.part_name(body)
        if progress:
            progress(f'Analyzing {name}', i, len(bodies))
        try:
            feats = ff.analyze(body, part_type)
        except Exception as e:
            result["warnings"].append(str(e))
            continue
        result['warnings'] += feats.warnings
        result['summary'].append(ff.summarize(feats))
        if feats.kind == 'tube':
            if not feats.sides:
                result['warnings'].append(f'{name}: tube has no holes or cut-outs to machine')
                continue
            result['setups'] += build_tube(cam, feats, ctx, machine, f'{name} - {material} - {machine_name}')
        else:
            if feats.thickness > 0.26 and material != 'Wood':
                result['warnings'].append(f'{name}: {feats.thickness:.3f}" is thicker than the 1/4" '
                                          f'the {material} feeds were written for')
            sheets.setdefault(_sheet_key(feats), []).append(feats)

    # One setup per sheet of plates.
    for sheet in sheets.values():
        what = ff.part_name(sheet[0].body) if len(sheet) == 1 else f'{len(sheet)} parts'
        thick = [f.thickness for f in sheet]
        if max(thick) - min(thick) > 0.010:
            result['warnings'].append(f'{what} are on one setup but range from {min(thick):.3f}" to '
                                      f'{max(thick):.3f}" thick -- the stock uses the thickest')
        setup_name = f'{what} - {max(thick):.3f}" {material} - {machine_name}'
        result['setups'] += build_plate(cam, sheet, ctx, machine, tabs, setup_name)
    if len(sheets) > 1:
        result['warnings'].append(f'The plates sit at {len(sheets)} different heights / orientations, '
                                  'so they got one setup each')

    if generate and result['setups']:
        if progress:
            progress('Generating toolpaths', len(bodies), len(bodies))
        coll = adsk.core.ObjectCollection.create()
        for s in result['setups']:
            coll.add(s)
        future = cam.generateToolpath(coll)
        t0 = time.time()
        while not future.isGenerationCompleted and time.time() - t0 < 300:
            adsk.doEvents()
            time.sleep(0.1)
        for s in result['setups']:
            for op in s.allOperations:
                if not op.hasToolpath or op.operationState != adsk.cam.OperationStates.IsValidOperationState:
                    result['failed_ops'].append(f'{s.name} / {op.name}')
    return result
