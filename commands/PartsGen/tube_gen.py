import adsk.core
import adsk.fusion
import math
from ...lib import fusionAddInUtils as futil

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
IN_TO_CM = 2.54

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PART_TUBE    = 'Tube'
LEN_FACES    = 'Between Two Faces'

THICK_1_16   = '1/16"'
THICK_1_8    = '1/8"'
THICK_CUSTOM = 'Custom'

THICKNESS_MAP = {
    THICK_1_16: 0.0625 * IN_TO_CM,
    THICK_1_8:  0.125  * IN_TO_CM,
}

HOLE_RIVENUT = 'Rivenut (19/64")'
HOLE_10_32   = '10-32 (13/64")'
HOLE_CUSTOM  = 'Custom'

HOLE_SIZE_MAP = {
    HOLE_RIVENUT: (19.0 / 64.0) * IN_TO_CM,
    HOLE_10_32:   (13.0 / 64.0) * IN_TO_CM,
}

HOLE_OFFSET_CM = 0.5 * IN_TO_CM   # 0.5 in from two edges

ATTR_GROUP       = 'FRCTools_PartsGen'
ATTR_PART_TYPE   = 'part_type'
ATTR_TUBE_WIDTH  = 'tube_width_expr'
ATTR_TUBE_HEIGHT = 'tube_height_expr'
ATTR_TUBE_THICK  = 'tube_thickness'
ATTR_CUSTOM_THICK = 'custom_thickness_expr'
ATTR_ADD_HOLES   = 'tube_add_holes'
ATTR_HOLE_SIZE   = 'hole_size'
ATTR_HOLE_DIAM   = 'hole_diam_expr'
ATTR_LEN_EXPR    = 'custom_len_expr'


# ===========================================================================
# Helpers
# ===========================================================================

def _bbox_center(face: adsk.fusion.BRepFace) -> adsk.core.Point3D:
    bb = face.boundingBox
    return adsk.core.Point3D.create(
        (bb.minPoint.x + bb.maxPoint.x) / 2.0,
        (bb.minPoint.y + bb.maxPoint.y) / 2.0,
        (bb.minPoint.z + bb.maxPoint.z) / 2.0,
    )


def _largest_profile(sketch: adsk.fusion.Sketch) -> adsk.fusion.Profile:
    """Return the sketch profile with the largest area."""
    best = sketch.profiles.item(0)
    best_area = best.areaProperties().area
    for i in range(1, sketch.profiles.count):
        p = sketch.profiles.item(i)
        a = p.areaProperties().area
        if a > best_area:
            best_area = a
            best = p
    return best


def _extrude_direction(face1: adsk.fusion.BRepFace,
                       centroid1: adsk.core.Point3D,
                       centroid2: adsk.core.Point3D):
    """Return the ExtentDirection such that the extrusion goes from face1 toward face2."""
    dx = centroid2.x - centroid1.x
    dy = centroid2.y - centroid1.y
    dz = centroid2.z - centroid1.z
    _, n = face1.evaluator.getNormalAtPoint(face1.pointOnFace)
    dot = dx * n.x + dy * n.y + dz * n.z
    return (
        adsk.fusion.ExtentDirections.PositiveExtentDirection
        if dot >= 0
        else adsk.fusion.ExtentDirections.NegativeExtentDirection
    )


def _extrude_one_side(comp: adsk.fusion.Component,
                      profile: adsk.fusion.Profile,
                      operation: adsk.fusion.FeatureOperations,
                      face2_or_none,
                      custom_len_expr,
                      ext_dir: adsk.fusion.ExtentDirections) -> adsk.fusion.ExtrudeFeature:
    """Extrude `profile` either to face2 (parametric) or by a fixed length."""
    extrudes = comp.features.extrudeFeatures
    extInput = extrudes.createInput(profile, operation)
    if face2_or_none is not None:
        extent = adsk.fusion.ToEntityExtentDefinition.create(face2_or_none, False)
    else:
        extent = adsk.fusion.DistanceExtentDefinition.create(
            adsk.core.ValueInput.createByString(custom_len_expr)
        )
    extInput.setOneSideExtent(extent, ext_dir)
    return extrudes.add(extInput)


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


# ===========================================================================
# Tube face holes — seed hole + feature rectangular pattern
# ===========================================================================

