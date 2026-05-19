"""
pulley_gen.py  —  Timing Pulley geometry for PartsGen

Contains the geometry creation functions for HTD and GT2 timing pulleys,
plus the _create_pulley() helper that PartsGen/entry.py calls when the
user selects "Timing Pulley" as the Part Type.
"""

import adsk.core
import adsk.fusion
import math
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Belt type labels (must match the dropdown items in entry.py)
# ---------------------------------------------------------------------------
BELT_HTD = 'HTD 5mm Pitch'
BELT_GT2 = 'GT2 3mm Pitch'

# ---------------------------------------------------------------------------
# Attribute keys (written to the component so the edit command can restore)
# ---------------------------------------------------------------------------
ATTR_GROUP              = 'FRCTools_PartsGen'
ATTR_PART_TYPE          = 'part_type'
ATTR_PULLEY_BELT_TYPE        = 'pulley_belt_type'
ATTR_PULLEY_TOOTH_COUNT     = 'pulley_tooth_count'
ATTR_PULLEY_BELT_WIDTH      = 'pulley_belt_width'
ATTR_PULLEY_SHOW_TEETH      = 'pulley_show_teeth'
ATTR_PULLEY_BELT_COMP_TOKEN  = 'pulley_belt_comp_token'
ATTR_PULLEY_PITCH_CIRCLE_IDX = 'pulley_pitch_circle_index'

# ---------------------------------------------------------------------------
# Flange dimensions (Fusion 360 uses centimetres internally)
# ---------------------------------------------------------------------------
FLANGE_OD_OFFSET_CM  = 0.196 * 2.54   # flange OD = tooth OD + 0.196 in
FLANGE_THICKNESS_CM  = 0.053 * 2.54   # 0.053 in  →  0.13462 cm
HEX_BORE_FLATS_CM    = 0.5   * 2.54   # 0.5 in across flats →  1.27 cm
LABEL_TEXT_HEIGHT_CM = 0.15 * 2.54    # 0.15 in font height for engraved label
LABEL_ENGRAVE_CM     = 0.02 * 2.54    # 0.02 in engraving depth


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _outer_diameter_cm(belt_pitch_mm: int, n_teeth: int) -> float:
    """Return the tooth outer diameter in cm without creating any sketch geometry."""
    if belt_pitch_mm == 5:  # HTD 5mm
        return (n_teeth * 5.0 / math.pi - 1.74) / 10
    else:  # GT2 3mm
        return (n_teeth * 3.0 / math.pi - 2 * 0.381) / 10


# ---------------------------------------------------------------------------
# Timeline grouping helper (duplicated from entry.py to avoid circular import)
# ---------------------------------------------------------------------------

def _group_timeline_features(design: adsk.fusion.Design, start_marker: int, group_name: str):
    """Group all timeline items from start_marker to the current marker into a named group."""
    try:
        timeline = design.timeline
        end_marker = timeline.markerPosition - 1
        if end_marker > start_marker:
            group = timeline.timelineGroups.add(start_marker, end_marker)
            group.name = group_name
    except Exception:
        futil.log(f'PartsGen: failed to create timeline group "{group_name}"')


# ---------------------------------------------------------------------------
# Flange helper — adds a disk flange on each side of the pulley body
# ---------------------------------------------------------------------------

def _add_flanges(comp: adsk.fusion.Component, belt_width_cm: float, tooth_od_cm: float):
    """Extrude a flange disk on each side of the pulley body.

    Flange OD = tooth_od_cm + FLANGE_OD_OFFSET_CM (0.196 in larger than tooth OD).
    Bottom flange: from Z=0 downward by FLANGE_THICKNESS_CM.
    Top flange:    from Z=belt_width_cm upward by FLANGE_THICKNESS_CM.
    Both are joined to the existing pulley body.
    """
    extrudes      = comp.features.extrudeFeatures
    flange_radius = (tooth_od_cm + FLANGE_OD_OFFSET_CM) / 2

    # --- Bottom flange (extruded in -Z from component XY plane) ---
    sk_bot = comp.sketches.add(comp.xYConstructionPlane)
    sk_bot.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(0, 0, 0), flange_radius)
    extrudes.addSimple(
        sk_bot.profiles.item(0),
        adsk.core.ValueInput.createByReal(-FLANGE_THICKNESS_CM),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
    )

    # --- Top flange — offset construction plane at Z = belt_width_cm ---
    planes      = comp.constructionPlanes
    plane_input = planes.createInput()
    plane_input.setByOffset(
        comp.xYConstructionPlane,
        adsk.core.ValueInput.createByReal(belt_width_cm)
    )
    top_plane = planes.add(plane_input)

    sk_top = comp.sketches.add(top_plane)
    sk_top.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(0, 0, 0), flange_radius)
    extrudes.addSimple(
        sk_top.profiles.item(0),
        adsk.core.ValueInput.createByReal(FLANGE_THICKNESS_CM),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
    )


