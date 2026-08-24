import adsk.core
import adsk.fusion
import datetime
import math
import os
import traceback
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_AutoHoleDialog'
CMD_NAME = 'AutoHole'
CMD_Description = ('Cut the Argos standard rivenut hole pattern starting from a point on an edge, '
                    'either just along that edge or all the way around its boundary loop')

# Specify that the command will be promoted to the panel.
IS_PROMOTED = False

# Resource location for command icons.
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []

# Written fresh every run (preview or execute) with a full trace of what the
# command did, so behavior can be inspected after the fact without relying on
# Fusion's Text Command window (which requires config.DEBUG and scrolls away).
DEBUG_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug_log.txt')

# Holes the user has clicked out via the "Suppress Holes" input. Keyed by
# rounded (x, y, z) model-space position rather than by index, so the same set
# applies uniformly whether the current run is a single-edge row, a full
# circle ring, or a loop trace. Reset whenever a position-affecting input
# changes (old suppressions no longer correspond to anything meaningful), or
# when the user clicks Reset.
_suppressed_world_keys = set()

# Every candidate hole position shown in the most recent run (preview or
# execute), in model space — used to resolve a "Suppress Holes" click back to
# the position it refers to. Rebuilt fresh at the start of every `_run()`.
_last_candidates_world = []


def _world_key(pt: adsk.core.Point3D, ndigits: int = 3) -> tuple:
    return (round(pt.x, ndigits), round(pt.y, ndigits), round(pt.z, ndigits))

# ---------------------------------------------------------------------------
# Units / constants
# ---------------------------------------------------------------------------
IN_TO_CM = 2.54

HOLE_OFFSET_CM = 0.5 * IN_TO_CM   # fixed longitudinal offset from the start point

HOLE_RIVENUT = 'Rivenut (19/64")'
HOLE_10_32   = '10-32 (13/64")'
HOLE_CUSTOM  = 'Custom'

HOLE_SIZE_MAP = {
    HOLE_RIVENUT: (19.0 / 64.0) * IN_TO_CM,
    HOLE_10_32:   (13.0 / 64.0) * IN_TO_CM,
}

FOLLOW_JUST_EDGE = 'Just This Edge'
FOLLOW_OUTER     = 'Outer Edge (All The Way Around)'

COUNT_FILL   = 'Fill'
COUNT_CUSTOM = 'Custom'


# Executed when add-in is run.
def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)

    submenu = config.get_solid_submenu()
    control = submenu.controls.addCommand(cmd_def)
    control.isPromoted = IS_PROMOTED


# Executed when add-in is stopped.
def stop():
    submenu = config.get_solid_submenu()
    command_control = submenu.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    if command_control:
        command_control.isPromoted = False
        command_control.deleteMe()

    if command_definition:
        command_definition.deleteMe()

    global local_handlers
    local_handlers = []


