"""
chain_gen.py  —  Roller Chain geometry for PartsGen

Supports ANSI #25 (6.35 mm pitch) and #35 (9.525 mm pitch) roller chain.
The chain type is determined automatically from the CCDistance sketch that
the user selects — motion==3 → #25, motion==4 → #35.

Architecture mirrors belt_gen.py exactly: CCLine auto-detection, pitch-loop
construction, simple offset-body extrusion, optional sprocket auto-generation
(each also jointed to the user's own C-C circle, sprocket 1 carrying a Z offset),
and a commandTerminated name-sync hook so the chain rebuilds and the sprockets
are resized in place when the CC distance is edited.
"""

import adsk.core
import adsk.fusion
import math
from ...lib import fusionAddInUtils as futil
from ..CCDistance.CCLine import getCCLineFromEntity
from .belt_gen import (createPitchLoopFromSketchCircles, should_run_part_sync,
                       _cc_joint_offset_cm, _add_cc_joint)
from .pulley_gen import BORE_OFFSET_DEFAULT_IN
from .sprocket_gen import (
    create_sprocket_for_chain,
    update_sprocket_teeth,
    ATTR_SPROCKET_CHAIN_COMP_TOKEN,
    ATTR_SPROCKET_PITCH_CIRCLE_IDX,
    ATTR_SPROCKET_CHAIN_PITCH,
    ATTR_SPROCKET_TOOTH_COUNT,
    ATTR_SPROCKET_WIDTH,
    ATTR_SPROCKET_BORE_OFFSET,
)

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Motion type constants (matches CCDistance entry.py motionTypes indices)
# ---------------------------------------------------------------------------
MOTION_CHAIN_25 = 3
MOTION_CHAIN_35 = 4

# ---------------------------------------------------------------------------
# ANSI chain constants
# ---------------------------------------------------------------------------
# #25 chain (0.25 in pitch) — ANSI B29.1 / WCP-0764
CHAIN_25_PITCH_MM            = 6.35
CHAIN_25_LINK_HEIGHT_CM      = 0.232 * 2.54   # C: link plate height   = 0.232 in ≈ 5.89 mm
CHAIN_25_WIDTH_CM            = 0.307 * 2.54   # E: outer chain width   = 0.307 in ≈ 7.80 mm
CHAIN_25_SPROCKET_FACE_CM    = 0.375 * 2.54   # WCP Double Hub sprocket hub-to-hub width = 0.375 in ≈ 9.53 mm

# #35 chain (3/8 in pitch) — ANSI B29.1 / WCP-0770
CHAIN_35_PITCH_MM            = 9.525
CHAIN_35_LINK_HEIGHT_CM      = 0.352 * 2.54   # C: link plate height   = 0.352 in ≈ 8.94 mm
CHAIN_35_WIDTH_CM            = 0.463 * 2.54   # E: outer chain width   = 0.463 in ≈ 11.76 mm
CHAIN_35_SPROCKET_FACE_CM    = 0.53125 * 2.54 # WCP Double Hub sprocket hub-to-hub width = 17/32 in ≈ 13.49 mm

# ---------------------------------------------------------------------------
# Attribute keys (written to the component so the edit command can restore)
# ---------------------------------------------------------------------------
ATTR_GROUP               = 'FRCTools_PartsGen'
ATTR_PART_TYPE           = 'part_type'
ATTR_CHAIN_TYPE          = 'chain_type'           # '25' or '35'
ATTR_CHAIN_SPROCKET_WIDTH = 'chain_sprocket_width_expr'
ATTR_CHAIN_GEN_SPROCKETS  = 'chain_gen_sprockets'
ATTR_CHAIN_LOOP_LENGTH    = 'chain_loop_length'
ATTR_CUSTOM_NAME          = 'custom_name'
ATTR_CHAIN_BORE_OFFSET    = 'chain_bore_offset'         # sprockets' hex bore offset, as typed
ATTR_CHAIN_OFFSET_EXPR    = 'chain_offset_expr'         # Sprocket 1's Z Offset, as typed
ATTR_CHAIN_OFFSET_FACE    = 'chain_offset_face_token'   # entityToken of its "Offset From" face
ATTR_CHAIN_OFFSET_SIDE    = 'chain_offset_side'         # which sprocket face it's measured to
ATTR_CHAIN_CC_CIRCLE      = 'chain_cc_circle_token'     # the user's C-C circles, suffixed _1 / _2
ATTR_CHAIN_JOINT_OFFSET   = 'chain_joint_offset_cm'     # the offset Sprocket 1's C-C joint got