def _add_face_holes(comp: adsk.fusion.Component,
                    body: adsk.fusion.BRepBody,
                    wall_thickness_cm: float,
                    hole_diam_cm: float,
                    extrusion_axis: adsk.core.Vector3D,
                    custom_len_expr=None):
    """Drill holes on each outer long face.

    One seed hole is sketched and extruded, then a **feature** rectangular
    pattern replicates that cut.  Because the replication lives at the
    feature level (not the sketch level), Fusion re-drives every cut
    instance on each recompute — so when ``custom_len_expr`` is a user
    parameter the hole count updates automatically alongside the tube length.

    Pattern quantity along the tube axis:
      - Custom Length mode: ``floor(expr / 1 in)`` — fully parametric.
      - Between-Faces mode: computed once from the actual face edge length.
    """
    futil.log(f'_add_face_holes: starting, diam={hole_diam_cm:.4f} cm, faces={body.faces.count}')

    bb = body.boundingBox
    body_cx = (bb.minPoint.x + bb.maxPoint.x) / 2.0
    body_cy = (bb.minPoint.y + bb.maxPoint.y) / 2.0
    body_cz = (bb.minPoint.z + bb.maxPoint.z) / 2.0

    # --- Collect outer wall faces -------------------------------------------
    outer_faces = []
    seen_ids    = set()

    for face in body.faces:
        _, face_normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
        if abs(face_normal.dotProduct(extrusion_axis)) > 0.5:
            continue
        face_center = _bbox_center(face)
        to_face = adsk.core.Vector3D.create(
            face_center.x - body_cx,
            face_center.y - body_cy,
            face_center.z - body_cz,
        )
        if to_face.dotProduct(face_normal) <= 0:
            continue
        fid = face.tempId
        if fid in seen_ids:
            continue
        seen_ids.add(fid)
        outer_faces.append(face)

    futil.log(f'_add_face_holes: {len(outer_faces)} outer faces')

    holeArea = hole_diam_cm ** 2 * math.pi / 4.0

    for face in outer_faces:
        try:
            # --- Sketch: seed hole + direction construction lines -----------
            sketch: adsk.fusion.Sketch = comp.sketches.add(face)
            sketch.name = 'SeedHole'

            sketchEdges = adsk.core.ObjectCollection.create()
            for edge in face.edges:
                projected = sketch.project(edge)
                if projected.count > 0:
                    sketchEdges.add(projected.item(0))

            edge_list = []
            for idx in range(sketchEdges.count):
                e = sketchEdges.item(idx)
                try:
                    if e.length > 1e-4:
                        edge_list.append(e)
                except Exception:
                    pass

            if len(edge_list) < 2:
                futil.log('  _add_face_holes: insufficient edges, skipping face')
                continue

            edge_list.sort(key=lambda e: e.length, reverse=True)
            longEdge  = edge_list[0]
            shortEdge = edge_list[-1]

            lengthIn = longEdge.length  / IN_TO_CM
            widthIn  = shortEdge.length / IN_TO_CM

            if widthIn < 0.9:
                futil.log(f'  _add_face_holes: face too narrow ({widthIn:.2f}"), skipping')
                continue

            leUnitVec = futil.sketchLineUnitVec(longEdge)
            seUnitVec = futil.sketchLineUnitVec(shortEdge)
            cornerPoint = None

            if longEdge.startSketchPoint.geometry.isEqualTo(shortEdge.startSketchPoint.geometry):
                cornerPoint = longEdge.startSketchPoint
            elif longEdge.startSketchPoint.geometry.isEqualTo(shortEdge.endSketchPoint.geometry):
                cornerPoint = longEdge.startSketchPoint
                seUnitVec = futil.multVector2D(seUnitVec, -1.0)
            elif longEdge.endSketchPoint.geometry.isEqualTo(shortEdge.startSketchPoint.geometry):
                cornerPoint = longEdge.endSketchPoint
                leUnitVec = futil.multVector2D(leUnitVec, -1.0)
            elif longEdge.endSketchPoint.geometry.isEqualTo(shortEdge.endSketchPoint.geometry):
                cornerPoint = longEdge.endSketchPoint
                leUnitVec = futil.multVector2D(leUnitVec, -1.0)
                seUnitVec = futil.multVector2D(seUnitVec, -1.0)

            if cornerPoint is None:
                futil.log('  _add_face_holes: no shared corner, skipping face')
                continue

            # Seed hole circle
            diag = leUnitVec.copy()
            diag.add(seUnitVec)
            seedPt = adsk.core.Point3D.create(
                cornerPoint.geometry.x + diag.x,
                cornerPoint.geometry.y + diag.y,
                0.0,
            )
            cornerHole = sketch.sketchCurves.sketchCircles.addByCenterRadius(
                seedPt, hole_diam_cm / 2.0
            )

            textPt = futil.offsetPoint3D(cornerHole.centerSketchPoint.geometry, 0.1, 0.1, 0)
            diamDim = sketch.sketchDimensions.addDiameterDimension(cornerHole, textPt)
            diamDim.value = hole_diam_cm

            textPt = cornerPoint.geometry
            widthDim = sketch.sketchDimensions.addOffsetDimension(
                longEdge, cornerHole.centerSketchPoint, textPt)
            widthDim.value = HOLE_OFFSET_CM

            lengthDim = sketch.sketchDimensions.addOffsetDimension(
                shortEdge, cornerHole.centerSketchPoint, textPt)
            lengthDim.value = HOLE_OFFSET_CM

            # Construction lines from the corner in the two pattern directions.
            cp = cornerPoint.geometry
            len_dir_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
                cp,
                adsk.core.Point3D.create(
                    cp.x + leUnitVec.x * 20.0,
                    cp.y + leUnitVec.y * 20.0,
                    0.0,
                ),
            )
            len_dir_line.isConstruction = True

            wid_dir_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
                cp,
                adsk.core.Point3D.create(
                    cp.x + seUnitVec.x * 20.0,
                    cp.y + seUnitVec.y * 20.0,
                    0.0,
                ),
            )
            wid_dir_line.isConstruction = True

            # --- Extrude-cut the single seed hole --------------------------
            seed_profile = None
            for profile in sketch.profiles:
                if abs(holeArea - profile.areaProperties().area) < holeArea * 0.05:
                    seed_profile = profile
                    break

            if seed_profile is None:
                futil.log('  _add_face_holes: seed profile not found')
                continue

            extrudes = comp.features.extrudeFeatures
            cutInput = extrudes.createInput(
                seed_profile, adsk.fusion.FeatureOperations.CutFeatureOperation
            )
            cutExtent = adsk.fusion.DistanceExtentDefinition.create(
                adsk.core.ValueInput.createByReal(wall_thickness_cm)
            )
            cutInput.setOneSideExtent(
                cutExtent, adsk.fusion.ExtentDirections.NegativeExtentDirection
            )
            cutInput.participantBodies = [body]
            seed_cut = extrudes.add(cutInput)

            # --- Feature rectangular pattern — replicates the seed cut -----
            # Add a small epsilon before truncation to counteract floating-point
            # imprecision in edge-length measurements from ToEntity extrusions.
            # Without this, a 2" face may measure as 1.9999…" and int() gives 1
            # instead of 2, dropping the second row of holes.
            n_width  = max(1, int(widthIn  + 1e-9))
            n_length = max(1, int(lengthIn + 1e-9))

            if custom_len_expr is not None:
                qty_length = adsk.core.ValueInput.createByString(
                    f'floor({custom_len_expr} / 1 in)'
                )
            else:
                qty_length = futil.Value(n_length)

            futil.log(f'  _add_face_holes: {n_width}×{n_length} pattern '
                      f'({widthIn:.2f}"×{lengthIn:.2f}")')

            feat_col = adsk.core.ObjectCollection.create()
            feat_col.add(seed_cut)

            pat_feats = comp.features.rectangularPatternFeatures
            pat_input = pat_feats.createInput(
                feat_col,
                len_dir_line,
                qty_length,
                adsk.core.ValueInput.createByString('1 in'),
                adsk.fusion.PatternDistanceType.SpacingPatternDistanceType,
            )
            if n_width > 1:
                pat_input.directionTwoEntity = wid_dir_line
                pat_input.quantityTwo        = futil.Value(n_width)
                pat_input.distanceTwo        = adsk.core.ValueInput.createByString('1 in')
            pat_feats.add(pat_input)

        except Exception:
            futil.handle_error('_add_face_holes face', show_message_box=True)