# Called when the user clicks the button — builds the command dialog.
def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.log(f'{CMD_NAME} Command Created Event')

    inputs = args.command.commandInputs

    startPointInp = inputs.addSelectionInput(
        'start_point', 'Start Point',
        'Select the vertex to start the hole row from. Not needed for a full '
        'circular edge or for "Outer Edge (All The Way Around)" — both always '
        'cut a complete loop; picking a point there only shifts which hole is first.'
    )
    startPointInp.addSelectionFilter('Vertices')
    startPointInp.setSelectionLimits(0, 1)

    followEdgeInp = inputs.addSelectionInput(
        'follow_edge', 'Edge to Follow',
        'Select the edge the holes should run along, starting from the Start '
        'Point. A full circular edge is always cut as a complete ring.'
    )
    followEdgeInp.addSelectionFilter('Edges')
    followEdgeInp.setSelectionLimits(1, 1)

    followModeInp = inputs.addDropDownCommandInput(
        'follow_mode', 'Follow', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    followModeInp.listItems.add(FOLLOW_JUST_EDGE, True, '')
    followModeInp.listItems.add(FOLLOW_OUTER, False, '')

    inputs.addValueInput(
        'edge_offset', 'Edge Offset', 'in', adsk.core.ValueInput.createByString('0.5 in')
    )

    holeSizeInp = inputs.addDropDownCommandInput(
        'hole_size', 'Hole Size', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    holeSizeInp.listItems.add(HOLE_RIVENUT, True, '')
    holeSizeInp.listItems.add(HOLE_10_32, False, '')
    holeSizeInp.listItems.add(HOLE_CUSTOM, False, '')

    holeDiamInp = inputs.addValueInput(
        'hole_diameter', 'Custom Hole Diameter', 'in', adsk.core.ValueInput.createByString('0.25 in')
    )
    holeDiamInp.isVisible = False

    countTypeInp = inputs.addDropDownCommandInput(
        'hole_count_type', 'Hole Count', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    countTypeInp.listItems.add(COUNT_FILL, True, '')
    countTypeInp.listItems.add(COUNT_CUSTOM, False, '')

    holeSpacingInp = inputs.addValueInput(
        'hole_spacing', 'Hole Spacing', 'in', adsk.core.ValueInput.createByString('1 in')
    )

    customCountInp = inputs.addIntegerSpinnerCommandInput(
        'custom_hole_count', 'Number of Holes', 1, 500, 1, 10
    )
    customCountInp.isVisible = False

    suppressInp = inputs.addSelectionInput(
        'suppress_holes', 'Suppress Holes',
        'Click a hole in the preview to leave it out of the cut.'
    )
    suppressInp.addSelectionFilter('CircularEdges')
    suppressInp.setSelectionLimits(0, 1)

    resetSuppressedInp = inputs.addBoolValueInput(
        'reset_suppressed', 'Reset Suppressed Holes', True, '', False
    )

    global _suppressed_world_keys, _last_candidates_world
    _suppressed_world_keys = set()
    _last_candidates_world = []

    # Wire up event handlers
    futil.add_handler(args.command.execute,        command_execute,        local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview,        local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged,   command_input_changed,  local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy,        command_destroy,        local_handlers=local_handlers)


# ---------------------------------------------------------------------------
# Reactive UI
# ---------------------------------------------------------------------------

_POSITION_AFFECTING_IDS = {'start_point', 'follow_edge', 'follow_mode', 'edge_offset',
                           'hole_count_type', 'custom_hole_count', 'hole_spacing'}


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed = args.input
    inputs = args.inputs
    global _suppressed_world_keys

    if changed.id == 'start_point':
        startPointInp: adsk.core.SelectionCommandInput = inputs.itemById('start_point')
        if startPointInp.selectionCount > 0:
            # Move selection focus straight to Edge to Follow so the next
            # click in the viewport goes there instead of requiring the user
            # to click into that input themselves first.
            inputs.itemById('follow_edge').hasFocus = True
    elif changed.id == 'hole_size':
        holeSizeInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
        holeDiamInp = inputs.itemById('hole_diameter')
        holeDiamInp.isVisible = (holeSizeInp.selectedItem.name == HOLE_CUSTOM)
    elif changed.id == 'hole_count_type':
        countTypeInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_count_type')
        customCountInp = inputs.itemById('custom_hole_count')
        is_custom = (countTypeInp.selectedItem.name == COUNT_CUSTOM)
        customCountInp.isVisible = is_custom
        # Hole Spacing stays visible either way -- Custom sets the exact count
        # directly but still needs a distance between holes, same as Fill.
    elif changed.id == 'reset_suppressed':
        _suppressed_world_keys = set()
        resetInp: adsk.core.BoolValueCommandInput = inputs.itemById('reset_suppressed')
        resetInp.value = False
    elif changed.id == 'suppress_holes':
        suppressInp: adsk.core.SelectionCommandInput = inputs.itemById('suppress_holes')
        if suppressInp.selectionCount > 0:
            clicked = suppressInp.selection(0).entity
            clicked_pt = clicked.geometry.center if hasattr(clicked.geometry, 'center') else None
            if clicked_pt is not None:
                best_key, best_dist_sq = None, None
                for world_pt in _last_candidates_world:
                    dist_sq = ((world_pt.x - clicked_pt.x) ** 2 + (world_pt.y - clicked_pt.y) ** 2
                               + (world_pt.z - clicked_pt.z) ** 2)
                    if best_dist_sq is None or dist_sq < best_dist_sq:
                        best_key, best_dist_sq = _world_key(world_pt), dist_sq
                if best_key is not None:
                    _suppressed_world_keys.add(best_key)
            suppressInp.clearSelection()

    # Any input that changes where holes would go invalidates previous
    # suppression choices — they no longer correspond to meaningful positions.
    if changed.id in _POSITION_AFFECTING_IDS:
        _suppressed_world_keys = set()

    _update_suppress_visibility(inputs)


def _update_suppress_visibility(inputs: adsk.core.CommandInputs):
    """Fill mode's straight-edge row (`_build_edge_row_line_fill`) and outer-
    edge loop trace (`_process_loop_fill`) both cut via a feature-level
    pattern, which can't support per-hole suppression from script -- hide
    "Suppress Holes" for those specific combinations rather than leave a
    control that silently does nothing when clicked. The circular-edge ring
    (`_process_circular_edge`) still uses a sketch-level pattern, so
    suppression keeps working there regardless of Follow mode."""
    countTypeInp:  adsk.core.DropDownCommandInput  = inputs.itemById('hole_count_type')
    followEdgeInp: adsk.core.SelectionCommandInput = inputs.itemById('follow_edge')
    followModeInp: adsk.core.DropDownCommandInput  = inputs.itemById('follow_mode')

    hides_suppression = False
    if countTypeInp.selectedItem.name == COUNT_FILL and followEdgeInp.selectionCount == 1:
        geo = followEdgeInp.selection(0).entity.geometry
        if isinstance(geo, adsk.core.Circle3D):
            hides_suppression = False
        elif followModeInp.selectedItem.name == FOLLOW_OUTER:
            hides_suppression = True
        else:
            hides_suppression = isinstance(geo, adsk.core.Line3D)

    inputs.itemById('suppress_holes').isVisible = not hides_suppression
    inputs.itemById('reset_suppressed').isVisible = not hides_suppression


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs

    followEdgeInp:  adsk.core.SelectionCommandInput = inputs.itemById('follow_edge')
    startPointInp:  adsk.core.SelectionCommandInput = inputs.itemById('start_point')
    followModeInp:  adsk.core.DropDownCommandInput  = inputs.itemById('follow_mode')
    edgeOffsetInp:  adsk.core.ValueCommandInput     = inputs.itemById('edge_offset')
    holeSizeInp:    adsk.core.DropDownCommandInput  = inputs.itemById('hole_size')
    holeDiamInp:    adsk.core.ValueCommandInput     = inputs.itemById('hole_diameter')
    countTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('hole_count_type')
    customCountInp                                  = inputs.itemById('custom_hole_count')
    holeSpacingInp: adsk.core.ValueCommandInput     = inputs.itemById('hole_spacing')

    edge_ok = followEdgeInp.selectionCount == 1
    if edge_ok:
        entity = followEdgeInp.selection(0).entity
        is_circle = isinstance(entity.geometry, adsk.core.Circle3D)
        follow_outer = followModeInp.selectedItem.name == FOLLOW_OUTER
        # A full circular edge has no vertices to pick a start point from, and
        # is always cut as a complete ring — same for the outer-edge loop
        # trace, where _process_loop_from_edge only ever uses the start point
        # to phase-anchor which hole comes first, not to anchor the loop
        # itself. The point is only actually required to anchor a single-edge
        # row, which has no other way to know which end to start from.
        point_ok = is_circle or follow_outer or startPointInp.selectionCount == 1
    else:
        point_ok = startPointInp.selectionCount <= 1

    offset_ok = edgeOffsetInp.value > 0

    if holeSizeInp.selectedItem.name == HOLE_CUSTOM:
        diam_ok = holeDiamInp.value > 0
    else:
        diam_ok = True

    spacing_ok = holeSpacingInp.value > 0
    if countTypeInp.selectedItem.name == COUNT_CUSTOM:
        count_ok = customCountInp.value >= 1 and spacing_ok
    else:
        count_ok = spacing_ok

    args.areInputsValid = edge_ok and point_ok and offset_ok and diam_ok and count_ok


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dbg_reset(is_preview: bool):
    """Start a fresh debug_log.txt for this run — overwrites the previous run's
    log so the file always reflects only the most recent _run() call."""
    try:
        with open(DEBUG_LOG_PATH, 'w', encoding='utf-8') as f:
            f.write(f'=== {CMD_NAME} run {datetime.datetime.now().isoformat(timespec="seconds")} '
                    f'(preview={is_preview}) ===\n')
    except Exception:
        pass


def _dbg(message: str):
    """Log to both Fusion's Text Command window (futil.log) and debug_log.txt,
    so a full trace of the run is always available on disk to inspect afterward."""
    futil.log(message)
    try:
        with open(DEBUG_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(message + '\n')
    except Exception:
        pass


def _resolve_hole_diam_cm(inputs: adsk.core.CommandInputs) -> float:
    holeSizeInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
    if holeSizeInp.selectedItem.name == HOLE_CUSTOM:
        return inputs.itemById('hole_diameter').value
    return HOLE_SIZE_MAP[holeSizeInp.selectedItem.name]


def _register_and_check_suppressed(sketch: adsk.fusion.Sketch, center_local: adsk.core.Point3D) -> bool:
    """Record `center_local` (sketch space) as a candidate hole position for
    this run — so a later "Suppress Holes" click can be resolved back to it —
    and report whether the user already suppressed it. Converts to model space
    so the same suppression set applies no matter which sketch/face a hole's
    row or loop happens to live on."""
    world_pt = sketch.sketchToModelSpace(center_local)
    _last_candidates_world.append(world_pt)
    return _world_key(world_pt) in _suppressed_world_keys


def _curve_length_cm(curve) -> float:
    if isinstance(curve, adsk.fusion.SketchLine):
        return curve.length
    if isinstance(curve, adsk.fusion.SketchArc):
        geo = curve.geometry
        return geo.radius * abs(geo.endAngle - geo.startAngle)
    if isinstance(curve, adsk.fusion.SketchCircle):
        return 2 * math.pi * curve.radius
    return 0.0


def _point_at_distance_cm(curve, dist_cm: float) -> adsk.core.Point3D:
    """A point `dist_cm` along `curve` measured from its start, in sketch space."""
    if isinstance(curve, adsk.fusion.SketchLine):
        sp = curve.startSketchPoint.geometry
        ep = curve.endSketchPoint.geometry
        t = dist_cm / curve.length if curve.length > 0 else 0.0
        return adsk.core.Point3D.create(
            sp.x + (ep.x - sp.x) * t, sp.y + (ep.y - sp.y) * t, sp.z + (ep.z - sp.z) * t
        )
    if isinstance(curve, adsk.fusion.SketchArc):
        geo = curve.geometry
        sweep = geo.endAngle - geo.startAngle
        length = geo.radius * abs(sweep)
        frac = dist_cm / length if length > 0 else 0.0
        angle = geo.startAngle + sweep * frac
        c, n, r = geo.center, geo.normal, geo.referenceVector
        # perp = n x r, so (r, perp) spans the arc's plane — computed from raw
        # components rather than a Vector3D.crossProduct call to avoid relying
        # on its in-place-vs-returns-new-object behavior.
        perp_x = n.y * r.z - n.z * r.y
        perp_y = n.z * r.x - n.x * r.z
        perp_z = n.x * r.y - n.y * r.x
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        return adsk.core.Point3D.create(
            c.x + geo.radius * (cos_a * r.x + sin_a * perp_x),
            c.y + geo.radius * (cos_a * r.y + sin_a * perp_y),
            c.z + geo.radius * (cos_a * r.z + sin_a * perp_z),
        )
    return None


def _match_profiles_to_centers(sketch: adsk.fusion.Sketch, centers: list, hole_area: float):
    """Match each target center in `centers` to the sketch profile it bounds, by
    nearest centroid among profiles whose area is close to `hole_area`. One pass
    over sketch.profiles regardless of how many centers are being matched, so
    resolving N new holes doesn't cost N separate kernel queries the way calling
    the single-hole lookup N times would. Returns a list of Profile (or None
    where no match was found), in the same order as `centers`.

    `profile.areaProperties()` is real geometry integration and measurably the
    slow part of this function once a sketch has many holes in it (e.g. a
    100+-hole loop trace) — most of that cost is wasted on the sketch's own
    large leftover-face profile, which is never going to match a hole's tiny
    area. `boundingBox` is cheap by comparison, so reject anything wildly the
    wrong size by bounding box before paying for the expensive area call."""
    hole_diam_cm = 2.0 * math.sqrt(hole_area / math.pi)
    candidates = []
    for profile in sketch.profiles:
        bb = profile.boundingBox
        width = bb.maxPoint.x - bb.minPoint.x
        height = bb.maxPoint.y - bb.minPoint.y
        if not (0.5 * hole_diam_cm <= width <= 1.5 * hole_diam_cm
                and 0.5 * hole_diam_cm <= height <= 1.5 * hole_diam_cm):
            continue
        props = profile.areaProperties()
        if abs(hole_area - props.area) > hole_area * 0.05:
            continue
        candidates.append((profile, props.centroid))

    matches = [None] * len(centers)
    used = set()
    for i, center in enumerate(centers):
        best_idx = None
        best_dist_sq = None
        for ci, (_, centroid) in enumerate(candidates):
            if ci in used:
                continue
            dist_sq = (centroid.x - center.x) ** 2 + (centroid.y - center.y) ** 2
            if best_idx is None or dist_sq < best_dist_sq:
                best_idx = ci
                best_dist_sq = dist_sq
        if best_idx is not None:
            used.add(best_idx)
            matches[i] = candidates[best_idx][0]
    return matches


def _nearest_dist_along_curve(curve, pt_sketch: adsk.core.Point3D, samples: int = 64):
    """Closest location on `curve` (line or arc) to `pt_sketch`, both in sketch
    space. Returns (distance from curve's start along the curve, perpendicular
    distance to that closest point) — found by uniform sampling rather than an
    exact projection, since this is only used to pick which hole in a loop
    counts as "first" and doesn't need to be exact."""
    length = _curve_length_cm(curve)
    if length <= 0:
        return 0.0, float('inf')
    best_dist, best_perp = 0.0, None
    for i in range(samples + 1):
        d = length * i / samples
        p = _point_at_distance_cm(curve, d)
        perp = math.hypot(pt_sketch.x - p.x, pt_sketch.y - p.y)
        if best_perp is None or perp < best_perp:
            best_perp, best_dist = perp, d
    return best_dist, best_perp


def _nearest_dist_along_loop(segments: list, seg_lengths: list, pt_sketch: adsk.core.Point3D) -> float:
    """Distance along the whole loop (summed over `segments` in order) to the
    point on the loop closest to `pt_sketch` — used to anchor a loop trace's
    first hole near a user-picked start point."""
    cum = 0.0
    best_total, best_perp = 0.0, None
    for seg, seg_len in zip(segments, seg_lengths):
        d, perp = _nearest_dist_along_curve(seg, pt_sketch)
        if best_perp is None or perp < best_perp:
            best_perp, best_total = perp, cum + d
        cum += seg_len
    return best_total


def _perp_toward(dir_vec: adsk.core.Vector2D,
                 from_pt: adsk.core.Point3D,
                 hint_pt: adsk.core.Point3D) -> adsk.core.Vector2D:
    """Unit vector perpendicular to `dir_vec`, oriented toward `hint_pt` as seen
    from `from_pt` (both in the same 2D sketch space). Deliberately does its own
    dot-product sign test rather than using `futil.sketchLineNormal`'s towardPt
    hint — that hint is a no-op there, since Vector2D.angleTo() only ever
    returns an unsigned angle in [0, pi], so its `abs(angle) > math.pi` flip
    condition can never be true."""
    perp = adsk.core.Vector2D.create(-dir_vec.y, dir_vec.x)
    to_hint_x = hint_pt.x - from_pt.x
    to_hint_y = hint_pt.y - from_pt.y
    if perp.x * to_hint_x + perp.y * to_hint_y < 0:
        perp = futil.multVector2D(perp, -1.0)
    return perp


def _seed_center(corner_pt: adsk.core.Point3D,
                 dir_vec: adsk.core.Vector2D,
                 ref_dir_vec: adsk.core.Vector2D,
                 start_offset_cm: float,
                 edge_offset_cm: float) -> adsk.core.Point3D:
    return adsk.core.Point3D.create(
        corner_pt.x + dir_vec.x * start_offset_cm + ref_dir_vec.x * edge_offset_cm,
        corner_pt.y + dir_vec.y * start_offset_cm + ref_dir_vec.y * edge_offset_cm,
        corner_pt.z,
    )


# ===========================================================================
# Single-edge row — seed hole + feature rectangular pattern (straight edges),
# or manual sampling + one-shot multi-profile cut (arcs)
# ===========================================================================

def _cut_hole_centers(sketch: adsk.fusion.Sketch,
                      body: adsk.fusion.BRepBody,
                      centers: list,
                      hole_diam_cm: float) -> int:
    """Shared final step for every "cut a batch of individually-suppressible
    holes" path (straight-edge row, arc row, loop trace): check suppression
    before drawing anything -- a suppressed hole never gets a circle at all,
    rather than being drawn and filtered out afterward, so it costs nothing in
    every later profile lookup either -- then cut every surviving hole in one
    combined multi-profile extrude. Returns the number of holes actually cut."""
    cut_centers = [c for c in centers if not _register_and_check_suppressed(sketch, c)]
    if not cut_centers:
        _dbg(f'{CMD_NAME}:   every hole here is suppressed, nothing to cut')
        return 0
    for c in cut_centers:
        sketch.sketchCurves.sketchCircles.addByCenterRadius(c, hole_diam_cm / 2.0)

    holeArea = hole_diam_cm ** 2 * math.pi / 4.0
    profiles = _match_profiles_to_centers(sketch, cut_centers, holeArea)
    profileCollection = adsk.core.ObjectCollection.create()
    missing = 0
    for p in profiles:
        if p is not None:
            profileCollection.add(p)
        else:
            missing += 1
    if missing:
        _dbg(f'{CMD_NAME}:   {missing} of {len(cut_centers)} hole profiles not found')
    if profileCollection.count == 0:
        _dbg(f'{CMD_NAME}:   no profiles found, skipping cut')
        return 0

    comp = body.parentComponent
    extrudes = comp.features.extrudeFeatures
    cutInput = extrudes.createInput(profileCollection, adsk.fusion.FeatureOperations.CutFeatureOperation)
    cutExtent = adsk.fusion.ThroughAllExtentDefinition.create()
    cutInput.setOneSideExtent(cutExtent, adsk.fusion.ExtentDirections.NegativeExtentDirection)
    cutInput.participantBodies = [body]
    try:
        extrudes.add(cutInput)
    except RuntimeError as err:
        _dbg(f'{CMD_NAME}:   cut FAILED, skipping. Error: {err}')
        return 0
    _dbg(f'{CMD_NAME}:   cut OK, {profileCollection.count} holes')
    return profileCollection.count


def _length_param_name(sketch: adsk.fusion.Sketch, line: adsk.fusion.SketchLine):
    """Adds a *reference* distance dimension across `line` (the projected,
    associative copy of the selected edge) so its live length is exposed as a
    named parameter other expressions can reference. Mirrors
    `_radius_param_name` for the circular-edge case. Returns None (falls back
    to a fixed count) if this fails."""
    try:
        start_pt = line.startSketchPoint.geometry
        end_pt = line.endSketchPoint.geometry
        mid_pt = futil.midPoint3D(start_pt, end_pt)
        dim = sketch.sketchDimensions.addDistanceDimension(
            line.startSketchPoint, line.endSketchPoint,
            adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
            mid_pt, False,
        )
        return dim.parameter.name
    except Exception:
        futil.handle_error(f'{CMD_NAME} _length_param_name', show_message_box=True)
        return None


def _build_edge_row_line(sketch: adsk.fusion.Sketch,
                         body: adsk.fusion.BRepBody,
                         corner: adsk.fusion.SketchPoint,
                         dir_vec: adsk.core.Vector2D,
                         perp_vec: adsk.core.Vector2D,
                         own_line: adsk.fusion.SketchLine,
                         length_in: float,
                         hole_diam_cm: float,
                         edge_offset_cm: float,
                         custom_count: int,
                         spacing_cm: float,
                         is_preview: bool = False):
    """Cuts a *Custom*-count row of holes starting near `corner`, running along
    `dir_vec` (from the picked Start Point toward the far end of the edge),
    offset `HOLE_OFFSET_CM` from the start (longitudinal) and `edge_offset_cm`
    inward from `own_line` (perpendicular, along `perp_vec`). Fill-mode rows
    use `_build_edge_row_line_fill` instead -- see that function's docstring
    for why the two need different architectures.

    Only the *seed* hole is placed and dimensioned directly (diameter, plus
    perpendicular/longitudinal offsets pinning it to the real edge); the rest
    come from a sketch-level `RectangularPatternConstraint` off that seed, so
    every hole ends up fully constrained -- letting any of them float
    unconstrained (drawn from a bare coordinate, as a prior version of this
    function did) risks it silently drifting if the sketch is later touched.

    That sketch-level pattern is also where suppression is implemented, via
    its own `isSuppressed` property -- unlike a *feature*-level pattern's
    `PatternElement.isSuppressed` / `suppressedElementsIds`, which consistently
    failed with "Didn't roll editing feature back" in testing regardless of
    configuration, the sketch-level equivalent reliably removes the specific
    suppressed copies from the sketch entirely. Only the seed itself can't be
    addressed this way (a pattern's base instance is never in its own
    `isSuppressed` list), so the seed's own suppression is instead handled by
    just leaving its profile out of the final cut -- the circle still exists
    (fully constrained) but nothing gets cut there."""
    start_offset_cm = HOLE_OFFSET_CM
    count = max(0, custom_count)

    _dbg(f'{CMD_NAME}:   _build_edge_row_line length_in={length_in:.4f} '
         f'custom_count={custom_count} spacing_cm={spacing_cm:.4f} -> count={count}, '
         f'start_offset_cm={start_offset_cm:.4f}')

    if count <= 0:
        _dbg(f'{CMD_NAME}:   no holes requested, skipping row')
        return
    if length_in * IN_TO_CM <= start_offset_cm:
        _dbg(f'{CMD_NAME}:   edge too short for the required start offset, skipping row')
        return

    corner_pt = corner.geometry
    seed_center = _seed_center(corner_pt, dir_vec, perp_vec, start_offset_cm, edge_offset_cm)
    seed_suppressed = _register_and_check_suppressed(sketch, seed_center)

    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(seed_center, hole_diam_cm / 2.0)
    all_circles = [] if seed_suppressed else [circle]

    # Build the direction line and the sketch-level rectangular pattern before
    # dimensioning the seed below (creating the pattern from an *already*
    # fully-dimensioned seed was tried and measurably shifted the whole
    # pattern by one spacing unit, confirmed by comparing resulting positions
    # directly against this order).
    pattern = None
    if count > 1:
        dir_len_cm = length_in * IN_TO_CM + 5.0
        dir_end = adsk.core.Point3D.create(
            corner_pt.x + dir_vec.x * dir_len_cm, corner_pt.y + dir_vec.y * dir_len_cm, corner_pt.z
        )
        dir_line = sketch.sketchCurves.sketchLines.addByTwoPoints(corner_pt, dir_end)
        dir_line.isConstruction = True
        # Keep this line's *direction* tied to the real edge (own_line) via a
        # parallel constraint, and its start point tied to the real corner via
        # an explicit coincident constraint -- rather than freezing the far
        # endpoint's absolute coordinates with isFixed, or relying on passing
        # `corner` itself into addByTwoPoints to reuse that same point object.
        # That reuse looks identical right after creation (confirmed via `is`)
        # but does NOT survive a later recompute that regenerates the
        # projected edge: `own_line` picks up the edge's new position
        # correctly, but a point merely *reused* from its old corner ends up
        # orphaned at the old location once that regeneration replaces it --
        # confirmed by resizing a part with an edge whose start vertex moves,
        # where the projected edge updated but this line's reused point didn't
        # move with it. An explicit constraint is re-solved every recompute
        # against the corner's live identity, not a point object snapshot.
        sketch.geometricConstraints.addParallel(dir_line, own_line)
        sketch.geometricConstraints.addCoincident(dir_line.startSketchPoint, corner)

        patInput = sketch.geometricConstraints.createRectangularPatternInput(
            [circle], adsk.fusion.PatternDistanceType.SpacingPatternDistanceType
        )
        # Unlike most ValueInput.createByReal() uses elsewhere in the API (raw
        # internal cm), this specific distance parameter was found to read a
        # bare real number in the document's *display* unit (inches here) --
        # confirmed by testing directly against a live pattern's resulting
        # distanceOne.value. An explicit unit string sidesteps the ambiguity.
        patInput.setDirectionOne(dir_line, futil.Value(count),
                                 adsk.core.ValueInput.createByString(f'{spacing_cm} cm'))
        pattern = sketch.geometricConstraints.addRectangularPattern(patInput)
        all_circles.extend(pattern.createdEntities)

        # dir_line's length is deliberately left undimensioned -- its direction
        # is already fully fixed by the parallel constraint above, which is all
        # setDirectionOne needs. Pinning the length too would add a driving
        # dimension with an arbitrary value that has no geometric meaning,
        # cluttering the sketch with a number nobody reading it should care
        # about.

    # Dimensioning is skipped during a live preview -- mirrors
    # _process_circular_edge's is_preview handling, and matches the
    # "lightweight executePreview" pattern documented in LESSONS_LEARNED.md:
    # only build what's needed to show the result, and let command_execute
    # build the real, fully-dimensioned/parametric sketch on OK.
    if not is_preview:
        textPt = futil.offsetPoint3D(circle.centerSketchPoint.geometry, 0.1, 0.1, 0)
        diamDim = sketch.sketchDimensions.addDiameterDimension(circle, textPt)
        diamDim.value = hole_diam_cm

        perpDim = sketch.sketchDimensions.addOffsetDimension(own_line, circle.centerSketchPoint, corner_pt)
        perpDim.value = edge_offset_cm

        # A short construction line perpendicular to own_line, through the same
        # associative corner point, purely to give the longitudinal offset
        # dimension something to measure from -- there's no second selected edge
        # to serve that role now that holes follow a single picked edge. Reusing
        # `corner` directly (rather than a fresh point at the same coordinates)
        # keeps this line's start attached to the real edge; a perpendicular
        # constraint to own_line (rather than freezing the far endpoint's absolute
        # coordinates with isFixed) keeps its direction correct even if the edge's
        # start vertex moves later -- a hard-fixed far end would otherwise let the
        # line distort out of true perpendicularity as soon as only its start end
        # moves, corrupting the longitudinal dimension measured off it.
        ref_len_cm = edge_offset_cm + 2.0
        ref_end = adsk.core.Point3D.create(
            corner_pt.x + perp_vec.x * ref_len_cm, corner_pt.y + perp_vec.y * ref_len_cm, corner_pt.z
        )
        # A fresh point plus an explicit coincident constraint to `corner`, not
        # `corner` reused directly as the start -- see the matching note on
        # dir_line above for why reuse alone doesn't survive a later recompute
        # that regenerates the projected edge.
        ref_line = sketch.sketchCurves.sketchLines.addByTwoPoints(corner_pt, ref_end)
        ref_line.isConstruction = True
        sketch.geometricConstraints.addPerpendicular(ref_line, own_line)
        sketch.geometricConstraints.addCoincident(ref_line.startSketchPoint, corner)
        # ref_line's length is left undimensioned, same reasoning as dir_line
        # above -- addOffsetDimension below only needs its direction (fixed by
        # the perpendicular constraint), not an arbitrary pinned length.

        longDim = sketch.sketchDimensions.addOffsetDimension(ref_line, circle.centerSketchPoint, corner_pt)
        longDim.value = start_offset_cm

    # Suppression is applied last, only once the seed is fully dimensioned and
    # only if something actually needs suppressing -- writing `isSuppressed`
    # at all (even an all-False list) while the seed still has any remaining
    # DOF, or with no True values in it, was found to make the solver pick a
    # different (but still technically valid) solution branch, silently
    # reversing the whole pattern's direction. Confirmed directly: identical
    # setup, only the presence/timing of this assignment differed.
    if pattern is not None:
        # createdEntities is in creation order (k=1..count-1 along dir_vec),
        # matching this same stepping formula used for the seed (k=0).
        suppressed_flags = [
            _register_and_check_suppressed(
                sketch, _seed_center(corner_pt, dir_vec, perp_vec, start_offset_cm + k * spacing_cm, edge_offset_cm)
            )
            for k in range(1, count)
        ]
        if any(suppressed_flags):
            pattern.isSuppressed = suppressed_flags
            all_circles = ([] if seed_suppressed else [circle]) + list(pattern.createdEntities)

    if not all_circles:
        _dbg(f'{CMD_NAME}:   every hole in this row is suppressed, nothing to cut')
        return

    holeArea = hole_diam_cm ** 2 * math.pi / 4.0
    hole_centers = [c.centerSketchPoint.geometry for c in all_circles]
    profiles = _match_profiles_to_centers(sketch, hole_centers, holeArea)
    profileCollection = adsk.core.ObjectCollection.create()
    missing = 0
    for p in profiles:
        if p is not None:
            profileCollection.add(p)
        else:
            missing += 1
    if missing:
        _dbg(f'{CMD_NAME}:   {missing} of {len(hole_centers)} hole profiles not found')
    if profileCollection.count == 0:
        _dbg(f'{CMD_NAME}:   no profiles found for row, skipping')
        return

    comp = body.parentComponent
    extrudes = comp.features.extrudeFeatures
    cutInput = extrudes.createInput(profileCollection, adsk.fusion.FeatureOperations.CutFeatureOperation)
    cutExtent = adsk.fusion.ThroughAllExtentDefinition.create()
    cutInput.setOneSideExtent(cutExtent, adsk.fusion.ExtentDirections.NegativeExtentDirection)
    cutInput.participantBodies = [body]
    try:
        extrudes.add(cutInput)
    except RuntimeError as err:
        _dbg(f'{CMD_NAME}:   row cut FAILED, skipping. Error: {err}')
        return
    _dbg(f'{CMD_NAME}:   row cut OK, {profileCollection.count} holes')


def _build_edge_row_line_fill(sketch: adsk.fusion.Sketch,
                              body: adsk.fusion.BRepBody,
                              corner: adsk.fusion.SketchPoint,
                              dir_vec: adsk.core.Vector2D,
                              perp_vec: adsk.core.Vector2D,
                              own_line: adsk.fusion.SketchLine,
                              length_in: float,
                              hole_diam_cm: float,
                              edge_offset_cm: float,
                              spacing_cm: float,
                              is_preview: bool = False):
    """Fill-mode straight-edge row. Cuts only the *seed* hole as its own
    extrude feature, then replicates that cut with a *feature*-level
    `RectangularPatternFeature` whose quantity is a live expression tied to
    the edge's length (via `_length_param_name`). Unlike `_build_edge_row_line`
    (which builds N sketch circles up front and cuts them all in one
    multi-profile extrude), this makes the CUT itself re-run however many
    times the current edge length calls for -- a plain extrude only ever
    knows about the specific profiles it was given at creation, so a sketch
    pattern that grows later never gets material removed for the new holes.
    A feature-level pattern is the only construct that reruns the underlying
    cut a live, expression-driven number of times.

    This trades away per-hole suppression: `PatternElement.isSuppressed` /
    `suppressedElementsIds` reliably fails with "Didn't roll editing feature
    back" for this exact feature type (see LESSONS_LEARNED.md), so "Suppress
    Holes" can't apply here -- the dialog hides that input for Fill mode on a
    straight edge (see `_update_suppress_visibility`). Custom-count rows keep
    using `_build_edge_row_line`'s sketch-level pattern instead, since their
    count never needs to grow after the fact and suppression still works."""
    start_offset_cm = HOLE_OFFSET_CM
    spacing_in = spacing_cm / IN_TO_CM
    # Count from the *usable* length (after the fixed start offset), not the
    # raw edge length -- mirrors _build_edge_row_arc's usable_length_in.
    # Using the raw length let the last hole's center land past the edge's
    # far end whenever spacing didn't evenly divide it, which a feature-level
    # pattern cuts as a real half-moon notch bitten out of the part's
    # boundary rather than a clean hole -- confirmed live via the Fusion MCP
    # tools (6in edge, 0.5in offset, 0.4in spacing produced a crescent Arc3D
    # edge at the plate's far end instead of a 15th round hole).
    usable_length_in = (length_in * IN_TO_CM - start_offset_cm) / IN_TO_CM
    count = max(1, int(usable_length_in / spacing_in + 1e-9))

    _dbg(f'{CMD_NAME}:   _build_edge_row_line_fill length_in={length_in:.4f} '
         f'spacing_cm={spacing_cm:.4f} -> count={count}, start_offset_cm={start_offset_cm:.4f}')

    if length_in * IN_TO_CM <= start_offset_cm:
        _dbg(f'{CMD_NAME}:   edge too short for the required start offset, skipping row')
        return

    corner_pt = corner.geometry
    seed_center = _seed_center(corner_pt, dir_vec, perp_vec, start_offset_cm, edge_offset_cm)
    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(seed_center, hole_diam_cm / 2.0)

    # Same direction-line setup as _build_edge_row_line: an explicit parallel
    # + coincident constraint (not a reused point) so it survives a later
    # recompute that regenerates the projected edge.
    dir_line = None
    if count > 1:
        dir_len_cm = length_in * IN_TO_CM + 5.0
        dir_end = adsk.core.Point3D.create(
            corner_pt.x + dir_vec.x * dir_len_cm, corner_pt.y + dir_vec.y * dir_len_cm, corner_pt.z
        )
        dir_line = sketch.sketchCurves.sketchLines.addByTwoPoints(corner_pt, dir_end)
        dir_line.isConstruction = True
        sketch.geometricConstraints.addParallel(dir_line, own_line)
        sketch.geometricConstraints.addCoincident(dir_line.startSketchPoint, corner)

    if not is_preview:
        textPt = futil.offsetPoint3D(circle.centerSketchPoint.geometry, 0.1, 0.1, 0)
        diamDim = sketch.sketchDimensions.addDiameterDimension(circle, textPt)
        diamDim.value = hole_diam_cm

        perpDim = sketch.sketchDimensions.addOffsetDimension(own_line, circle.centerSketchPoint, corner_pt)
        perpDim.value = edge_offset_cm

        ref_len_cm = edge_offset_cm + 2.0
        ref_end = adsk.core.Point3D.create(
            corner_pt.x + perp_vec.x * ref_len_cm, corner_pt.y + perp_vec.y * ref_len_cm, corner_pt.z
        )
        ref_line = sketch.sketchCurves.sketchLines.addByTwoPoints(corner_pt, ref_end)
        ref_line.isConstruction = True
        sketch.geometricConstraints.addPerpendicular(ref_line, own_line)
        sketch.geometricConstraints.addCoincident(ref_line.startSketchPoint, corner)

        longDim = sketch.sketchDimensions.addOffsetDimension(ref_line, circle.centerSketchPoint, corner_pt)
        longDim.value = start_offset_cm

    holeArea = hole_diam_cm ** 2 * math.pi / 4.0
    seed_profile = _match_profiles_to_centers(sketch, [seed_center], holeArea)[0]
    if seed_profile is None:
        _dbg(f'{CMD_NAME}:   seed hole profile not found, skipping row')
        return

    comp = body.parentComponent
    extrudes = comp.features.extrudeFeatures
    cutInput = extrudes.createInput(seed_profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    cutExtent = adsk.fusion.ThroughAllExtentDefinition.create()
    cutInput.setOneSideExtent(cutExtent, adsk.fusion.ExtentDirections.NegativeExtentDirection)
    cutInput.participantBodies = [body]
    try:
        seed_cut = extrudes.add(cutInput)
    except RuntimeError as err:
        _dbg(f'{CMD_NAME}:   seed cut FAILED, skipping row. Error: {err}')
        return
    _dbg(f'{CMD_NAME}:   seed cut OK')

    if count <= 1:
        return

    feat_col = adsk.core.ObjectCollection.create()
    feat_col.add(seed_cut)
    pat_feats = comp.features.rectangularPatternFeatures
    patInput = pat_feats.createInput(
        feat_col, dir_line, futil.Value(count),
        adsk.core.ValueInput.createByString(f'{spacing_cm} cm'),
        adsk.fusion.PatternDistanceType.SpacingPatternDistanceType,
    )
    try:
        pattern = pat_feats.add(patInput)
    except RuntimeError as err:
        _dbg(f'{CMD_NAME}:   row pattern FAILED, only the seed hole was cut. Error: {err}')
        return
    _dbg(f'{CMD_NAME}:   row pattern OK, {count} holes')

    # Upgrade the quantity to a live formula tied to the edge's length, same
    # recipe as _process_circular_edge's radius-driven ring count. Only in
    # non-preview execute -- lightweight preview matches LESSONS_LEARNED.md.
    if not is_preview:
        try:
            length_param = _length_param_name(sketch, own_line)
            if length_param is not None:
                # Subtract the start offset before dividing by spacing, same
                # as the usable_length_in fix above -- this expression is
                # what actually drives quantityOne after a resize (it
                # overwrites the literal `count` this function was just
                # built with), so leaving it off the raw edge length would
                # silently reintroduce the exact same far-end overshoot the
                # moment the part is resized and recomputed.
                start_offset_in = start_offset_cm / IN_TO_CM
                formula = f'max(1; floor(({length_param} - {start_offset_in} in) / {spacing_in} in))'
                pattern.quantityOne.expression = formula
        except Exception:
            futil.handle_error(f'{CMD_NAME} line live-quantity link', show_message_box=False)


def _build_edge_row_arc(sketch: adsk.fusion.Sketch,
                        body: adsk.fusion.BRepBody,
                        own_arc: adsk.fusion.SketchArc,
                        anchor_is_curve_start: bool,
                        length_cm: float,
                        face_hint_sketch: adsk.core.Point3D,
                        hole_diam_cm: float,
                        edge_offset_cm: float,
                        is_fill: bool,
                        custom_count: int,
                        spacing_cm: float):
    """Cut a row of holes along a single arc edge, sampling points at
    `spacing_cm` pitch (mirroring the full loop trace) and cutting them all in
    one multi-profile extrude via `_cut_hole_centers`. Unlike the straight-line
    row, there's no live-updating Fill count here — same limitation as the
    loop trace, since there's no single simple length parameter for an
    arbitrary arc offset.

    Note on the inward test: comparing the *hint point's distance from the
    arc's center* against the arc's radius (the way `_process_circular_edge`
    does) only works when the circle's inside/outside genuinely spans the
    whole local region, which is true for a full bore or the part's own outer
    round edge. It's wrong for a small arc that's just one segment of a larger
    loop (e.g. a corner fillet on a rectangular plate) — a hint point far
    across the face can sit "outside" the small fillet circle by that
    center-distance test while still being on the *inward* side of the arc's
    own local boundary. So this uses a local dot-product test at the arc's own
    midpoint instead — same idea as `_perp_toward` for a line, just against the
    radial direction instead of a line's perpendicular."""
    geo = own_arc.geometry
    center = geo.center
    mid_pt = _point_at_distance_cm(own_arc, length_cm / 2.0)
    radial_x, radial_y = mid_pt.x - center.x, mid_pt.y - center.y
    hint_x, hint_y = face_hint_sketch.x - mid_pt.x, face_hint_sketch.y - mid_pt.y
    toward_center = (radial_x * hint_x + radial_y * hint_y) < 0
    pattern_radius_cm = (own_arc.radius - edge_offset_cm) if toward_center else (own_arc.radius + edge_offset_cm)
    if pattern_radius_cm <= 0:
        _dbg(f'{CMD_NAME}:   edge offset collapses the arc row to nothing, skipping')
        return

    usable_length_in = (length_cm - HOLE_OFFSET_CM) / IN_TO_CM
    if usable_length_in <= 0:
        _dbg(f'{CMD_NAME}:   arc too short for the required start offset, skipping')
        return

    if is_fill:
        spacing_in = spacing_cm / IN_TO_CM
        count = max(1, int(usable_length_in / spacing_in + 1e-9))
    else:
        count = max(0, custom_count)
    if count <= 0:
        _dbg(f'{CMD_NAME}:   no holes requested, skipping arc row')
        return

    _dbg(f'{CMD_NAME}:   arc row toward_center={toward_center} '
         f'pattern_radius_in={pattern_radius_cm / IN_TO_CM:.4f} usable_length_in={usable_length_in:.4f} '
         f'spacing_cm={spacing_cm:.4f} -> count={count}')

    centers = []
    for k in range(count):
        dist_from_anchor_cm = HOLE_OFFSET_CM + k * spacing_cm
        curve_dist = dist_from_anchor_cm if anchor_is_curve_start else (length_cm - dist_from_anchor_cm)
        curve_dist = max(0.0, min(length_cm, curve_dist))
        edge_pt = _point_at_distance_cm(own_arc, curve_dist)
        ang = math.atan2(edge_pt.y - center.y, edge_pt.x - center.x)
        centers.append(adsk.core.Point3D.create(
            center.x + pattern_radius_cm * math.cos(ang),
            center.y + pattern_radius_cm * math.sin(ang),
            edge_pt.z,
        ))

    _cut_hole_centers(sketch, body, centers, hole_diam_cm)


def _process_single_edge_row(edge: adsk.fusion.BRepEdge,
                             anchor_pt_model: adsk.core.Point3D,
                             hole_diam_cm: float,
                             edge_offset_cm: float,
                             is_fill: bool,
                             custom_count: int,
                             spacing_cm: float,
                             is_preview: bool = False):
    """Cut one row of holes starting near `anchor_pt_model`, running along
    `edge` to its far end. `edge` may be straight or an arc. The perpendicular
    inset direction (which side of the edge counts as "inward") is inferred
    from the larger planar face bordering the edge, using a guaranteed
    interior point on that face as a hint — the same inward/outward test the
    full loop trace uses, generalized down to a single edge."""
    _dbg(f'{CMD_NAME}: single-edge row edge.entityToken={edge.entityToken}')
    try:
        planar_faces = [f for f in edge.faces if isinstance(f.geometry, adsk.core.Plane)]
        target_face = max(planar_faces, key=lambda f: f.area) if planar_faces else None
        if target_face is None:
            _dbg(f'{CMD_NAME}: edge does not border a flat face, skipping')
            return

        comp = target_face.body.parentComponent
        body = target_face.body
        sketch = comp.sketches.add(target_face)
        sketch.name = 'AutoHole'

        proj = sketch.project(edge)
        if proj.count == 0:
            _dbg(f'{CMD_NAME}: failed to project edge into sketch, skipping')
            return
        own_curve = proj.item(0)

        if not isinstance(own_curve, (adsk.fusion.SketchLine, adsk.fusion.SketchArc)):
            _dbg(f"{CMD_NAME}: edge type isn't a line or arc, skipping single-edge row")
            return

        anchor_sketch = sketch.modelToSketchSpace(anchor_pt_model)
        start_geo = own_curve.startSketchPoint.geometry
        end_geo = own_curve.endSketchPoint.geometry
        dist_to_start = math.hypot(anchor_sketch.x - start_geo.x, anchor_sketch.y - start_geo.y)
        dist_to_end = math.hypot(anchor_sketch.x - end_geo.x, anchor_sketch.y - end_geo.y)
        anchor_is_curve_start = dist_to_start <= dist_to_end
        corner = own_curve.startSketchPoint if anchor_is_curve_start else own_curve.endSketchPoint
        corner_pt = corner.geometry

        length_cm = _curve_length_cm(own_curve)
        length_in = length_cm / IN_TO_CM
        face_hint_sketch = sketch.modelToSketchSpace(target_face.pointOnFace)

        _dbg(f'{CMD_NAME}: corner={futil.format_Point3D(corner_pt)}, length_in={length_in:.4f}, '
             f'anchor_is_curve_start={anchor_is_curve_start}')

        if isinstance(own_curve, adsk.fusion.SketchLine):
            dir_vec = futil.sketchLineUnitVec(own_curve)
            if not anchor_is_curve_start:
                dir_vec = futil.multVector2D(dir_vec, -1.0)
            perp_vec = _perp_toward(dir_vec, corner_pt, face_hint_sketch)
            if is_fill:
                # Fill needs the cut itself (not just the sketch pattern) to
                # grow/shrink with the edge later, which requires a
                # feature-level pattern -- see _build_edge_row_line_fill.
                _build_edge_row_line_fill(sketch, body, corner, dir_vec, perp_vec, own_curve,
                                          length_in, hole_diam_cm, edge_offset_cm, spacing_cm,
                                          is_preview=is_preview)
            else:
                _build_edge_row_line(sketch, body, corner, dir_vec, perp_vec, own_curve, length_in,
                                     hole_diam_cm, edge_offset_cm, custom_count, spacing_cm,
                                     is_preview=is_preview)
        else:
            _build_edge_row_arc(sketch, body, own_curve, anchor_is_curve_start, length_cm,
                                face_hint_sketch, hole_diam_cm, edge_offset_cm, is_fill,
                                custom_count, spacing_cm)

    except Exception:
        _dbg(f'{CMD_NAME}: EXCEPTION in _process_single_edge_row:\n{traceback.format_exc()}')
        futil.handle_error(f'{CMD_NAME} _process_single_edge_row', show_message_box=True)


# ===========================================================================
# Circular edge — full-circumference ring of holes
# ===========================================================================

def _radius_param_name(sketch: adsk.fusion.Sketch, circle: adsk.fusion.SketchCircle):
    """Adds a *reference* radial dimension on `circle` (the projected,
    associative copy of the selected edge) so its live radius is exposed as a
    named parameter other expressions can reference. Returns None (falls back
    to a fixed count) if this fails."""
    try:
        text_pt = futil.offsetPoint3D(
            circle.centerSketchPoint.geometry, circle.radius * 0.7, circle.radius * 0.7, 0
        )
        dim = sketch.sketchDimensions.addRadialDimension(circle, text_pt, False)
        return dim.parameter.name
    except Exception:
        futil.handle_error(f'{CMD_NAME} _radius_param_name', show_message_box=True)
        return None


def _process_circular_edge(edge: adsk.fusion.BRepEdge,
                           hole_diam_cm: float,
                           edge_offset_cm: float,
                           is_fill: bool,
                           custom_count: int,
                           spacing_cm: float,
                           is_preview: bool = False,
                           anchor_pt_model: adsk.core.Point3D = None):
    """A circular edge needs no partner edge or corner — it's already a closed
    loop, so the row simply wraps all the way around, offset `edge_offset_cm`
    in from the edge, cut straight through the flat face it borders (same cut
    direction as the linear-edge case, just arranged in a ring instead of a
    row). All N holes are cut in a single multi-profile extrude, matching the
    straight-line row and loop trace."""
    _dbg(f'{CMD_NAME}: circular edge.entityToken={edge.entityToken}')

    try:
        target_face = next((f for f in edge.faces if isinstance(f.geometry, adsk.core.Plane)), None)
        if target_face is None:
            _dbg(f'{CMD_NAME}: circular edge does not border a flat face, skipping')
            return

        comp = target_face.body.parentComponent
        body = target_face.body

        sketch = comp.sketches.add(target_face)
        sketch.name = 'AutoHole'

        circle_col = sketch.project(edge)
        if circle_col.count == 0:
            _dbg(f'{CMD_NAME}: failed to project circular edge into sketch, skipping')
            return
        rim_circle: adsk.fusion.SketchCircle = circle_col.item(0)
        center_pt = rim_circle.centerSketchPoint
        center_geo = center_pt.geometry

        pattern_radius_cm = rim_circle.radius - edge_offset_cm
        if pattern_radius_cm <= 0:
            _dbg(f'{CMD_NAME}: edge offset is >= the edge radius, skipping')
            return

        circumference_in = 2 * math.pi * (pattern_radius_cm / IN_TO_CM)
        if is_fill:
            spacing_in = spacing_cm / IN_TO_CM
            count = max(1, int(circumference_in / spacing_in + 1e-9))
        else:
            count = max(1, custom_count)

        _dbg(f'{CMD_NAME}:   circular edge radius_in={rim_circle.radius / IN_TO_CM:.4f} '
             f'pattern_radius_in={pattern_radius_cm / IN_TO_CM:.4f} is_fill={is_fill} '
             f'custom_count={custom_count} spacing_cm={spacing_cm:.4f} -> count={count}')

        # The start angle is arbitrary (local +X) unless the user picked a
        # Start Point, in which case the ring is phase-anchored so the first
        # hole lands near it.
        if anchor_pt_model is not None:
            anchor_sketch = sketch.modelToSketchSpace(anchor_pt_model)
            start_angle = math.atan2(anchor_sketch.y - center_geo.y, anchor_sketch.x - center_geo.x)
        else:
            start_angle = 0.0
        seed_center = adsk.core.Point3D.create(
            center_geo.x + pattern_radius_cm * math.cos(start_angle),
            center_geo.y + pattern_radius_cm * math.sin(start_angle),
            center_geo.z,
        )
        hole = sketch.sketchCurves.sketchCircles.addByCenterRadius(seed_center, hole_diam_cm / 2.0)

        if not is_preview:
            textPt = futil.offsetPoint3D(hole.centerSketchPoint.geometry, 0.1, 0.1, 0)
            diamDim = sketch.sketchDimensions.addDiameterDimension(hole, textPt)
            diamDim.value = hole_diam_cm

            # Pin the seed hole's distance from the rim's center to the real
            # offset — the only positional constraint that matters here, since
            # the start angle itself is arbitrary (or just a UX anchor).
            radiusTextPt = futil.offsetPoint3D(center_geo, pattern_radius_cm / 2, 0.1, 0)
            radiusDim = sketch.sketchDimensions.addDistanceDimension(
                center_pt, hole.centerSketchPoint,
                adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
                radiusTextPt, True,
            )
            radiusDim.value = pattern_radius_cm

        centers = [seed_center]
        skPattern = None
        if count > 1:
            patInput = sketch.geometricConstraints.createCircularPatternInput([hole], center_pt)
            patInput.quantity = futil.Value(count)
            skPattern = sketch.geometricConstraints.addCircularPattern(patInput)
            for created in skPattern.createdEntities:
                centers.append(created.centerSketchPoint.geometry)

        centers = [c for c in centers if not _register_and_check_suppressed(sketch, c)]
        if not centers:
            _dbg(f'{CMD_NAME}:   every hole in this ring is suppressed, nothing to cut')
            return

        holeArea = hole_diam_cm ** 2 * math.pi / 4.0
        profiles = _match_profiles_to_centers(sketch, centers, holeArea)
        profileCollection = adsk.core.ObjectCollection.create()
        missing = 0
        for p in profiles:
            if p is not None:
                profileCollection.add(p)
            else:
                missing += 1
        if missing:
            _dbg(f'{CMD_NAME}:   {missing} of {len(centers)} hole profiles not found in the ring')
        if profileCollection.count == 0:
            _dbg(f'{CMD_NAME}:   no profiles found for circular edge, skipping')
            return

        extrudes = comp.features.extrudeFeatures
        cutInput = extrudes.createInput(profileCollection, adsk.fusion.FeatureOperations.CutFeatureOperation)
        cutExtent = adsk.fusion.ThroughAllExtentDefinition.create()
        cutInput.setOneSideExtent(cutExtent, adsk.fusion.ExtentDirections.NegativeExtentDirection)
        cutInput.participantBodies = [body]
        try:
            extrudes.add(cutInput)
        except RuntimeError as err:
            _dbg(f'{CMD_NAME}:   ring cut FAILED, skipping. Error: {err}')
            return
        else:
            _dbg(f'{CMD_NAME}:   ring cut OK, {profileCollection.count} holes')

        # For Fill mode, upgrade the quantity to a live formula tied to the
        # edge's radius, mirroring the linear-edge live-length link. On any
        # failure we keep the literal count silently — see `_build_edge_row_line`.
        if is_fill and not is_preview and skPattern is not None:
            try:
                radius_param = _radius_param_name(sketch, rim_circle)
                if radius_param is not None:
                    edge_offset_in = edge_offset_cm / IN_TO_CM
                    spacing_in = spacing_cm / IN_TO_CM
                    # Fusion's expression parser doesn't recognize `pi` (or `pi()`) as a
                    # constant — confirmed by testing directly against a live parameter,
                    # where only a numeric literal was accepted. The chosen spacing is
                    # likewise baked in as a literal rather than a live parameter -- same
                    # as edge_offset_in above.
                    formula = (f'max(1; floor((2 * 3.14159265358979 * ({radius_param} - {edge_offset_in} in)) '
                               f'/ {spacing_in} in))')
                    skPattern.quantity.expression = formula
            except Exception:
                futil.handle_error(f'{CMD_NAME} circular live-quantity link', show_message_box=False)

    except Exception:
        _dbg(f'{CMD_NAME}: EXCEPTION in _process_circular_edge:\n{traceback.format_exc()}')
        futil.handle_error(f'{CMD_NAME} _process_circular_edge', show_message_box=True)


# ===========================================================================
# Loop tracing — one selected edge, auto-follow the whole boundary it's on
# ===========================================================================

def _process_loop_from_edge(edge: adsk.fusion.BRepEdge,
                            hole_diam_cm: float,
                            edge_offset_cm: float,
                            is_fill: bool,
                            custom_count: int,
                            spacing_cm: float,
                            is_preview: bool = False,
                            anchor_pt_model: adsk.core.Point3D = None):
    """"Outer Edge (All The Way Around)" mode — walk every edge of the loop
    that `edge` belongs to (whatever mix of lines and arcs make it up), build
    an inset copy of the whole loop with Fusion's sketch Offset constraint
    (handles corners the same way the UI's Offset command does), then place
    holes evenly around that inset path by arc length and cut them all in one
    multi-profile extrude. If `anchor_pt_model` (the picked Start Point) is
    given, the sampling is phase-shifted so the first hole lands near it.

    Unlike the single-edge-row and single-circle cases, holes here aren't
    pinned with driving dimensions and Fill mode doesn't get a live-updating
    count — there's no single simple parameter (like one edge's length, or one
    circle's radius) to tie an arbitrary multi-segment loop's perimeter to."""
    _dbg(f'{CMD_NAME}: loop-trace seed edge.entityToken={edge.entityToken}')

    if isinstance(edge.geometry, adsk.core.Circle3D):
        # A lone full circle is already a complete, single-edge loop — reuse
        # the simpler, already-proven ring logic directly.
        _process_circular_edge(edge, hole_diam_cm, edge_offset_cm, is_fill, custom_count, spacing_cm,
                               is_preview=is_preview, anchor_pt_model=anchor_pt_model)
        return

    try:
        # A straight or arc edge on a thin plate typically borders two planar
        # faces — the flat top/bottom face we want to trace, and a thin side
        # wall that's incidentally planar too. Prefer the larger one; a real
        # part's cap face is normally far bigger than its edge-band side walls.
        planar_faces = [f for f in edge.faces if isinstance(f.geometry, adsk.core.Plane)]
        target_face = max(planar_faces, key=lambda f: f.area) if planar_faces else None
        if target_face is None:
            _dbg(f'{CMD_NAME}: loop-trace edge does not border a flat face, skipping')
            return

        loop = next((l for l in target_face.loops
                    if any(e2.entityToken == edge.entityToken for e2 in l.edges)), None)
        if loop is None:
            _dbg(f"{CMD_NAME}: could not find the selected edge's loop, skipping")
            return

        loop_edges = list(loop.edges)

        if any(not isinstance(e2.geometry, (adsk.core.Line3D, adsk.core.Arc3D)) for e2 in loop_edges):
            _dbg(f"{CMD_NAME}: loop contains a curve type this tool doesn't support "
                 f'(only straight and arc edges), skipping')
            return

        comp = target_face.body.parentComponent
        body = target_face.body
        sketch = comp.sketches.add(target_face)
        sketch.name = 'AutoHole'

        projected = []
        for e2 in loop_edges:
            col = sketch.project(e2)
            if col.count == 0:
                _dbg(f'{CMD_NAME}: failed to project a loop edge, skipping whole loop')
                return
            projected.append(col.item(0))

        loop_profile = next(iter(sketch.profiles), None)
        if loop_profile is None:
            _dbg(f'{CMD_NAME}: projected loop did not form a closed profile, skipping')
            return
        centroid = loop_profile.areaProperties().centroid

        base_pt = projected[0].startSketchPoint.geometry
        offset_val = edge_offset_cm
        offConstraint = sketch.geometricConstraints.addOffset(
            projected, adsk.core.ValueInput.createByReal(offset_val), base_pt
        )
        child_curves = list(offConstraint.childCurves)

        # Fusion picks the offset direction from the loop's own curve-flow
        # direction, which isn't something this tool controls — detect whether
        # it went inward (toward the loop's centroid, what we want for an
        # outer boundary) or outward, and flip it if not.
        sample_child_curve = child_curves[0]
        sample_child = (sample_child_curve.startSketchPoint.geometry
                        if hasattr(sample_child_curve, 'startSketchPoint')
                        else sample_child_curve.centerSketchPoint.geometry)
        d_parent = math.hypot(base_pt.x - centroid.x, base_pt.y - centroid.y)
        d_child = math.hypot(sample_child.x - centroid.x, sample_child.y - centroid.y)
        if d_child > d_parent:
            _dbg(f'{CMD_NAME}:   offset went outward, flipping direction')
            offConstraint.deleteMe()
            offConstraint = sketch.geometricConstraints.addOffset(
                projected, adsk.core.ValueInput.createByReal(-offset_val), base_pt
            )
            child_curves = list(offConstraint.childCurves)

        # The offset curves are only a computational aid for where hole centers
        # go — as ordinary (non-construction) geometry they'd pass straight
        # through each hole's center and split its circle into two crescent
        # profiles instead of one clean circular one, which is exactly why the
        # profile-matching below would otherwise come up empty for every hole.
        for c in child_curves:
            c.isConstruction = True

        segments = [c for c in child_curves if isinstance(c, (adsk.fusion.SketchLine, adsk.fusion.SketchArc))]
        seg_lengths = [_curve_length_cm(c) for c in segments]
        total_length_cm = sum(seg_lengths)
        if total_length_cm <= 0 or not segments:
            _dbg(f'{CMD_NAME}:   offset loop has no usable length, skipping')
            return

        total_length_in = total_length_cm / IN_TO_CM
        if is_fill:
            count = max(1, int(total_length_cm / spacing_cm + 1e-9))
        else:
            count = max(1, custom_count)

        _dbg(f'{CMD_NAME}:   loop has {len(loop_edges)} edges, total_length_in={total_length_in:.4f}, '
             f'is_fill={is_fill}, custom_count={custom_count} spacing_cm={spacing_cm:.4f} -> count={count}')

        start_dist_cm = 0.0
        if anchor_pt_model is not None:
            anchor_sketch = sketch.modelToSketchSpace(anchor_pt_model)
            start_dist_cm = _nearest_dist_along_loop(segments, seg_lengths, anchor_sketch)
            _dbg(f'{CMD_NAME}:   anchored loop start at dist_cm={start_dist_cm:.4f} '
                 f'(of {total_length_cm:.4f} total)')

        # The count above is rounded down to a whole number of holes, so the
        # even spacing actually used around the loop's full perimeter is
        # slightly tighter than the requested `spacing_cm` (never looser).
        even_spacing_cm = total_length_cm / count

        if is_fill:
            # Fill needs the cut itself (not just a sketch full of circles) to
            # reliably match `count` -- a plain multi-profile cut extrude is
            # frozen to the profiles it was given at creation, same problem
            # _build_edge_row_line_fill solves for a straight edge. See that
            # function's docstring and _process_loop_fill's below for why the
            # loop case can only get part of the same fix.
            _process_loop_fill(sketch, body, child_curves, segments, seg_lengths, hole_diam_cm,
                               start_dist_cm, even_spacing_cm, count,
                               is_preview=is_preview)
            return

        centers = []
        for k in range(count):
            target_dist = (start_dist_cm + k * even_spacing_cm) % total_length_cm
            acc = 0.0
            for idx, (seg, seg_len) in enumerate(zip(segments, seg_lengths)):
                if target_dist <= acc + seg_len or idx == len(segments) - 1:
                    centers.append(_point_at_distance_cm(seg, max(0.0, target_dist - acc)))
                    break
                acc += seg_len

        # Check suppression before drawing anything -- a suppressed hole never
        # gets a circle at all, rather than being drawn and then filtered out.
        # Fewer curves in the sketch means less for every later profile lookup
        # to sift through, which matters a lot on a large loop: suppressing one
        # more hole out of e.g. 100+ re-runs this whole function from scratch,
        # so every curve this skips is pure savings on every future click.
        cut_centers = [c for c in centers if not _register_and_check_suppressed(sketch, c)]
        if not cut_centers:
            _dbg(f'{CMD_NAME}:   every hole around this loop is suppressed, nothing to cut')
            return
        for center in cut_centers:
            sketch.sketchCurves.sketchCircles.addByCenterRadius(center, hole_diam_cm / 2.0)

        holeArea = hole_diam_cm ** 2 * math.pi / 4.0
        profiles = _match_profiles_to_centers(sketch, cut_centers, holeArea)
        profileCollection = adsk.core.ObjectCollection.create()
        missing = 0
        for p in profiles:
            if p is not None:
                profileCollection.add(p)
            else:
                missing += 1
        if missing:
            _dbg(f'{CMD_NAME}:   {missing} of {len(cut_centers)} hole profiles not found around the loop')
        if profileCollection.count == 0:
            _dbg(f'{CMD_NAME}:   no profiles found for loop, skipping')
            return

        extrudes = comp.features.extrudeFeatures
        cutInput = extrudes.createInput(profileCollection, adsk.fusion.FeatureOperations.CutFeatureOperation)
        cutExtent = adsk.fusion.ThroughAllExtentDefinition.create()
        cutInput.setOneSideExtent(cutExtent, adsk.fusion.ExtentDirections.NegativeExtentDirection)
        cutInput.participantBodies = [body]
        try:
            extrudes.add(cutInput)
        except RuntimeError as err:
            _dbg(f'{CMD_NAME}:   loop cut FAILED, skipping. Error: {err}')
            return
        else:
            _dbg(f'{CMD_NAME}:   loop cut OK, {profileCollection.count} holes')

    except Exception:
        _dbg(f'{CMD_NAME}: EXCEPTION in _process_loop_from_edge:\n{traceback.format_exc()}')
        futil.handle_error(f'{CMD_NAME} _process_loop_from_edge', show_message_box=True)


def _process_loop_fill(sketch: adsk.fusion.Sketch,
                       body: adsk.fusion.BRepBody,
                       child_curves: list,
                       segments: list,
                       seg_lengths: list,
                       hole_diam_cm: float,
                       start_dist_cm: float,
                       even_spacing_cm: float,
                       count: int,
                       is_preview: bool = False):
    """Fill mode's outer-edge loop trace. Cuts only the *seed* hole (at
    `start_dist_cm` along the offset loop) as its own extrude feature, then
    replicates that cut with a feature-level `PathPatternFeature` walking the
    offset loop path -- the same "seed cut + feature pattern" fix
    `_build_edge_row_line_fill` uses for a straight edge, and needed for the
    same reason: a plain multi-profile cut extrude is frozen to the profiles
    it was given at creation, so a sketch full of circles that later grows
    (or a loop that changes shape) never gets new material actually removed.

    This only gets *part* of the straight-edge fix, though: there's no live
    "total perimeter" parameter to tie the pattern's quantity to, the way
    `_length_param_name`/`_radius_param_name` expose one line's length or one
    circle's radius -- Fusion has no dimension type for an arbitrary
    multi-segment loop's combined length. So `count` here is still a one-time
    Python computation from the loop's length at the moment this runs, same
    as before. What this function actually fixes is narrower: the cut now
    reliably contains every hole the sketch says it should, right now.
    Resizing the part afterward still requires re-running AutoHole to
    recompute `count` -- it won't grow/shrink on its own the way the
    straight-edge row's cut does.

    Also drops per-hole suppression, same trade-off and same reason as
    `_build_edge_row_line_fill`: `PatternElement.isSuppressed` reliably fails
    from script for a feature-level pattern (see LESSONS_LEARNED.md)."""
    acc = 0.0
    seed_center = None
    for idx, seg in enumerate(segments):
        seg_len = seg_lengths[idx]
        if start_dist_cm <= acc + seg_len or idx == len(segments) - 1:
            seed_center = _point_at_distance_cm(seg, max(0.0, start_dist_cm - acc))
            break
        acc += seg_len

    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(seed_center, hole_diam_cm / 2.0)

    holeArea = hole_diam_cm ** 2 * math.pi / 4.0
    seed_profile = _match_profiles_to_centers(sketch, [circle.centerSketchPoint.geometry], holeArea)[0]
    if seed_profile is None:
        _dbg(f'{CMD_NAME}:   loop seed hole profile not found, skipping')
        return

    comp = body.parentComponent
    extrudes = comp.features.extrudeFeatures
    cutInput = extrudes.createInput(seed_profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    cutExtent = adsk.fusion.ThroughAllExtentDefinition.create()
    cutInput.setOneSideExtent(cutExtent, adsk.fusion.ExtentDirections.NegativeExtentDirection)
    cutInput.participantBodies = [body]
    try:
        seed_cut = extrudes.add(cutInput)
    except RuntimeError as err:
        _dbg(f'{CMD_NAME}:   loop seed cut FAILED, skipping. Error: {err}')
        return
    _dbg(f'{CMD_NAME}:   loop seed cut OK')

    if count <= 1:
        return

    curve_collection = adsk.core.ObjectCollection.create()
    for c in child_curves:
        curve_collection.add(c)
    path = adsk.fusion.Path.create(curve_collection, adsk.fusion.ChainedCurveOptions.noChainedCurves)

    # The seed hole is already physically positioned at start_dist_cm along the
    # path (above), which is all Fusion needs to phase-match the pattern to it
    # -- confirmed live that also setting patInput.startPoint to that same
    # position as a fraction breaks the pattern outright (every instance fails
    # to intersect the body) even though the seed's own placement alone works
    # correctly. See LESSONS_LEARNED.md.
    feat_col = adsk.core.ObjectCollection.create()
    feat_col.add(seed_cut)
    pat_feats = comp.features.pathPatternFeatures
    patInput = pat_feats.createInput(
        feat_col, path, futil.Value(count),
        adsk.core.ValueInput.createByString(f'{even_spacing_cm} cm'),
        adsk.fusion.PatternDistanceType.SpacingPatternDistanceType,
    )
    try:
        pat_feats.add(patInput)
    except RuntimeError as err:
        _dbg(f'{CMD_NAME}:   loop pattern FAILED, only the seed hole was cut. Error: {err}')
        return
    _dbg(f'{CMD_NAME}:   loop pattern OK, {count} holes')


def _run(inputs: adsk.core.CommandInputs, is_preview: bool = False):
    followEdgeInp:  adsk.core.SelectionCommandInput = inputs.itemById('follow_edge')
    startPointInp:  adsk.core.SelectionCommandInput = inputs.itemById('start_point')
    followModeInp:  adsk.core.DropDownCommandInput  = inputs.itemById('follow_mode')
    edgeOffsetInp:  adsk.core.ValueCommandInput     = inputs.itemById('edge_offset')
    countTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('hole_count_type')
    customCountInp                                  = inputs.itemById('custom_hole_count')
    holeSpacingInp: adsk.core.ValueCommandInput     = inputs.itemById('hole_spacing')

    # Rebuilt fresh every run so a "Suppress Holes" click always resolves
    # against this run's own candidate positions.
    global _last_candidates_world
    _last_candidates_world = []

    hole_diam_cm   = _resolve_hole_diam_cm(inputs)
    edge_offset_cm = edgeOffsetInp.value
    follow_outer   = (followModeInp.selectedItem.name == FOLLOW_OUTER)
    is_fill        = (countTypeInp.selectedItem.name == COUNT_FILL)
    custom_count   = int(customCountInp.value) if not is_fill else 0
    # Fill derives the count from spacing; Custom sets the count directly but
    # still uses spacing_cm for the distance between holes -- every mode's
    # hole-placement math steps by spacing_cm regardless of is_fill.
    spacing_cm     = holeSpacingInp.value

    if followEdgeInp.selectionCount == 0:
        return
    edge = followEdgeInp.selection(0).entity
    anchor_pt_model = startPointInp.selection(0).entity.geometry if startPointInp.selectionCount > 0 else None

    _dbg_reset(is_preview)
    _dbg(f'{CMD_NAME}: edge.entityToken={edge.entityToken}, has_anchor={anchor_pt_model is not None}, '
         f'follow_outer={follow_outer}, hole_diam_cm={hole_diam_cm:.4f}, edge_offset_cm={edge_offset_cm:.4f}, '
         f'is_fill={is_fill}, custom_count={custom_count}, spacing_cm={spacing_cm:.4f}')

    design       = adsk.fusion.Design.cast(app.activeProduct)
    start_marker = design.timeline.markerPosition

    if isinstance(edge.geometry, adsk.core.Circle3D):
        _dbg(f'{CMD_NAME}: --- circular edge ---')
        _process_circular_edge(edge, hole_diam_cm, edge_offset_cm, is_fill, custom_count, spacing_cm,
                               is_preview=is_preview, anchor_pt_model=anchor_pt_model)
    elif follow_outer:
        _dbg(f'{CMD_NAME}: --- outer edge loop trace ---')
        _process_loop_from_edge(edge, hole_diam_cm, edge_offset_cm, is_fill, custom_count, spacing_cm,
                                is_preview=is_preview, anchor_pt_model=anchor_pt_model)
    elif anchor_pt_model is None:
        _dbg(f'{CMD_NAME}: no Start Point selected for a single-edge row, skipping')
    else:
        _dbg(f'{CMD_NAME}: --- single edge row ---')
        _process_single_edge_row(edge, anchor_pt_model, hole_diam_cm, edge_offset_cm,
                                 is_fill, custom_count, spacing_cm, is_preview=is_preview)

    # Timeline grouping triggers a recompute and only matters for the committed
    # result — skip it during preview.
    if not is_preview:
        futil.group_timeline_features(design, start_marker, CMD_NAME)

    _dbg(f'{CMD_NAME}: run complete')


# Called when the user clicks OK.
def command_execute(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Execute Event')
    try:
        _run(args.command.commandInputs, is_preview=False)
    except Exception:
        futil.handle_error(f'{CMD_NAME} command_execute', show_message_box=True)


# Called whenever Fusion needs a fresh preview.
def command_preview(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Preview Event')
    try:
        _run(args.command.commandInputs, is_preview=True)
        # Leave isValidResult False so Fusion discards this lightweight preview
        # and re-runs command_execute for the full, dimensioned/parametric result
        # on OK — rather than reusing the stripped-down preview geometry.
        args.isValidResult = False
    except Exception:
        futil.handle_error(f'{CMD_NAME} command_preview', show_message_box=False)


# Called when the command is closed/cancelled.
def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Destroy Event')

    global local_handlers
    local_handlers = []