def _add_hex_bore(comp: adsk.fusion.Component, belt_width_cm: float):
    """Cut a 0.5 in hex bore through the entire pulley (flanges + belt body)."""
    # Circumscribed radius (center to corner) from across-flats dimension
    circumradius = (HEX_BORE_FLATS_CM / 2) / math.cos(math.radians(30))

    # Sketch on a plane at the bottom of the lower flange
    planes      = comp.constructionPlanes
    plane_input = planes.createInput()
    plane_input.setByOffset(
        comp.xYConstructionPlane,
        adsk.core.ValueInput.createByReal(-FLANGE_THICKNESS_CM)
    )
    bot_plane = planes.add(plane_input)

    sk    = comp.sketches.add(bot_plane)
    lines = sk.sketchCurves.sketchLines
    # Flat-top orientation: corners at 30°, 90°, 150°, 210°, 270°, 330°
    pts = [
        adsk.core.Point3D.create(
            circumradius * math.cos(math.radians(30 + i * 60)),
            circumradius * math.sin(math.radians(30 + i * 60)),
            0,
        )
        for i in range(6)
    ]
    for i in range(6):
        lines.addByTwoPoints(pts[i], pts[(i + 1) % 6])

    # Cut through bottom flange + belt body + top flange
    total_cm = 2 * FLANGE_THICKNESS_CM + belt_width_cm
    comp.features.extrudeFeatures.addSimple(
        sk.profiles.item(0),
        adsk.core.ValueInput.createByReal(total_cm),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
    )


def _engrave_label_face(comp: adsk.fusion.Component, label: str,
                        z_offset_cm: float, cut_direction, mirror: bool):
    """Engrave *label* on one flange face.

    Args:
        z_offset_cm:   Z position of the sketch plane (in component local space).
        cut_direction: ExtentDirections constant — Negative cuts into the body
                       from the top face; Positive cuts in from the bottom face.
        mirror:        When True the text corners are swapped in X so the label
                       reads correctly when viewed from the outside of the bottom
                       flange (i.e. from the -Z direction).
    """
    planes      = comp.constructionPlanes
    plane_input = planes.createInput()
    plane_input.setByOffset(
        comp.xYConstructionPlane,
        adsk.core.ValueInput.createByReal(z_offset_cm)
    )
    plane = planes.add(plane_input)
    sk    = comp.sketches.add(plane)

    hex_circumradius = (HEX_BORE_FLATS_CM / 2) / math.cos(math.radians(30))
    y_bot = hex_circumradius + 0.02
    y_top = y_bot + LABEL_TEXT_HEIGHT_CM * 1.2

    # Mirroring the X corners causes the text to appear reversed in the sketch,
    # which reads correctly when the face is viewed from the outside (-Z side).
    if mirror:
        corner1 = adsk.core.Point3D.create( 1.5, y_bot, 0)
        corner2 = adsk.core.Point3D.create(-1.5, y_top, 0)
    else:
        corner1 = adsk.core.Point3D.create(-1.5, y_bot, 0)
        corner2 = adsk.core.Point3D.create( 1.5, y_top, 0)

    text_input = sk.sketchTexts.createInput2(label, LABEL_TEXT_HEIGHT_CM)
    text_input.setAsMultiLine(
        corner1, corner2,
        adsk.core.HorizontalAlignments.CenterHorizontalAlignment,
        adsk.core.VerticalAlignments.MiddleVerticalAlignment,
        0
    )
    sk.sketchTexts.add(text_input).explode()

    n_profiles = sk.profiles.count
    if n_profiles == 0:
        futil.popup_error(
            f'PartsGen label: sketch text "{label}" generated 0 profiles — '
            f'cannot engrave. (text height = {LABEL_TEXT_HEIGHT_CM:.3f} cm)'
        )
        return

    # Engrave each character profile individually so that profiles which fall
    # outside the body boundary are skipped rather than aborting the whole cut.
    extrudes = comp.features.extrudeFeatures
    engraved = 0
    for i in range(n_profiles):
        try:
            ext_in = extrudes.createInput(
                sk.profiles.item(i),
                adsk.fusion.FeatureOperations.CutFeatureOperation
            )
            ext_in.setOneSideExtent(
                adsk.fusion.DistanceExtentDefinition.create(
                    adsk.core.ValueInput.createByReal(LABEL_ENGRAVE_CM)
                ),
                cut_direction
            )
            extrudes.add(ext_in)
            engraved += 1
        except Exception:
            pass  # profile does not intersect any body — skip it

    if engraved == 0:
        futil.popup_error(
            f'PartsGen label: {n_profiles} profile(s) found but none could be '
            f'engraved on the {"bottom" if mirror else "top"} face. '
            f'(z_offset={z_offset_cm:.3f} cm)'
        )


