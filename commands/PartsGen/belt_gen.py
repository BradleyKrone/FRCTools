"""
belt_gen.py  —  Timing Belt geometry for PartsGen

Contains all belt geometry functions plus the _create_belt() entry point
and handle_belt_selection_changed() helper that PartsGen/entry.py calls.
"""

import adsk.core
import adsk.fusion
import math
from ...lib import fusionAddInUtils as futil
from ... import config
from ..CCDistance.CCLine import getCCLineFromEntity
from .pulley_gen import (create_pulley_for_belt, pulley_outer_occurrence, update_pulley_teeth,
                          _belt_width_mm, _tooth_length_cm,
                          ATTR_PULLEY_BELT_TYPE, ATTR_PULLEY_BELT_COMP_TOKEN,
                          ATTR_PULLEY_PITCH_CIRCLE_IDX, ATTR_PULLEY_TOOTH_COUNT,
                          ATTR_PULLEY_BELT_WIDTH, ATTR_PULLEY_SHOW_TEETH,
                          ATTR_PULLEY_BORE_TYPE, ATTR_PULLEY_BORE_OFFSET, ATTR_PULLEY_ADAPTER,
                          BORE_HALF_HEX, BORE_OFFSET_DEFAULT_IN, FLANGE_THICKNESS_CM)
from .shaft_gen import _world_plane, _hide_joint

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Attribute keys (written to the component so the edit command can restore)
# ---------------------------------------------------------------------------
ATTR_GROUP        = 'FRCTools_PartsGen'
ATTR_PART_TYPE    = 'part_type'
ATTR_BELT_TYPE       = 'belt_type'
ATTR_BELT_WIDTH      = 'belt_width_expr'
ATTR_BELT_SUPPRESS   = 'belt_suppress_teeth'
ATTR_BELT_GEN_PULLEYS  = 'belt_gen_pulleys'
ATTR_BELT_PULLEY_TEETH = 'belt_pulley_teeth'
ATTR_BELT_BORE_TYPE    = 'belt_pulley_bore_type'    # stored per pulley, suffixed _1 / _2
ATTR_BELT_BORE_OFFSET  = 'belt_pulley_bore_offset'
ATTR_BELT_ADAPTER      = 'belt_pulley_adapter'      # stored per pulley, suffixed _1 / _2
ATTR_BELT_LOOP_LENGTH  = 'belt_loop_length'
ATTR_CUSTOM_NAME       = 'custom_name'
ATTR_BELT_OFFSET_EXPR  = 'belt_offset_expr'         # Pulley 1's Z Offset, as typed
ATTR_BELT_OFFSET_FACE  = 'belt_offset_face_token'   # entityToken of its "Offset From" face
ATTR_BELT_CC_CIRCLE    = 'belt_cc_circle_token'     # the user's C-C circles, suffixed _1 / _2
ATTR_BELT_JOINT_OFFSET = 'belt_joint_offset_cm'     # the offset Pulley 1's C-C joint got
ATTR_BELT_OFFSET_FLANGE = 'belt_offset_flange'      # which flange the Z Offset is measured to

# "Measured To" choices for Pulley 1's Z Offset
OFFSET_FLANGE_BOTTOM = 'Bottom Flange'
OFFSET_FLANGE_TOP    = 'Top Flange'

# ---------------------------------------------------------------------------
# Belt-name-sync (commandTerminated hook)
# ---------------------------------------------------------------------------
_belt_sync_registered  = False
_belt_sync_handlers: list = []  # keeps event handler alive (prevent GC)


# ---------------------------------------------------------------------------
# Belt-name-sync — registration and update logic
# ---------------------------------------------------------------------------

def register_belt_name_sync():
    """Hook into commandTerminated so belt names update after any design edit."""
    global _belt_sync_registered
    if _belt_sync_registered:
        return
    try:
        ui = app.userInterface
        futil.add_handler(ui.commandTerminated, _on_command_terminated,
                          local_handlers=_belt_sync_handlers)
        _belt_sync_registered = True
    except Exception:
        futil.handle_error('PartsGen register_belt_name_sync', show_message_box=False)


def unregister_belt_name_sync():
    """Release the commandTerminated handler (call from add-in stop())."""
    global _belt_sync_registered
    _belt_sync_handlers.clear()
    _belt_sync_registered = False


_belt_sync_running = False


def should_run_part_sync(args: adsk.core.ApplicationCommandEventArgs, design) -> bool:
    """Whether a commandTerminated part-sync hook should run now.

    Every dimension edit inside a sketch is its own command, so without this the pulley
    update (which rolls the timeline back) ran mid-sketch, once per edit, and hung Fusion.
    Skipping while a sketch is open means the sync runs once, on Finish Sketch."""
    if design is None:
        return False
    if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
        return False
    if args.terminationReason == adsk.core.CommandTerminationReason.CancelledTerminationReason:
        return False
    if adsk.fusion.Sketch.cast(design.activeEditObject) is not None:
        return False
    return True


def _on_command_terminated(args: adsk.core.ApplicationCommandEventArgs):
    """After any command, scan belt components and update names if needed."""
    global _belt_sync_running
    if _belt_sync_running:
        return  # a command fired by the sync's own edits
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not should_run_part_sync(args, design):
            return
        _belt_sync_running = True
        _scan_and_update_belt_names(design)
    except Exception:
        pass  # must never interrupt normal Fusion operation
    finally:
        _belt_sync_running = False


def _find_comp_by_token(design: adsk.fusion.Design, token: str):
    """Find a component anywhere in the design tree by its entityToken."""
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


def _scan_and_update_belt_names(design: adsk.fusion.Design):
    """Walk every component in the design and fix belt and pulley names.

    Collects the components first and updates them after: a pulley that has to be
    rebuilt deletes its occurrence, and walking on through it used to throw and end the
    scan early, leaving the belt's other pulley at its old tooth count."""
    belts, pulleys = [], []
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

            belt_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_BELT_TYPE)
            if belt_attr is not None:
                belts.append((comp, belt_attr.value))

            pulley_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_PULLEY_BELT_TYPE)
            if pulley_attr is not None:
                pulleys.append((comp, pulley_attr.value))

            for i in range(comp.occurrences.count):
                queue.append(comp.occurrences.item(i).component)
    except Exception:
        pass
    for comp, belt_type in belts:
        _update_belt_name(comp, belt_type)
    for comp, pulley_type in pulleys:
        if comp.isValid:
            _update_pulley_name(comp, pulley_type, design)


def scan_and_rebuild_belts(design: adsk.fusion.Design):
    """Rebuild belt 3D features for all belt components in the design.

    Belt-only (no pulley occurrence changes) — safe to call during command
    preview so the belt body updates without disruptive occurrence rebuilds.
    Pulley name/tooth-count sync happens later via the commandTerminated hook.
    """
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

            belt_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_BELT_TYPE)
            if belt_attr is not None:
                _update_belt_name(comp, belt_attr.value)

            for i in range(comp.occurrences.count):
                queue.append(comp.occurrences.item(i).component)
    except Exception:
        pass


