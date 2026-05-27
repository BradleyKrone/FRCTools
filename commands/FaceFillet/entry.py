import adsk.core
import adsk.fusion
import os
import math
import traceback
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_FaceFilletDialog'
CMD_NAME = 'FaceFillet'
CMD_Description = 'Fillet edges perpendicular to a selected face'

# Specify that the command will be promoted to the panel.
IS_PROMOTED = True

# Resource location for command icons.
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []


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

    # Face selection input
    faceSelection = inputs.addSelectionInput('face_selection', 'Face',
                                             'Select the face whose perpendicular edges will be filleted')
    faceSelection.addSelectionFilter('Faces')
    faceSelection.setSelectionLimits(1, 1)

    # Fillet radius input
    default_radius = adsk.core.ValueInput.createByString('0.25')
    inputs.addValueInput('fillet_radius', 'Fillet Radius', 'in', default_radius)

    # Edge-type filter checkboxes (both on by default)
    inputs.addBoolValueInput('external_edges', 'External Edges', True, '', True)
    inputs.addBoolValueInput('internal_edges', 'Internal Edges', True, '', True)

    # Wire up event handlers
    futil.add_handler(args.command.execute,        command_execute,        local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview,        local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged,   command_input_changed,  local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy,        command_destroy,        local_handlers=local_handlers)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _edge_is_convex(edge: adsk.fusion.BRepEdge) -> bool:
    """
    Returns True if the edge is a convex (external) corner,
    False if it is a concave (internal) corner.

    Method: the bisector of the two outward face normals points away from the
    body centre for a convex edge and toward it for a concave edge.
    """
    try:
        faces = list(edge.faces)
        if len(faces) != 2:
            return True  # degenerate — treat as external

        face1, face2 = faces[0], faces[1]

        # Midpoint on the edge
        _, start_p, end_p = edge.evaluator.getParameterExtents()
        _, mid_pt = edge.evaluator.getPointAtParameter((start_p + end_p) / 2.0)

        # Outward normals of both faces at the edge midpoint
        _, n1 = face1.evaluator.getNormalAtPoint(mid_pt)
        _, n2 = face2.evaluator.getNormalAtPoint(mid_pt)

        # Bisector of the two normals
        bx, by, bz = n1.x + n2.x, n1.y + n2.y, n1.z + n2.z
        b_len = math.sqrt(bx*bx + by*by + bz*bz)
        if b_len < 1e-10:
            return True

        # Approximate body centre via bounding box
        body = face1.body
        bb = body.boundingBox
        cx = (bb.minPoint.x + bb.maxPoint.x) / 2.0
        cy = (bb.minPoint.y + bb.maxPoint.y) / 2.0
        cz = (bb.minPoint.z + bb.maxPoint.z) / 2.0

        # Vector from body centre to edge midpoint
        tx, ty, tz = mid_pt.x - cx, mid_pt.y - cy, mid_pt.z - cz

        # Positive dot → bisector points away from centre → convex (external)
        return (bx*tx + by*ty + bz*tz) > 0

    except Exception:
        futil.log(f'{CMD_NAME} _edge_is_convex failed:\n{traceback.format_exc()}')
        return True  # safe default


def _collect_perp_edges(face: adsk.fusion.BRepFace,
                        want_external: bool,
                        want_internal: bool) -> list:
    """
    Returns a list of BRepEdge objects that are perpendicular to *face* (i.e.
    whose direction ≈ face normal) and pass the convexity filter.
    """
    _, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
    boundary_tokens = {e.entityToken for e in face.edges}

    ANGLE_TOL = math.radians(30)
    result = []
    seen_tokens: set = set()

    for vertex in face.vertices:
        for edge in vertex.edges:
            token = edge.entityToken
            if token in boundary_tokens or token in seen_tokens:
                continue

            sp = edge.startVertex.geometry
            ep = edge.endVertex.geometry
            dx, dy, dz = ep.x - sp.x, ep.y - sp.y, ep.z - sp.z
            length = math.sqrt(dx*dx + dy*dy + dz*dz)
            if length < 1e-10:
                continue

            edge_vec = adsk.core.Vector3D.create(dx/length, dy/length, dz/length)
            angle = normal.angleTo(edge_vec)
            if not (angle < ANGLE_TOL or abs(angle - math.pi) < ANGLE_TOL):
                continue

            is_convex = _edge_is_convex(edge)
            if (is_convex and want_external) or (not is_convex and want_internal):
                result.append(edge)
                seen_tokens.add(token)

    return result