# ===========================================================================
# Tube creation
# ===========================================================================

def _create_tube(inputs: adsk.core.CommandInputs):
    tubeWidthInp:   adsk.core.ValueCommandInput     = inputs.itemById('tube_width')
    tubeHeightInp:  adsk.core.ValueCommandInput     = inputs.itemById('tube_height')
    tubeThickInp:   adsk.core.DropDownCommandInput  = inputs.itemById('tube_thickness')
    customThickInp: adsk.core.ValueCommandInput     = inputs.itemById('custom_thickness')
    lenTypeInp:     adsk.core.DropDownCommandInput  = inputs.itemById('length_type')
    face1Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face1_selection')
    face2Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face2_selection')
    customLenInp:   adsk.core.ValueCommandInput     = inputs.itemById('custom_length')

    w_cm = tubeWidthInp.value
    h_cm = tubeHeightInp.value
    if tubeThickInp.selectedItem.name == THICK_CUSTOM:
        t_cm = customThickInp.value
    else:
        t_cm = THICKNESS_MAP[tubeThickInp.selectedItem.name]

    len_type = lenTypeInp.selectedItem.name

    design       = adsk.fusion.Design.cast(app.activeProduct)
    rootComp     = design.rootComponent
    start_marker = design.timeline.markerPosition
    trans        = adsk.core.Matrix3D.create()
    try:
        workingOcc   = rootComp.occurrences.addNewComponent(trans)
    except RuntimeError:
        futil.popup_error(
            'Cannot create tube: this document is in Part Design mode, '
            'which only supports a single component.\n\n'
            'Please open or create an Assembly document and try again.'
        )
        return
    workingComp  = workingOcc.component

    w_in = w_cm / IN_TO_CM
    h_in = h_cm / IN_TO_CM
    t_in = t_cm / IN_TO_CM
    workingComp.name = f'Tube_{w_in:.4g}x{h_in:.4g}_T{t_in:.4g}in'

    if len_type == LEN_FACES:
        face1: adsk.fusion.BRepFace = face1Sel.selection(0).entity
        face2: adsk.fusion.BRepFace = face2Sel.selection(0).entity
        centroid1       = _bbox_center(face1)
        centroid2       = _bbox_center(face2)
        ext_dir         = _extrude_direction(face1, centroid1, centroid2)
        sketch_plane    = face1
        face2_target    = face2
        custom_len_expr = None
        _, face1_normal = face1.evaluator.getNormalAtPoint(face1.pointOnFace)
        extrusion_axis  = face1_normal
    else:
        face1           = None
        face2_target    = None
        centroid1       = adsk.core.Point3D.create(0, 0, 0)
        ext_dir         = adsk.fusion.ExtentDirections.PositiveExtentDirection
        custom_len_expr = customLenInp.expression
        sketch_plane    = rootComp.xYConstructionPlane
        extrusion_axis  = adsk.core.Vector3D.create(0, 0, 1)

    # --- Outer rectangle sketch ---------------------------------------------
    outer_sketch: adsk.fusion.Sketch = workingComp.sketches.addWithoutEdges(sketch_plane)
    outer_sketch.name = 'TubeOuterProfile'

    c_sk   = outer_sketch.modelToSketchSpace(centroid1)
    cx, cy = c_sk.x, c_sk.y

    hw = w_cm / 2.0
    hh = h_cm / 2.0

    lines = outer_sketch.sketchCurves.sketchLines
    corners_outer = [
        adsk.core.Point3D.create(cx - hw, cy - hh, 0),
        adsk.core.Point3D.create(cx + hw, cy - hh, 0),
        adsk.core.Point3D.create(cx + hw, cy + hh, 0),
        adsk.core.Point3D.create(cx - hw, cy + hh, 0),
    ]
    for i in range(4):
        lines.addByTwoPoints(corners_outer[i], corners_outer[(i + 1) % 4])

    if outer_sketch.profiles.count < 1:
        futil.popup_error('Parts Gen: could not create outer tube profile.')
        return

    outer_profile = _largest_profile(outer_sketch)

    # --- Extrude outer solid ------------------------------------------------
    outer_feat = _extrude_one_side(
        workingComp, outer_profile,
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        face2_target, custom_len_expr, ext_dir
    )
    body = outer_feat.bodies.item(0)

    # --- Inner rectangle (cut) sketch ---------------------------------------
    inner_sketch: adsk.fusion.Sketch = workingComp.sketches.addWithoutEdges(sketch_plane)
    inner_sketch.name = 'TubeInnerProfile'

    ihw = (w_cm - 2 * t_cm) / 2.0
    ihh = (h_cm - 2 * t_cm) / 2.0
    corners_inner = [
        adsk.core.Point3D.create(cx - ihw, cy - ihh, 0),
        adsk.core.Point3D.create(cx + ihw, cy - ihh, 0),
        adsk.core.Point3D.create(cx + ihw, cy + ihh, 0),
        adsk.core.Point3D.create(cx - ihw, cy + ihh, 0),
    ]
    inner_lines = inner_sketch.sketchCurves.sketchLines
    for i in range(4):
        inner_lines.addByTwoPoints(corners_inner[i], corners_inner[(i + 1) % 4])

    if inner_sketch.profiles.count < 1:
        futil.popup_error('Parts Gen: could not create inner tube profile.')
        return

    inner_profile = _largest_profile(inner_sketch)

    cut_extrudes = workingComp.features.extrudeFeatures
    cut_input    = cut_extrudes.createInput(
        inner_profile, adsk.fusion.FeatureOperations.CutFeatureOperation
    )
    if face2_target is not None:
        cut_extent = adsk.fusion.ToEntityExtentDefinition.create(face2_target, False)
    else:
        cut_extent = adsk.fusion.DistanceExtentDefinition.create(
            adsk.core.ValueInput.createByString(custom_len_expr)
        )
    cut_input.setOneSideExtent(cut_extent, ext_dir)
    cut_input.participantBodies = [body]
    cut_extrudes.add(cut_input)

    # --- Holes on each outer face --------------------------------------------
    try:
        tubeHolesInp: adsk.core.BoolValueCommandInput = inputs.itemById('tube_add_holes')
        if tubeHolesInp is not None and tubeHolesInp.value:
            holeSizeInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
            if holeSizeInp.selectedItem.name == HOLE_CUSTOM:
                hole_diam_cm = inputs.itemById('hole_diameter').value
            else:
                hole_diam_cm = HOLE_SIZE_MAP[holeSizeInp.selectedItem.name]
            _add_face_holes(workingComp, body, t_cm, hole_diam_cm, extrusion_axis, custom_len_expr)
    except Exception:
        futil.handle_error('PartsGen _add_face_holes', show_message_box=True)

    # --- Save PartsGen attributes for right-click edit ----------------------
    try:
        comp_attrs = workingComp.attributes
        comp_attrs.add(ATTR_GROUP, ATTR_PART_TYPE,   PART_TUBE)
        comp_attrs.add(ATTR_GROUP, ATTR_TUBE_WIDTH,  tubeWidthInp.expression)
        comp_attrs.add(ATTR_GROUP, ATTR_TUBE_HEIGHT, tubeHeightInp.expression)
        comp_attrs.add(ATTR_GROUP, ATTR_TUBE_THICK,  tubeThickInp.selectedItem.name)
        if tubeThickInp.selectedItem.name == THICK_CUSTOM:
            comp_attrs.add(ATTR_GROUP, ATTR_CUSTOM_THICK, customThickInp.expression)
        _ahi = inputs.itemById('tube_add_holes')
        _add = _ahi.value if _ahi else False
        comp_attrs.add(ATTR_GROUP, ATTR_ADD_HOLES, str(_add))
        if _add:
            _hsi = inputs.itemById('hole_size')
            if _hsi:
                comp_attrs.add(ATTR_GROUP, ATTR_HOLE_SIZE, _hsi.selectedItem.name)
                if _hsi.selectedItem.name == HOLE_CUSTOM:
                    _hdi = inputs.itemById('hole_diameter')
                    if _hdi:
                        comp_attrs.add(ATTR_GROUP, ATTR_HOLE_DIAM, _hdi.expression)
        if len_type == LEN_FACES:
            d = math.sqrt(
                (centroid2.x - centroid1.x) ** 2 +
                (centroid2.y - centroid1.y) ** 2 +
                (centroid2.z - centroid1.z) ** 2
            )
            comp_attrs.add(ATTR_GROUP, ATTR_LEN_EXPR, f'{d / IN_TO_CM:.6g} in')
        else:
            comp_attrs.add(ATTR_GROUP, ATTR_LEN_EXPR, custom_len_expr)
    except Exception:
        futil.log('PartsGen: failed to save tube attributes')

    _group_timeline_features(design, start_marker, workingComp.name)