def _get_pitch_circle(belt_comp: adsk.fusion.Component, circle_idx: int):
    """Return the circle_idx-th construction circle from the belt's TimingBelt sketch."""
    belt_sketch = belt_comp.sketches.itemByName('TimingBelt')
    if belt_sketch is None:
        return None
    circles = [belt_sketch.sketchCurves.sketchCircles.item(i)
               for i in range(belt_sketch.sketchCurves.sketchCircles.count)
               if belt_sketch.sketchCurves.sketchCircles.item(i).isConstruction]
    if circle_idx >= len(circles):
        return None
    return circles[circle_idx]


def _find_occurrence_of(design: adsk.fusion.Design, comp_token: str):
    """Return the first occurrence whose component has the given entityToken."""
    root = design.rootComponent
    for i in range(root.allOccurrences.count):
        occ = root.allOccurrences.item(i)
        if occ.component.entityToken == comp_token:
            return occ
    return None


def _belt_normal(belt_occ: adsk.fusion.Occurrence) -> adsk.core.Vector3D:
    """World unit normal of the belt's TimingBelt sketch -- the way its pulleys point."""
    sk = belt_occ.component.sketches.itemByName('TimingBelt')
    n = sk.xDirection.crossProduct(sk.yDirection)   # the component's own frame
    n.transformBy(belt_occ.transform2)
    n.normalize()
    return n


def _cc_joint_offset_cm(belt_occ: adsk.fusion.Occurrence,
                        circle_proj: adsk.fusion.SketchCircle,
                        face: adsk.fusion.BRepFace, dist_cm: float,
                        flange: str = OFFSET_FLANGE_BOTTOM, belt_width_cm: float = 0.0):
    """Z offset for Pulley 1's C-C joint that puts the outer face of its `flange`
    (OFFSET_FLANGE_BOTTOM / _TOP) `dist_cm` from `face` along the pulley axis (positive =
    the way the pulleys extend). With no face the distance is the joint offset itself
    (0 = on the C-C sketch plane, as before). Returns None when the face isn't parallel
    to the belt."""
    if face is None:
        return dist_cm
    n = _belt_normal(belt_occ)
    origin, face_n = _world_plane(face)
    if abs(face_n.dotProduct(n)) < 0.999:
        return None
    # The belt occurrence is still at identity here, so the sketch point's world
    # geometry is on the C-C sketch plane.
    center = circle_proj.centerSketchPoint.worldGeometry
    s_face   = n.dotProduct(origin.asVector())
    s_sketch = n.dotProduct(center.asVector())
    # Outer flange faces, relative to the pulley's joint circle (see _add_flanges): the
    # bottom one FLANGE_THICKNESS_CM below it, the top one a tooth length + that above it.
    tooth_len_cm = _tooth_length_cm(int(round(belt_width_cm * 10)))
    flange_z = (tooth_len_cm + FLANGE_THICKNESS_CM if flange == OFFSET_FLANGE_TOP
                else -FLANGE_THICKNESS_CM)
    return s_face + dist_cm - s_sketch - flange_z