# "Measured To" choices for Sprocket 1's Z Offset
OFFSET_SIDE_BOTTOM = 'Bottom Face'
OFFSET_SIDE_TOP    = 'Top Face'

# ---------------------------------------------------------------------------
# Chain-name-sync (commandTerminated hook)
# ---------------------------------------------------------------------------
_chain_sync_registered  = False
_chain_sync_handlers: list = []


# ---------------------------------------------------------------------------
# Chain parameter lookup
# ---------------------------------------------------------------------------

def _chain_params(motion: int):
    """Return (pitch_mm, link_height_cm, sketch_name, comp_prefix, chain_label) for a motion type."""
    if motion == MOTION_CHAIN_35:
        return (CHAIN_35_PITCH_MM, CHAIN_35_LINK_HEIGHT_CM, 'Chain35', 'Chain_35', '#35')
    return (CHAIN_25_PITCH_MM, CHAIN_25_LINK_HEIGHT_CM, 'Chain25', 'Chain_25', '#25')


def _pitch_cm(motion: int) -> float:
    pitch_mm, *_ = _chain_params(motion)
    return pitch_mm / 10.0


def _n_teeth_from_radius(radius_cm: float, pitch_mm: float) -> int:
    """Compute sprocket tooth count from pitch-circle radius using the chain formula.

    Chain: PD = pitch / sin(π/N)  →  N = π / arcsin(pitch / PD)
    """
    try:
        ratio = (pitch_mm / 10.0) / (2.0 * radius_cm)
        ratio = max(-1.0, min(1.0, ratio))
        return round(math.pi / math.asin(ratio))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Name-sync helpers
# ---------------------------------------------------------------------------

def register_chain_name_sync():
    global _chain_sync_registered
    if _chain_sync_registered:
        return
    try:
        ui = app.userInterface
        futil.add_handler(ui.commandTerminated, _on_command_terminated,
                          local_handlers=_chain_sync_handlers)
        _chain_sync_registered = True
    except Exception:
        futil.handle_error('PartsGen register_chain_name_sync', show_message_box=False)


def unregister_chain_name_sync():
    global _chain_sync_registered
    _chain_sync_handlers.clear()
    _chain_sync_registered = False


_chain_sync_running = False


def _on_command_terminated(args: adsk.core.ApplicationCommandEventArgs):
    global _chain_sync_running
    if _chain_sync_running:
        return  # a command fired by the sync's own edits
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not should_run_part_sync(args, design):
            return
        _chain_sync_running = True
        _scan_and_update_chain_names(design)
    except Exception:
        pass
    finally:
        _chain_sync_running = False


def _find_comp_by_token(design: adsk.fusion.Design, token: str):
    try:
        root = design.rootComponent
        visited: set = set()
        queue = [root]
        while queue:
            comp = queue.pop()
            et = comp.entityToken
            if et in visited:
                continue
            visited.add(et)
            if et == token:
                return comp
            for i in range(comp.occurrences.count):
                queue.append(comp.occurrences.item(i).component)
    except Exception:
        pass
    return None


def _find_occurrence_of(design: adsk.fusion.Design, comp_token: str):
    root = design.rootComponent
    for i in range(root.allOccurrences.count):
        occ = root.allOccurrences.item(i)
        if occ.component.entityToken == comp_token:
            return occ
    return None


def _scan_and_update_chain_names(design: adsk.fusion.Design):
    """Walk every component, then fix chains and their sprockets. Collect first, update
    after: a sprocket that has to be rebuilt deletes its occurrence, which would end a
    walk in progress (LESSONS_LEARNED.md)."""
    chains, sprockets = [], []
    try:
        root    = design.rootComponent
        visited: set = set()
        queue   = [root]
        while queue:
            comp = queue.pop()
            token = comp.entityToken
            if token in visited:
                continue
            visited.add(token)

            if comp.attributes.itemByName(ATTR_GROUP, ATTR_CHAIN_LOOP_LENGTH) is not None:
                chains.append(comp)
            if comp.attributes.itemByName(ATTR_GROUP, ATTR_SPROCKET_CHAIN_COMP_TOKEN) is not None:
                sprockets.append(comp)

            for i in range(comp.occurrences.count):
                queue.append(comp.occurrences.item(i).component)
    except Exception:
        pass
    for comp in chains:
        _update_chain_name(comp)
    for comp in sprockets:
        if comp.isValid:
            _update_chain_sprocket_name(comp, design)