def _add_label(comp: adsk.fusion.Component, belt_width_cm: float, n_teeth: int):
    """Engrave tooth count on both flange faces (e.g. \"18T\").

    Top face: normal orientation, readable from above (+Z).
    Bottom face: mirrored so the label reads correctly from below (-Z).
    """
    try:
        label = f'{n_teeth}T'
        _engrave_label_face(
            comp, label,
            z_offset_cm  = belt_width_cm + FLANGE_THICKNESS_CM,
            cut_direction = adsk.fusion.ExtentDirections.NegativeExtentDirection,
            mirror        = False,
        )
        _engrave_label_face(
            comp, label,
            z_offset_cm  = -FLANGE_THICKNESS_CM,
            cut_direction = adsk.fusion.ExtentDirections.PositiveExtentDirection,
            mirror        = True,
        )
    except Exception:
        futil.handle_error('PartsGen _add_label', show_message_box=True)


# ---------------------------------------------------------------------------
# Public creation entry point — called by PartsGen/entry.py
# ---------------------------------------------------------------------------

def _create_pulley(inputs: adsk.core.CommandInputs):
    """Create a timing pulley component from the PartsGen dialog inputs."""

    beltType:      adsk.core.DropDownCommandInput  = inputs.itemById('belt_type')
    toothCount:    adsk.core.ValueCommandInput     = inputs.itemById('tooth_count')
    beltWidth:     adsk.core.ValueCommandInput     = inputs.itemById('belt_width')
    showTeethInp:  adsk.core.BoolValueCommandInput = inputs.itemById('pulley_show_teeth')

    show_teeth = showTeethInp.value if showTeethInp is not None else False

    design    = adsk.fusion.Design.cast(app.activeProduct)
    rootComp  = design.rootComponent
    start_marker = design.timeline.markerPosition
    trans     = adsk.core.Matrix3D.create()
    workingOcc  = rootComp.occurrences.addNewComponent(trans)
    workingComp = workingOcc.component

    n_teeth  = int(toothCount.value)
    width_mm = int(beltWidth.value * 10)   # value is in cm; *10 gives mm

    if beltType.selectedItem.index == 0:
        comp_name   = f'Pulley_HTD_5mm-{n_teeth}Tx{width_mm}mm'
        belt_pitch  = 5
        geometry_fn = createHTDPulleyGeometry
    else:
        comp_name   = f'Pulley_GT2_3mm-{n_teeth}Tx{width_mm}mm'
        belt_pitch  = 3
        geometry_fn = createGT2PulleyGeometry

    workingComp.name = comp_name

    extrudes   = workingComp.features.extrudeFeatures
    widthValue = adsk.core.ValueInput.createByReal(beltWidth.value)

    if show_teeth:
        # Full toothed profile
        sketchPlane = rootComp.xYConstructionPlane
        sketch = workingComp.sketches.add(sketchPlane, workingOcc)
        outer_diameter_cm = geometry_fn(sketch, belt_pitch, n_teeth)

        if sketch.profiles.count != 1:
            futil.popup_error(
                f'Parts Gen: Timing Pulley sketch has {sketch.profiles.count} profiles '
                f'(expected 1).  The tooth geometry may not have closed correctly.'
            )
            workingOcc.deleteMe()
            return

        extrudes.addSimple(
            sketch.profiles.item(0),
            widthValue,
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        )
    else:
        # Smooth cylinder — diameter equals the tooth outer diameter
        outer_diameter_cm = _outer_diameter_cm(belt_pitch, n_teeth)
        sk_cyl = workingComp.sketches.add(rootComp.xYConstructionPlane, workingOcc)
        sk_cyl.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(0, 0, 0), outer_diameter_cm / 2
        )
        extrudes.addSimple(
            sk_cyl.profiles.item(0),
            widthValue,
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        )

    _add_flanges(workingComp, beltWidth.value, outer_diameter_cm)
    _add_hex_bore(workingComp, beltWidth.value)
    _add_label(workingComp, beltWidth.value, n_teeth)

    # Save attributes so the right-click Edit command can restore the dialog
    try:
        attrs = workingComp.attributes
        attrs.add(ATTR_GROUP, ATTR_PART_TYPE,           'Timing Pulley')
        attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_TYPE,    beltType.selectedItem.name)
        attrs.add(ATTR_GROUP, ATTR_PULLEY_TOOTH_COUNT,  str(n_teeth))
        attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_WIDTH,   beltWidth.expression)
        attrs.add(ATTR_GROUP, ATTR_PULLEY_SHOW_TEETH,   str(show_teeth))
    except Exception:
        futil.log('PartsGen: failed to save pulley attributes')

    _group_timeline_features(design, start_marker, comp_name)