def _add_cc_joint(belt_occ: adsk.fusion.Occurrence, pulley: tuple,
                  cc_circle: adsk.fusion.SketchCircle, circle_idx: int,
                  offset_cm: float = 0.0, is_preview: bool = False):
    """Joint a belt pulley to the user's own C-C sketch circle, in the root component.

    `pulley` is create_pulley_for_belt's (pulley occurrence in the belt comp, joint circle).
    Pulley 1 (circle_idx 0) gets a revolute joint carrying the Z offset -- it sets the
    height of the whole belt, since both pulleys are also jointed to the belt. Pulley 2
    gets a cylindrical joint, which stops the belt spinning about Pulley 1 but lets its
    height follow."""
    pulley_occ, joint_circle = pulley
    name = pulley_occ.component.name
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        root   = design.rootComponent
        pulley_root = pulley_occ.createForAssemblyContext(belt_occ)

        def _add(flip: bool):
            pulley_geom = adsk.fusion.JointGeometry.createByCurve(
                joint_circle.createForAssemblyContext(pulley_root),
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            cc_geom = adsk.fusion.JointGeometry.createByCurve(
                cc_circle, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            joint_input = root.joints.createInput(pulley_geom, cc_geom)
            joint_input.isFlipped = flip
            if circle_idx == 0:
                joint_input.offset = adsk.core.ValueInput.createByReal(offset_cm)
                joint_input.setAsRevoluteJointMotion(
                    adsk.fusion.JointDirections.ZAxisJointDirection)
            else:
                joint_input.setAsCylindricalJointMotion(
                    adsk.fusion.JointDirections.ZAxisJointDirection)
            joint = root.joints.add(joint_input)
            if joint is None:
                raise RuntimeError('Joints.add returned null')
            return joint

        # Neither the flip nor the offset's sign can be read off the JointGeometry frames:
        # their primaryAxisVector is component-native (seen live: +Z for a C-C sketch in a
        # component turned upside down). So add the joint, then check what it did to the
        # belt: it must keep its orientation, and move `offset_cm` along its normal (it
        # was built on the C-C sketch plane).
        before = belt_occ.transform2
        z_before = before.getAsCoordinateSystem()[3]
        joint = _add(False)
        if belt_occ.transform2.getAsCoordinateSystem()[3].dotProduct(z_before) < 0.999:
            joint.deleteMe()                 # doesn't put the belt back (LESSONS_LEARNED.md)
            belt_occ.transform2 = before
            joint = _add(True)
        if circle_idx == 0 and abs(offset_cm) > 1e-9:
            moved = _belt_normal(belt_occ).dotProduct(belt_occ.transform2.translation)
            if abs(moved + offset_cm) < abs(moved - offset_cm):
                joint.offset.value = -joint.offset.value   # went the wrong way
        joint.name = name + ('_cc_revolute' if circle_idx == 0 else '_cc_cylindrical')
        _hide_joint(joint)
        return joint
    except Exception:
        futil.handle_error(f'PartsGen: C-C joint for {name}', show_message_box=not is_preview)
    return None


def _rebuild_pulley(old_comp: adsk.fusion.Component, design: adsk.fusion.Design,
                    new_n_teeth: int, belt_pitch_mm: int,
                    belt_comp_token: str, circle_idx: int):
    """Delete the stale pulley occurrence and recreate it with the updated tooth count."""
    try:
        # Read attributes BEFORE deletion (comp becomes invalid after deleteMe)
        attrs      = old_comp.attributes
        width_attr = attrs.itemByName(ATTR_GROUP, ATTR_PULLEY_BELT_WIDTH)
        teeth_attr = attrs.itemByName(ATTR_GROUP, ATTR_PULLEY_SHOW_TEETH)
        width_mm   = int(width_attr.value.split()[0]) if width_attr else 9
        show_teeth = teeth_attr is not None and teeth_attr.value.lower() == 'true'
        bore_attr    = attrs.itemByName(ATTR_GROUP, ATTR_PULLEY_BORE_TYPE)
        offset_attr  = attrs.itemByName(ATTR_GROUP, ATTR_PULLEY_BORE_OFFSET)
        adapter_attr = attrs.itemByName(ATTR_GROUP, ATTR_PULLEY_ADAPTER)
        bore_type    = bore_attr.value if bore_attr else BORE_HALF_HEX
        bore_offset_cm = (design.unitsManager.evaluateExpression(offset_attr.value, 'in')
                          if offset_attr else BORE_OFFSET_DEFAULT_IN * 2.54)
        use_adapter  = adapter_attr is not None and adapter_attr.value.lower() == 'true'
        old_token  = old_comp.entityToken

        belt_comp = _find_comp_by_token(design, belt_comp_token)
        if belt_comp is None:
            return

        proj_circle = _get_pitch_circle(belt_comp, circle_idx)
        if proj_circle is None:
            return

        belt_occ   = _find_occurrence_of(design, belt_comp.entityToken)
        pulley_occ = _find_occurrence_of(design, old_token)
        if belt_occ is None or pulley_occ is None:
            return

        rebuild_start = design.timeline.markerPosition

        # Edit the pulley in place when possible: deleting it also deletes every joint
        # the user made to it (e.g. to its flange faces).
        if update_pulley_teeth(pulley_occ, new_n_teeth, design):
            return

        futil.log(f'PartsGen: rebuilding {old_comp.name} from scratch -- '
                  'joints made to it will be lost')
        # Its "<pulley>_Group" when it has an adapter; removes any associated joints too.
        pulley_outer_occurrence(pulley_occ).deleteMe()

        pulley = create_pulley_for_belt(belt_pitch_mm, new_n_teeth, width_mm / 10.0,
                                        belt_occ, proj_circle, show_teeth, circle_idx,
                                        parent_comp=belt_comp, bore_type=bore_type,
                                        bore_offset_cm=bore_offset_cm, use_adapter=use_adapter)

        # Its joint to the user's C-C circle went with the old occurrence -- re-add it.
        cc_attr  = belt_comp.attributes.itemByName(ATTR_GROUP, f'{ATTR_BELT_CC_CIRCLE}_{circle_idx + 1}')
        off_attr = belt_comp.attributes.itemByName(ATTR_GROUP, ATTR_BELT_JOINT_OFFSET)
        if pulley is not None and cc_attr is not None:
            found = design.findEntityByToken(cc_attr.value)
            if found:
                _add_cc_joint(belt_occ, pulley, found[0], circle_idx,
                              float(off_attr.value) if off_attr else 0.0)

        # Wrap the delete + recreation into one named timeline group
        prefix   = 'Pulley_HTD_5mm' if belt_pitch_mm == 5 else 'Pulley_GT2_3mm'
        new_name = f'{prefix}-{new_n_teeth}Tx{width_mm}mm'
        futil.group_timeline_features(design, rebuild_start, new_name)
    except Exception:
        futil.log('PartsGen: _rebuild_pulley failed')


def _rebuild_belt_3d(comp: adsk.fusion.Component, belt_pitch_mm: int,
                     tooth_count: int, new_loop_cm: float):
    """Delete the belt's 3D features and recreate them from the current sketch geometry.

    Called when the belt loop length changes (CC distance edited without entering the
    sketch) so that the tooth path-pattern uses the updated pitch-loop path and count.
    """
    try:
        sk = comp.sketches.itemByName('TimingBelt')
        if sk is None:
            return

        suppress_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_BELT_SUPPRESS)
        suppressed = suppress_attr is not None and suppress_attr.value.lower() == 'true'

        width_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_BELT_WIDTH)
        if width_attr is None:
            return

        design = adsk.fusion.Design.cast(app.activeProduct)
        belt_width_cm = design.unitsManager.evaluateExpression(width_attr.value)

        features = comp.features

        futil.log(f'PartsGen: _rebuild_belt_3d — patterns={features.pathPatternFeatures.count}'
                  f' extrudes={features.extrudeFeatures.count}'
                  f' bodies={comp.bRepBodies.count}')

        # Delete path-pattern features first (they depend on the extrude features)
        for i in range(features.pathPatternFeatures.count - 1, -1, -1):
            try:
                features.pathPatternFeatures.item(i).deleteMe()
            except Exception as e:
                futil.log(f'PartsGen: _rebuild_belt_3d — pattern delete failed: {e}')

        # Delete extrude features
        for i in range(features.extrudeFeatures.count - 1, -1, -1):
            try:
                features.extrudeFeatures.item(i).deleteMe()
            except Exception as e:
                futil.log(f'PartsGen: _rebuild_belt_3d — extrude delete failed: {e}')

        # Explicitly remove any bodies left behind by failed features (orphaned geometry)
        for i in range(comp.bRepBodies.count - 1, -1, -1):
            try:
                comp.bRepBodies.item(i).deleteMe()
            except Exception as e:
                futil.log(f'PartsGen: _rebuild_belt_3d — body delete failed: {e}')

        futil.log(f'PartsGen: _rebuild_belt_3d — after cleanup: bodies={comp.bRepBodies.count}')

        # Anchor findConnectedCurves on any construction line (the tangent lines)
        tangent_line = None
        for i in range(sk.sketchCurves.sketchLines.count):
            ln = sk.sketchCurves.sketchLines.item(i)
            if ln.isConstruction:
                tangent_line = ln
                break
        if tangent_line is None:
            futil.log('PartsGen: _rebuild_belt_3d — no construction tangent line found')
            return

        path_curves = sk.findConnectedCurves(tangent_line)
        futil.log(f'PartsGen: _rebuild_belt_3d — path_curves count={path_curves.count}')

        if suppressed:
            extrudeBeltPreview(sk, path_curves, belt_width_cm)
        else:
            extrudeBelt(sk, path_curves, belt_width_cm, tooth_count, belt_pitch_mm)

        futil.log(f'PartsGen: _rebuild_belt_3d — done, bodies={comp.bRepBodies.count}')

        # Update the stored loop length so the next command-terminated event is a no-op
        existing = comp.attributes.itemByName(ATTR_GROUP, ATTR_BELT_LOOP_LENGTH)
        if existing:
            existing.deleteMe()
        comp.attributes.add(ATTR_GROUP, ATTR_BELT_LOOP_LENGTH, str(round(new_loop_cm, 8)))

    except Exception:
        futil.log('PartsGen: _rebuild_belt_3d failed')


def _update_pulley_name(comp: adsk.fusion.Component, pulley_type_str: str,
                         design: adsk.fusion.Design):
    """Detect tooth-count change and rebuild the pulley geometry (and name) if needed."""
    try:
        belt_pitch_mm = 5 if '5mm' in pulley_type_str else 3

        belt_token_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_PULLEY_BELT_COMP_TOKEN)
        circle_idx_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_PULLEY_PITCH_CIRCLE_IDX)
        if belt_token_attr is None or circle_idx_attr is None:
            return  # standalone pulley — no belt link, skip auto-update

        circle_idx = int(circle_idx_attr.value)
        belt_comp  = _find_comp_by_token(design, belt_token_attr.value)
        if belt_comp is None:
            return

        pitch_circle = _get_pitch_circle(belt_comp, circle_idx)
        if pitch_circle is None:
            return

        new_n_teeth = int(2 * math.pi * pitch_circle.radius * 10 / belt_pitch_mm + 0.5)

        stored_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_PULLEY_TOOTH_COUNT)
        stored_n_teeth = int(stored_attr.value) if stored_attr else -1

        if new_n_teeth != stored_n_teeth:
            _rebuild_pulley(comp, design, new_n_teeth, belt_pitch_mm,
                            belt_token_attr.value, circle_idx)
    except Exception:
        pass  # silently skip this component


