import adsk.core
import adsk.fusion
import os
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_JointInspectorDialog'
CMD_NAME = 'Joint Inspector'
CMD_Description = ('Select a body and see every joint that '
                    'touches it -- pick one to highlight what it connects to and which face/edge/point '
                    'each side is attached to, right in the viewport.')

# Specify that the command will be promoted to the panel.
IS_PROMOTED = False

# Resource location for command icons.
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []

# (joint, matched_side) pairs for the currently selected target, rebuilt whenever
# target_entity changes. matched_side is 0 or 1 -- which of occurrenceOne/
# occurrenceTwo on the joint corresponds to "this side" (the selected target).
_current_matches = []

# True when target_entity resolved to a Component with multiple occurrence
# instances and there was no assemblyContext available to tell which one was
# actually picked -- OK is disabled rather than silently guessing.
_target_ambiguous = False

# CustomGraphicsGroup drawn on the root component to show the currently
# selected joint's connection -- rebuilt every time the joint selection
# changes, torn down in command_destroy. Kept as a plain module global
# (not local_handlers) since it isn't an event handler.
_highlight_group = None

# Index into _current_matches of the joint the dropdown currently shows, or -1.
# The highlight is drawn from executePreview rather than straight from
# inputChanged: Fusion tears down every CustomGraphicsGroup on the design each
# time it rebuilds the command preview (confirmed live -- the group went
# isValid=False and rootComponent.customGraphicsGroups.count dropped to 0
# between two validateInputs calls, with no _clear_highlight() of ours in
# between). Graphics drawn from inputChanged therefore flash up and vanish on
# the very next rebuild; executePreview is re-invoked after each rebuild, so
# drawing there is what makes the highlight stick.
_selected_idx = -1

_THIS_SIDE_COLOR = (0, 200, 60)
# Deliberately NOT blue: Fusion paints the target body with its own blue
# selection highlight (the body stays selected in `target_entity` the whole
# time the dialog is open), which swamped both a 0.35-opacity green "this
# side" wash and a blue "other side" one -- every tube came out blue-ish and
# the highlight read as "nothing happened". Orange is the one strong colour
# that cannot be confused with Fusion's selection blue.
_OTHER_SIDE_COLOR = (255, 130, 0)
_CONNECTOR_COLOR = (230, 0, 200)

# Custom graphics are depth-tested against the model and Fusion offers no
# "draw on top" flag (the whole CustomGraphicsEntity API is deleteMe /
# get|setOpacity / isVisible / isSelectable / depthPriority / viewScale /
# viewPlacement -- `depthPriority` only orders custom graphics against *each
# other*, not against solid bodies). So a joint attached to a face or edge
# that points away from the camera gets drawn perfectly and is still totally
# invisible, buried inside the solid. Every side therefore also gets a
# translucent whole-body wash (too big to hide) and a view-scaled origin
# marker (constant pixel size, so it never shrinks to an unnoticeable speck).
_BODY_OPACITY = 0.65
_EXACT_OPACITY = 0.9
_MARKER_PIXELS_THIS = 22.0
_MARKER_PIXELS_OTHER = 13.0

# Relative draw order *within* the highlight group: body wash at the bottom,
# then the exact face/edge, then the origin markers on top.
_DEPTH_BODY = 1
_DEPTH_EXACT = 10
_DEPTH_MARKER = 100


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


