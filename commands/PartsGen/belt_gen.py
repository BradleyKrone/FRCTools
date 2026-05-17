"""
belt_gen.py  —  Timing Belt geometry for PartsGen

Contains all belt geometry functions plus the _create_belt() entry point
and handle_belt_selection_changed() helper that PartsGen/entry.py calls.
"""

import adsk.core
import adsk.fusion
import math
from ...lib import fusionAddInUtils as futil
from ..CCDistance.CCLine import getCCLineFromEntity

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Attribute keys (written to the component so the edit command can restore)
# ---------------------------------------------------------------------------
ATTR_GROUP        = 'FRCTools_PartsGen'
ATTR_PART_TYPE    = 'part_type'
ATTR_BELT_TYPE    = 'belt_type'
ATTR_BELT_WIDTH   = 'belt_width_expr'
ATTR_BELT_SUPPRESS = 'belt_suppress_teeth'


# ---------------------------------------------------------------------------
# Timeline grouping helper (duplicated from entry.py to avoid circular import)
# ---------------------------------------------------------------------------

def _group_timeline_features(design: adsk.fusion.Design, start_marker: int, group_name: str):
    """Group all timeline items from start_marker to the current marker."""
    try:
        timeline = design.timeline
        end_marker = timeline.markerPosition - 1
        if end_marker > start_marker:
            group = timeline.timelineGroups.add(start_marker, end_marker)
            group.name = group_name
    except Exception:
        futil.log(f'PartsGen: failed to create timeline group "{group_name}"')


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
    beltWidthInp:       adsk.core.ValueCommandInput     = inputs.itemById('tb_belt_width')
    suppressTeethInp:   adsk.core.BoolValueCommandInput = inputs.itemById('tb_suppress_teeth')

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
    workingOcc  = rootComp.occurrences.addNewComponent(trans)
    workingComp = workingOcc.component

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

    PitchLoop = createPitchLoopFromSketchCircles(sketch, circle1_proj, circle2_proj)

    pathCurves = adsk.core.ObjectCollection.create()
    for curve in PitchLoop:
        pathCurves.add(curve.createForAssemblyContext(workingOcc))

    curveLength = sum(curve.length for curve in pathCurves)
    toothCount  = int(curveLength * 10 / beltPitchLength + 0.5)
    futil.log(f'Belt: loop length={curveLength:.4f}, teeth={toothCount}')

    if beltPitchLength == 5:
        comp_name = f'Belt_HTD_5mm-{toothCount}Tx{int(beltWidthInp.value * 10)}mm'
    else:
        comp_name = f'Belt_GT2_3mm-{toothCount}Tx{int(beltWidthInp.value * 10)}mm'
    workingComp.name = comp_name

    # Build the belt thickness offset around the pitch loop
    half_belt_thickness = adsk.core.ValueInput.createByReal(beltThickness / 2)
    geoConstraints      = sketch.geometricConstraints
    curves              = list(PitchLoop)

    offsetInput = geoConstraints.createOffsetInput(curves, half_belt_thickness)
    geoConstraints.addTwoSidesOffset(offsetInput, True)

    futil.log(f'Belt offset created {sketch.profiles.count} profiles')
    if sketch.profiles.count < 2:
        futil.popup_error('Parts Gen: belt offset profiles not created correctly.')
        workingOcc.deleteMe()
        return

    # Find the annular belt profile (neither the smallest nor the largest area)
    maxArea = max(sketch.profiles.item(i).areaProperties().area for i in range(sketch.profiles.count))
    insideLoop = None
    for i in range(sketch.profiles.count):
        profile = sketch.profiles.item(i)
        if profile.areaProperties().area == maxArea:
            insideLoop = profile.profileLoops.item(0)
            break

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

    if is_preview:
        extrudeBeltPreview(sketch, pathCurves, beltWidthInp.value)
        return

    extrudeBelt(sketch, pathCurves, beltWidthInp.value, toothCount, beltPitchLength)

    # Save attributes so the right-click Edit command can restore the dialog
    if not is_preview:
        try:
            attrs = workingComp.attributes
            attrs.add(ATTR_GROUP, ATTR_PART_TYPE,     'Timing Belt')
            attrs.add(ATTR_GROUP, ATTR_BELT_TYPE,     beltTypeInp.selectedItem.name)
            attrs.add(ATTR_GROUP, ATTR_BELT_WIDTH,    beltWidthInp.expression)
            attrs.add(ATTR_GROUP, ATTR_BELT_SUPPRESS, str(suppressTeethInp.value))
        except Exception:
            futil.log('PartsGen: failed to save belt attributes')

        _group_timeline_features(design, start_marker, comp_name)


# ---------------------------------------------------------------------------
# Belt extrude helpers
# ---------------------------------------------------------------------------

def extrudeBeltPreview(sketch: adsk.fusion.Sketch,
                       path: adsk.core.ObjectCollection,
                       beltWidth: float):
    """Extrude only the belt shell (no tooth patterning) for live preview."""
    workingComp = sketch.parentComponent

    maxArea = 0
    minArea = 9999999
    for i in range(sketch.profiles.count):
        a = sketch.profiles.item(i).areaProperties().area
        if a > maxArea:
            maxArea = a
        if a < minArea:
            minArea = a

    beltLoop = None
    for i in range(sketch.profiles.count):
        a = sketch.profiles.item(i).areaProperties().area
        if minArea < a < maxArea:
            beltLoop = sketch.profiles.item(i)
            break

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

    maxArea = 0
    minArea = 9999999
    for i in range(sketch.profiles.count):
        a = sketch.profiles.item(i).areaProperties().area
        if a > maxArea:
            maxArea = a
        if a < minArea:
            minArea = a

    beltLoop    = None
    profileLoop = None
    for i in range(sketch.profiles.count):
        profile = sketch.profiles.item(i)
        a = profile.areaProperties().area
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

    futil.print_SketchObjectCollection(path)
    patternPath   = adsk.fusion.Path.create(path, adsk.fusion.ChainedCurveOptions.noChainedCurves)
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