def _update_belt_name(comp: adsk.fusion.Component, belt_type_str: str):
    """Recompute tooth count from pitch-circle geometry and rename the component."""
    try:
        belt_pitch_mm = 5 if '5mm' in belt_type_str else 3

        sk = comp.sketches.itemByName('TimingBelt')
        if sk is None:
            return

        # The only construction SketchCircles in the belt sketch are the two
        # projected pitch circles.  Neither tooth-profile setup function adds
        # construction circles, so this list always has exactly 2 items.
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
            return  # degenerate — one circle inside the other

        # Open-belt pitch-loop: 2 tangent lines + 2 wrap arcs
        sin_a   = max(-1.0, min(1.0, (r1 - r2) / cc))
        alpha   = math.asin(sin_a)
        tangent = math.sqrt(cc ** 2 - (r1 - r2) ** 2)
        loop_cm = 2 * tangent + r1 * (math.pi + 2 * alpha) + r2 * (math.pi - 2 * alpha)

        tooth_count = int(loop_cm * 10 / belt_pitch_mm + 0.5)

        stored_len_attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_BELT_LOOP_LENGTH)
        stored_len = float(stored_len_attr.value) if stored_len_attr else None
        if stored_len is None or abs(loop_cm - stored_len) > 1e-6:
            _rebuild_belt_3d(comp, belt_pitch_mm, tooth_count, loop_cm)

        if comp.attributes.itemByName(ATTR_GROUP, ATTR_CUSTOM_NAME):
            return  # user gave this belt a custom name — don't auto-rename it

        # Extract belt width from the existing name (format: "Belt_XXX-NNNTxMMmm")
        try:
            width_mm = int(comp.name.split('Tx')[1].replace('mm', ''))
        except Exception:
            width_mm = 9  # fallback

        prefix   = 'Belt_HTD_5mm' if belt_pitch_mm == 5 else 'Belt_GT2_3mm'
        new_name = f'{prefix}-{tooth_count}Tx{width_mm}mm'
        if comp.name != new_name:
            comp.name = new_name
    except Exception:
        pass  # silently skip this component


# ---------------------------------------------------------------------------
# Public helpers called from PartsGen/entry.py
# ---------------------------------------------------------------------------

def handle_belt_selection_changed(inputs: adsk.core.CommandInputs):
    """React to changes in the tb_pitch_circles selection input.

    Mirrors the CCLine-detection logic from the original TimingBelt command:
    - If a C-C Line is clicked, auto-select its two pitch circles and lock
      the belt-type dropdown to the CCLine's motion type.
    - Otherwise enable the belt-type dropdown for manual selection.
    """
    pitchLineSelection: adsk.core.SelectionCommandInput = inputs.itemById('tb_pitch_circles')
    beltType: adsk.core.DropDownCommandInput            = inputs.itemById('tb_belt_type')

    if pitchLineSelection is None or beltType is None:
        return

    count = pitchLineSelection.selectionCount

    if count == 1:
        ccLine = getCCLineFromEntity(pitchLineSelection.selection(0).entity)
        if ccLine:
            pitchLineSelection.clearSelection()
            if ccLine.data.motion != 0:
                beltType.listItems.item(ccLine.data.motion - 1).isSelected = True
                beltType.isEnabled = False
            pitchLineSelection.addSelection(ccLine.pitchCircle1)
            pitchLineSelection.addSelection(ccLine.pitchCircle2)
        else:
            beltType.isEnabled = True

    elif count == 3 and not beltType.isEnabled:
        # A third entity was picked while a CCLine had locked the dropdown.
        newEntity = pitchLineSelection.selection(2).entity
        ccLine = getCCLineFromEntity(newEntity)
        if ccLine:
            pitchLineSelection.clearSelection()
            if ccLine.data.motion != 0:
                beltType.listItems.item(ccLine.data.motion - 1).isSelected = True
                beltType.isEnabled = False
            pitchLineSelection.addSelection(ccLine.pitchCircle1)
            pitchLineSelection.addSelection(ccLine.pitchCircle2)
        else:
            pitchLineSelection.clearSelection()
            pitchLineSelection.addSelection(newEntity)
            beltType.isEnabled = True