# ---------------------------------------------------------------------------
# Pitch-circle helpers
# ---------------------------------------------------------------------------

def _get_pitch_circle(chain_comp: adsk.fusion.Component, circle_idx: int):
    """Return the circle_idx-th construction circle from the chain sketch."""
    sketch_name = chain_comp.attributes.itemByName(ATTR_GROUP, ATTR_CHAIN_TYPE)
    if sketch_name is None:
        return None
    sname = 'Chain35' if sketch_name.value == '35' else 'Chain25'
    sk = chain_comp.sketches.itemByName(sname)
    if sk is None:
        return None
    circles = [sk.sketchCurves.sketchCircles.item(i)
               for i in range(sk.sketchCurves.sketchCircles.count)
               if sk.sketchCurves.sketchCircles.item(i).isConstruction]
    if circle_idx >= len(circles):
        return None
    return circles[circle_idx]


# ---------------------------------------------------------------------------
# Rebuild helpers
# ---------------------------------------------------------------------------

def _rebuild_chain_3d(comp: adsk.fusion.Component, new_loop_cm: float, sprocket_width_cm: float,
                      link_height_cm: float):
    """Delete the chain body and recreate it from current sketch geometry."""
    try:
        chain_type_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_CHAIN_TYPE)
        sname = 'Chain35' if (chain_type_attr and chain_type_attr.value == '35') else 'Chain25'
        sk = comp.sketches.itemByName(sname)
        if sk is None:
            return

        features = comp.features

        for i in range(features.extrudeFeatures.count - 1, -1, -1):
            try:
                features.extrudeFeatures.item(i).deleteMe()
            except Exception:
                pass

        for i in range(comp.bRepBodies.count - 1, -1, -1):
            try:
                comp.bRepBodies.item(i).deleteMe()
            except Exception:
                pass

        # Find any construction line (tangent line in the loop) to seed findConnectedCurves
        tangent_line = None
        for i in range(sk.sketchCurves.sketchLines.count):
            ln = sk.sketchCurves.sketchLines.item(i)
            if ln.isConstruction:
                tangent_line = ln
                break
        if tangent_line is None:
            return

        path_curves = sk.findConnectedCurves(tangent_line)
        extrudeChain(sk, sprocket_width_cm,
                     CHAIN_35_WIDTH_CM if sname == 'Chain35' else CHAIN_25_WIDTH_CM)

        existing = comp.attributes.itemByName(ATTR_GROUP, ATTR_CHAIN_LOOP_LENGTH)
        if existing:
            existing.deleteMe()
        comp.attributes.add(ATTR_GROUP, ATTR_CHAIN_LOOP_LENGTH, str(round(new_loop_cm, 8)))
    except Exception:
        futil.log('PartsGen: _rebuild_chain_3d failed')