def create_pulley_for_belt(belt_pitch_mm: int, n_teeth: int, belt_width_cm: float,
                           belt_occ: adsk.fusion.Occurrence = None,
                           proj_circle: adsk.fusion.SketchCircle = None,
                           show_teeth: bool = False,
                           circle_index: int = 0,
                           parent_comp: adsk.fusion.Component = None):
    """Create a timing pulley component from raw parameters.

    Called by belt_gen._create_belt() to auto-generate matched pulleys when
    a belt is created.  Uses the same geometry functions as _create_pulley()
    but accepts numeric values instead of CommandInputs.

    A parametric revolute Joint is created between the pulley and belt_occ
    using proj_circle.centerSketchPoint as the live anchor, so the pulley
    automatically repositions when the belt's C-C distance is edited.
    """
    design    = adsk.fusion.Design.cast(app.activeProduct)
    rootComp  = design.rootComponent
    parent    = parent_comp if parent_comp is not None else rootComp
    start_marker = design.timeline.markerPosition

    # Pre-position the occurrence at the pitch-circle centre so the two pulleys
    # do not overlap during geometry creation (label engraving in particular).
    # Without this, both pulleys start at the origin and the label cut from one
    # pulley visually overlaps the other, making the tooth count hard to read.
    # The parametric Joint created below locks the position parametrically so
    # the pulley follows automatically when the C-C distance is edited.
    trans = adsk.core.Matrix3D.create()
    if proj_circle is not None:
        c = proj_circle.centerSketchPoint.geometry
        trans.translation = adsk.core.Vector3D.create(c.x, c.y, 0.0)
    workingOcc  = parent.occurrences.addNewComponent(trans)
    workingComp = workingOcc.component

    width_mm = int(belt_width_cm * 10)   # cm → mm, then truncate

    if belt_pitch_mm == 5:
        comp_name      = f'Pulley_HTD_5mm-{n_teeth}Tx{width_mm}mm'
        belt_type_name = BELT_HTD
        geometry_fn    = createHTDPulleyGeometry
    else:
        comp_name      = f'Pulley_GT2_3mm-{n_teeth}Tx{width_mm}mm'
        belt_type_name = BELT_GT2
        geometry_fn    = createGT2PulleyGeometry

    workingComp.name = comp_name

    # Local XY plane — sketch geometry is in the component's local space and
    # is transformed to the pitch-circle centre via the occurrence transform above.
    extrudes   = workingComp.features.extrudeFeatures
    widthValue = adsk.core.ValueInput.createByReal(belt_width_cm)

    if show_teeth:
        sketch = workingComp.sketches.add(workingComp.xYConstructionPlane)
        outer_diameter_cm = geometry_fn(sketch, belt_pitch_mm, n_teeth)
        if sketch.profiles.count != 1:
            futil.popup_error(
                f'PartsGen: auto-pulley sketch has {sketch.profiles.count} profiles '
                f'(expected 1, n_teeth={n_teeth}, pitch={belt_pitch_mm}mm) — aborting.'
            )
            workingOcc.deleteMe()
            return
        extrudes.addSimple(sketch.profiles.item(0), widthValue,
                           adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        joint_circle = sketch.sketchCurves.sketchCircles.item(0)
    else:
        outer_diameter_cm = _outer_diameter_cm(belt_pitch_mm, n_teeth)
        sk_cyl = workingComp.sketches.add(workingComp.xYConstructionPlane)
        sk_cyl.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(0, 0, 0), outer_diameter_cm / 2)
        extrudes.addSimple(sk_cyl.profiles.item(0), widthValue,
                           adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        joint_circle = sk_cyl.sketchCurves.sketchCircles.item(0)

    _add_flanges(workingComp, belt_width_cm, outer_diameter_cm)
    _add_hex_bore(workingComp, belt_width_cm)
    _add_label(workingComp, belt_width_cm, n_teeth)

    try:
        attrs = workingComp.attributes
        attrs.add(ATTR_GROUP, ATTR_PART_TYPE,           'Timing Pulley')
        attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_TYPE,    belt_type_name)
        attrs.add(ATTR_GROUP, ATTR_PULLEY_TOOTH_COUNT,  str(n_teeth))
        attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_WIDTH,   f'{width_mm} mm')
        attrs.add(ATTR_GROUP, ATTR_PULLEY_SHOW_TEETH,   str(show_teeth))
        if belt_occ is not None:
            attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_COMP_TOKEN,  belt_occ.component.entityToken)
            attrs.add(ATTR_GROUP, ATTR_PULLEY_PITCH_CIRCLE_IDX, str(circle_index))
    except Exception:
        futil.log('PartsGen: failed to save auto-pulley attributes')

    # Parametric revolute Joint: pulley centre <-> projected pitch-circle centre in
    # the belt sketch.  When CCDistance edits the belt the projected point moves,
    # Fusion re-solves the joint, and the pulley repositions automatically.
    if proj_circle is not None and belt_occ is not None:
        try:
            pulley_ctx = adsk.core.ObjectCollection.create()
            pulley_ctx.add(workingOcc)
            belt_ctx = adsk.core.ObjectCollection.create()
            belt_ctx.add(belt_occ)
            # Use createForAssemblyContext so the joint targets this specific
            # occurrence rather than the component prototype.
            outer_circle = joint_circle.createForAssemblyContext(workingOcc)
            pulley_geom = adsk.fusion.JointGeometry.createByCurve(
                outer_circle,
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            belt_geom   = adsk.fusion.JointGeometry.createByCurve(
                proj_circle,
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            joint_input = parent.joints.createInput(pulley_geom, belt_geom)
            joint_input.setAsRevoluteJointMotion(
                adsk.fusion.JointDirections.ZAxisJointDirection)
            joint = parent.joints.add(joint_input)
            joint.name = f'{comp_name}_revolute'
        except Exception:
            futil.handle_error(f'PartsGen: joint for {comp_name}', show_message_box=True)

    _group_timeline_features(design, start_marker, comp_name)


# ---------------------------------------------------------------------------
# Geometry — HTD 5mm pitch
# ---------------------------------------------------------------------------

def createHTDPulleyGeometry(sketch: adsk.fusion.Sketch, beltPitchMM: float, toothCount: int):
    geoConstraints = sketch.geometricConstraints

    beltThickness = 1.74
    topRadius     = 0.43
    rootRadius    = 1.49
    rootHeight    = 2.06
    rootWidth     = 3.05

    pitch_diameter = toothCount * beltPitchMM / math.pi
    outer_diameter = pitch_diameter - beltThickness

    # Outer diameter construction circle
    centerPt    = adsk.core.Point3D.create()
    outerCircle = sketch.sketchCurves.sketchCircles.addByCenterRadius(centerPt, outer_diameter)
    outerCircle.isConstruction = True
    geoConstraints.addCoincident(outerCircle.centerSketchPoint, sketch.originPoint)

    textPoint = adsk.core.Point3D.create(-outer_diameter / 40, 0, 0)
    diameter  = sketch.sketchDimensions.addDiameterDimension(outerCircle, textPoint)
    diameter.value = outer_diameter / 10  # cm

    # Vertical construction line (centre → top of circle)
    endPt    = adsk.core.Point3D.create(0, outer_diameter / 20, 0)
    vertLine = sketch.sketchCurves.sketchLines.addByTwoPoints(outerCircle.centerSketchPoint, endPt)
    vertLine.isConstruction = True
    geoConstraints.addCoincident(vertLine.endSketchPoint, outerCircle)
    geoConstraints.addVertical(vertLine)

    # Pie construction line (centre → right of circle)
    endPt   = adsk.core.Point3D.create(outer_diameter / 20, 0, 0)
    pieLine = sketch.sketchCurves.sketchLines.addByTwoPoints(outerCircle.centerSketchPoint, endPt)
    pieLine.isConstruction = True
    geoConstraints.addCoincident(pieLine.endSketchPoint, outerCircle)
    textPoint = adsk.core.Point3D.create(outer_diameter / 40, outer_diameter / 40, 0)
    angleDim  = sketch.sketchDimensions.addAngularDimension(vertLine, pieLine, textPoint)
    angleDim.value = math.pi / toothCount

    # Tooth top arc
    topArc = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        outerCircle.centerSketchPoint, vertLine.endSketchPoint, -math.pi / toothCount / 4)
    geoConstraints.addConcentric(topArc, outerCircle)

    # Tooth top arc mirror
    topArcMirror = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        outerCircle.centerSketchPoint, vertLine.endSketchPoint, math.pi / toothCount / 4)
    geoConstraints.addConcentric(topArcMirror, outerCircle)
    geoConstraints.addSymmetry(topArc.startSketchPoint, topArcMirror.endSketchPoint, vertLine)

    # Top radius arc
    centerPt     = futil.offsetPoint3D(topArc.startSketchPoint.geometry, 0, -topRadius / 10, 0)
    topRadiusArc = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        centerPt, topArc.startSketchPoint, -math.pi / 4)
    geoConstraints.addTangent(topArc, topRadiusArc)
    textPoint = centerPt
    radius    = sketch.sketchDimensions.addRadialDimension(topRadiusArc, textPoint)
    radius.value = topRadius / 10  # cm

    # Top radius arc mirror
    centerPt          = futil.offsetPoint3D(topArcMirror.endSketchPoint.geometry, 0, -topRadius / 10, 0)
    topRadiusArcMirror = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        centerPt, topArcMirror.endSketchPoint, math.pi / 4)
    geoConstraints.addTangent(topArcMirror, topRadiusArcMirror)
    geoConstraints.addSymmetry(topRadiusArc.startSketchPoint, topRadiusArcMirror.endSketchPoint, vertLine)

    # Linear tooth segment
    endPt     = futil.offsetPoint3D(topRadiusArc.startSketchPoint.geometry, topRadius / 20, -topRadius / 20, 0)
    toothLine = sketch.sketchCurves.sketchLines.addByTwoPoints(topRadiusArc.startSketchPoint, endPt)
    geoConstraints.addTangent(toothLine, topRadiusArc)
    textPoint = futil.offsetPoint3D(toothLine.startSketchPoint.geometry, rootWidth / 40, 0, 0)
    dist      = sketch.sketchDimensions.addOffsetDimension(pieLine, toothLine.startSketchPoint, textPoint)
    dist.value = rootWidth / 20  # cm

    # Linear tooth segment mirror
    endPt           = futil.offsetPoint3D(topRadiusArcMirror.startSketchPoint.geometry, -topRadius / 20, -topRadius / 20, 0)
    toothLineMirror = sketch.sketchCurves.sketchLines.addByTwoPoints(topRadiusArcMirror.endSketchPoint, endPt)
    geoConstraints.addTangent(toothLineMirror, topRadiusArcMirror)
    geoConstraints.addSymmetry(toothLineMirror.endSketchPoint, toothLine.endSketchPoint, vertLine)

    # Root arc
    centerPt = futil.offsetPoint3D(pieLine.endSketchPoint.geometry, -topRadius / 10, -topRadius / 10, 0)
    rootArc  = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        pieLine.endSketchPoint, toothLine.endSketchPoint, math.pi / 4)
    geoConstraints.addTangent(rootArc, toothLine)
    textPoint = futil.offsetPoint3D(pieLine.endSketchPoint.geometry, -0.05, -0.05, 0)
    radius    = sketch.sketchDimensions.addRadialDimension(rootArc, textPoint)
    radius.value = rootRadius / 10  # cm
    geoConstraints.addCoincident(rootArc.endSketchPoint, pieLine)
    geoConstraints.addCoincident(rootArc.centerSketchPoint, pieLine)

    textPoint = adsk.core.Point3D.create(outer_diameter / 40, outer_diameter / 40, 0)
    rootDist  = sketch.sketchDimensions.addDistanceDimension(
        outerCircle.centerSketchPoint, rootArc.endSketchPoint,
        adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, textPoint)
    rootDist.value = (outer_diameter / 2 - rootHeight) / 10  # cm

    # Root arc mirror
    centerPt    = rootArc.centerSketchPoint.geometry
    centerPt.x  = centerPt.x * -1.0
    rootArcMirror = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        centerPt, toothLineMirror.endSketchPoint, -math.pi / 4)
    geoConstraints.addTangent(toothLineMirror, rootArcMirror)
    geoConstraints.addSymmetry(rootArcMirror.startSketchPoint, rootArc.endSketchPoint, vertLine)

    # Circular pattern of one full tooth profile
    toothEntities = [rootArcMirror, toothLineMirror, topRadiusArcMirror, topArcMirror,
                     topArc, topRadiusArc, toothLine, rootArc]
    circularPattern = geoConstraints.createCircularPatternInput(toothEntities, outerCircle.centerSketchPoint)
    circularPattern.quantity = adsk.core.ValueInput.createByReal(toothCount)
    geoConstraints.addCircularPattern(circularPattern)
    return outer_diameter / 10  # cm