def _create_belt(inputs: adsk.core.CommandInputs, is_preview: bool = False):
    """Create (or preview) a timing belt component from the PartsGen dialog inputs."""

    pitchLineSelection: adsk.core.SelectionCommandInput = inputs.itemById('tb_pitch_circles')
    beltTypeInp:        adsk.core.DropDownCommandInput  = inputs.itemById('tb_belt_type')
    beltWidthInp:       adsk.core.DropDownCommandInput  = inputs.itemById('tb_belt_width')
    suppressTeethInp:   adsk.core.BoolValueCommandInput = inputs.itemById('tb_suppress_teeth')
    genPulleysInp:      adsk.core.BoolValueCommandInput  = inputs.itemById('tb_gen_pulleys')
    pulleyTeethInp:     adsk.core.BoolValueCommandInput  = inputs.itemById('tb_pulley_teeth')
    boreOffsetInp:      adsk.core.ValueCommandInput      = inputs.itemById('tb_bore_offset')

    # One width drives both the belt and its pulleys.
    width_mm      = _belt_width_mm(beltWidthInp)
    belt_width_cm = width_mm / 10.0
    bore_offset_cm = (boreOffsetInp.value if boreOffsetInp is not None
                      else BORE_OFFSET_DEFAULT_IN * 2.54)
    # Bore and 3D print adapter are chosen per pulley: index 0 = the first End Circle.
    bore_types, use_adapters = [], []
    for i in (1, 2):
        boreTypeInp: adsk.core.DropDownCommandInput  = inputs.itemById(f'tb_bore_type_{i}')
        adapterInp:  adsk.core.BoolValueCommandInput = inputs.itemById(f'tb_adapter_{i}')
        bore_types.append(boreTypeInp.selectedItem.name
                          if boreTypeInp is not None and boreTypeInp.selectedItem is not None
                          else BORE_HALF_HEX)
        use_adapters.append(adapterInp is not None and adapterInp.value)

    # Pulley 1's height: its bottom flange sits `offset_dist_cm` from the picked face.
    offsetFaceInp: adsk.core.SelectionCommandInput = inputs.itemById('tb_offset_face')
    offsetDistInp: adsk.core.ValueCommandInput     = inputs.itemById('tb_offset_dist')
    offsetFlangeInp: adsk.core.DropDownCommandInput = inputs.itemById('tb_offset_flange')
    offset_dist_cm = offsetDistInp.value if offsetDistInp is not None else 0.0
    offset_flange  = (offsetFlangeInp.selectedItem.name
                      if offsetFlangeInp is not None and offsetFlangeInp.selectedItem is not None
                      else OFFSET_FLANGE_BOTTOM)
    offset_face = None
    if offsetFaceInp is not None and offsetFaceInp.selectionCount > 0:
        try:
            offset_face = offsetFaceInp.selection(0).entity
        except RuntimeError:
            pass    # the count can run ahead of the indexer mid-click (LESSONS_LEARNED.md)

    if pitchLineSelection.selectionCount < 2:
        futil.popup_error('Parts Gen: please select two pitch circles for the Timing Belt.')
        return

    # Save entity references now — creating a new component will clear the selection
    userSelections = [pitchLineSelection.selection(i).entity
                      for i in range(pitchLineSelection.selectionCount)]

    originalSketch: adsk.fusion.Sketch = userSelections[0].parentSketch

    design    = adsk.fusion.Design.cast(app.activeProduct)
    rootComp  = design.rootComponent
    start_marker = design.timeline.markerPosition
    trans     = adsk.core.Matrix3D.create()
    try:
        workingOcc  = rootComp.occurrences.addNewComponent(trans)
    except RuntimeError:
        futil.popup_error(
            'Cannot create belt: this document is in Part Design mode, '
            'which only supports a single component.\n\n'
            'Please open or create an Assembly document and try again.'
        )
        return
    workingComp = workingOcc.component

    try:
        sketch = workingComp.sketches.add(originalSketch.referencePlane, workingOcc)
        sketch.name = 'TimingBelt'

        if beltTypeInp.selectedItem.index == 0:
            beltPitchLength = 5
            beltThickness   = 0.174
        else:
            beltPitchLength = 3
            beltThickness   = 0.126

        # Belt only supports two directly-selected SketchCircles
        if userSelections[0].objectType != adsk.fusion.SketchCircle.classType():
            futil.popup_error('Parts Gen: please select two pitch circles (not a line).')
            workingOcc.deleteMe()
            return

        projList1 = sketch.include(userSelections[0])
        projList2 = sketch.include(userSelections[1])
        circle1_proj = projList1.item(0)
        circle2_proj = projList2.item(0)

        # Guard against coincident/near-coincident pitch circles — _buildPitchLoop divides
        # by the center-to-center distance, so this must be checked before it runs (mirrors
        # the same check already used on rebuild in _update_belt_name).
        p1_c, p2_c = circle1_proj.centerSketchPoint.geometry, circle2_proj.centerSketchPoint.geometry
        cc_dist = math.sqrt((p2_c.x - p1_c.x) ** 2 + (p2_c.y - p1_c.y) ** 2)
        if cc_dist < abs(circle1_proj.radius - circle2_proj.radius) + 1e-6:
            futil.popup_error(
                'Parts Gen: the two selected pitch circles are coincident or one is '
                'inside the other — cannot build a belt loop.'
            )
            workingOcc.deleteMe()
            return

        joint_offset_cm = _cc_joint_offset_cm(workingOcc, circle1_proj, offset_face,
                                              offset_dist_cm, offset_flange, belt_width_cm)
        if joint_offset_cm is None:
            if not is_preview:
                futil.popup_error('Parts Gen: the "Offset From" face must be parallel to the '
                                  'C-C sketch.')
            workingOcc.deleteMe()
            return

        PitchLoop = createPitchLoopFromSketchCircles(sketch, circle1_proj, circle2_proj)

        pathCurves = adsk.core.ObjectCollection.create()
        for curve in PitchLoop:
            pathCurves.add(curve)          # native SketchCurve — keeps parametric link alive

        curveLength = sum(curve.length for curve in PitchLoop)
        toothCount  = int(curveLength * 10 / beltPitchLength + 0.5)
        futil.log(f'Belt: loop length={curveLength:.4f}, teeth={toothCount}')

        if beltPitchLength == 5:
            comp_name = f'Belt_HTD_5mm-{toothCount}Tx{width_mm}mm'
        else:
            comp_name = f'Belt_GT2_3mm-{toothCount}Tx{width_mm}mm'
        workingComp.name = comp_name

        customNameInp = inputs.itemById('custom_name')
        custom_name = customNameInp.value.strip() if customNameInp is not None else ''
        if custom_name:
            workingComp.name = comp_name = custom_name

        # Build the belt thickness offset around the pitch loop
        half_belt_thickness = adsk.core.ValueInput.createByReal(beltThickness / 2)
        geoConstraints      = sketch.geometricConstraints
        curves              = list(PitchLoop)

        offsetInput = geoConstraints.createOffsetInput(curves, half_belt_thickness)
        geoConstraints.addTwoSidesOffset(offsetInput, True)

        profile_areas = _profile_areas(sketch)
        futil.log(f'Belt offset created {len(profile_areas)} profiles')
        if len(profile_areas) < 2:
            futil.popup_error('Parts Gen: belt offset profiles not created correctly.')
            workingOcc.deleteMe()
            return

        # Non-final preview (suppress_teeth is off): just show the shell; execute will finalize.
        # Returns before the tooth-profile sketch, which only the tooth pattern uses.
        if is_preview and not (suppressTeethInp and suppressTeethInp.value):
            extrudeBeltPreview(sketch, pathCurves, belt_width_cm, profile_areas)
            return

        # The largest profile is the region inside the belt; its loop is the belt's inner face
        insideLoop = max(profile_areas, key=lambda pa: pa[1])[0].profileLoops.item(0)

        if insideLoop is None:
            futil.popup_error('Parts Gen: could not find belt inside loop.')
            workingOcc.deleteMe()
            return

        (lineCurve, lineNormal, toothAnchorPoint) = findToothAnchor(insideLoop)

        if beltPitchLength == 5:
            baseLine = createHTD_5mmProfile(sketch)
        else:
            baseLine = createGT2_3mmProfile(sketch)

        geoConstraints.addCoincident(baseLine.startSketchPoint, toothAnchorPoint)
        angleDim = sketch.sketchDimensions.addAngularDimension(
            baseLine, lineCurve, baseLine.startSketchPoint.geometry)
        angleDim.value = 0.1
        angleDim.deleteMe()
        geoConstraints.addCollinear(baseLine, lineCurve)

        # Final creation: suppress_teeth uses preview extrude (no patterning); otherwise full belt.
        if suppressTeethInp and suppressTeethInp.value:
            extrudeBeltPreview(sketch, pathCurves, belt_width_cm)
        else:
            extrudeBelt(sketch, pathCurves, belt_width_cm, toothCount, beltPitchLength)

        # Save attributes so the right-click Edit command can restore the dialog
        try:
            attrs = workingComp.attributes
            attrs.add(ATTR_GROUP, ATTR_PART_TYPE,     'Timing Belt')
            attrs.add(ATTR_GROUP, ATTR_BELT_TYPE,     beltTypeInp.selectedItem.name)
            attrs.add(ATTR_GROUP, ATTR_BELT_WIDTH,    f'{width_mm} mm')
            attrs.add(ATTR_GROUP, ATTR_BELT_SUPPRESS, str(suppressTeethInp.value))
            attrs.add(ATTR_GROUP, ATTR_BELT_LOOP_LENGTH, str(round(curveLength, 8)))
            attrs.add(ATTR_GROUP, ATTR_BELT_GEN_PULLEYS,  str(genPulleysInp.value  if genPulleysInp  is not None else True))
            attrs.add(ATTR_GROUP, ATTR_BELT_PULLEY_TEETH, str(pulleyTeethInp.value if pulleyTeethInp is not None else False))
            if boreOffsetInp is not None:
                attrs.add(ATTR_GROUP, ATTR_BELT_BORE_OFFSET, boreOffsetInp.expression)
            for i in (1, 2):
                attrs.add(ATTR_GROUP, f'{ATTR_BELT_BORE_TYPE}_{i}', bore_types[i - 1])
                attrs.add(ATTR_GROUP, f'{ATTR_BELT_ADAPTER}_{i}',   str(use_adapters[i - 1]))
            if custom_name:
                attrs.add(ATTR_GROUP, ATTR_CUSTOM_NAME, custom_name)
            if offsetDistInp is not None:
                attrs.add(ATTR_GROUP, ATTR_BELT_OFFSET_EXPR, offsetDistInp.expression)
            attrs.add(ATTR_GROUP, ATTR_BELT_JOINT_OFFSET, str(round(joint_offset_cm, 8)))
            attrs.add(ATTR_GROUP, ATTR_BELT_OFFSET_FLANGE, offset_flange)
            for key, entity in [(ATTR_BELT_OFFSET_FACE, offset_face),
                                (f'{ATTR_BELT_CC_CIRCLE}_1', userSelections[0]),
                                (f'{ATTR_BELT_CC_CIRCLE}_2', userSelections[1])]:
                try:
                    if entity is not None:
                        attrs.add(ATTR_GROUP, key, entity.entityToken)
                except Exception:
                    futil.log(f'PartsGen: could not save belt attribute {key}')
        except Exception:
            futil.log('PartsGen: failed to save belt attributes')

        # Auto-generate matching pulleys for both pitch circles (optional).
        gen_pulleys    = genPulleysInp  is not None and genPulleysInp.value
        pulley_teeth   = pulleyTeethInp is not None and pulleyTeethInp.value
        if gen_pulleys:
            # proj_circles are the projected SketchCircles inside the belt sketch — they
            # are live parametric references so the pulleys follow when C-C distance changes.
            proj_circles = [circle1_proj, circle2_proj]
            for i, circle in enumerate(userSelections[:2]):
                try:
                    n_pulley_teeth = int(circle.radius * 20 * math.pi / beltPitchLength + 0.5)
                    futil.log(f'Belt: auto-pulley {i+1} — radius={circle.radius:.4f} cm, teeth={n_pulley_teeth}')
                    if n_pulley_teeth < 8:
                        futil.log(f'Belt: skipping auto-pulley {i+1} — tooth count {n_pulley_teeth} too small')
                        continue
                    pulley = create_pulley_for_belt(beltPitchLength, n_pulley_teeth, belt_width_cm,
                                                    workingOcc, proj_circles[i], pulley_teeth,
                                                    circle_index=i, parent_comp=workingComp,
                                                    bore_type=bore_types[i],
                                                    bore_offset_cm=bore_offset_cm,
                                                    use_adapter=use_adapters[i],
                                                    is_preview=is_preview)
                    # ...and to the user's own C-C circle, which places the whole belt.
                    if pulley is not None:
                        _add_cc_joint(workingOcc, pulley, circle, i, joint_offset_cm, is_preview)
                except Exception:
                    futil.handle_error(f'PartsGen: auto-pulley {i+1} failed', show_message_box=True)

        # Group belt + all auto-generated pulleys into one timeline entry
        futil.group_timeline_features(design, start_marker, comp_name)
    except Exception:
        try:
            workingOcc.deleteMe()
        except Exception:
            pass
        futil.handle_error('PartsGen _create_belt', show_message_box=True)