def _rebuild_sprocket(old_comp: adsk.fusion.Component, design: adsk.fusion.Design,
                      new_n_teeth: int, chain_pitch_mm: float,
                      chain_comp_token: str, circle_idx: int):
    """Bring the sprocket to its new tooth count: edited in place when possible (joints the
    user made to it survive), else deleted and recreated, with its C-C joint re-added."""
    try:
        # Read attributes BEFORE any deletion (comp becomes invalid after deleteMe)
        attrs  = old_comp.attributes
        units  = design.unitsManager
        width_attr  = attrs.itemByName(ATTR_GROUP, ATTR_SPROCKET_WIDTH)
        offset_attr = attrs.itemByName(ATTR_GROUP, ATTR_SPROCKET_BORE_OFFSET)
        try:
            # Older sprockets stored the rounded width as 'NN mm'; newer ones exact inches.
            width_cm = units.evaluateExpression(width_attr.value, 'in') if width_attr else 0.9525
        except Exception:
            width_cm = 0.9525
        try:
            bore_offset_cm = (units.evaluateExpression(offset_attr.value, 'in') if offset_attr
                              else BORE_OFFSET_DEFAULT_IN * 2.54)
        except Exception:
            bore_offset_cm = BORE_OFFSET_DEFAULT_IN * 2.54

        old_token  = old_comp.entityToken
        chain_comp = _find_comp_by_token(design, chain_comp_token)
        if chain_comp is None:
            return

        proj_circle = _get_pitch_circle(chain_comp, circle_idx)
        if proj_circle is None:
            return

        chain_occ   = _find_occurrence_of(design, chain_comp.entityToken)
        sprocket_occ = _find_occurrence_of(design, old_token)
        if chain_occ is None or sprocket_occ is None:
            return

        if update_sprocket_teeth(sprocket_occ, new_n_teeth, design):
            return

        futil.log(f'PartsGen: rebuilding {old_comp.name} from scratch -- '
                  'joints made to it will be lost')
        rebuild_start = design.timeline.markerPosition
        sprocket_occ.deleteMe()     # removes its joints too

        sprocket = create_sprocket_for_chain(new_n_teeth, width_cm, chain_pitch_mm,
                                             chain_occ, proj_circle, circle_idx,
                                             parent_comp=chain_comp,
                                             bore_offset_cm=bore_offset_cm,
                                             group_timeline=False)

        # Its joint to the user's C-C circle went with the old occurrence -- re-add it.
        cc_attr  = chain_comp.attributes.itemByName(ATTR_GROUP, f'{ATTR_CHAIN_CC_CIRCLE}_{circle_idx + 1}')
        off_attr = chain_comp.attributes.itemByName(ATTR_GROUP, ATTR_CHAIN_JOINT_OFFSET)
        if sprocket is not None and cc_attr is not None:
            found = design.findEntityByToken(cc_attr.value)
            if found:
                _add_cc_joint(chain_occ, sprocket, found[0], circle_idx,
                              float(off_attr.value) if off_attr else 0.0,
                              sketch_name=proj_circle.parentSketch.name)
        if sprocket is not None:
            futil.group_timeline_features(design, rebuild_start, sprocket[0].component.name)
    except Exception:
        futil.log('PartsGen: _rebuild_sprocket failed')


def _update_chain_name(comp: adsk.fusion.Component):
    """Recompute loop length from pitch-circle geometry and rename / rebuild if needed."""
    try:
        chain_type_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_CHAIN_TYPE)
        chain_type = chain_type_attr.value if chain_type_attr else '25'
        motion = MOTION_CHAIN_35 if chain_type == '35' else MOTION_CHAIN_25
        pitch_mm, link_height_cm, sname, comp_prefix, _ = _chain_params(motion)

        sk = comp.sketches.itemByName(sname)
        if sk is None:
            return

        circles = [sk.sketchCurves.sketchCircles.item(i)
                   for i in range(sk.sketchCurves.sketchCircles.count)
                   if sk.sketchCurves.sketchCircles.item(i).isConstruction]
        if len(circles) < 2:
            return

        r1, r2 = circles[0].radius, circles[1].radius
        p1 = circles[0].centerSketchPoint.geometry
        p2 = circles[1].centerSketchPoint.geometry
        cc = math.sqrt((p2.x - p1.x) ** 2 + (p2.y - p1.y) ** 2)

        if cc < abs(r1 - r2) + 1e-6:
            return

        sin_a   = max(-1.0, min(1.0, (r1 - r2) / cc))
        alpha   = math.asin(sin_a)
        tangent = math.sqrt(cc ** 2 - (r1 - r2) ** 2)
        loop_cm = 2 * tangent + r1 * (math.pi + 2 * alpha) + r2 * (math.pi - 2 * alpha)

        link_count = int(loop_cm * 10 / pitch_mm + 0.5)

        stored_len_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_CHAIN_LOOP_LENGTH)
        stored_len = float(stored_len_attr.value) if stored_len_attr else None

        width_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_CHAIN_SPROCKET_WIDTH)
        try:
            design = adsk.fusion.Design.cast(app.activeProduct)
            width_cm = design.unitsManager.evaluateExpression(width_attr.value) if width_attr else 0.9525
        except Exception:
            width_cm = 0.9525

        if stored_len is None or abs(loop_cm - stored_len) > 1e-6:
            _rebuild_chain_3d(comp, loop_cm, width_cm, link_height_cm)

        if comp.attributes.itemByName(ATTR_GROUP, ATTR_CUSTOM_NAME):
            return  # user gave this chain a custom name — don't auto-rename it

        width_mm = round(width_cm * 10)
        new_name = f'{comp_prefix}-{link_count}Lx{width_mm}mm'
        if comp.name != new_name:
            comp.name = new_name
    except Exception:
        pass


