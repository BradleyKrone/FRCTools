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
CMD_Description = 'Cut the Argos standard rivenut hole pattern through selected edges'

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

# ---------------------------------------------------------------------------
# Units / constants
# ---------------------------------------------------------------------------
IN_TO_CM = 2.54

HOLE_OFFSET_CM = 0.5 * IN_TO_CM   # fixed longitudinal offset from the corner

HOLE_RIVENUT = 'Rivenut (19/64")'
HOLE_10_32   = '10-32 (13/64")'
HOLE_CUSTOM  = 'Custom'

HOLE_SIZE_MAP = {
    HOLE_RIVENUT: (19.0 / 64.0) * IN_TO_CM,
    HOLE_10_32:   (13.0 / 64.0) * IN_TO_CM,
}

ROW_BOTH    = 'Both Edges'
ROW_PRIMARY = 'Primary Edge Only'

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

    edgeSelection = inputs.addSelectionInput(
        'edge_selection', 'Edges',
        'Select edge pairs that meet at a corner — one row is cut per pair.'
    )
    edgeSelection.addSelectionFilter('LinearEdges')
    edgeSelection.setSelectionLimits(2, 0)

    rowPlacementInp = inputs.addDropDownCommandInput(
        'row_placement', 'Row Placement', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    rowPlacementInp.listItems.add(ROW_BOTH, True, '')
    rowPlacementInp.listItems.add(ROW_PRIMARY, False, '')

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

    customCountInp = inputs.addIntegerSpinnerCommandInput(
        'custom_hole_count', 'Number of Holes', 1, 500, 1, 10
    )
    customCountInp.isVisible = False

    # Wire up event handlers
    futil.add_handler(args.command.execute,        command_execute,        local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview,        local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged,   command_input_changed,  local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy,        command_destroy,        local_handlers=local_handlers)


# ---------------------------------------------------------------------------
# Reactive UI
# ---------------------------------------------------------------------------

def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed = args.input
    inputs = args.inputs

    if changed.id == 'hole_size':
        holeSizeInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
        holeDiamInp = inputs.itemById('hole_diameter')
        holeDiamInp.isVisible = (holeSizeInp.selectedItem.name == HOLE_CUSTOM)
    elif changed.id == 'hole_count_type':
        countTypeInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_count_type')
        customCountInp = inputs.itemById('custom_hole_count')
        customCountInp.isVisible = (countTypeInp.selectedItem.name == COUNT_CUSTOM)


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs

    edgeSel:        adsk.core.SelectionCommandInput = inputs.itemById('edge_selection')
    edgeOffsetInp:  adsk.core.ValueCommandInput     = inputs.itemById('edge_offset')
    holeSizeInp:    adsk.core.DropDownCommandInput  = inputs.itemById('hole_size')
    holeDiamInp:    adsk.core.ValueCommandInput     = inputs.itemById('hole_diameter')
    countTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('hole_count_type')
    customCountInp                                  = inputs.itemById('custom_hole_count')

    count = edgeSel.selectionCount
    edges_ok  = count >= 2 and count % 2 == 0
    offset_ok = edgeOffsetInp.value > 0

    if holeSizeInp.selectedItem.name == HOLE_CUSTOM:
        diam_ok = holeDiamInp.value > 0
    else:
        diam_ok = True

    if countTypeInp.selectedItem.name == COUNT_CUSTOM:
        count_ok = customCountInp.value >= 1
    else:
        count_ok = True

    args.areInputsValid = edges_ok and offset_ok and diam_ok and count_ok


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


# ===========================================================================
# Hole row — seed hole + feature rectangular pattern
# ===========================================================================

def _length_param_name(sketch: adsk.fusion.Sketch, line: adsk.fusion.SketchLine):
    """Add a *reference* dimension across `line`'s own endpoints so its length is
    exposed as a named parameter other expressions can reference. `line` is a
    projected (associative) copy of the selected edge, so it's already fully
    defined — passing isDriving=False creates this as a driven/reference
    dimension (just reporting the live length) instead of over-constraining the
    sketch. Returns the bare parameter name (e.g. "d15"): all model parameters,
    including sketch dimensions, share one global namespace in Fusion and are
    referenced by bare name — a sketch-qualified form like "AutoHole.d15" is an
    invalid expression. Returns None (falls back to a fixed count) if this fails."""
    try:
        text_pt = futil.offsetPoint3D(line.startSketchPoint.geometry, 0.2, 0.2, 0)
        dim = sketch.sketchDimensions.addDistanceDimension(
            line.startSketchPoint, line.endSketchPoint,
            adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
            text_pt,
            False,  # isDriving — reference dimension, doesn't over-constrain the projected edge
        )
        return dim.parameter.name
    except Exception:
        futil.handle_error(f'{CMD_NAME} _length_param_name', show_message_box=True)
        return None


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


def _build_hole_row(sketch: adsk.fusion.Sketch,
                    body: adsk.fusion.BRepBody,
                    corner_pt: adsk.core.Point3D,
                    dir_vec: adsk.core.Vector2D,
                    ref_dir_vec: adsk.core.Vector2D,
                    own_line: adsk.fusion.SketchLine,
                    other_line: adsk.fusion.SketchLine,
                    length_in: float,
                    hole_diam_cm: float,
                    edge_offset_cm: float,
                    is_fill: bool,
                    custom_count: int,
                    skip_first: bool = False,
                    is_preview: bool = False):
    """Cut one row of holes starting near `corner_pt`, running along `dir_vec`,
    offset `HOLE_OFFSET_CM` from the corner (longitudinal) and `edge_offset_cm`
    from the *other* edge of the pair (perpendicular, along `ref_dir_vec`).

    The seed hole is placed approximately via `dir_vec`/`ref_dir_vec` first (to
    land it on the correct side of both edges), then pinned exactly in place with
    driving offset dimensions from `own_line` and `other_line` — the actual
    projected edges — so the hole stays correctly positioned if the part's
    geometry changes later, instead of floating at a fixed sketch coordinate.

    `skip_first` is set when this row's natural corner hole would coincide with
    the *other* row's corner hole in a "Both Edges" pair (they share that one
    corner hole) — the row then starts one pitch (1 in) further along and its
    count is reduced by one, since the corner hole already exists.

    `is_preview` builds a lightweight version: the seed hole is placed by
    coordinate only, and all sketch dimensions plus the live-length parameter
    link are skipped. Dimension solving and parameter-expression recomputes are
    the expensive, flicker-inducing steps, and they aren't needed for a preview
    that Fusion rolls back on the next edit — they're only done on execute."""
    start_offset_in = 0.5 + (1.0 if skip_first else 0.0)
    start_offset_cm = start_offset_in * IN_TO_CM

    if is_fill:
        usable_length_in = length_in - (1.0 if skip_first else 0.0)
        count = int(usable_length_in + 1e-9)
        if not skip_first:
            count = max(1, count)
        else:
            count = max(0, count)
    else:
        count = max(0, custom_count - (1 if skip_first else 0))

    _dbg(f'{CMD_NAME}:   _build_hole_row length_in={length_in:.4f} skip_first={skip_first} '
         f'is_fill={is_fill} custom_count={custom_count} -> count={count}, '
         f'start_offset_cm={start_offset_cm:.4f}')

    if count <= 0:
        _dbg(f'{CMD_NAME}:   no additional holes needed for this row (shared corner hole), skipping')
        return
    if length_in * IN_TO_CM <= start_offset_cm:
        _dbg(f'{CMD_NAME}:   edge too short for the required start offset, skipping row')
        return

    comp = body.parentComponent

    center = _seed_center(corner_pt, dir_vec, ref_dir_vec, start_offset_cm, edge_offset_cm)
    _dbg(f'{CMD_NAME}:   seed center={futil.format_Point3D(center)}, hole_diam_cm={hole_diam_cm:.4f}')

    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(center, hole_diam_cm / 2.0)

    # Dimensioning (below) is the expensive part — skip it entirely for preview,
    # where the hole only needs to be shown, not permanently constrained.
    if not is_preview:
        textPt = futil.offsetPoint3D(circle.centerSketchPoint.geometry, 0.1, 0.1, 0)
        diamDim = sketch.sketchDimensions.addDiameterDimension(circle, textPt)
        diamDim.value = hole_diam_cm

        # Pin the hole's position with real driving dimensions off the actual edges,
        # rather than leaving it floating at a fixed sketch coordinate.
        perpDim = sketch.sketchDimensions.addOffsetDimension(own_line, circle.centerSketchPoint, corner_pt)
        perpDim.value = edge_offset_cm

        longDim = sketch.sketchDimensions.addOffsetDimension(other_line, circle.centerSketchPoint, corner_pt)
        longDim.value = start_offset_cm

    # Construction line along the pattern direction, used by the rectangular pattern.
    line_len_cm = length_in * IN_TO_CM + 5.0
    dir_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
        corner_pt,
        adsk.core.Point3D.create(
            corner_pt.x + dir_vec.x * line_len_cm,
            corner_pt.y + dir_vec.y * line_len_cm,
            corner_pt.z,
        ),
    )
    dir_line.isConstruction = True

    # Find the profile for the circle we just created (by area, disambiguated by
    # centroid proximity in case another hole already shares this sketch).
    holeArea = hole_diam_cm ** 2 * math.pi / 4.0
    seed_profile = None
    best_dist_sq = None
    for profile in sketch.profiles:
        props = profile.areaProperties()
        if abs(holeArea - props.area) > holeArea * 0.05:
            continue
        c = props.centroid
        dist_sq = (c.x - center.x) ** 2 + (c.y - center.y) ** 2
        if seed_profile is None or dist_sq < best_dist_sq:
            seed_profile = profile
            best_dist_sq = dist_sq

    _dbg(f'{CMD_NAME}:   sketch has {sketch.profiles.count} profiles total, holeArea={holeArea:.6f}')

    if seed_profile is None:
        _dbg(f'{CMD_NAME}:   seed profile not found for row (no profile matched area/centroid), skipping')
        return

    _dbg(f'{CMD_NAME}:   seed profile found, dist_sq from target center={best_dist_sq:.6f}')

    extrudes = comp.features.extrudeFeatures
    cutInput = extrudes.createInput(seed_profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    cutExtent = adsk.fusion.ThroughAllExtentDefinition.create()
    cutInput.setOneSideExtent(cutExtent, adsk.fusion.ExtentDirections.NegativeExtentDirection)
    cutInput.participantBodies = [body]
    try:
        seed_cut = extrudes.add(cutInput)
    except RuntimeError as err:
        # The seed hole's profile can fall outside the body's remaining material
        # when it lands on/overlaps a hole already cut by another row (e.g. two
        # rows converging from different, non-shared edges). That's an expected
        # geometric collision, not a bug — skip this row quietly instead of
        # surfacing a crash-looking error dialog. Logged in full either way so the
        # cause is visible in debug_log.txt if it's actually something else.
        _dbg(f'{CMD_NAME}:   seed extrude FAILED, skipping row. Error: {err}')
        return
    else:
        _dbg(f'{CMD_NAME}:   seed extrude OK')

    # Create the pattern with a plain integer count first — a literal quantity
    # always succeeds, so the holes are placed regardless of whether the live
    # link below can be established.
    feat_col = adsk.core.ObjectCollection.create()
    feat_col.add(seed_cut)

    pat_feats = comp.features.rectangularPatternFeatures
    pat_input = pat_feats.createInput(
        feat_col,
        dir_line,
        futil.Value(count),
        adsk.core.ValueInput.createByString('1 in'),
        adsk.fusion.PatternDistanceType.SpacingPatternDistanceType,
    )
    pattern = pat_feats.add(pat_input)

    # For Fill mode, upgrade the quantity to a live formula tied to the edge's
    # length so the hole count auto-updates when the edge changes. This is done
    # by editing the resulting quantity parameter's expression (the normal
    # parameter-editing path, which fully supports formulas) rather than passing
    # the formula at creation time. Note: Fusion's expression parser separates
    # function arguments with ';', not ','. On any failure we keep the literal
    # count silently — the holes are still correct, just not auto-updating.
    if is_fill and not is_preview:
        try:
            length_param = _length_param_name(sketch, own_line)
            if length_param is not None:
                if skip_first:
                    formula = f'max(0; floor(({length_param} - 1 in) / 1 in))'
                else:
                    formula = f'max(1; floor({length_param} / 1 in))'
                pattern.quantityOne.expression = formula
        except Exception:
            futil.handle_error(f'{CMD_NAME} live-quantity link', show_message_box=False)


def _perp_vec(dir_vec: adsk.core.Vector2D, hint_vec: adsk.core.Vector2D) -> adsk.core.Vector2D:
    """Unit vector perpendicular to `dir_vec` in the sketch plane, oriented toward
    whichever side `hint_vec` points to. Using a true perpendicular (rather than
    the other edge's raw direction) keeps the offset a real perpendicular distance
    even when the two selected edges don't meet at a right angle."""
    perp = adsk.core.Vector2D.create(-dir_vec.y, dir_vec.x)
    if perp.x * hint_vec.x + perp.y * hint_vec.y < 0:
        perp = futil.multVector2D(perp, -1.0)
    return perp


def _process_edge_pair(edgeA: adsk.fusion.BRepEdge,
                       edgeB: adsk.fusion.BRepEdge,
                       hole_diam_cm: float,
                       edge_offset_cm: float,
                       both_edges: bool,
                       is_fill: bool,
                       custom_count: int,
                       processed_edges: set,
                       is_preview: bool = False):
    _dbg(f'{CMD_NAME}: edgeA.entityToken={edgeA.entityToken}, edgeB.entityToken={edgeB.entityToken}')
    try:
        if not isinstance(edgeA.geometry, adsk.core.Line3D) or not isinstance(edgeB.geometry, adsk.core.Line3D):
            _dbg(f'{CMD_NAME}: edge pair is not linear, skipping')
            return

        facesB = list(edgeB.faces)
        common = [f for f in edgeA.faces if any(f.entityToken == g.entityToken for g in facesB)]
        if not common:
            _dbg(f'{CMD_NAME}: edge pair does not share a common face, skipping')
            return
        target_face = common[0] if len(common) == 1 else max(common, key=lambda f: f.area)

        comp = target_face.body.parentComponent
        body = target_face.body

        sketch = comp.sketches.add(target_face)
        sketch.name = 'AutoHole'

        lineA_col = sketch.project(edgeA)
        lineB_col = sketch.project(edgeB)
        if lineA_col.count == 0 or lineB_col.count == 0:
            _dbg(f'{CMD_NAME}: failed to project edge(s) into sketch, skipping pair')
            return
        lineA: adsk.fusion.SketchLine = lineA_col.item(0)
        lineB: adsk.fusion.SketchLine = lineB_col.item(0)

        dirA = futil.sketchLineUnitVec(lineA)
        dirB = futil.sketchLineUnitVec(lineB)
        corner = None

        if lineA.startSketchPoint.geometry.isEqualTo(lineB.startSketchPoint.geometry):
            corner = lineA.startSketchPoint
        elif lineA.startSketchPoint.geometry.isEqualTo(lineB.endSketchPoint.geometry):
            corner = lineA.startSketchPoint
            dirB = futil.multVector2D(dirB, -1.0)
        elif lineA.endSketchPoint.geometry.isEqualTo(lineB.startSketchPoint.geometry):
            corner = lineA.endSketchPoint
            dirA = futil.multVector2D(dirA, -1.0)
        elif lineA.endSketchPoint.geometry.isEqualTo(lineB.endSketchPoint.geometry):
            corner = lineA.endSketchPoint
            dirA = futil.multVector2D(dirA, -1.0)
            dirB = futil.multVector2D(dirB, -1.0)

        if corner is None:
            _dbg(f'{CMD_NAME}: selected edges do not share a vertex, skipping pair')
            return

        lengthA_in = lineA.length / IN_TO_CM
        lengthB_in = lineB.length / IN_TO_CM
        corner_pt = corner.geometry
        _dbg(f'{CMD_NAME}: corner={futil.format_Point3D(corner_pt)}, lengthA_in={lengthA_in:.4f}, '
             f'lengthB_in={lengthB_in:.4f}, both_edges={both_edges}')

        # True perpendiculars to each edge, oriented toward the other edge's side —
        # correct regardless of the angle at which the two edges meet.
        perpA = _perp_vec(dirA, dirB)
        perpB = _perp_vec(dirB, dirA)

        # If both rows are being cut, their corner-most holes can coincide (e.g. the
        # common case where the longitudinal offset equals the perpendicular Edge
        # Offset at a square corner) — detect that and have row B skip its own
        # corner hole rather than re-cutting a spot row A already removed.
        both_corners_collide = False
        if both_edges:
            centerA = _seed_center(corner_pt, dirA, perpA, HOLE_OFFSET_CM, edge_offset_cm)
            centerB = _seed_center(corner_pt, dirB, perpB, HOLE_OFFSET_CM, edge_offset_cm)
            dist = math.hypot(centerA.x - centerB.x, centerA.y - centerB.y)
            both_corners_collide = dist < hole_diam_cm
            _dbg(f'{CMD_NAME}: corner-hole dist={dist:.4f} vs hole_diam_cm={hole_diam_cm:.4f} '
                 f'-> both_corners_collide={both_corners_collide}')

        # An edge can belong to two different selected corner pairs (e.g. all 4
        # corners of a rectangular plate selected for a full perimeter — each edge
        # is shared by its two adjoining corners). Cutting a row for it twice makes
        # the second row's holes collide with the first row's already-cut holes,
        # which fails the seed extrude ("profile falls outside the boundary of the
        # body"). Track edges already given a row (by entityToken) across the whole
        # run and skip re-processing one.
        if edgeA.entityToken in processed_edges:
            _dbg(f'{CMD_NAME}: primary edge already has a hole row from another pair, skipping')
        else:
            processed_edges.add(edgeA.entityToken)
            try:
                _build_hole_row(sketch, body, corner_pt, dirA, perpA, lineA, lineB, lengthA_in,
                                hole_diam_cm, edge_offset_cm, is_fill, custom_count,
                                is_preview=is_preview)
            except Exception:
                _dbg(f'{CMD_NAME}: EXCEPTION building primary row:\n{traceback.format_exc()}')
                futil.handle_error(f'{CMD_NAME} _process_edge_pair (primary row)', show_message_box=True)

        if both_edges:
            if edgeB.entityToken in processed_edges:
                _dbg(f'{CMD_NAME}: secondary edge already has a hole row from another pair, skipping')
            else:
                processed_edges.add(edgeB.entityToken)
                try:
                    _build_hole_row(sketch, body, corner_pt, dirB, perpB, lineB, lineA, lengthB_in,
                                    hole_diam_cm, edge_offset_cm, is_fill, custom_count,
                                    skip_first=both_corners_collide, is_preview=is_preview)
                except Exception:
                    _dbg(f'{CMD_NAME}: EXCEPTION building second row:\n{traceback.format_exc()}')
                    futil.handle_error(f'{CMD_NAME} _process_edge_pair (second row)', show_message_box=True)

    except Exception:
        _dbg(f'{CMD_NAME}: EXCEPTION in _process_edge_pair:\n{traceback.format_exc()}')
        futil.handle_error(f'{CMD_NAME} _process_edge_pair', show_message_box=True)


def _run(inputs: adsk.core.CommandInputs, is_preview: bool = False):
    edgeSel:        adsk.core.SelectionCommandInput = inputs.itemById('edge_selection')
    rowPlacementInp: adsk.core.DropDownCommandInput = inputs.itemById('row_placement')
    edgeOffsetInp:  adsk.core.ValueCommandInput     = inputs.itemById('edge_offset')
    countTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('hole_count_type')
    customCountInp                                  = inputs.itemById('custom_hole_count')

    hole_diam_cm   = _resolve_hole_diam_cm(inputs)
    edge_offset_cm = edgeOffsetInp.value
    both_edges     = (rowPlacementInp.selectedItem.name == ROW_BOTH)
    is_fill        = (countTypeInp.selectedItem.name == COUNT_FILL)
    custom_count   = int(customCountInp.value) if not is_fill else 0

    entities = [edgeSel.selection(i).entity for i in range(edgeSel.selectionCount)]

    _dbg_reset(is_preview)
    _dbg(f'{CMD_NAME}: {len(entities)} edges selected ({len(entities) // 2} pairs), '
         f'hole_diam_cm={hole_diam_cm:.4f}, edge_offset_cm={edge_offset_cm:.4f}, '
         f'both_edges={both_edges}, is_fill={is_fill}, custom_count={custom_count}')

    design       = adsk.fusion.Design.cast(app.activeProduct)
    start_marker = design.timeline.markerPosition

    # Shared across all pairs in this run so an edge already given a row (e.g. by
    # an adjoining corner's pair) isn't processed again — see _process_edge_pair.
    processed_edges = set()

    for i in range(0, len(entities) - 1, 2):
        _dbg(f'{CMD_NAME}: --- pair {i // 2} ---')
        _process_edge_pair(entities[i], entities[i + 1], hole_diam_cm, edge_offset_cm,
                           both_edges, is_fill, custom_count, processed_edges,
                           is_preview=is_preview)

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