# ---------------------------------------------------------------------------
# Belt extrude helpers
# ---------------------------------------------------------------------------

def _profile_areas(sketch: adsk.fusion.Sketch) -> list:
    """(profile, area) for every profile in the sketch -- areaProperties() is slow, so the
    belt helpers compute it once per profile and reuse it."""
    profiles = sketch.profiles
    result = []
    for i in range(profiles.count):
        profile = profiles.item(i)
        result.append((profile, profile.areaProperties().area))
    return result


def extrudeBeltPreview(sketch: adsk.fusion.Sketch,
                       path: adsk.core.ObjectCollection,
                       beltWidth: float,
                       profile_areas: list = None):
    """Extrude only the belt shell (no tooth patterning) for live preview.

    `profile_areas` is _profile_areas(sketch) when the caller already has it (it must be
    current -- drawing the tooth profile afterwards changes the profiles)."""
    workingComp = sketch.parentComponent
    if profile_areas is None:
        profile_areas = _profile_areas(sketch)

    beltLoop = None
    if len(profile_areas) >= 3:
        # Neither the smallest (the tooth) nor the largest (inside the belt) area
        areas   = [a for _, a in profile_areas]
        maxArea = max(areas)
        minArea = min(areas)
        beltLoop = next((p for p, a in profile_areas if minArea < a < maxArea), None)
    else:
        # No tooth profile yet (the teeth-on preview): the belt shell is the ring between
        # the two offset loops -- the only profile with two loops.
        beltLoop = next((p for p, _ in profile_areas if p.profileLoops.count == 2), None)

    if beltLoop is None:
        return

    extrudes      = workingComp.features.extrudeFeatures
    beltWidthValue = adsk.core.ValueInput.createByReal(beltWidth)
    extrudes.addSimple(beltLoop, beltWidthValue, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)