def _update_chain_sprocket_name(comp: adsk.fusion.Component, design: adsk.fusion.Design):
    """Detect tooth-count change and rebuild the sprocket geometry if needed."""
    try:
        chain_token_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_SPROCKET_CHAIN_COMP_TOKEN)
        circle_idx_attr  = comp.attributes.itemByName(ATTR_GROUP, ATTR_SPROCKET_PITCH_CIRCLE_IDX)
        pitch_attr       = comp.attributes.itemByName(ATTR_GROUP, ATTR_SPROCKET_CHAIN_PITCH)
        if chain_token_attr is None or circle_idx_attr is None or pitch_attr is None:
            return

        circle_idx   = int(circle_idx_attr.value)
        chain_pitch_mm = float(pitch_attr.value)
        chain_comp   = _find_comp_by_token(design, chain_token_attr.value)
        if chain_comp is None:
            return

        pitch_circle = _get_pitch_circle(chain_comp, circle_idx)
        if pitch_circle is None:
            return

        new_n_teeth = _n_teeth_from_radius(pitch_circle.radius, chain_pitch_mm)
        stored_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_SPROCKET_TOOTH_COUNT)
        stored_n = int(stored_attr.value) if stored_attr else -1

        if new_n_teeth != stored_n:
            _rebuild_sprocket(comp, design, new_n_teeth, chain_pitch_mm,
                              chain_token_attr.value, circle_idx)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Extrude helper
# ---------------------------------------------------------------------------

def extrudeChain(sketch: adsk.fusion.Sketch, sprocket_width_cm: float,
                 chain_width_cm: float = None):
    """Extrude the annular chain loop profile (the 'belt shell' equivalent): `chain_width_cm`
    wide (the chain's outer width), centred on the sprockets' plates, i.e. mid-way across
    their `sprocket_width_cm` hub-to-hub width. None = the full sprocket width."""
    workingComp = sketch.parentComponent

    if sketch.profiles.count == 0:
        return

    # The annular ring is uniquely identified by having exactly 2 profile loops:
    # one outer-boundary loop and one inner-hole loop.  All other profiles
    # (inner fill, background region) have exactly 1 loop.  This works whether
    # Fusion creates 2 profiles (inner fill + annular) or 3+ (inner + annular +
    # background), so it is immune to the 2-profile case where the old
    # "strictly between min and max area" heuristic always returns None.
    chain_loop = None
    for i in range(sketch.profiles.count):
        p = sketch.profiles.item(i)
        if p.profileLoops.count == 2:
            chain_loop = p
            break

    # Fallback: 3+ profiles — use the middle-area heuristic (same as extrudeBeltPreview)
    if chain_loop is None and sketch.profiles.count >= 3:
        max_area = max(sketch.profiles.item(i).areaProperties().area
                       for i in range(sketch.profiles.count))
        min_area = min(sketch.profiles.item(i).areaProperties().area
                       for i in range(sketch.profiles.count))
        for i in range(sketch.profiles.count):
            a = sketch.profiles.item(i).areaProperties().area
            if min_area < a < max_area:
                chain_loop = sketch.profiles.item(i)
                break

    if chain_loop is None:
        futil.log(f'extrudeChain: could not identify annular profile '
                  f'(profiles={sketch.profiles.count})')
        return

    width_cm = sprocket_width_cm if chain_width_cm is None else chain_width_cm
    extrudes = workingComp.features.extrudeFeatures
    ext_in = extrudes.createInput(chain_loop, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext_in.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(
        adsk.core.ValueInput.createByReal(width_cm)),
        adsk.fusion.ExtentDirections.PositiveExtentDirection)
    start_cm = (sprocket_width_cm - width_cm) / 2
    if abs(start_cm) > 1e-6:
        ext_in.startExtent = adsk.fusion.OffsetStartDefinition.create(
            adsk.core.ValueInput.createByReal(start_cm))
    extrudes.add(ext_in)


# ---------------------------------------------------------------------------
# Selection helper — detect chain CCLine from a selected entity
# ---------------------------------------------------------------------------

def _detect_chain_motion(entity) -> int:
    """Return the motion type (3 or 4) if entity belongs to a chain CCLine, else -1."""
    try:
        ccLine = getCCLineFromEntity(entity)
        if ccLine and ccLine.data.motion in (MOTION_CHAIN_25, MOTION_CHAIN_35):
            return ccLine.data.motion
    except Exception:
        pass
    return -1


