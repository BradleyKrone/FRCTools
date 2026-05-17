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
ATTR_GROUP             = 'FRCTools_PartsGen'
ATTR_PART_TYPE         = 'part_type'
ATTR_PULLEY_BELT_TYPE  = 'pulley_belt_type'
ATTR_PULLEY_TOOTH_COUNT = 'pulley_tooth_count'
ATTR_PULLEY_BELT_WIDTH = 'pulley_belt_width'


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
# Public creation entry point — called by PartsGen/entry.py
# ---------------------------------------------------------------------------

def _create_pulley(inputs: adsk.core.CommandInputs):
    """Create a timing pulley component from the PartsGen dialog inputs."""

    beltType:   adsk.core.DropDownCommandInput = inputs.itemById('belt_type')
    toothCount: adsk.core.ValueCommandInput    = inputs.itemById('tooth_count')
    beltWidth:  adsk.core.ValueCommandInput    = inputs.itemById('belt_width')

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

    # Sketch on the root XY construction plane
    sketchPlane = rootComp.xYConstructionPlane
    sketch = workingComp.sketches.add(sketchPlane, workingOcc)
    geometry_fn(sketch, belt_pitch, n_teeth)

    # Extrude the single closed profile to form the pulley body
    if sketch.profiles.count != 1:
        futil.popup_error(
            f'Parts Gen: Timing Pulley sketch has {sketch.profiles.count} profiles '
            f'(expected 1).  The tooth geometry may not have closed correctly.'
        )
        workingOcc.deleteMe()
        return

    extrudes   = workingComp.features.extrudeFeatures
    widthValue = adsk.core.ValueInput.createByReal(beltWidth.value)
    extrudes.addSimple(
        sketch.profiles.item(0),
        widthValue,
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
    )

    # Save attributes so the right-click Edit command can restore the dialog
    try:
        attrs = workingComp.attributes
        attrs.add(ATTR_GROUP, ATTR_PART_TYPE,          'Timing Pulley')
        attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_TYPE,   beltType.selectedItem.name)
        attrs.add(ATTR_GROUP, ATTR_PULLEY_TOOTH_COUNT, str(n_teeth))
        attrs.add(ATTR_GROUP, ATTR_PULLEY_BELT_WIDTH,  beltWidth.expression)
    except Exception:
        futil.log('PartsGen: failed to save pulley attributes')

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