def extrudeBelt(sketch: adsk.fusion.Sketch,
                path: adsk.core.ObjectCollection,
                beltWidth: float,
                toothCount: int,
                beltPitchMM: int):
    """Extrude the belt shell and then path-pattern the tooth profile."""
    workingComp = sketch.parentComponent

    profile_areas = _profile_areas(sketch)
    areas   = [a for _, a in profile_areas]
    maxArea = max(areas, default=0)
    minArea = min(areas, default=9999999)

    beltLoop    = None
    profileLoop = None
    for profile, a in profile_areas:
        if minArea < a < maxArea:
            beltLoop = profile
        elif a < maxArea:
            profileLoop = profile

    if beltLoop is None or profileLoop is None:
        futil.log('extrudeBelt: could not identify belt/tooth profiles')
        return

    extrudes       = workingComp.features.extrudeFeatures
    beltWidthValue = adsk.core.ValueInput.createByReal(beltWidth)
    extrudeBeltFeat  = extrudes.addSimple(beltLoop,    beltWidthValue, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    extrudeToothFeat = extrudes.addSimple(profileLoop, beltWidthValue, adsk.fusion.FeatureOperations.JoinFeatureOperation)

    pathPatterns  = workingComp.features.pathPatternFeatures
    beltPitch     = adsk.core.ValueInput.createByReal(beltPitchMM / 10.0)  # mm → cm
    toothCountVI  = adsk.core.ValueInput.createByReal(toothCount)
    patternCol    = adsk.core.ObjectCollection.create()
    patternCol.add(extrudeToothFeat)

    if config.DEBUG:    # the dumper makes its API calls even when log() drops the output
        futil.print_SketchObjectCollection(path)
    # Not Path.create: the belt sketch's curves are native to the belt sub-component, and
    # Path.create resolves them from the root and throws InternalValidationError
    # (Utils::getObjectPath). The component's own createPath takes them as they are.
    patternPath   = workingComp.features.createPath(path, False)   # False = no chaining
    toothPatInput = pathPatterns.createInput(
        patternCol, patternPath, toothCountVI, beltPitch,
        adsk.fusion.PatternDistanceType.SpacingPatternDistanceType,
    )
    toothPatInput.isOrientationAlongPath  = True
    toothPatInput.patternComputeOption    = adsk.fusion.PatternComputeOptions.IdenticalPatternCompute
    pathPatterns.add(toothPatInput)


# ---------------------------------------------------------------------------
# Pitch loop construction
# ---------------------------------------------------------------------------

def createPitchLoopFromSketchCircles(sketch: adsk.fusion.Sketch,
                                     circle1: adsk.fusion.SketchCircle,
                                     circle2: adsk.fusion.SketchCircle) -> adsk.core.ObjectCollection:
    """Build the pitch-loop tangent lines and end arcs from already-projected SketchCircles.

    Constrains the tangent geometry directly to the supplied circles so that
    the belt updates when the source sketch is edited.
    """
    geoConstraints = sketch.geometricConstraints
    circle1.isConstruction = True
    circle2.isConstruction = True
    return _buildPitchLoop(sketch, geoConstraints, circle1, circle2)


def createPitchLoopFromCircles(sketch: adsk.fusion.Sketch,
                               c1: adsk.core.Circle3D,
                               c2: adsk.core.Circle3D) -> adsk.core.ObjectCollection:
    geoConstraints = sketch.geometricConstraints
    circle1 = sketch.sketchCurves.sketchCircles.addByCenterRadius(c1.center, c1.radius)
    circle1.isConstruction = True
    circle2 = sketch.sketchCurves.sketchCircles.addByCenterRadius(c2.center, c2.radius)
    circle2.isConstruction = True
    return _buildPitchLoop(sketch, geoConstraints, circle1, circle2)


def _buildPitchLoop(sketch: adsk.fusion.Sketch,
                    geoConstraints,
                    circle1: adsk.fusion.SketchCircle,
                    circle2: adsk.fusion.SketchCircle) -> adsk.core.ObjectCollection:
    CLstartPt = futil.toPoint2D(circle1.centerSketchPoint.geometry)
    CLendPt   = futil.toPoint2D(circle2.centerSketchPoint.geometry)
    CLnormal  = futil.lineNormal(CLstartPt, CLendPt)

    T1startPt = futil.addPoint2D(CLstartPt, futil.multVector2D(CLnormal,  circle1.radius))
    T1endPt   = futil.addPoint2D(CLendPt,   futil.multVector2D(CLnormal,  circle2.radius))
    tangentLine1 = sketch.sketchCurves.sketchLines.addByTwoPoints(
        futil.toPoint3D(T1startPt), futil.toPoint3D(T1endPt))
    tangentLine1.isConstruction = True
    geoConstraints.addCoincident(tangentLine1.startSketchPoint, circle1)
    geoConstraints.addCoincident(tangentLine1.endSketchPoint,   circle2)
    geoConstraints.addTangent(tangentLine1, circle1)
    geoConstraints.addTangent(tangentLine1, circle2)

    T2startPt = futil.addPoint2D(CLstartPt, futil.multVector2D(CLnormal, -circle1.radius))
    T2endPt   = futil.addPoint2D(CLendPt,   futil.multVector2D(CLnormal, -circle2.radius))
    tangentLine2 = sketch.sketchCurves.sketchLines.addByTwoPoints(
        futil.toPoint3D(T2startPt), futil.toPoint3D(T2endPt))
    tangentLine2.isConstruction = True
    geoConstraints.addCoincident(tangentLine2.startSketchPoint, circle1)
    geoConstraints.addCoincident(tangentLine2.endSketchPoint,   circle2)
    geoConstraints.addTangent(tangentLine2, circle1)
    geoConstraints.addTangent(tangentLine2, circle2)

    arc1 = sketch.sketchCurves.sketchArcs.addByCenterStartEnd(
        circle1.centerSketchPoint, tangentLine1.startSketchPoint, tangentLine2.startSketchPoint)
    arc1.isConstruction = True
    try:
        geoConstraints.addTangent(arc1, tangentLine1)
    except Exception:
        pass

    arc2 = sketch.sketchCurves.sketchArcs.addByCenterStartEnd(
        circle2.centerSketchPoint, tangentLine2.endSketchPoint, tangentLine1.endSketchPoint)
    arc2.isConstruction = True
    try:
        geoConstraints.addTangent(arc2, tangentLine2)
    except Exception:
        pass

    return sketch.findConnectedCurves(tangentLine1)


# ---------------------------------------------------------------------------
# Tooth anchor finder
# ---------------------------------------------------------------------------

def findToothAnchor(insideLoop: adsk.fusion.ProfileLoop):
    """Find the straight-line edge and an endpoint on it to anchor the tooth profile."""
    bbox = insideLoop.profileCurves.item(0).sketchEntity.boundingBox
    for i in range(insideLoop.profileCurves.count):
        bbox.combine(insideLoop.profileCurves.item(i).sketchEntity.boundingBox)

    centroid = futil.BBCentroid(bbox)

    lineCurve       = None
    lineNormal      = adsk.core.Vector2D.create()
    toothAnchorPoint = None

    for i in range(insideLoop.profileCurves.count):
        curve = insideLoop.profileCurves.item(i).sketchEntity
        if config.DEBUG:
            futil.print_SketchCurve(curve)
        if curve.objectType == adsk.fusion.SketchLine.classType():
            curve: adsk.fusion.SketchLine = curve
            insideNormal = futil.sketchLineNormal(curve, centroid)
            if futil.toTheRightOf(futil.toLine2D(curve.geometry), centroid):
                lineCurve        = curve
                lineNormal       = insideNormal
                toothAnchorPoint = lineCurve.endSketchPoint
            else:
                lineCurve        = curve
                lineNormal       = insideNormal
                toothAnchorPoint = lineCurve.startSketchPoint
            futil.log(f'    Belt tooth anchor set, normal=({insideNormal.x:.3},{insideNormal.y:.3})')
            break

    return (lineCurve, lineNormal, toothAnchorPoint)


# ---------------------------------------------------------------------------
# HTD 5mm tooth profile
# ---------------------------------------------------------------------------

def createHTD_5mmProfile(sketch: adsk.fusion.Sketch) -> adsk.fusion.SketchLine:
    geoConstraints = sketch.geometricConstraints
    sketchCurves   = sketch.sketchCurves
    sketchDims     = sketch.sketchDimensions

    baseLineLength      = 0.385373
    filletRadius        = 0.043
    toothBumpRadius     = 0.15
    toothBumpOffset     = 0.054
    filletSweepAngle    = 1.6284       # 93.3 degrees
    toothBumpSweepAngle = -3.25548     # 186.525 degrees

    originPt = adsk.core.Point3D.create(0, 0, 0)
    baseLine = sketchCurves.sketchLines.addByTwoPoints(
        originPt, adsk.core.Point3D.create(baseLineLength, 0, 0))

    arcEndLine = sketchCurves.sketchLines.addByTwoPoints(
        baseLine.startSketchPoint, adsk.core.Point3D.create(0, filletRadius, 0))
    arcEndLine.isConstruction = True
    geoConstraints.addPerpendicular(arcEndLine, baseLine)
    textPt  = adsk.core.Point3D.create(-0.01, 0.02, 0)
    linDim  = sketchDims.addDistanceDimension(
        arcEndLine.startSketchPoint, arcEndLine.endSketchPoint,
        adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, textPt)
    linDim.value = filletRadius

    toothline = sketchCurves.sketchLines.addByTwoPoints(
        adsk.core.Point3D.create(baseLineLength / 2, 0, 0),
        adsk.core.Point3D.create(baseLineLength / 2, toothBumpOffset, 0))
    toothline.isConstruction = True
    geoConstraints.addPerpendicular(toothline, baseLine)
    geoConstraints.addCoincident(toothline.startSketchPoint, baseLine)
    textPt  = adsk.core.Point3D.create(baseLineLength / 2, toothBumpOffset, 0)
    textPt.translateBy(adsk.core.Vector3D.create(-0.02, 0, 0))
    linDim  = sketchDims.addDistanceDimension(
        toothline.startSketchPoint, toothline.endSketchPoint,
        adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, textPt)
    linDim.value = toothBumpOffset

    firstFillet = sketchCurves.sketchArcs.addByCenterStartSweep(
        arcEndLine.endSketchPoint, baseLine.startSketchPoint, filletSweepAngle)
    geoConstraints.addTangent(firstFillet, baseLine)
    geoConstraints.addCoincident(firstFillet.centerSketchPoint, arcEndLine.endSketchPoint)

    toothBump = sketchCurves.sketchArcs.addByCenterStartSweep(
        toothline.endSketchPoint, firstFillet.endSketchPoint, toothBumpSweepAngle)
    geoConstraints.addCoincident(toothBump.centerSketchPoint, toothline.endSketchPoint)
    geoConstraints.addTangent(firstFillet, toothBump)
    textPt = toothline.startSketchPoint.geometry.copy()
    textPt.translateBy(adsk.core.Vector3D.create(-0.05, 0.05, 0))
    cirDim = sketchDims.addRadialDimension(toothBump, textPt)
    cirDim.value = toothBumpRadius

    secondFillet = sketchCurves.sketchArcs.addByCenterStartSweep(
        adsk.core.Point3D.create(baseLineLength, filletRadius, 0),
        toothBump.startSketchPoint, filletSweepAngle)
    geoConstraints.addCoincident(secondFillet.endSketchPoint, baseLine.endSketchPoint)
    geoConstraints.addTangent(secondFillet, baseLine)
    geoConstraints.addTangent(secondFillet, toothBump)
    textPt = secondFillet.centerSketchPoint.geometry.copy()
    textPt.translateBy(adsk.core.Vector3D.create(0.05, 0.05, 0))
    arcDim = sketchDims.addRadialDimension(secondFillet, textPt)
    arcDim.value = filletRadius

    return baseLine


# ---------------------------------------------------------------------------
# GT2 3mm tooth profile
# ---------------------------------------------------------------------------

def createGT2_3mmProfile(sketch: adsk.fusion.Sketch) -> adsk.fusion.SketchLine:
    geoConstraints = sketch.geometricConstraints

    baseLineLength      = 0.2044505
    filletRadius        = 0.035
    toothBumpRadius     = 0.085
    toothBumpOffset     = 0.025
    filletSweepAngle    = 1.64322      # 94.14961 degrees
    toothBumpSweepAngle = -3.28806     # 188.3922 degrees

    originPt = adsk.core.Point3D.create(0, 0, 0)
    baseLine = sketch.sketchCurves.sketchLines.addByTwoPoints(
        originPt, adsk.core.Point3D.create(baseLineLength, 0, 0))

    arcEndLine = sketch.sketchCurves.sketchLines.addByTwoPoints(
        baseLine.startSketchPoint, adsk.core.Point3D.create(0, filletRadius, 0))
    arcEndLine.isConstruction = True
    geoConstraints.addPerpendicular(arcEndLine, baseLine)
    textPt  = adsk.core.Point3D.create(-0.01, 0.02, 0)
    linDim  = sketch.sketchDimensions.addDistanceDimension(
        arcEndLine.startSketchPoint, arcEndLine.endSketchPoint,
        adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, textPt)
    linDim.value = filletRadius

    toothline = sketch.sketchCurves.sketchLines.addByTwoPoints(
        adsk.core.Point3D.create(baseLineLength / 2, 0, 0),
        adsk.core.Point3D.create(baseLineLength / 2, toothBumpOffset, 0))
    toothline.isConstruction = True
    geoConstraints.addPerpendicular(toothline, baseLine)
    geoConstraints.addCoincident(toothline.startSketchPoint, baseLine)
    textPt = adsk.core.Point3D.create(baseLineLength / 2, toothBumpOffset, 0)
    textPt.translateBy(adsk.core.Vector3D.create(-0.02, 0, 0))
    linDim = sketch.sketchDimensions.addDistanceDimension(
        toothline.startSketchPoint, toothline.endSketchPoint,
        adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, textPt)
    linDim.value = toothBumpOffset

    firstFillet = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        arcEndLine.endSketchPoint, baseLine.startSketchPoint, filletSweepAngle)
    geoConstraints.addTangent(firstFillet, baseLine)
    geoConstraints.addCoincident(firstFillet.centerSketchPoint, arcEndLine.endSketchPoint)

    toothBump = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        toothline.endSketchPoint, firstFillet.endSketchPoint, toothBumpSweepAngle)
    geoConstraints.addCoincident(toothBump.centerSketchPoint, toothline.endSketchPoint)
    geoConstraints.addTangent(firstFillet, toothBump)
    textPt = toothline.startSketchPoint.geometry.copy()
    textPt.translateBy(adsk.core.Vector3D.create(-0.05, 0.05, 0))
    cirDim = sketch.sketchDimensions.addRadialDimension(toothBump, textPt)
    cirDim.value = toothBumpRadius

    secondFillet = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        adsk.core.Point3D.create(baseLineLength, filletRadius, 0),
        toothBump.startSketchPoint, filletSweepAngle)
    geoConstraints.addCoincident(secondFillet.endSketchPoint, baseLine.endSketchPoint)
    geoConstraints.addTangent(secondFillet, baseLine)
    geoConstraints.addTangent(secondFillet, toothBump)
    textPt = secondFillet.centerSketchPoint.geometry.copy()
    textPt.translateBy(adsk.core.Vector3D.create(0.05, 0.05, 0))
    arcDim = sketch.sketchDimensions.addRadialDimension(secondFillet, textPt)
    arcDim.value = filletRadius

    return baseLine