# Called when the user clicks the button -- builds the command dialog.
def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.log(f'{CMD_NAME} Command Created Event')

    inputs = args.command.commandInputs

    targetInp = inputs.addSelectionInput(
        'target_entity', 'Component / Body',
        'Select a body to see the joints connected to it.'
    )
    # Mixing a whole-body filter ('SolidBodies') with sub-entity filters
    # ('Faces'/'Edges') on the same input made every click select nothing at
    # all (verified live) -- every other selection input in this codebase only
    # ever combines filters from one family (e.g. CCDistance's three Sketch*
    # filters together), never a body-level filter with a face/edge-level one.
    # A single 'SolidBodies' filter is the exact pattern already proven to
    # work standalone in Tubify/Lighten. _resolve_target() already walks up
    # from a picked body to its owning Occurrence via assemblyContext, so
    # clicking a body still resolves the right component.
    targetInp.addSelectionFilter('SolidBodies')
    targetInp.setSelectionLimits(1, 1)

    inputs.addTextBoxCommandInput('target_info', '', 'Select a body.', 2, True)

    jointListInp = inputs.addDropDownCommandInput(
        'joint_list', 'Joints', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    jointListInp.isEnabled = False

    inputs.addTextBoxCommandInput('joint_info', '', 'No joint selected.', 8, True)

    global _current_matches, _target_ambiguous, _selected_idx
    _current_matches = []
    _target_ambiguous = False
    _selected_idx = -1

    # Wire up event handlers. Nothing here creates geometry, but executePreview
    # is still required: it is the only place custom graphics survive Fusion's
    # preview rebuilds (see _selected_idx).
    futil.add_handler(args.command.execute,        command_execute,        local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview,        local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged,   command_input_changed,  local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy,        command_destroy,        local_handlers=local_handlers)


# ---------------------------------------------------------------------------
# Reactive UI
# ---------------------------------------------------------------------------

def command_input_changed(args: adsk.core.InputChangedEventArgs):
    try:
        _command_input_changed(args)
    except Exception:
        futil.handle_error(f'{CMD_NAME} command_input_changed', show_message_box=True)


def _command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed = args.input
    inputs = args.inputs
    global _current_matches, _target_ambiguous

    if changed.id == 'target_entity':
        _reset_joint_ui(inputs)

        targetInp: adsk.core.SelectionCommandInput = inputs.itemById('target_entity')
        targetInfoInp: adsk.core.TextBoxCommandInput = inputs.itemById('target_info')

        if targetInp.selectionCount == 0:
            _target_ambiguous = False
            targetInfoInp.text = 'Select a body.'
            return

        entity = targetInp.selection(0).entity
        target_occ, ambiguous = _resolve_target(entity)
        _target_ambiguous = ambiguous

        if ambiguous:
            targetInfoInp.formattedText = (
                '<div style="color:#b00020">Multiple instances of this component exist and the '
                'exact one picked could not be determined. Select it from the browser tree, or '
                'pick a face/edge on it instead.</div>'
            )
            return

        target_label = target_occ.fullPathName if target_occ is not None else 'Ground / Root Component'
        _current_matches = _find_joint_matches(target_occ)

        if not _current_matches:
            targetInfoInp.text = f'{target_label} -- no joints found.'
            return

        targetInfoInp.text = f'{target_label} -- {len(_current_matches)} joint(s) found.'

        jointListInp: adsk.core.DropDownCommandInput = inputs.itemById('joint_list')
        jointListInp.listItems.clear()
        for i, (joint, matched_side) in enumerate(_current_matches):
            other_occ = _other_side(joint, matched_side)
            other_label = other_occ.fullPathName if other_occ is not None else 'Ground'
            jointListInp.listItems.add(f'{_joint_name(joint)}  ->  {other_label}', i == 0, '')
        jointListInp.isEnabled = True

        _render_joint_info(inputs, 0)

    elif changed.id == 'joint_list':
        jointListInp: adsk.core.DropDownCommandInput = inputs.itemById('joint_list')
        sel = jointListInp.selectedItem
        idx = sel.index if sel else -1
        _render_joint_info(inputs, idx)


def _reset_joint_ui(inputs: adsk.core.CommandInputs):
    global _current_matches, _selected_idx
    _current_matches = []
    _selected_idx = -1
    jointListInp: adsk.core.DropDownCommandInput = inputs.itemById('joint_list')
    jointListInp.listItems.clear()
    jointListInp.isEnabled = False
    inputs.itemById('joint_info').text = 'No joint selected.'
    _clear_highlight()
    _refresh_viewport()


def _render_joint_info(inputs: adsk.core.CommandInputs, idx: int):
    """Update the dialog text and record which joint is selected.

    Deliberately does NOT draw -- command_preview() owns the viewport, because
    only graphics created there survive Fusion's preview rebuilds.
    """
    global _selected_idx
    jointInfoInp: adsk.core.TextBoxCommandInput = inputs.itemById('joint_info')

    if idx < 0 or idx >= len(_current_matches):
        _selected_idx = -1
        _clear_highlight()
        jointInfoInp.text = 'No joint selected.'
        _refresh_viewport()
        return

    _selected_idx = idx

    joint, matched_side = _current_matches[idx]
    other_occ = _other_side(joint, matched_side)
    other_label = other_occ.fullPathName if other_occ is not None else 'Ground / Root Component'

    legend = (
        f'<b>{_joint_name(joint)}</b><br>'
        f'Connects to: <b>{other_label}</b><br><br>'
        f'<span style="color:rgb{_OTHER_SIDE_COLOR}">&#9632;</span> <b>Orange</b> = the body this '
        f'joint connects to<br>'
        f'<span style="color:rgb{_THIS_SIDE_COLOR}">&#9632;</span> Green = this side (the body you '
        f'picked also keeps Fusion&apos;s own blue selection highlight)'
    )

    jointInfoInp.formattedText = legend


def command_preview(args: adsk.core.CommandEventArgs):
    """Draw the selected joint's highlight.

    Fusion calls this after every preview rebuild -- which is also what wipes
    the previous CustomGraphicsGroup -- so redrawing unconditionally here is
    what keeps the highlight on screen instead of flashing once and vanishing.
    """
    try:
        if 0 <= _selected_idx < len(_current_matches):
            joint, matched_side = _current_matches[_selected_idx]
            _draw_joint_highlight(joint, matched_side)
        else:
            _clear_highlight()
            _refresh_viewport()
    except Exception:
        # Never fail silently here: a swallowed exception in the redraw path
        # once looked exactly like "the tool just stopped working".
        futil.handle_error(f'{CMD_NAME} command_preview', show_message_box=False)

    # Nothing is being built -- keep the command in preview mode so Fusion
    # doesn't treat this as a computed result.
    args.isValidResult = False


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    try:
        inputs = args.inputs
        targetInp: adsk.core.SelectionCommandInput = inputs.itemById('target_entity')
        args.areInputsValid = targetInp.selectionCount == 1 and not _target_ambiguous
    except Exception:
        futil.handle_error(f'{CMD_NAME} command_validate_input', show_message_box=False)
        args.areInputsValid = False


def command_execute(args: adsk.core.CommandEventArgs):
    try:
        inputs = args.command.commandInputs
        jointListInp: adsk.core.DropDownCommandInput = inputs.itemById('joint_list')
        if jointListInp.selectedItem:
            futil.log(f'{CMD_NAME}: last inspected "{jointListInp.selectedItem.name}"')
    except Exception:
        futil.handle_error(f'{CMD_NAME} command_execute', show_message_box=True)


def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers, _current_matches, _target_ambiguous, _selected_idx
    local_handlers = []
    _current_matches = []
    _target_ambiguous = False
    _selected_idx = -1
    _clear_highlight()


# ---------------------------------------------------------------------------
# Entity / occurrence resolution
# ---------------------------------------------------------------------------

def _get_entity_component(entity) -> adsk.fusion.Component:
    """Return the parent Component for a BRep entity or Occurrence, or None."""
    try:
        ot = entity.objectType
        if ot in (adsk.fusion.BRepFace.classType(),
                  adsk.fusion.BRepEdge.classType(),
                  adsk.fusion.BRepVertex.classType()):
            return entity.body.parentComponent
        elif ot == adsk.fusion.BRepBody.classType():
            return entity.parentComponent
        elif ot == adsk.fusion.Occurrence.classType():
            return entity.component
    except Exception:
        pass
    return None


def _resolve_target(entity):
    """Resolve whatever the user picked to the Occurrence it belongs to.

    Returns (occurrence_or_None, ambiguous_bool). occurrence is None when the
    entity belongs directly to the root component (i.e. it's "ground", not
    joined through any occurrence). ambiguous is True when the entity's owning
    Component has multiple occurrence instances and there was no
    assemblyContext available to tell which one was actually picked -- the
    caller must not silently guess in that case.
    """
    design = adsk.fusion.Design.cast(app.activeProduct)

    if entity.objectType == adsk.fusion.Occurrence.classType():
        return entity, False

    try:
        occ = entity.assemblyContext
        if occ and occ.isValid:
            return occ, False
    except Exception:
        pass

    native = entity
    try:
        if entity.nativeObject:
            native = entity.nativeObject
    except Exception:
        pass

    comp = _get_entity_component(native)
    if comp is None:
        return None, False

    if comp == design.rootComponent:
        return None, False

    matches = [occ for occ in design.rootComponent.allOccurrences if occ.component == comp]
    if len(matches) == 1:
        return matches[0], False
    if len(matches) == 0:
        return None, False
    return None, True


def _joint_occurrence(joint, side):
    """`joint.occurrenceOne`/`occurrenceTwo` for side 0/1, or None for "ground".

    Reading these raises `RuntimeError: 2 : InternalValidationError : jointOcc`
    (not return None) when that side of the joint is attached to the root
    component rather than to an occurrence -- confirmed live from a real
    traceback. Left unguarded it aborted the whole `target_entity` handler, so
    `_current_matches` was never assigned and the joint dropdown stayed empty
    for that selection *and every selection after it*. "Attached to the root"
    is exactly the None case the rest of this module already means by ground.
    """
    try:
        return joint.occurrenceOne if side == 0 else joint.occurrenceTwo
    except Exception as err:
        futil.log(f'{CMD_NAME}: joint "{_joint_name(joint)}" side {side} has no '
                  f'occurrence, treating as ground ({err})')
        return None


def _joint_name(joint):
    try:
        return joint.name
    except Exception:
        return '<unnamed joint>'


def _side_matches(occ, target_occ) -> bool:
    if target_occ is None:
        return occ is None
    if occ is None:
        return False
    return occ.fullPathName == target_occ.fullPathName


def _find_joint_matches(target_occ):
    design = adsk.fusion.Design.cast(app.activeProduct)
    matches = []
    for joint in design.rootComponent.allJoints:
        if _side_matches(_joint_occurrence(joint, 0), target_occ):
            matches.append((joint, 0))
        elif _side_matches(_joint_occurrence(joint, 1), target_occ):
            matches.append((joint, 1))
    return matches


def _other_side(joint, matched_side):
    return _joint_occurrence(joint, 1 if matched_side == 0 else 0)


# ---------------------------------------------------------------------------
# Joint geometry -- viewport highlight
# ---------------------------------------------------------------------------
#
# Rather than describe each side's attachment geometry in text, draw it
# directly in the viewport via CustomGraphics: this side's face(s)/edge(s)/
# point in green, the other side's in blue, plus a marker at each side's
# joint origin and a connector line between them. CustomGraphics live outside
# the undo stack and don't touch ui.activeSelections, so this doesn't fight
# the target_entity SelectionCommandInput (see LESSONS_LEARNED.md).

def _unwrap(geom_or_origin):
    """A joint side is either a plain JointGeometry or a JointOrigin wrapping
    one -- always go through this before touching entityOne/entityTwo/origin."""
    if geom_or_origin is None:
        return None
    if geom_or_origin.objectType == adsk.fusion.JointOrigin.classType():
        return geom_or_origin.geometry
    return geom_or_origin


def _solid_color(rgb):
    r, g, b = rgb
    return adsk.fusion.CustomGraphicsSolidColorEffect.create(adsk.core.Color.create(r, g, b, 255))


def _add_mesh(group, mesh, rgb, opacity, depth):
    if mesh is None:
        return
    coords = adsk.fusion.CustomGraphicsCoordinates.create(mesh.nodeCoordinatesAsDouble)
    entity = group.addMesh(coords, mesh.nodeIndices, mesh.normalVectorsAsDouble, [])
    entity.color = _solid_color(rgb)
    entity.setOpacity(opacity, True)
    entity.depthPriority = depth


def _highlight_body(group, body: adsk.fusion.BRepBody, rgb):
    """Translucent wash over the side's whole body.

    This is what makes a joint readable from any camera angle: the exact
    face/edge/point below is frequently hidden inside the solid, but a whole
    tube changing colour never is.
    """
    if body is None:
        return
    try:
        _add_mesh(group, body.meshManager.displayMeshes.bestMesh, rgb, _BODY_OPACITY, _DEPTH_BODY)
    except Exception as err:
        futil.log(f'{CMD_NAME}: could not wash body: {err}')


def _highlight_face(group, face: adsk.fusion.BRepFace, rgb):
    _add_mesh(group, face.meshManager.displayMeshes.bestMesh, rgb, _EXACT_OPACITY, _DEPTH_EXACT)


def _highlight_edge(group, edge: adsk.fusion.BRepEdge, rgb):
    entity = group.addCurve(edge.geometry)
    entity.color = _solid_color(rgb)
    entity.weight = 6
    entity.depthPriority = _DEPTH_EXACT


def _point_from_entity(entity):
    """Best-effort Point3D for a joint-geometry entity that isn't a face/edge."""
    try:
        ot = entity.objectType
        if ot == adsk.fusion.BRepVertex.classType():
            return entity.geometry
        elif ot == adsk.fusion.ConstructionPoint.classType():
            return entity.geometry
        elif ot == adsk.fusion.SketchPoint.classType():
            return entity.worldGeometry
    except Exception:
        pass
    return None


def _add_marker(group, point: adsk.core.Point3D, rgb, pixels):
    """A 3D crosshair at `point`, sized in *pixels* rather than centimetres.

    A fixed centimetre size (the previous 0.6cm) is nearly invisible on an
    assembly-sized model -- and the two sides of a joint usually share one
    origin, so they land on top of each other. `viewScale` keeps the marker a
    constant on-screen size at any zoom, and the two sides use different pixel
    sizes so the coincident case reads as two nested crosshairs.
    """
    if point is None:
        return
    x, y, z = point.x, point.y, point.z
    coords = adsk.fusion.CustomGraphicsCoordinates.create([
        x - 1.0, y, z,  x + 1.0, y, z,
        x, y - 1.0, z,  x, y + 1.0, z,
        x, y, z - 1.0,  x, y, z + 1.0,
    ])
    entity = group.addLines(coords, [], False, [])
    entity.color = _solid_color(rgb)
    entity.weight = 4
    entity.depthPriority = _DEPTH_MARKER
    try:
        entity.viewScale = adsk.fusion.CustomGraphicsViewScale.create(pixels, point)
    except Exception as err:
        futil.log(f'{CMD_NAME}: viewScale unavailable for marker: {err}')


def _side_body(joint_geometry):
    """The BRepBody a joint side hangs off, or None for non-BRep geometry
    (a ConstructionPoint or SketchPoint has no owning body)."""
    for entity in (joint_geometry.entityOne, joint_geometry.entityTwo):
        if entity is None:
            continue
        try:
            if entity.body is not None:
                return entity.body
        except Exception:
            continue
    return None


def _highlight_side(group, joint_geometry, rgb, marker_pixels):
    if joint_geometry is None:
        return

    _highlight_body(group, _side_body(joint_geometry), rgb)

    for entity in (joint_geometry.entityOne, joint_geometry.entityTwo):
        if entity is None:
            continue
        ot = entity.objectType
        if ot == adsk.fusion.BRepFace.classType():
            _highlight_face(group, entity, rgb)
        elif ot == adsk.fusion.BRepEdge.classType():
            _highlight_edge(group, entity, rgb)
        else:
            _add_marker(group, _point_from_entity(entity), rgb, marker_pixels)

    try:
        _add_marker(group, joint_geometry.origin, rgb, marker_pixels)
    except Exception:
        pass


def _clear_highlight():
    global _highlight_group
    if _highlight_group is None:
        return
    group = _highlight_group
    _highlight_group = None
    try:
        if group.isValid:
            group.deleteMe()
    except Exception as err:
        futil.log(f'{CMD_NAME}: failed to delete highlight graphics: {err}')


def _draw_joint_highlight(joint, matched_side):
    global _highlight_group
    _clear_highlight()

    design = adsk.fusion.Design.cast(app.activeProduct)
    group = design.rootComponent.customGraphicsGroups.add()
    # Take ownership of the group immediately, not after it's fully populated:
    # if any of the _highlight_side() calls below raise, the caller swallows it
    # and a group assigned only at the end would never be reachable by the next
    # _clear_highlight() -- leaking a half-drawn highlight that then sticks in
    # the viewport on top of every later one.
    _highlight_group = group

    this_geom = _unwrap(joint.geometryOrOriginOne if matched_side == 0 else joint.geometryOrOriginTwo)
    other_geom = _unwrap(joint.geometryOrOriginTwo if matched_side == 0 else joint.geometryOrOriginOne)

    _highlight_side(group, this_geom, _THIS_SIDE_COLOR, _MARKER_PIXELS_THIS)
    _highlight_side(group, other_geom, _OTHER_SIDE_COLOR, _MARKER_PIXELS_OTHER)

    if this_geom is not None and other_geom is not None:
        try:
            coords = adsk.fusion.CustomGraphicsCoordinates.create([
                this_geom.origin.x, this_geom.origin.y, this_geom.origin.z,
                other_geom.origin.x, other_geom.origin.y, other_geom.origin.z,
            ])
            connector = group.addLines(coords, [], False, [])
            connector.color = _solid_color(_CONNECTOR_COLOR)
            connector.weight = 3
            connector.depthPriority = _DEPTH_EXACT
        except Exception:
            pass

    _refresh_viewport()


def _refresh_viewport():
    # CustomGraphics changes made via the API don't always trigger an
    # automatic repaint on their own -- force one so switching joints in the
    # dropdown updates the viewport immediately instead of only on the next
    # unrelated redraw (camera orbit, etc). Only ever called once per redraw
    # cycle -- calling it twice back-to-back (e.g. once from a clear and
    # again from the draw that follows) was observed to occasionally raise,
    # which then silently dropped the highlight group reference.
    try:
        app.activeViewport.refresh()
    except Exception as err:
        futil.log(f'{CMD_NAME}: viewport refresh failed: {err}')