# ---------------------------------------------------------------------------
# Geometry — GT2 3mm pitch
# ---------------------------------------------------------------------------

def createGT2PulleyGeometry(sketch: adsk.fusion.Sketch, beltPitchMM: float, toothCount: int):
    geoConstraints = sketch.geometricConstraints

    pitchLineOffset   = 0.381
    topRadius         = 0.25
    rootRadius        = 0.85
    rootHeight        = 1.14
    transitionRadius  = 1.52
    transitionOffset  = 0.61

    pitch_diameter = toothCount * beltPitchMM / math.pi
    outer_diameter = pitch_diameter - 2 * pitchLineOffset

    # Outer diameter construction circle
    centerPt    = adsk.core.Point3D.create()
    outerCircle = sketch.sketchCurves.sketchCircles.addByCenterRadius(centerPt, outer_diameter)
    outerCircle.isConstruction = True
    geoConstraints.addCoincident(outerCircle.centerSketchPoint, sketch.originPoint)

    textPoint = adsk.core.Point3D.create(-outer_diameter / 40, 0, 0)
    diameter  = sketch.sketchDimensions.addDiameterDimension(outerCircle, textPoint)
    diameter.value = outer_diameter / 10  # cm

    # Vertical construction line
    endPt    = adsk.core.Point3D.create(0, outer_diameter / 20, 0)
    vertLine = sketch.sketchCurves.sketchLines.addByTwoPoints(outerCircle.centerSketchPoint, endPt)
    vertLine.isConstruction = True
    geoConstraints.addCoincident(vertLine.endSketchPoint, outerCircle)
    geoConstraints.addVertical(vertLine)

    # Pie construction line
    endPt   = adsk.core.Point3D.create(outer_diameter / 20, 0, 0)
    pieLine = sketch.sketchCurves.sketchLines.addByTwoPoints(outerCircle.centerSketchPoint, endPt)
    pieLine.isConstruction = True
    geoConstraints.addCoincident(pieLine.endSketchPoint, outerCircle)
    textPoint = adsk.core.Point3D.create(outer_diameter / 40, outer_diameter / 40, 0)
    angleDim  = sketch.sketchDimensions.addAngularDimension(vertLine, pieLine, textPoint)
    angleDim.value = math.pi / toothCount

    # Tooth top arc
    topArc = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        outerCircle.centerSketchPoint, vertLine.endSketchPoint, -math.pi / toothCount / 4)
    geoConstraints.addConcentric(topArc, outerCircle)

    # Tooth top arc mirror
    topArcMirror = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        outerCircle.centerSketchPoint, vertLine.endSketchPoint, math.pi / toothCount / 4)
    geoConstraints.addConcentric(topArcMirror, outerCircle)
    geoConstraints.addSymmetry(topArc.startSketchPoint, topArcMirror.endSketchPoint, vertLine)

    # Top radius arc
    centerPt     = futil.offsetPoint3D(topArc.startSketchPoint.geometry, 0, -topRadius / 10, 0)
    topRadiusArc = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        centerPt, topArc.startSketchPoint, -math.pi / 2)
    geoConstraints.addTangent(topArc, topRadiusArc)
    textPoint = centerPt
    radius    = sketch.sketchDimensions.addRadialDimension(topRadiusArc, textPoint)
    radius.value = topRadius / 10  # cm

    # Top radius arc mirror
    centerPt          = futil.offsetPoint3D(topArcMirror.endSketchPoint.geometry, 0, -topRadius / 10, 0)
    topRadiusArcMirror = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        centerPt, topArcMirror.endSketchPoint, math.pi / 2)
    geoConstraints.addTangent(topArcMirror, topRadiusArcMirror)
    geoConstraints.addSymmetry(topRadiusArc.startSketchPoint, topRadiusArcMirror.endSketchPoint, vertLine)

    # Transition arc
    centerPt      = futil.offsetPoint3D(pieLine.endSketchPoint.geometry, transitionOffset / 10, -transitionOffset / 100, 0)
    endPt         = futil.offsetPoint3D(topRadiusArc.startSketchPoint.geometry, topRadius / 20, -topRadius / 10, 0)
    transistionArc = sketch.sketchCurves.sketchArcs.addByCenterStartEnd(
        centerPt, topRadiusArc.startSketchPoint, endPt)
    geoConstraints.addTangent(transistionArc, topRadiusArc)
    textPoint = futil.midPoint3D(centerPt, transistionArc.startSketchPoint.geometry)
    radius    = sketch.sketchDimensions.addRadialDimension(transistionArc, textPoint)
    radius.value = transitionRadius / 10  # cm
    textPoint = futil.midPoint3D(centerPt, transistionArc.endSketchPoint.geometry)
    dist      = sketch.sketchDimensions.addOffsetDimension(
        pieLine, transistionArc.centerSketchPoint, textPoint)
    dist.value = transitionOffset / 10  # cm

    # Transition arc mirror
    centerPt            = transistionArc.centerSketchPoint.geometry
    centerPt.x          = centerPt.x * -1.0
    endPt               = transistionArc.endSketchPoint.geometry
    endPt.x             = endPt.x * -1.0
    transistionArcMirror = sketch.sketchCurves.sketchArcs.addByCenterStartEnd(
        centerPt, endPt, topRadiusArcMirror.endSketchPoint)
    geoConstraints.addTangent(transistionArcMirror, topRadiusArcMirror)
    geoConstraints.addSymmetry(transistionArc.endSketchPoint, transistionArcMirror.startSketchPoint, vertLine)

    # Root arc
    centerPt = futil.offsetPoint3D(pieLine.endSketchPoint.geometry, -topRadius / 10, -topRadius / 10, 0)
    rootArc  = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        pieLine.endSketchPoint, transistionArc.endSketchPoint, math.pi / 4)
    geoConstraints.addTangent(rootArc, transistionArc)
    textPoint = futil.offsetPoint3D(pieLine.endSketchPoint.geometry, -0.05, -0.05, 0)
    radius    = sketch.sketchDimensions.addRadialDimension(rootArc, textPoint)
    radius.value = rootRadius / 10  # cm
    geoConstraints.addCoincident(rootArc.endSketchPoint, pieLine)
    geoConstraints.addCoincident(rootArc.centerSketchPoint, pieLine)

    textPoint = adsk.core.Point3D.create(outer_diameter / 40, outer_diameter / 40, 0)
    rootDist  = sketch.sketchDimensions.addDistanceDimension(
        outerCircle.centerSketchPoint, rootArc.endSketchPoint,
        adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, textPoint)
    rootDist.value = (outer_diameter / 2 - rootHeight) / 10  # cm

    # Root arc mirror
    centerPt      = rootArc.centerSketchPoint.geometry
    centerPt.x    = centerPt.x * -1.0
    rootArcMirror = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
        centerPt, transistionArcMirror.startSketchPoint, -math.pi / 4)
    geoConstraints.addSymmetry(rootArcMirror.centerSketchPoint, rootArc.centerSketchPoint, vertLine)
    geoConstraints.addSymmetry(rootArcMirror.startSketchPoint,  rootArc.endSketchPoint,    vertLine)

    # Circular pattern of one full tooth profile
    toothEntities = [rootArcMirror, transistionArcMirror, topRadiusArcMirror, topArcMirror,
                     topArc, topRadiusArc, transistionArc, rootArc]
    circularPattern = geoConstraints.createCircularPatternInput(toothEntities, outerCircle.centerSketchPoint)
    circularPattern.quantity = adsk.core.ValueInput.createByReal(toothCount)
    geoConstraints.addCircularPattern(circularPattern)
    return outer_diameter / 10  # cm