def _make_fillet(comp: adsk.fusion.Component,
                 edge_collection: adsk.core.ObjectCollection,
                 radius_cm: float):
    """Create a single fillet feature. Raises on failure."""
    fi = comp.features.filletFeatures.createInput()
    fi.isRollingBallCorner = True
    fi.addConstantRadiusEdgeSet(
        edge_collection,
        adsk.core.ValueInput.createByReal(radius_cm),
        True  # isTangentChain
    )
    comp.features.filletFeatures.add(fi)


# ---------------------------------------------------------------------------
# Main fillet logic
# ---------------------------------------------------------------------------

def _apply_fillet(inputs: adsk.core.CommandInputs,
                  is_preview: bool = False) -> tuple:
    """
    Collect perpendicular edges, filter by convexity checkboxes, then fillet.

    Strategy:
      1. Try all qualifying edges in a single fillet feature.
      2. If that fails (e.g. radius too large for some edges), fall back to
         trying each edge individually and skip the ones that still fail.
         (Skipped only during execute; preview always uses attempt-all.)

    Returns (any_success: bool, skipped_count: int).
    """
    try:
        faceSelInput = inputs.itemById('face_selection')
        radiusInput  = inputs.itemById('fillet_radius')
        extCheck     = inputs.itemById('external_edges')
        intCheck     = inputs.itemById('internal_edges')

        face: adsk.fusion.BRepFace = faceSelInput.selection(0).entity
        radius_cm: float = radiusInput.value
        want_ext: bool = extCheck.value
        want_int: bool = intCheck.value

        edges = _collect_perp_edges(face, want_ext, want_int)
        if not edges:
            return False, 0

        comp = face.body.parentComponent

        # Build full collection and attempt a single fillet
        all_coll = adsk.core.ObjectCollection.create()
        for e in edges:
            all_coll.add(e)

        try:
            _make_fillet(comp, all_coll, radius_cm)
            return True, 0
        except Exception:
            futil.log(f'{CMD_NAME} all-at-once fillet failed, falling back to per-edge')

        # Preview mode — don't create partial features; just report failure
        if is_preview:
            return False, 0

        # Execute mode — fillet each edge individually, skip failures
        skipped = 0
        succeeded = 0
        for edge in edges:
            single = adsk.core.ObjectCollection.create()
            single.add(edge)
            try:
                _make_fillet(comp, single, radius_cm)
                succeeded += 1
            except Exception:
                skipped += 1

        return succeeded > 0, skipped

    except Exception:
        futil.log(f'{CMD_NAME} _apply_fillet failed:\n{traceback.format_exc()}')
        return False, 0


# Called when the user clicks OK.
def command_execute(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Execute Event')

    success, skipped = _apply_fillet(args.command.commandInputs, is_preview=False)

    if not success:
        args.executeFailed = True
        args.executeFailedMessage = ('FaceFillet: No edges could be filleted. '
                                     'Try a smaller radius or check the edge filters.')
        return

    if skipped > 0:
        ui.messageBox(
            f'FaceFillet completed, but {skipped} edge(s) were skipped because '
            f'the radius is too large for those edges.\n\n'
            f'Try a smaller Fillet Radius to include the skipped edges.',
            'FaceFillet — Edges Skipped',
            adsk.core.MessageBoxButtonTypes.OKButtonType,
            adsk.core.MessageBoxIconTypes.WarningIconType
        )


# Called whenever Fusion needs a fresh preview.
def command_preview(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Preview Event')

    success, _ = _apply_fillet(args.command.commandInputs, is_preview=True)
    if success:
        args.isValidResult = True


# Called when any input changes — allows reactive UI updates.
def command_input_changed(args: adsk.core.InputChangedEventArgs):
    futil.log(f'{CMD_NAME} Input Changed: {args.input.id}')


# Called to decide whether the OK button should be enabled.
def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs
    faceSelInput = inputs.itemById('face_selection')
    radiusInput  = inputs.itemById('fillet_radius')
    extCheck     = inputs.itemById('external_edges')
    intCheck     = inputs.itemById('internal_edges')

    face_ok   = faceSelInput is not None and faceSelInput.selectionCount >= 1
    radius_ok = radiusInput  is not None and radiusInput.value > 0
    # At least one edge-type filter must be enabled
    filter_ok = (extCheck is not None and extCheck.value) or \
                (intCheck is not None and intCheck.value)

    args.areInputsValid = face_ok and radius_ok and filter_ok


# Called when the command is closed/cancelled.
def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Destroy Event')

    global local_handlers
    local_handlers = []