# ---------------------------------------------------------------------------
# Public helper called from PartsGen/entry.py on selection changed
# ---------------------------------------------------------------------------

def handle_chain_selection_changed(inputs: adsk.core.CommandInputs):
    """React to changes in the chain_pitch_circles selection input.

    If a CCLine is clicked:
    - If motion in (3, 4): auto-select its two pitch circles.
    - If wrong motion type: show error and clear.
    Otherwise (bare circles): allow selection without restriction.
    """
    selInp: adsk.core.SelectionCommandInput = inputs.itemById('chain_pitch_circles')
    if selInp is None:
        return

    count = selInp.selectionCount

    if count == 1:
        entity  = selInp.selection(0).entity
        ccLine  = getCCLineFromEntity(entity)
        if ccLine:
            motion = ccLine.data.motion
            if motion in (MOTION_CHAIN_25, MOTION_CHAIN_35):
                selInp.clearSelection()
                selInp.addSelection(ccLine.pitchCircle1)
                selInp.addSelection(ccLine.pitchCircle2)
                # Snap width to the correct ANSI outer chain width (E dimension)
                widthInp = inputs.itemById('chain_sprocket_width')
                if widthInp is not None:
                    widthInp.value = (CHAIN_35_SPROCKET_FACE_CM if motion == MOTION_CHAIN_35
                                      else CHAIN_25_SPROCKET_FACE_CM)
            else:
                selInp.clearSelection()
                futil.popup_error(
                    'Parts Gen: the selected C-C Line is not a chain type.\n'
                    'Please select a #25 or #35 Chain C-C Distance sketch.'
                )

    elif count == 3:
        # Third entity added — try to re-detect a new CCLine
        newEntity = selInp.selection(2).entity
        ccLine = getCCLineFromEntity(newEntity)
        if ccLine and ccLine.data.motion in (MOTION_CHAIN_25, MOTION_CHAIN_35):
            selInp.clearSelection()
            selInp.addSelection(ccLine.pitchCircle1)
            selInp.addSelection(ccLine.pitchCircle2)
        elif ccLine:
            selInp.clearSelection()
            selInp.addSelection(newEntity)
            futil.popup_error(
                'Parts Gen: the selected C-C Line is not a chain type.\n'
                'Please select a #25 or #35 Chain C-C Distance sketch.'
            )
        else:
            selInp.clearSelection()
            selInp.addSelection(newEntity)


# ---------------------------------------------------------------------------
# Main creation entry point
# ---------------------------------------------------------------------------

def _create_chain(inputs: adsk.core.CommandInputs, is_preview: bool = False):
    """Create (or preview) a roller chain component from the PartsGen dialog inputs.

    With "Generate Sprockets" on, both sprockets are built toothless with a 1/2in hex bore
    and jointed to the chain's pitch circles *and* to the user's own C-C circles: Sprocket 1
    with a revolute joint carrying the Z offset (it sets the height of the whole chain),
    Sprocket 2 with a cylindrical one -- the same scheme as the belt's pulleys."""

    selInp:         adsk.core.SelectionCommandInput = inputs.itemById('chain_pitch_circles')
    widthInp:       adsk.core.ValueCommandInput     = inputs.itemById('chain_sprocket_width')
    genSprocketsInp: adsk.core.BoolValueCommandInput = inputs.itemById('chain_gen_sprockets')
    boreOffsetInp:  adsk.core.ValueCommandInput     = inputs.itemById('chain_bore_offset')
    offsetFaceInp:  adsk.core.SelectionCommandInput = inputs.itemById('chain_offset_face')
    offsetDistInp:  adsk.core.ValueCommandInput     = inputs.itemById('chain_offset_dist')
    offsetSideInp:  adsk.core.DropDownCommandInput  = inputs.itemById('chain_offset_side')

    bore_offset_cm = (boreOffsetInp.value if boreOffsetInp is not None
                      else BORE_OFFSET_DEFAULT_IN * 2.54)
    # Sprocket 1's height: its `offset_side` face sits `offset_dist_cm` from the picked face.
    offset_dist_cm = offsetDistInp.value if offsetDistInp is not None else 0.0
    offset_side    = (offsetSideInp.selectedItem.name
                      if offsetSideInp is not None and offsetSideInp.selectedItem is not None
                      else OFFSET_SIDE_BOTTOM)
    offset_face = None
    if offsetFaceInp is not None and offsetFaceInp.selectionCount > 0:
        try:
            offset_face = offsetFaceInp.selection(0).entity
        except RuntimeError:
            pass    # the count can run ahead of the indexer mid-click (LESSONS_LEARNED.md)

    if selInp.selectionCount < 2:
        if not is_preview:
            futil.popup_error('Parts Gen: please select two pitch circles for the Chain.')
        return

    # Cache selections before new component creation clears them
    userSelections = [selInp.selection(i).entity for i in range(selInp.selectionCount)]
    originalSketch: adsk.fusion.Sketch = userSelections[0].parentSketch

    # Detect chain type from the CCLine that owns the selected circles
    motion = _detect_chain_motion(userSelections[0])
    if motion == -1:
        motion = _detect_chain_motion(userSelections[1])
    if motion == -1:
        motion = MOTION_CHAIN_25   # fallback

    pitch_mm, link_height_cm, sketch_name, comp_prefix, chain_label = _chain_params(motion)
    chain_type_str = '35' if motion == MOTION_CHAIN_35 else '25'

    design    = adsk.fusion.Design.cast(app.activeProduct)
    start_marker = design.timeline.markerPosition
    try:
        workingOcc  = futil.add_occurrence_in_active(design)   # root-context proxy
    except RuntimeError:
        if not is_preview:
            futil.popup_error(
                'Cannot create chain: this document is in Part Design mode, '
                'which only supports a single component.\n\n'
                'Please open or create an Assembly document and try again.'
            )
        return
    workingComp = workingOcc.component

    try:
        sketch = workingComp.sketches.add(originalSketch.referencePlane, workingOcc)
        sketch.name = sketch_name

        if userSelections[0].objectType != adsk.fusion.SketchCircle.classType():
            if not is_preview:
                futil.popup_error('Parts Gen: please select two pitch circles (not a line).')
            workingOcc.deleteMe()
            return

        projList1 = sketch.include(userSelections[0])
        projList2 = sketch.include(userSelections[1])
        circle1_proj = projList1.item(0)
        circle2_proj = projList2.item(0)

        # Guard against coincident/near-coincident pitch circles — _buildPitchLoop divides
        # by the center-to-center distance, so this must be checked before it runs (mirrors
        # the same check already used on rebuild in _update_chain_name).
        p1_c, p2_c = circle1_proj.centerSketchPoint.geometry, circle2_proj.centerSketchPoint.geometry
        cc_dist = math.sqrt((p2_c.x - p1_c.x) ** 2 + (p2_c.y - p1_c.y) ** 2)
        if cc_dist < abs(circle1_proj.radius - circle2_proj.radius) + 1e-6:
            if not is_preview:
                futil.popup_error(
                    'Parts Gen: the two selected pitch circles are coincident or one is '
                    'inside the other — cannot build a chain loop.'
                )
            workingOcc.deleteMe()
            return

        # The sprockets span Z=0..width from their joint circle, so the "Measured To" face
        # is at 0 (bottom) or the width (top).
        part_face_z = widthInp.value if offset_side == OFFSET_SIDE_TOP else 0.0
        joint_offset_cm = _cc_joint_offset_cm(workingOcc, circle1_proj, offset_face,
                                              offset_dist_cm, part_face_z, sketch_name)
        if joint_offset_cm is None:
            if not is_preview:
                futil.popup_error('Parts Gen: the "Offset From" face must be parallel to the '
                                  'C-C sketch.')
            workingOcc.deleteMe()
            return

        PitchLoop = createPitchLoopFromSketchCircles(sketch, circle1_proj, circle2_proj)

        curveLength = sum(curve.length for curve in PitchLoop)
        link_count  = int(curveLength * 10 / pitch_mm + 0.5)
        futil.log(f'Chain {chain_label}: loop length={curveLength:.4f} cm, links={link_count}')

        width_mm = round(widthInp.value * 10)
        comp_name = f'{comp_prefix}-{link_count}Lx{width_mm}mm'
        workingComp.name = comp_name

        customNameInp = inputs.itemById('custom_name')
        custom_name = customNameInp.value.strip() if customNameInp is not None else ''
        if custom_name:
            workingComp.name = comp_name = custom_name

        # Build offset profiles around the pitch loop for the chain body cross-section
        half_thickness = adsk.core.ValueInput.createByReal(link_height_cm / 2)
        geoConstraints = sketch.geometricConstraints
        curves         = list(PitchLoop)

        offsetInput = geoConstraints.createOffsetInput(curves, half_thickness)
        geoConstraints.addTwoSidesOffset(offsetInput, True)

        futil.log(f'Chain offset created {sketch.profiles.count} profiles')
        if sketch.profiles.count < 2:
            if not is_preview:
                futil.popup_error('Parts Gen: chain offset profiles not created correctly.')
            workingOcc.deleteMe()
            return

        extrudeChain(sketch, widthInp.value,
                     CHAIN_35_WIDTH_CM if motion == MOTION_CHAIN_35 else CHAIN_25_WIDTH_CM)

        # Save attributes
        gen_sprockets = genSprocketsInp is not None and genSprocketsInp.value
        try:
            attrs = workingComp.attributes
            attrs.add(ATTR_GROUP, ATTR_PART_TYPE,            'Chain')
            attrs.add(ATTR_GROUP, ATTR_CHAIN_TYPE,           chain_type_str)
            attrs.add(ATTR_GROUP, ATTR_CHAIN_SPROCKET_WIDTH, widthInp.expression)
            attrs.add(ATTR_GROUP, ATTR_CHAIN_GEN_SPROCKETS,  str(gen_sprockets))
            attrs.add(ATTR_GROUP, ATTR_CHAIN_LOOP_LENGTH,    str(round(curveLength, 8)))
            if custom_name:
                attrs.add(ATTR_GROUP, ATTR_CUSTOM_NAME,      custom_name)
            if boreOffsetInp is not None:
                attrs.add(ATTR_GROUP, ATTR_CHAIN_BORE_OFFSET, boreOffsetInp.expression)
            if offsetDistInp is not None:
                attrs.add(ATTR_GROUP, ATTR_CHAIN_OFFSET_EXPR, offsetDistInp.expression)
            attrs.add(ATTR_GROUP, ATTR_CHAIN_JOINT_OFFSET, str(round(joint_offset_cm, 8)))
            attrs.add(ATTR_GROUP, ATTR_CHAIN_OFFSET_SIDE,  offset_side)
            for key, entity in [(ATTR_CHAIN_OFFSET_FACE, offset_face),
                                (f'{ATTR_CHAIN_CC_CIRCLE}_1', userSelections[0]),
                                (f'{ATTR_CHAIN_CC_CIRCLE}_2', userSelections[1])]:
                try:
                    if entity is not None:
                        attrs.add(ATTR_GROUP, key, entity.entityToken)
                except Exception:
                    futil.log(f'PartsGen: could not save chain attribute {key}')
        except Exception:
            futil.log('PartsGen: failed to save chain attributes')

        # Auto-generate sprockets (toothless; cheap enough to build in preview too)
        if gen_sprockets:
            proj_circles = [circle1_proj, circle2_proj]
            for i, circle in enumerate(userSelections[:2]):
                try:
                    n_teeth = _n_teeth_from_radius(circle.radius, pitch_mm)
                    futil.log(f'Chain {chain_label}: auto-sprocket {i+1} — radius={circle.radius:.4f} cm, teeth={n_teeth}')
                    if n_teeth < 9:
                        futil.log(f'Chain: skipping auto-sprocket {i+1} — tooth count {n_teeth} too small')
                        continue
                    sprocket = create_sprocket_for_chain(
                        n_teeth, widthInp.value, pitch_mm,
                        workingOcc, proj_circles[i],
                        circle_index=i, parent_comp=workingComp,
                        bore_offset_cm=bore_offset_cm, is_preview=is_preview,
                        group_timeline=False,
                    )
                    # ...and to the user's own C-C circle, which places the whole chain.
                    if sprocket is not None:
                        _add_cc_joint(workingOcc, sprocket, circle, i, joint_offset_cm,
                                      is_preview, sketch_name)
                except Exception:
                    futil.handle_error(f'PartsGen: auto-sprocket {i+1} failed',
                                       show_message_box=not is_preview)

        futil.group_timeline_features(design, start_marker, comp_name)
    except Exception:
        try:
            workingOcc.deleteMe()
        except Exception:
            pass
        futil.handle_error('PartsGen _create_chain', show_message_box=not is_preview)
