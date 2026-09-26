import adsk.core
import adsk.fusion
import os
from ...lib import fusionAddInUtils as futil
from ... import config
from .shaft_gen import (_create_shaft, _hex_spacer_bore_dims_cm, delete_shaft_bearings,
                        shaft_outer_occurrence, BEARING_NONE, bearing_choices, bearing_part_name)
from .tube_gen import _create_tube
from .pulley_gen import (_create_pulley, BORE_HALF_HEX, BORE_TYPES, BORE_OFFSET_DEFAULT_IN,
                         ATTR_PULLEY_BORE_TYPE, ATTR_PULLEY_BORE_OFFSET, ATTR_PULLEY_ADAPTER,
                         PULLEY_BELT_WIDTH_ITEMS, pulley_outer_occurrence)
from .belt_gen import _create_belt, handle_belt_selection_changed, register_belt_name_sync, unregister_belt_name_sync
from .sprocket_gen import _create_sprocket
from .chain_gen import _create_chain, handle_chain_selection_changed, register_chain_name_sync, unregister_chain_name_sync

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_PartsGenDialog'
CMD_NAME = 'Parts Gen'
CMD_Description = 'Create FRC robot parts (shafts and tubes)'

IS_PROMOTED = True

ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

local_handlers         = []
edit_local_handlers   = []
ui_handlers            = []
_edit_target_occ       = None   # occurrence being edited; set in ui_command_starting
_edit_ref_entities     = None   # (ref_point, face2) recovered from the edited shaft's stored
                                # entity tokens, re-selected in edit_command_activate --
                                # SelectionCommandInput.addSelection() doesn't stick when it is
                                # called from commandCreated.
_selected_partsgen_occ = None   # currently-selected PartsGen occ; tracked by ui_selection_changed

# ---------------------------------------------------------------------------
# Reference-face highlight (Shaft/Tube preview) -- see command_preview /
# command_destroy and _draw_ref_face_highlight below.
# ---------------------------------------------------------------------------
_ref_face_highlight_group = None

_REF_FACE_COLOR   = (120, 190, 255)  # light blue
_REF_FACE_OPACITY = 0.9
_DEPTH_REF_FACE   = 10

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
IN_TO_CM = 2.54

# ---------------------------------------------------------------------------
# Part types
# ---------------------------------------------------------------------------
PART_SHAFT   = 'Shaft'
PART_TUBE    = 'Tube'
PART_PULLEY  = 'Timing Pulley'
PART_BELT    = 'Timing Belt'
PART_SPROCKET = 'Sprocket'
PART_CHAIN   = 'Chain'

# ---------------------------------------------------------------------------
# Shaft types
# ---------------------------------------------------------------------------
SHAFT_HALF_HEX            = '1/2" Hex Shaft'
SHAFT_THREE_EIGHTH_HEX    = '3/8" Hex Shaft'
SHAFT_MAXSPLINE           = 'MAXSpline Shaft'
SHAFT_HALF_HEX_SPACER     = '1/2" Hex Spacer'
SHAFT_THREE_EIGHTH_SPACER = '3/8" Hex Spacer'
SHAFT_CUSTOM              = 'Custom (Round Tube)'

# Both hex sizes are generated as WCP rounded hex (hex flats + round bearing pilot);
# MAXSpline is REV Robotics' 6-lobe wavy spline shaft. A "Hex Spacer" is the inverse of its
# same-size hex shaft: round OD (user-set) with a clearance-fit hex bore, for a spacer that
# spins freely on that hex shaft. The profile constants for all of these live in shaft_gen.py

# ---------------------------------------------------------------------------
# Tube thickness options  (inches, label)
# ---------------------------------------------------------------------------
THICK_1_16   = '1/16"'
THICK_1_8    = '1/8"'
THICK_CUSTOM = 'Custom'

THICKNESS_MAP = {
    THICK_1_16: 0.0625 * IN_TO_CM,
    THICK_1_8:  0.125  * IN_TO_CM,
}

# ---------------------------------------------------------------------------
# Hole placement offset lives in tube_gen.py

# ---------------------------------------------------------------------------
# Hole size options
# ---------------------------------------------------------------------------
HOLE_RIVENUT = 'Rivenut (19/64")'
HOLE_10_32   = '10-32 (13/64")'
HOLE_CUSTOM  = 'Custom'

# Hole size numeric values live in tube_gen.py; only labels needed here.

# ---------------------------------------------------------------------------
# Length options
# ---------------------------------------------------------------------------
LEN_FACES  = 'Between Two Faces'
LEN_CUSTOM = 'Custom Length'

# ---------------------------------------------------------------------------
# Shaft reference-face joint options -- only meaningful in LEN_FACES mode,
# since that's the only mode where the reference face touches another part.
# ---------------------------------------------------------------------------
JOINT_REVOLUTE = 'Revolute (Spins Freely)'
JOINT_RIGID    = 'Rigid (Fixed)'

# The shaft's optional "Add Bearing" parts (BEARING_NONE, bearing_choices,
# bearing_part_name) are defined once in shaft_gen.py's BEARING_PARTS table.

# ---------------------------------------------------------------------------
# Edit command  (shown only via the right-click marking menu)
# ---------------------------------------------------------------------------
EDIT_CMD_ID          = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_PartsGenEdit'
EDIT_CMD_NAME        = 'Edit with PartsGen'
EDIT_CMD_Description = 'Edit an existing PartsGen part'

# Attribute group / keys — persisted on the component so it can be re-edited
ATTR_GROUP             = 'FRCTools_PartsGen'
ATTR_PART_TYPE         = 'part_type'
ATTR_SHAFT_TYPE        = 'shaft_type'
ATTR_CUSTOM_OD         = 'custom_od_expr'
ATTR_CUSTOM_ID         = 'custom_id_expr'
ATTR_TUBE_WIDTH        = 'tube_width_expr'
ATTR_TUBE_HEIGHT       = 'tube_height_expr'
ATTR_TUBE_THICK        = 'tube_thickness'
ATTR_CUSTOM_THICK      = 'custom_thickness_expr'
ATTR_ADD_HOLES         = 'tube_add_holes'
ATTR_HOLE_SIZE         = 'hole_size'
ATTR_HOLE_DIAM         = 'hole_diam_expr'
ATTR_LEN_EXPR          = 'custom_len_expr'
ATTR_PULLEY_BELT_TYPE   = 'pulley_belt_type'
ATTR_PULLEY_TOOTH_COUNT = 'pulley_tooth_count'
ATTR_PULLEY_BELT_WIDTH  = 'pulley_belt_width'
ATTR_PULLEY_SHOW_TEETH  = 'pulley_show_teeth'
ATTR_BELT_TYPE         = 'belt_type'
ATTR_BELT_WIDTH        = 'belt_width_expr'
ATTR_BELT_SUPPRESS     = 'belt_suppress_teeth'
ATTR_BELT_GEN_PULLEYS  = 'belt_gen_pulleys'
ATTR_BELT_PULLEY_TEETH = 'belt_pulley_teeth'
ATTR_BELT_BORE_TYPE    = 'belt_pulley_bore_type'
ATTR_BELT_BORE_OFFSET  = 'belt_pulley_bore_offset'
ATTR_BELT_ADAPTER      = 'belt_pulley_adapter'

ATTR_SPROCKET_TOOTH_COUNT = 'sprocket_tooth_count'
ATTR_SPROCKET_WIDTH       = 'sprocket_width_expr'
ATTR_SPROCKET_SHOW_TEETH  = 'sprocket_show_teeth'
ATTR_SPROCKET_CHAIN_TYPE  = 'sprocket_chain_type'

ATTR_CHAIN_TYPE           = 'chain_type'
ATTR_CHAIN_SPROCKET_WIDTH = 'chain_sprocket_width_expr'
ATTR_CHAIN_GEN_SPROCKETS  = 'chain_gen_sprockets'
ATTR_CHAIN_SPROCKET_TEETH = 'chain_sprocket_teeth'

ATTR_CUSTOM_NAME          = 'custom_name'

ATTR_CREATE_JOINT = 'shaft_create_joint'
ATTR_JOINT_TYPE   = 'shaft_joint_type'
ATTR_JOINT_FLIP   = 'shaft_joint_flip'
ATTR_REVERSE_DIR  = 'shaft_reverse_direction'
ATTR_BEARING_ENDS = 'shaft_bearing_ends'

# Entity tokens for the Between-Two-Faces picks, so right-click Edit can rebuild the shaft
# where it was instead of dropping back to a Custom Length at the world origin. A Custom
# Length shaft built from a Reference Point (no Face 2) stores only the ref-point token.
ATTR_REF_POINT_TOKEN = 'shaft_ref_point_token'
ATTR_FACE2_TOKEN     = 'shaft_face2_token'


# ===========================================================================
# start / stop
# ===========================================================================

def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER
    )
    futil.add_handler(cmd_def.commandCreated, command_created)

    panel = config.get_frc_panel()
    control = panel.controls.addCommand(cmd_def)
    control.isPromotedByDefault = IS_PROMOTED
    control.isPromoted = IS_PROMOTED

    # Edit command — shown only via the right-click marking menu; no toolbar button
    edit_cmd_def = ui.commandDefinitions.addButtonDefinition(
        EDIT_CMD_ID, EDIT_CMD_NAME, EDIT_CMD_Description, ICON_FOLDER
    )
    futil.add_handler(edit_cmd_def.commandCreated, edit_command_created)

    futil.add_handler(ui.commandStarting,        ui_command_starting, local_handlers=ui_handlers)
    futil.add_handler(ui.activeSelectionChanged, ui_selection_changed, local_handlers=ui_handlers)
    futil.add_handler(ui.markingMenuDisplaying,  ui_marking_menu,      local_handlers=ui_handlers)

    register_belt_name_sync()
    register_chain_name_sync()


def stop():
    panel = config.get_frc_panel()
    command_control  = panel.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)
    edit_cmd_def       = ui.commandDefinitions.itemById(EDIT_CMD_ID)

    if command_control:
        command_control.isPromoted = False
        command_control.deleteMe()
    if command_definition:
        command_definition.deleteMe()
    if edit_cmd_def:
        edit_cmd_def.deleteMe()

    unregister_belt_name_sync()
    unregister_chain_name_sync()

    global ui_handlers
    ui_handlers = []


# ===========================================================================
# command_created  –  build the dialog
# ===========================================================================

def _add_bearing_ends_input(placeInputs: adsk.core.CommandInputs, selected: str,
                            shaft_type: str):
    """Add the shaft's 'Add Bearing' dropdown (shared by the create and edit dialogs)."""
    bearingEndsInp = placeInputs.addDropDownCommandInput(
        'bearing_ends', 'Add Bearing', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    bearingEndsInp.tooltip = ('Add a flanged bearing or bushing to both ends of the shaft, '
                              'rigidly jointed with the flange flush to the shaft end')
    _refresh_bearing_items(bearingEndsInp, shaft_type, bearing_part_name(selected))
    return bearingEndsInp


def _refresh_bearing_items(bearingEndsInp: adsk.core.DropDownCommandInput, shaft_type: str,
                           selected: str = None):
    """Refill the 'Add Bearing' list with the parts that fit `shaft_type`, keeping the
    current pick (or `selected`) when it still fits, else falling back to None."""
    if selected is None:
        item = bearingEndsInp.selectedItem
        selected = item.name if item is not None else BEARING_NONE
    choices = bearing_choices(shaft_type)
    if selected not in choices:
        selected = BEARING_NONE
    bearingEndsInp.listItems.clear()
    for name in choices:
        bearingEndsInp.listItems.add(name, name == selected, '')


def _add_dialog_groups(inputs: adsk.core.CommandInputs):
    """Add the dialog's three groups and return their child collections.

    "Part" holds what to build (type, name, size, length); "Placement" holds where it goes
    (joint, reference picks, direction, bearings); "Display" holds preview-only options.
    Inputs live inside the groups, but itemById on the command's top-level inputs still
    finds them -- in inputChanged, though, `args.inputs` is only the changed input's own
    group, so look inputs up from the command instead.
    """
    partGroup = inputs.addGroupCommandInput('part_group', 'Part')
    partGroup.isExpanded = True
    placeGroup = inputs.addGroupCommandInput('placement_group', 'Placement')
    placeGroup.isExpanded = True
    displayGroup = inputs.addGroupCommandInput('display_group', 'Display')
    displayGroup.isExpanded = True
    return partGroup.children, placeGroup.children, displayGroup.children


def _add_pulley_belt_width_input(partInputs: adsk.core.CommandInputs,
                                 width_name: str, visible: bool):
    """Add the Timing Pulley's Belt Width dropdown (shared by the create and edit dialogs).
    A saved width that isn't one of the options (older pulleys) falls back to the first."""
    beltWidthInp = partInputs.addDropDownCommandInput(
        'belt_width', 'Belt Width', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    for name in PULLEY_BELT_WIDTH_ITEMS:
        beltWidthInp.listItems.add(name, name == width_name, '')
    if beltWidthInp.selectedItem is None:
        beltWidthInp.listItems.item(0).isSelected = True
    beltWidthInp.isVisible = visible


def _add_pulley_bore_inputs(partInputs: adsk.core.CommandInputs,
                            bore_type: str, offset_expr: str, visible: bool,
                            adapter: bool = False):
    """Add the Timing Pulley's Bore Type dropdown, Bore Offset input and 3D Print Adapter
    checkbox (shared by the create and edit dialogs)."""
    boreTypeInp = partInputs.addDropDownCommandInput(
        'pulley_bore_type', 'Bore', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    for name in BORE_TYPES:
        boreTypeInp.listItems.add(name, name == bore_type, '')
    if boreTypeInp.selectedItem is None:
        boreTypeInp.listItems.item(0).isSelected = True
    boreTypeInp.isVisible = visible

    boreOffsetInp = partInputs.addValueInput(
        'pulley_bore_offset', 'Bore Offset', 'in',
        adsk.core.ValueInput.createByString(offset_expr)
    )
    boreOffsetInp.tooltip = ('Added to every side of the bore: positive = looser, '
                             'negative = tighter.')
    boreOffsetInp.isVisible = visible

    adapterInp = partInputs.addBoolValueInput('pulley_adapter', '3D Print Adapter', True, '',
                                              adapter)
    adapterInp.tooltip = ('Press a 3D-printed hub adapter into the bottom of the pulley: '
                          'WCP-1121 for 1/2" Hex, WCP-1021 for SplineXS. The pulley is '
                          'pocketed to fit it, and both are placed in their own group.')
    adapterInp.isVisible = visible


def _add_belt_groups(inputs: adsk.core.CommandInputs, visible: bool,
                     belt_type: str = 'HTD 5mm Pitch',
                     width_name: str = PULLEY_BELT_WIDTH_ITEMS[0],
                     suppress_teeth: bool = True, belt_type_enabled: bool = False,
                     gen_pulleys: bool = True, pulley_teeth: bool = False,
                     bore_offset_expr: str = f'{BORE_OFFSET_DEFAULT_IN} in',
                     bore_types=(BORE_HALF_HEX, BORE_HALF_HEX),
                     adapters=(False, False)):
    """Add the Timing Belt's "Belt", "Pulleys", "Pulley 1" and "Pulley 2" groups (shared
    by the create and edit dialogs). The one Width dropdown sizes both the belt and its
    generated pulleys, and the pulley options mirror the Timing Pulley part's, with Bore
    and 3D Print Adapter chosen per pulley. command_input_changed shows the groups only
    for Timing Belt, and the pulley options only with Generate Pulleys on."""
    beltGroup = inputs.addGroupCommandInput('belt_group', 'Belt')
    beltGroup.isExpanded = True
    beltGroup.isVisible  = visible
    beltInputs = beltGroup.children

    tbCirclesInp = beltInputs.addSelectionInput(
        'tb_pitch_circles', 'End Circles', 'Select a C-C Line or two pitch circles'
    )
    tbCirclesInp.addSelectionFilter('SketchCurves')
    tbCirclesInp.setSelectionLimits(2 if visible else 0, 2)

    tbBeltTypeInp = beltInputs.addDropDownCommandInput(
        'tb_belt_type', 'Timing Belt Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    tbBeltTypeInp.listItems.add('HTD 5mm Pitch', belt_type == 'HTD 5mm Pitch', '')
    tbBeltTypeInp.listItems.add('GT2 3mm Pitch', belt_type == 'GT2 3mm Pitch', '')
    if tbBeltTypeInp.selectedItem is None:
        tbBeltTypeInp.listItems.item(0).isSelected = True
    tbBeltTypeInp.isEnabled = belt_type_enabled

    tbBeltWidthInp = beltInputs.addDropDownCommandInput(
        'tb_belt_width', 'Width', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    for name in PULLEY_BELT_WIDTH_ITEMS:
        tbBeltWidthInp.listItems.add(name, name == width_name, '')
    if tbBeltWidthInp.selectedItem is None:
        tbBeltWidthInp.listItems.item(0).isSelected = True
    tbBeltWidthInp.tooltip = 'Belt width -- the generated pulleys are made to match.'

    beltInputs.addBoolValueInput('tb_suppress_teeth', 'Toothless Belt', True, '',
                                 suppress_teeth)

    pulleyGroup = inputs.addGroupCommandInput('belt_pulley_group', 'Pulleys')
    pulleyGroup.isExpanded = True
    pulleyGroup.isVisible  = visible
    pulleyInputs = pulleyGroup.children

    pulleyInputs.addBoolValueInput('tb_gen_pulleys', 'Generate Pulleys', True, '', gen_pulleys)
    tbPulleyTeethInp = pulleyInputs.addBoolValueInput('tb_pulley_teeth', 'Show Teeth', True, '',
                                                      pulley_teeth)
    tbPulleyTeethInp.isVisible = gen_pulleys

    boreOffsetInp = pulleyInputs.addValueInput(
        'tb_bore_offset', 'Bore Offset', 'in',
        adsk.core.ValueInput.createByString(bore_offset_expr)
    )
    boreOffsetInp.tooltip = ('Added to every side of both pulleys\' bores: positive = looser, '
                             'negative = tighter.')
    boreOffsetInp.isVisible = gen_pulleys

    # One group per pulley so each can have its own bore (and adapter, which depends on
    # the bore). Input names can't change at runtime, so the group says which End Circle.
    for i in (1, 2):
        group = inputs.addGroupCommandInput(
            f'belt_pulley{i}_group',
            f'Pulley {i} ({"first" if i == 1 else "second"} End Circle)')
        group.isExpanded = True
        group.isVisible  = visible and gen_pulleys
        children = group.children

        boreTypeInp = children.addDropDownCommandInput(
            f'tb_bore_type_{i}', 'Bore', adsk.core.DropDownStyles.TextListDropDownStyle
        )
        for name in BORE_TYPES:
            boreTypeInp.listItems.add(name, name == bore_types[i - 1], '')
        if boreTypeInp.selectedItem is None:
            boreTypeInp.listItems.item(0).isSelected = True

        adapterInp = children.addBoolValueInput(f'tb_adapter_{i}', '3D Print Adapter', True,
                                                '', adapters[i - 1])
        adapterInp.tooltip = ('Press a 3D-printed hub adapter into the bottom of this pulley: '
                              'WCP-1121 for 1/2" Hex, WCP-1021 for SplineXS. The pulley is '
                              'pocketed to fit it, and both are placed in their own group.')


def command_created(args: adsk.core.CommandCreatedEventArgs):
    inputs = args.command.commandInputs
    partInputs, placeInputs, displayInputs = _add_dialog_groups(inputs)

    # --- Part type -----------------------------------------------------------
    partTypeInp = partInputs.addDropDownCommandInput(
        'part_type', 'Part Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    partTypeInp.listItems.add(PART_SHAFT,    True,  '')
    partTypeInp.listItems.add(PART_TUBE,    False, '')
    partTypeInp.listItems.add(PART_PULLEY,  False, '')
    partTypeInp.listItems.add(PART_BELT,    False, '')
    partTypeInp.listItems.add(PART_SPROCKET, False, '')
    partTypeInp.listItems.add(PART_CHAIN,   False, '')

    # --- Component name (optional override; blank = auto-generated name) -----
    customNameInp = partInputs.addStringValueInput('custom_name', 'Component Name', '')
    customNameInp.tooltip = 'Leave blank to use the automatically generated name.'

    # --- Shaft group ---------------------------------------------------------
    shaftTypeInp = partInputs.addDropDownCommandInput(
        'shaft_type', 'Shaft Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    shaftTypeInp.listItems.add(SHAFT_HALF_HEX, True, '')
    shaftTypeInp.listItems.add(SHAFT_THREE_EIGHTH_HEX, False, '')
    shaftTypeInp.listItems.add(SHAFT_MAXSPLINE, False, '')
    shaftTypeInp.listItems.add(SHAFT_HALF_HEX_SPACER, False, '')
    shaftTypeInp.listItems.add(SHAFT_THREE_EIGHTH_SPACER, False, '')
    shaftTypeInp.listItems.add(SHAFT_CUSTOM, False, '')

    customOD = partInputs.addValueInput(
        'custom_od', 'Outer Diameter', 'in',
        adsk.core.ValueInput.createByString('0.75 in')
    )
    customOD.isVisible = False

    customID = partInputs.addValueInput(
        'custom_id', 'Bore Diameter', 'in',
        adsk.core.ValueInput.createByString('0.159 in')
    )
    customID.isVisible = False

    # --- Tube group ----------------------------------------------------------
    tubeWidthInp = partInputs.addValueInput(
        'tube_width', 'Width', 'in',
        adsk.core.ValueInput.createByString('2 in')
    )
    tubeWidthInp.isVisible = False

    tubeHeightInp = partInputs.addValueInput(
        'tube_height', 'Height', 'in',
        adsk.core.ValueInput.createByString('1 in')
    )
    tubeHeightInp.isVisible = False

    tubeThickInp = partInputs.addDropDownCommandInput(
        'tube_thickness', 'Wall Thickness', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    tubeThickInp.listItems.add(THICK_1_16, True, '')
    tubeThickInp.listItems.add(THICK_1_8, False, '')
    tubeThickInp.listItems.add(THICK_CUSTOM, False, '')
    tubeThickInp.isVisible = False

    customThickInp = partInputs.addValueInput(
        'custom_thickness', 'Custom Thickness', 'in',
        adsk.core.ValueInput.createByString('0.1 in')
    )
    customThickInp.isVisible = False

    # --- Tube face holes -----------------------------------------------------
    tubeHolesInp = partInputs.addBoolValueInput('tube_add_holes', 'Add Corner Holes', True, '', True)
    tubeHolesInp.isVisible = False

    holeSizeInp = partInputs.addDropDownCommandInput(
        'hole_size', 'Hole Size', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    holeSizeInp.listItems.add(HOLE_RIVENUT, True, '')
    holeSizeInp.listItems.add(HOLE_10_32, False, '')
    holeSizeInp.listItems.add(HOLE_CUSTOM, False, '')
    holeSizeInp.isVisible = False

    holeDiamInp = partInputs.addValueInput(
        'hole_diameter', 'Custom Hole Diameter', 'in',
        adsk.core.ValueInput.createByString('0.25 in')
    )
    holeDiamInp.isVisible = False

    # --- Length group (shared) -----------------------------------------------
    lenTypeInp = partInputs.addDropDownCommandInput(
        'length_type', 'Length', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    lenTypeInp.listItems.add(LEN_FACES, True, '')
    lenTypeInp.listItems.add(LEN_CUSTOM, False, '')

    customLenInp = partInputs.addValueInput(
        'custom_length', 'Length', 'in',
        adsk.core.ValueInput.createByString('6 in')
    )
    customLenInp.isVisible = False

    # --- Placement group -----------------------------------------------------
    # Order: Create Joint, Reference Face/Point, Face 2, Reverse Direction, Joint Type,
    # Flip, Add Bearing.
    createJointInp = placeInputs.addBoolValueInput(
        'create_joint', 'Create Joint at Reference Face', True, '', True)
    createJointInp.isVisible = True

    # Face 1 -- used only by Tube now. Shaft uses Reference Point instead (below): a
    # face's own "center" is its area centroid, which is wrong for any face that isn't
    # fully symmetric (e.g. a plate face with other holes in it), so Shaft needs an
    # explicit point or circular edge to build from, not a face-center guess.
    face1Sel = placeInputs.addSelectionInput(
        'face1_selection', 'Reference Face', 'Select the starting planar face'
    )
    face1Sel.addSelectionFilter('PlanarFaces')
    face1Sel.setSelectionLimits(0, 1)
    face1Sel.isVisible = False

    # Reference Point -- Shaft only. Defines both where the shaft is built from AND
    # (when "Create Joint" is checked) the joint's target on the other part -- one
    # selection serves both roles, picked exactly like Fusion's own Joint command: a
    # point, edge, or face, snapping to its center/midpoint/centroid keypoint.
    refPointSel = placeInputs.addSelectionInput(
        'ref_point_selection', 'Reference Point',
        'Select a point, edge, or face that the shaft is built from -- just like picking a '
        'joint origin in the Joint command'
    )
    refPointSel.addSelectionFilter('Vertices')
    refPointSel.addSelectionFilter('SketchPoints')
    refPointSel.addSelectionFilter('ConstructionPoints')
    refPointSel.addSelectionFilter('Edges')
    refPointSel.addSelectionFilter('Faces')
    refPointSel.setSelectionLimits(1, 1)
    refPointSel.isVisible = True

    face2Sel = placeInputs.addSelectionInput(
        'face2_selection', 'Face 2', 'Select the ending planar face'
    )
    face2Sel.addSelectionFilter('PlanarFaces')
    face2Sel.setSelectionLimits(1, 1)
    face2Sel.isVisible = True

    # Custom Length has no Face 2 to derive an extrude direction from, so once a Reference
    # Point is picked (to place the shaft and/or joint it), which way the sketch plane's own
    # normal happens to point is arbitrary -- this is a plain manual override for that, same
    # spirit as the joint's own Flip checkbox below.
    reverseDirInp = placeInputs.addBoolValueInput(
        'reverse_direction', 'Reverse Direction', True, '', False)
    reverseDirInp.isVisible = False

    jointTypeInp = placeInputs.addDropDownCommandInput(
        'joint_type', 'Joint Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    jointTypeInp.listItems.add(JOINT_REVOLUTE, True, '')
    jointTypeInp.listItems.add(JOINT_RIGID, False, '')
    jointTypeInp.isVisible = True

    # Flip -- exactly the native Joint command's own Flip checkbox: no attempt is made to
    # guess which of the two valid orientations is "correct" from the Reference Point pick,
    # the user just toggles this if the shaft comes out backwards.
    flipJointInp = placeInputs.addBoolValueInput('flip_joint', 'Flip', True, '', False)
    flipJointInp.isVisible = True

    _add_bearing_ends_input(placeInputs, BEARING_NONE, shaftTypeInp.selectedItem.name)

    # --- Display group -------------------------------------------------------
    displayInputs.addBoolValueInput(
        'highlight_ref_face', 'Highlight Reference Face', True, '', True)

    # --- Pulley group --------------------------------------------------------
    beltTypeInp = partInputs.addDropDownCommandInput(
        'belt_type', 'Timing Belt Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    beltTypeInp.listItems.add('HTD 5mm Pitch', True,  '')
    beltTypeInp.listItems.add('GT2 3mm Pitch', False, '')
    beltTypeInp.isVisible = False

    defaultLengthUnits = ''
    toothCountInp = partInputs.addValueInput(
        'tooth_count', 'Tooth Count', defaultLengthUnits,
        adsk.core.ValueInput.createByString('18')
    )
    toothCountInp.isVisible = False

    _add_pulley_belt_width_input(partInputs, PULLEY_BELT_WIDTH_ITEMS[0], False)

    pulleyShowTeethInp = partInputs.addBoolValueInput('pulley_show_teeth', 'Show Teeth', True, '', False)
    pulleyShowTeethInp.isVisible = False

    _add_pulley_bore_inputs(partInputs, BORE_HALF_HEX, f'{BORE_OFFSET_DEFAULT_IN} in', False)

    # --- Timing Belt ("Belt" and "Pulleys" groups) ----------------------------
    _add_belt_groups(inputs, False)

    # --- Chain Sprocket group ------------------------------------------------
    sprocketToothCountInp = partInputs.addValueInput(
        'sprocket_tooth_count', 'Tooth Count', '',
        adsk.core.ValueInput.createByString('12')
    )
    sprocketToothCountInp.isVisible = False

    sprocketWidthInp = partInputs.addValueInput(
        'sprocket_width', 'Sprocket Width', 'in',
        adsk.core.ValueInput.createByString('0.375 in')
    )
    sprocketWidthInp.isVisible = False

    sprocketShowTeethInp = partInputs.addBoolValueInput(
        'sprocket_show_teeth', 'Show Teeth', True, '', False)
    sprocketShowTeethInp.isVisible = False

    sprocketChainTypeInp = partInputs.addDropDownCommandInput(
        'sprocket_chain_type', 'Chain Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    sprocketChainTypeInp.listItems.add('#25 Chain', True,  '')
    sprocketChainTypeInp.listItems.add('#35 Chain', False, '')
    sprocketChainTypeInp.isVisible = False

    # --- Chain group ---------------------------------------------------------
    chainCirclesInp = partInputs.addSelectionInput(
        'chain_pitch_circles', 'End Circles', 'Select a #25 or #35 Chain C-C Line or two pitch circles'
    )
    chainCirclesInp.addSelectionFilter('SketchCurves')
    chainCirclesInp.setSelectionLimits(0, 2)
    chainCirclesInp.isVisible = False

    chainSprocketWidthInp = partInputs.addValueInput(
        'chain_sprocket_width', 'Chain Width', 'in',
        adsk.core.ValueInput.createByString('0.375 in')
    )
    chainSprocketWidthInp.isVisible = False

    chainGenSprocketsInp = partInputs.addBoolValueInput(
        'chain_gen_sprockets', 'Generate Sprockets', True, '', True)
    chainGenSprocketsInp.isVisible = False

    chainSprocketTeethInp = partInputs.addBoolValueInput(
        'chain_sprocket_teeth', 'Sprocket Teeth', True, '', False)
    chainSprocketTeethInp.isVisible = False
    futil.add_handler(args.command.execute,        command_execute,        local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged,   command_input_changed,  local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview,        local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy,        command_destroy,        local_handlers=local_handlers)


# ===========================================================================
# inputChanged  –  show / hide inputs dynamically
# ===========================================================================

def command_input_changed(args: adsk.core.InputChangedEventArgs):
    # Not args.inputs -- that's only the changed input's own group (see _add_dialog_groups).
    inputs = args.input.parentCommand.commandInputs

    partTypeInp:    adsk.core.DropDownCommandInput   = inputs.itemById('part_type')
    shaftTypeInp:   adsk.core.DropDownCommandInput   = inputs.itemById('shaft_type')
    customOD:       adsk.core.ValueCommandInput      = inputs.itemById('custom_od')
    customID:       adsk.core.ValueCommandInput      = inputs.itemById('custom_id')
    tubeWidthInp:   adsk.core.ValueCommandInput      = inputs.itemById('tube_width')
    tubeHeightInp:  adsk.core.ValueCommandInput      = inputs.itemById('tube_height')
    tubeThickInp:   adsk.core.DropDownCommandInput   = inputs.itemById('tube_thickness')
    customThickInp: adsk.core.ValueCommandInput      = inputs.itemById('custom_thickness')
    tubeHolesInp:   adsk.core.BoolValueCommandInput  = inputs.itemById('tube_add_holes')
    holeSizeInp:    adsk.core.DropDownCommandInput   = inputs.itemById('hole_size')
    holeDiamInp:    adsk.core.ValueCommandInput      = inputs.itemById('hole_diameter')
    lenTypeInp:     adsk.core.DropDownCommandInput   = inputs.itemById('length_type')
    face1Sel:       adsk.core.SelectionCommandInput  = inputs.itemById('face1_selection')
    face2Sel:       adsk.core.SelectionCommandInput  = inputs.itemById('face2_selection')
    customLenInp:   adsk.core.ValueCommandInput      = inputs.itemById('custom_length')
    reverseDirInp:  adsk.core.BoolValueCommandInput  = inputs.itemById('reverse_direction')
    highlightRefFaceInp: adsk.core.BoolValueCommandInput = inputs.itemById('highlight_ref_face')
    createJointInp: adsk.core.BoolValueCommandInput  = inputs.itemById('create_joint')
    jointTypeInp:   adsk.core.DropDownCommandInput   = inputs.itemById('joint_type')
    flipJointInp:   adsk.core.BoolValueCommandInput  = inputs.itemById('flip_joint')
    refPointSel:    adsk.core.SelectionCommandInput  = inputs.itemById('ref_point_selection')
    beltTypeInp:    adsk.core.DropDownCommandInput   = inputs.itemById('belt_type')
    toothCountInp:  adsk.core.ValueCommandInput      = inputs.itemById('tooth_count')
    beltWidthInp:   adsk.core.DropDownCommandInput   = inputs.itemById('belt_width')
    tbCirclesInp:   adsk.core.SelectionCommandInput  = inputs.itemById('tb_pitch_circles')
    tbGenPulleysInp:     adsk.core.BoolValueCommandInput = inputs.itemById('tb_gen_pulleys')
    pulleyShowTeethInp:  adsk.core.BoolValueCommandInput = inputs.itemById('pulley_show_teeth')

    part_type        = partTypeInp.selectedItem.name
    part_is_shaft    = (part_type == PART_SHAFT)
    part_is_tube     = (part_type == PART_TUBE)
    part_is_pulley   = (part_type == PART_PULLEY)
    part_is_belt     = (part_type == PART_BELT)
    part_is_sprocket = (part_type == PART_SPROCKET)
    part_is_chain    = (part_type == PART_CHAIN)
    is_custom_shaft  = (shaftTypeInp.selectedItem.name == SHAFT_CUSTOM)
    is_spacer_shaft  = (shaftTypeInp.selectedItem.name in (SHAFT_HALF_HEX_SPACER, SHAFT_THREE_EIGHTH_SPACER))
    is_custom_thick  = (tubeThickInp.selectedItem.name == THICK_CUSTOM)
    is_custom_hole   = (holeSizeInp.selectedItem.name  == HOLE_CUSTOM)

    # Length dropdown is shared across part types. Tube is most often cut to a specific
    # custom length (e.g. a spacer), while Shaft is most often built Between Two Faces so
    # its joint has both faces to reference -- default each part type to whichever is more
    # common as soon as it's selected, same spirit as the customOD reset below.
    if args.input.id == 'part_type':
        if part_is_tube:
            lenTypeInp.listItems.item(1).isSelected = True  # Custom Length
        elif part_is_shaft:
            lenTypeInp.listItems.item(0).isSelected = True  # Between Two Faces

    is_between_faces = (lenTypeInp.selectedItem.name   == LEN_FACES)
    hide_length      = part_is_pulley or part_is_belt or part_is_sprocket or part_is_chain

    # "Create Joint" defaults on, but in Custom Length it needs a Reference Point that
    # command_validate_input insists on -- so with nothing picked (e.g. an empty design) the
    # inputs stay invalid, executePreview never fires and the shaft never shows up. Turn it
    # off on entering Custom Length unless a Reference Point is already there to joint to.
    if (args.input.id in ('part_type', 'length_type') and part_is_shaft
            and not is_between_faces and createJointInp is not None
            and (refPointSel is None or refPointSel.selectionCount < 1)):
        createJointInp.value = False

    # Shaft inputs
    shaftTypeInp.isVisible   = part_is_shaft
    customOD.isVisible       = part_is_shaft and (is_custom_shaft or is_spacer_shaft)
    customID.isVisible       = part_is_shaft and is_custom_shaft

    # Tube inputs
    tubeWidthInp.isVisible   = part_is_tube
    tubeHeightInp.isVisible  = part_is_tube
    tubeThickInp.isVisible   = part_is_tube
    customThickInp.isVisible = part_is_tube and is_custom_thick
    tubeHolesInp.isVisible   = part_is_tube
    holeSizeInp.isVisible    = part_is_tube and tubeHolesInp.value
    holeDiamInp.isVisible    = part_is_tube and tubeHolesInp.value and is_custom_hole

    # Pulley inputs
    beltTypeInp.isVisible    = part_is_pulley
    toothCountInp.isVisible  = part_is_pulley
    beltWidthInp.isVisible   = part_is_pulley
    if pulleyShowTeethInp is not None:
        pulleyShowTeethInp.isVisible = part_is_pulley
    for bore_inp_id in ('pulley_bore_type', 'pulley_bore_offset', 'pulley_adapter'):
        bore_inp = inputs.itemById(bore_inp_id)
        if bore_inp is not None:
            bore_inp.isVisible = part_is_pulley

    # Timing Belt groups -- the pulley options only matter when pulleys are generated
    gen_pulleys = tbGenPulleysInp is not None and tbGenPulleysInp.value
    for group_id, show in (('belt_group', part_is_belt), ('belt_pulley_group', part_is_belt),
                           ('belt_pulley1_group', part_is_belt and gen_pulleys),
                           ('belt_pulley2_group', part_is_belt and gen_pulleys)):
        group = inputs.itemById(group_id)
        if group is not None:
            group.isVisible = show
    for pulley_inp_id in ('tb_pulley_teeth', 'tb_bore_offset'):
        pulley_inp = inputs.itemById(pulley_inp_id)
        if pulley_inp is not None:
            pulley_inp.isVisible = gen_pulleys

    # Chain Sprocket inputs
    sprocketToothCountInp = inputs.itemById('sprocket_tooth_count')
    sprocketWidthInp      = inputs.itemById('sprocket_width')
    sprocketShowTeethInp  = inputs.itemById('sprocket_show_teeth')
    sprocketChainTypeInp  = inputs.itemById('sprocket_chain_type')
    if sprocketToothCountInp is not None:
        sprocketToothCountInp.isVisible = part_is_sprocket
    if sprocketWidthInp is not None:
        sprocketWidthInp.isVisible = part_is_sprocket
    if sprocketShowTeethInp is not None:
        sprocketShowTeethInp.isVisible = part_is_sprocket
    if sprocketChainTypeInp is not None:
        sprocketChainTypeInp.isVisible = part_is_sprocket

    # Chain inputs
    chainCirclesInp       = inputs.itemById('chain_pitch_circles')
    chainSprocketWidthInp = inputs.itemById('chain_sprocket_width')
    chainGenSprocketsInp  = inputs.itemById('chain_gen_sprockets')
    chainSprocketTeethInp = inputs.itemById('chain_sprocket_teeth')
    if chainCirclesInp is not None:
        chainCirclesInp.isVisible = part_is_chain
    if chainSprocketWidthInp is not None:
        chainSprocketWidthInp.isVisible = part_is_chain
    if chainGenSprocketsInp is not None:
        chainGenSprocketsInp.isVisible = part_is_chain
    if chainSprocketTeethInp is not None:
        chainSprocketTeethInp.isVisible = (
            part_is_chain and chainGenSprocketsInp is not None and chainGenSprocketsInp.value)

    # Length inputs — hidden when Pulley or Belt is selected. Face 1 (a planar face) is
    # only for Tube; Shaft uses Reference Point instead (a point/edge/face pick --
    # see the note where it's declared in command_created for why).
    lenTypeInp.isVisible   = not hide_length
    for group_id in ('placement_group', 'display_group'):
        group = inputs.itemById(group_id)
        if group is not None:
            group.isVisible = not hide_length
    face1Sel.isVisible    = not hide_length and is_between_faces and part_is_tube
    # Reference Point is required in Between-Two-Faces (positions the shaft). In Custom
    # Length it only exists to give the joint below a target, so it's shown only while
    # "Create Joint" is checked -- and cleared when hidden, so a stale pick can't silently
    # keep placing the shaft somewhere the user can no longer see.
    show_ref_point = (not hide_length and part_is_shaft
                      and (is_between_faces
                           or (createJointInp is not None and createJointInp.value)))
    if refPointSel is not None:
        refPointSel.isVisible = show_ref_point
        if not show_ref_point and refPointSel.selectionCount > 0:
            refPointSel.clearSelection()
    face2Sel.isVisible     = not hide_length and is_between_faces
    customLenInp.isVisible = not hide_length and not is_between_faces
    if reverseDirInp is not None:
        reverseDirInp.isVisible = not hide_length and not is_between_faces and part_is_shaft
    if highlightRefFaceInp is not None:
        highlightRefFaceInp.isVisible = not hide_length

    # Reference-face joint -- offered for a Shaft in either length mode, since Reference
    # Point (its target) is now available in both. It reuses Reference Point directly as the
    # joint's target -- no separate pick. Custom Length still requires an actual pick before
    # "Create Joint" can be turned on (enforced in command_validate_input), since the point is
    # optional there.
    show_joint = part_is_shaft
    show_joint_options = (show_joint and createJointInp is not None and createJointInp.value)
    if createJointInp is not None:
        createJointInp.isVisible = show_joint
    if jointTypeInp is not None:
        jointTypeInp.isVisible = show_joint_options
    if flipJointInp is not None:
        flipJointInp.isVisible = show_joint_options

    # "Add Bearing" lists only the parts that fit the chosen shaft type, and hides when no
    # part fits it at all.
    bearingEndsInp = inputs.itemById('bearing_ends')
    if bearingEndsInp is not None:
        shaft_type_name = shaftTypeInp.selectedItem.name
        if args.input.id == 'shaft_type':
            _refresh_bearing_items(bearingEndsInp, shaft_type_name)
        bearingEndsInp.isVisible = (not hide_length and part_is_shaft
                                    and len(bearing_choices(shaft_type_name)) > 1)

    # Sync selection limits with visibility
    if not hide_length and is_between_faces and part_is_tube:
        face1Sel.setSelectionLimits(1, 1)
    else:
        face1Sel.setSelectionLimits(0, 1)
    if refPointSel is not None:
        if not hide_length and is_between_faces and part_is_shaft:
            refPointSel.setSelectionLimits(1, 1)
        else:
            refPointSel.setSelectionLimits(0, 1)
    if not hide_length and is_between_faces:
        face2Sel.setSelectionLimits(1, 1)
    else:
        face2Sel.setSelectionLimits(0, 1)

    if part_is_belt and tbCirclesInp is not None:
        tbCirclesInp.setSelectionLimits(2, 2)
    elif tbCirclesInp is not None:
        tbCirclesInp.setSelectionLimits(0, 2)

    if chainCirclesInp is not None:
        if part_is_chain:
            chainCirclesInp.setSelectionLimits(2, 2)
        else:
            chainCirclesInp.setSelectionLimits(0, 2)

    if part_is_belt and tbCirclesInp is not None and args.input.id == 'part_type':
        tbCirclesInp.hasFocus = True

    if part_is_chain and chainCirclesInp is not None and args.input.id == 'part_type':
        chainCirclesInp.hasFocus = True

    # Outer Diameter is shared between Custom (Round Tube) and the two Hex Spacer shaft
    # types; reset it to a sensible starting point for whichever one was just picked
    # instead of leaving whatever the field last held.
    if args.input.id == 'shaft_type':
        if is_spacer_shaft:
            customOD.value = 0.65 * IN_TO_CM
        elif is_custom_shaft:
            customOD.value = 0.75 * IN_TO_CM

    # Auto-focus Face 1 (Tube) / Reference Point (Shaft) when switching length mode --
    # Reference Point is now offered (optionally) in Custom Length too, so focus it there
    # as well rather than only when switching to Between Two Faces.
    if args.input.id in ('length_type', 'create_joint'):
        if part_is_shaft and refPointSel is not None and show_ref_point:
            refPointSel.hasFocus = True
        elif part_is_tube and is_between_faces:
            face1Sel.hasFocus = True

    # Auto-advance to Face 2 once Face 1 / Reference Point is filled
    if args.input.id == 'face1_selection' and face1Sel.selectionCount >= 1:
        face2Sel.hasFocus = True
    if (args.input.id == 'ref_point_selection' and refPointSel is not None
            and refPointSel.selectionCount >= 1):
        face2Sel.hasFocus = True

    # CCLine detection for Timing Belt circles
    if args.input.id == 'tb_pitch_circles':
        handle_belt_selection_changed(inputs)

    # CCLine detection for Chain circles
    if args.input.id == 'chain_pitch_circles':
        handle_chain_selection_changed(inputs)


# ===========================================================================
# execute / preview
# ===========================================================================

def _run_part_creation(inputs: adsk.core.CommandInputs, show_message_box: bool,
                       is_preview: bool = False):
    """Dispatch to the selected part type's creation function.

    Returns (success, ref_face): success is False if an exception was caught — used
    by command_preview to report an honest isValidResult instead of always True, and
    to keep preview-time failures out of a blocking message box. ref_face is the
    BRepFace the part was extruded from (Shaft/Tube only, else None), used to draw
    the reference-face highlight.

    `is_preview` lets a generator skip work that only matters on the committed
    result; the shaft and tube use it to skip their (slow) sketch constraining.
    """
    partTypeInp: adsk.core.DropDownCommandInput = inputs.itemById('part_type')
    part_type = partTypeInp.selectedItem.name
    try:
        ref_face = None
        if part_type == PART_SHAFT:
            ref_face = _create_shaft(inputs, constrain=not is_preview)
        elif part_type == PART_TUBE:
            ref_face = _create_tube(inputs, constrain=not is_preview)
        elif part_type == PART_PULLEY:
            # The pulley reports its own errors (quietly in preview) and returns False.
            if not _create_pulley(inputs, is_preview=is_preview):
                return False, None
        elif part_type == PART_SPROCKET:
            _create_sprocket(inputs)
        elif part_type == PART_CHAIN:
            _create_chain(inputs)
        else:
            _create_belt(inputs)
        return True, ref_face
    except Exception:
        futil.handle_error('PartsGen command_execute', show_message_box=show_message_box)
        return False, None


def command_execute(args: adsk.core.CommandEventArgs):
    _run_part_creation(args.command.commandInputs, show_message_box=True)


def command_preview(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    partTypeInp: adsk.core.DropDownCommandInput = inputs.itemById('part_type')
    part_type = partTypeInp.selectedItem.name
    if part_type == PART_BELT:
        _create_belt(inputs, is_preview=True)
        # A toothless-belt preview is the full result -- unless its pulleys need 3D print
        # adapters, which the preview skips (a cloud insert per tick).
        suppressTeethInp = inputs.itemById('tb_suppress_teeth')
        genPulleysInp    = inputs.itemById('tb_gen_pulleys')
        belt_adapter = (genPulleysInp is not None and genPulleysInp.value
                        and any(inputs.itemById(f'tb_adapter_{i}') is not None
                                and inputs.itemById(f'tb_adapter_{i}').value for i in (1, 2)))
        if suppressTeethInp and suppressTeethInp.value and not belt_adapter:
            args.isValidResult = True
        _clear_ref_face_highlight()
    elif part_type == PART_CHAIN:
        _create_chain(inputs, is_preview=True)
        _clear_ref_face_highlight()
    else:
        # Quiet: preview can fire with a transient/invalid input state (e.g. mid-typing
        # a value) — don't pop a blocking message box on every tick, and report failure
        # honestly instead of always claiming success (previously masked here).
        ok, ref_face = _run_part_creation(inputs, show_message_box=False, is_preview=True)
        # A shaft/tube preview skips sketch constraining to stay responsive, so it must
        # not be reused as the result — let command_execute rebuild it fully constrained.
        # Likewise a pulley preview skips its 3D print adapter (a cloud insert per tick).
        adapterInp = inputs.itemById('pulley_adapter')
        pulley_adapter = (part_type == PART_PULLEY and adapterInp is not None
                          and adapterInp.value)
        args.isValidResult = (ok and part_type not in (PART_SHAFT, PART_TUBE)
                              and not pulley_adapter)
        highlightRefFaceInp = inputs.itemById('highlight_ref_face')
        highlight_enabled = highlightRefFaceInp is None or highlightRefFaceInp.value
        try:
            _draw_ref_face_highlight(ref_face if highlight_enabled else None)
        except Exception:
            futil.handle_error('PartsGen ref face highlight', show_message_box=False)


# ===========================================================================
# validateInputs
# ===========================================================================

def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs

    partTypeInp:    adsk.core.DropDownCommandInput  = inputs.itemById('part_type')
    shaftTypeInp:   adsk.core.DropDownCommandInput  = inputs.itemById('shaft_type')
    customOD:       adsk.core.ValueCommandInput     = inputs.itemById('custom_od')
    customID:       adsk.core.ValueCommandInput     = inputs.itemById('custom_id')
    tubeWidthInp:   adsk.core.ValueCommandInput     = inputs.itemById('tube_width')
    tubeHeightInp:  adsk.core.ValueCommandInput     = inputs.itemById('tube_height')
    tubeThickInp:   adsk.core.DropDownCommandInput  = inputs.itemById('tube_thickness')
    customThickInp: adsk.core.ValueCommandInput     = inputs.itemById('custom_thickness')
    lenTypeInp:     adsk.core.DropDownCommandInput  = inputs.itemById('length_type')
    face1Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face1_selection')
    face2Sel:       adsk.core.SelectionCommandInput = inputs.itemById('face2_selection')
    refPointSel:    adsk.core.SelectionCommandInput = inputs.itemById('ref_point_selection')
    customLenInp:   adsk.core.ValueCommandInput     = inputs.itemById('custom_length')

    part_type = partTypeInp.selectedItem.name

    # --- Timing Pulley validation -------------------------------------------
    if part_type == PART_PULLEY:
        toothCountInp:  adsk.core.ValueCommandInput     = inputs.itemById('tooth_count')
        beltWidthInp:   adsk.core.DropDownCommandInput  = inputs.itemById('belt_width')
        if toothCountInp is None or toothCountInp.value < 8:
            args.areInputsValid = False
            return
        if beltWidthInp is None or beltWidthInp.selectedItem is None:
            args.areInputsValid = False
            return
        # Bore offset is per side; keep it to a sane clearance/interference range.
        boreOffsetInp = inputs.itemById('pulley_bore_offset')
        if boreOffsetInp is not None and abs(boreOffsetInp.value) > 0.05 * 2.54:
            args.areInputsValid = False
            return
        args.areInputsValid = True
        return

    # --- Chain Sprocket validation ------------------------------------------
    if part_type == PART_SPROCKET:
        tc = inputs.itemById('sprocket_tooth_count')
        sw = inputs.itemById('sprocket_width')
        if tc is None or tc.value < 9:
            args.areInputsValid = False
            return
        if sw is None or sw.value <= 0:
            args.areInputsValid = False
            return
        args.areInputsValid = True
        return

    # --- Timing Belt validation ---------------------------------------------
    if part_type == PART_BELT:
        tbCirclesInp:   adsk.core.SelectionCommandInput = inputs.itemById('tb_pitch_circles')
        tbBeltWidthInp: adsk.core.DropDownCommandInput  = inputs.itemById('tb_belt_width')
        if tbCirclesInp is None or tbCirclesInp.selectionCount < 2:
            args.areInputsValid = False
            return
        if tbBeltWidthInp is None or tbBeltWidthInp.selectedItem is None:
            args.areInputsValid = False
            return
        # Same per-side bore offset range as the Timing Pulley.
        tbBoreOffsetInp = inputs.itemById('tb_bore_offset')
        if tbBoreOffsetInp is not None and abs(tbBoreOffsetInp.value) > 0.05 * 2.54:
            args.areInputsValid = False
            return
        args.areInputsValid = True
        return

    # --- Chain validation ---------------------------------------------------
    if part_type == PART_CHAIN:
        chainCirclesInp = inputs.itemById('chain_pitch_circles')
        chainWidthInp   = inputs.itemById('chain_sprocket_width')
        if chainCirclesInp is None or chainCirclesInp.selectionCount < 2:
            args.areInputsValid = False
            return
        if chainWidthInp is None or chainWidthInp.value <= 0:
            args.areInputsValid = False
            return
        args.areInputsValid = True
        return

    len_type  = lenTypeInp.selectedItem.name

    # Length validation -- Shaft uses Reference Point, Tube uses Face 1
    if len_type == LEN_FACES:
        if part_type == PART_SHAFT:
            if refPointSel is None or refPointSel.selectionCount < 1 or face2Sel.selectionCount < 1:
                args.areInputsValid = False
                return
        else:
            if face1Sel.selectionCount < 1 or face2Sel.selectionCount < 1:
                args.areInputsValid = False
                return
    else:
        if customLenInp.value <= 0:
            args.areInputsValid = False
            return
        # Custom Length's Reference Point is optional -- but "Create Joint" needs a real
        # target, so require the pick only once the checkbox is actually turned on.
        if part_type == PART_SHAFT:
            createJointInp = inputs.itemById('create_joint')
            if (createJointInp is not None and createJointInp.value
                    and (refPointSel is None or refPointSel.selectionCount < 1)):
                args.areInputsValid = False
                return

    if part_type == PART_SHAFT:
        shaft_type = shaftTypeInp.selectedItem.name
        if shaft_type == SHAFT_CUSTOM:
            od = customOD.value
            id_ = customID.value
            if od <= 0 or id_ <= 0 or id_ >= od:
                args.areInputsValid = False
                return
        elif shaft_type in (SHAFT_HALF_HEX_SPACER, SHAFT_THREE_EIGHTH_SPACER):
            od = customOD.value
            _, bore_round_dia_cm = _hex_spacer_bore_dims_cm(shaft_type)
            if od <= 0 or od <= bore_round_dia_cm:
                args.areInputsValid = False
                return
        # No separate joint-point check needed -- Reference Point is already required
        # above whenever len_type == LEN_FACES, and it doubles as the joint's target.
    else:
        w = tubeWidthInp.value
        h = tubeHeightInp.value
        if tubeThickInp.selectedItem.name == THICK_CUSTOM:
            t = customThickInp.value
        else:
            t = THICKNESS_MAP[tubeThickInp.selectedItem.name]
        if w <= 0 or h <= 0 or t <= 0 or t >= min(w, h) / 2.0:
            args.areInputsValid = False
            return
        tubeHolesInp: adsk.core.BoolValueCommandInput = inputs.itemById('tube_add_holes')
        if tubeHolesInp.value:
            holeSizeInp: adsk.core.DropDownCommandInput = inputs.itemById('hole_size')
            if holeSizeInp.selectedItem.name == HOLE_CUSTOM:
                holeDiamInp: adsk.core.ValueCommandInput = inputs.itemById('hole_diameter')
                if holeDiamInp.value <= 0:
                    args.areInputsValid = False
                    return

    args.areInputsValid = True


# ===========================================================================
# destroy
# ===========================================================================

def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers
    local_handlers = []
    _clear_ref_face_highlight()
    try:
        app.activeViewport.refresh()
    except Exception:
        pass


# ===========================================================================
# Reference-face highlight (live preview only)
# ===========================================================================
# A transient CustomGraphics overlay on the real start-cap face an extrude feature was
# built from (Shaft/Tube), so the user can tell which side of the part is the
# reference/extrude-from side while the dialog is still open and being adjusted.
# Cleared in command_destroy — the color that stays after OK is a separate, permanent
# BRepFace.appearance override applied by _create_shaft/_create_tube themselves once
# the real (non-preview) geometry is built. CustomGraphics is used here rather than
# that same appearance override, because per LESSONS_LEARNED.md, writing
# Occurrence.appearance from a live command handler either throws (read-only context)
# or, deferred, destabilizes an open SelectionCommandInput — CustomGraphics never
# mutates the design, so neither failure applies, and it's proven safe to redraw every
# executePreview tick even with Face 1/Face 2 selection inputs open (see
# commands/JointInspector/entry.py, the source of this pattern).

def _is_live(entity) -> bool:
    try:
        return entity is not None and entity.isValid
    except Exception:
        return False


def _solid_color(rgb):
    r, g, b = rgb
    return adsk.fusion.CustomGraphicsSolidColorEffect.create(adsk.core.Color.create(r, g, b, 255))


def _calc_mesh(entity):
    """Compute a fresh triangle mesh for `entity` (a BRepFace or BRepBody).

    Deliberately not entity.meshManager.displayMeshes.bestMesh: that reads Fusion's
    cached display mesh, which is empty for geometry created moments earlier in this
    same preview tick (confirmed live: bestMesh threw "InternalValidationError:
    count > 0" on a body extruded seconds before, in the same script). The reference
    face/body here is always freshly rebuilt every executePreview call, so the mesh
    has to be calculated on demand instead of assumed to already be cached.
    """
    try:
        calc = entity.meshManager.createMeshCalculator()
        calc.setQuality(adsk.fusion.TriangleMeshQualityOptions.NormalQualityTriangleMesh)
        return calc.calculate()
    except Exception as err:
        futil.log(f'{CMD_NAME}: could not calculate mesh: {err}')
        return None


def _add_mesh(group, mesh, rgb, opacity, depth):
    if mesh is None:
        return
    try:
        points  = mesh.nodeCoordinatesAsDouble
        indices = mesh.nodeIndices
        normals = mesh.normalVectorsAsDouble
    except Exception as err:
        futil.log(f'{CMD_NAME}: display mesh unreadable, skipping: {err}')
        return

    # An empty or inconsistent display mesh -- what a body that is hidden, suppressed
    # or mid-recompute hands back -- takes addMesh down with it (a Fusion crash, not a
    # Python exception), so it is rejected here rather than trusted.
    node_count = len(points) // 3
    if node_count == 0 or not indices or len(normals) != len(points):
        futil.log(f'{CMD_NAME}: skipping degenerate mesh (nodes={node_count}, '
                  f'indices={len(indices) if indices else 0}, '
                  f'normals={len(normals) if normals else 0})')
        return
    if max(indices) >= node_count:
        futil.log(f'{CMD_NAME}: skipping mesh with out-of-range node indices')
        return

    coords = adsk.fusion.CustomGraphicsCoordinates.create(points)
    entity = group.addMesh(coords, indices, normals, [])
    entity.color = _solid_color(rgb)
    entity.setOpacity(opacity, True)
    entity.depthPriority = depth


def _highlight_face(group, face: adsk.fusion.BRepFace, rgb):
    if not _is_live(face):
        return
    _add_mesh(group, _calc_mesh(face), rgb, _REF_FACE_OPACITY, _DEPTH_REF_FACE)


def _clear_ref_face_highlight():
    global _ref_face_highlight_group
    if _ref_face_highlight_group is None:
        return
    group = _ref_face_highlight_group
    _ref_face_highlight_group = None
    try:
        if group.isValid:
            group.deleteMe()
    except Exception as err:
        futil.log(f'{CMD_NAME}: failed to delete reference face highlight: {err}')


def _draw_ref_face_highlight(ref_face):
    global _ref_face_highlight_group
    _clear_ref_face_highlight()
    if not _is_live(ref_face):
        return
    design = adsk.fusion.Design.cast(app.activeProduct)
    group = design.rootComponent.customGraphicsGroups.add()
    _ref_face_highlight_group = group  # set immediately so a half-built group is
                                        # still reachable if something below throws
    _highlight_face(group, ref_face, _REF_FACE_COLOR)


# ===========================================================================
# Helpers — entity → component / occurrence lookup
# ===========================================================================

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


def _find_partsgen_occ(entity) -> adsk.fusion.Occurrence:
    """Return the PartsGen Occurrence that owns entity, or None.

    In 3D design mode, entities from args.selectedEntities are proxy objects
    whose body.parentComponent resolves to the ROOT component rather than
    the sub-component.  We therefore use two preferred paths before falling
    back to the original token-search approach:

    1. entity.assemblyContext  — directly returns the Occurrence for any
       proxy BRep entity (face / edge / body) clicked in 3D root context.
    2. entity.nativeObject     — unwraps the proxy to get the native object,
       whose parentComponent IS the sub-component.
    """
    # --- Path 1: assemblyContext (fastest, most reliable in 3D root context) ---
    try:
        occ = entity.assemblyContext
        if occ and occ.isValid:
            comp = occ.component
            if comp.attributes.itemByName(ATTR_GROUP, ATTR_PART_TYPE):
                return occ
    except Exception:
        pass

    # --- Path 2: nativeObject (unwrap proxy, get native parentComponent) -------
    try:
        native = entity.nativeObject
        if native:
            comp = _get_entity_component(native)
            if comp and comp.attributes.itemByName(ATTR_GROUP, ATTR_PART_TYPE):
                token = comp.entityToken
                design = adsk.fusion.Design.cast(app.activeProduct)
                for occ in design.rootComponent.allOccurrences:
                    try:
                        if occ.component.entityToken == token:
                            return occ
                    except Exception:
                        pass
    except Exception:
        pass

    # --- Path 3: direct parentComponent (works inside component edit context) --
    comp = _get_entity_component(entity)
    if not comp:
        return None
    if not comp.attributes.itemByName(ATTR_GROUP, ATTR_PART_TYPE):
        return None
    try:
        token = comp.entityToken
        design = adsk.fusion.Design.cast(app.activeProduct)
        for occ in design.rootComponent.allOccurrences:
            try:
                if occ.component.entityToken == token:
                    return occ
            except Exception:
                pass
    except Exception:
        pass
    return None


# ===========================================================================
# UI-level event handlers  (selection tracking + marking menu)
# Mirrors the CCDistance pattern: track selection via activeSelectionChanged,
# save the target in commandStarting, add menu item always and show/hide it.
# ===========================================================================

def ui_selection_changed(args: adsk.core.ActiveSelectionEventArgs):
    """Track which (if any) PartsGen occurrence is currently selected."""
    global _selected_partsgen_occ

    _selected_partsgen_occ = None
    if app.activeProduct.objectType != adsk.fusion.Design.classType():
        return

    for sel in args.currentSelection:
        try:
            occ = _find_partsgen_occ(sel.entity)
            if occ:
                _selected_partsgen_occ = occ
                break
        except Exception:
            pass


def ui_command_starting(args: adsk.core.ApplicationCommandEventArgs):
    """Before a command starts, save the selected PartsGen occurrence.

    Firing a command clears the active selection, so ui_selection_changed will
    set _selected_partsgen_occ back to None.  We capture it here first.
    """
    global _edit_target_occ
    if _selected_partsgen_occ:
        _edit_target_occ = _selected_partsgen_occ


def ui_marking_menu(args: adsk.core.MarkingMenuEventArgs):
    """Always insert the Edit menu item; show it only when a PartsGen part is selected.

    Follows the CCDistance pattern: the menu item lives in the control list at all
    times (with a duplicate guard) and its visibility is toggled on every call.
    """
    global _edit_target_occ

    if app.activeProduct.objectType != adsk.fusion.Design.classType():
        return

    controls = args.linearMarkingMenu.controls

    # --- Ensure the menu item exists in this menu instance ------------------
    edit_menu_item = controls.itemById(EDIT_CMD_ID)
    if not edit_menu_item:
        edit_cmd_def = ui.commandDefinitions.itemById(EDIT_CMD_ID)
        if not edit_cmd_def:
            return
        try:
            # Append at end of the linear menu (safest across all Fusion versions)
            edit_menu_item = controls.addCommand(edit_cmd_def, '', False)
        except Exception:
            futil.log('PartsGen: could not add edit item to marking menu')
            return

    if not edit_menu_item:
        return

    # --- Find which PartsGen occurrence is relevant -------------------------
    # Search order:
    #   1. _selected_partsgen_occ — set by ui_selection_changed on left-click
    #   2. args.selectedEntities  — entities under the cursor when right-clicking
    #   3. ui.activeSelections    — current active selection (left-click state)
    found_occ = _selected_partsgen_occ

    if not found_occ:
        for ent in (args.selectedEntities or []):
            try:
                found_occ = _find_partsgen_occ(ent)
                if found_occ:
                    break
            except Exception:
                pass

    if not found_occ:
        for i in range(ui.activeSelections.count):
            try:
                sel = ui.activeSelections.item(i)
                if sel:
                    found_occ = _find_partsgen_occ(sel.entity)
                    if found_occ:
                        break
            except Exception:
                pass

    # Cache the target now so edit_command_created can use it even if selection
    # is cleared before the command fires.
    if found_occ:
        _edit_target_occ = found_occ

    edit_menu_item.isVisible = (found_occ is not None)


# ===========================================================================
# Edit command — created (dialog pre-populated from stored attributes)
# ===========================================================================

def _resolve_entity_token(token: str, label: str):
    """Resolve a stored entity token back to the entity it came from, or None.

    `Design.findEntityByToken` returns a (possibly empty) list and preserves the entity's
    assembly context, so a token taken from a proxy comes back as that same proxy --
    verified live. Unlike `Component.entityToken`, which collides across unrelated
    components, BRep and sketch entity tokens are reliable for this.
    """
    if not token:
        return None
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        found  = design.findEntityByToken(token)
        if found and len(found) > 0:
            return found[0]
    except Exception:
        pass
    futil.log(f'{CMD_NAME} edit: stored {label} could not be resolved; '
              'falling back to Custom Length')
    return None


def edit_command_created(args: adsk.core.CommandCreatedEventArgs):
    global _edit_target_occ, _edit_ref_entities

    inputs = args.command.commandInputs

    # Read stored attributes from the targeted occurrence's component
    attrs = {}
    if _edit_target_occ:
        comp = _edit_target_occ.component
        for key in (ATTR_PART_TYPE, ATTR_SHAFT_TYPE, ATTR_CUSTOM_OD, ATTR_CUSTOM_ID,
                    ATTR_TUBE_WIDTH, ATTR_TUBE_HEIGHT, ATTR_TUBE_THICK, ATTR_CUSTOM_THICK,
                    ATTR_ADD_HOLES, ATTR_HOLE_SIZE, ATTR_HOLE_DIAM, ATTR_LEN_EXPR,
                    ATTR_PULLEY_BELT_TYPE, ATTR_PULLEY_TOOTH_COUNT, ATTR_PULLEY_BELT_WIDTH,
                    ATTR_PULLEY_SHOW_TEETH, ATTR_PULLEY_BORE_TYPE, ATTR_PULLEY_BORE_OFFSET,
                    ATTR_PULLEY_ADAPTER,
                    ATTR_BELT_TYPE, ATTR_BELT_WIDTH, ATTR_BELT_SUPPRESS,
                    ATTR_BELT_GEN_PULLEYS, ATTR_BELT_PULLEY_TEETH, ATTR_BELT_BORE_TYPE,
                    ATTR_BELT_BORE_OFFSET, ATTR_BELT_ADAPTER,
                    *(f'{k}_{i}' for k in (ATTR_BELT_BORE_TYPE, ATTR_BELT_ADAPTER) for i in (1, 2)),
                    ATTR_SPROCKET_TOOTH_COUNT, ATTR_SPROCKET_WIDTH, ATTR_SPROCKET_SHOW_TEETH,
                    ATTR_SPROCKET_CHAIN_TYPE,
                    ATTR_CHAIN_TYPE, ATTR_CHAIN_SPROCKET_WIDTH,
                    ATTR_CHAIN_GEN_SPROCKETS, ATTR_CHAIN_SPROCKET_TEETH,
                    ATTR_CUSTOM_NAME, ATTR_CREATE_JOINT, ATTR_JOINT_TYPE, ATTR_JOINT_FLIP,
                    ATTR_REVERSE_DIR, ATTR_REF_POINT_TOKEN, ATTR_FACE2_TOKEN,
                    ATTR_BEARING_ENDS):
            a = comp.attributes.itemByName(ATTR_GROUP, key)
            if a:
                attrs[key] = a.value

    def _s(key, default=''):
        return attrs.get(key, default)

    def _b(key, default=True):
        v = attrs.get(key)
        return (v.lower() == 'true') if v is not None else default

    part_type       = _s(ATTR_PART_TYPE,    PART_SHAFT)
    shaft_type      = _s(ATTR_SHAFT_TYPE,   SHAFT_HALF_HEX)
    is_shaft        = (part_type == PART_SHAFT)
    is_tube         = (part_type == PART_TUBE)
    is_pulley       = (part_type == PART_PULLEY)
    is_belt         = (part_type == PART_BELT)
    is_sprocket     = (part_type == PART_SPROCKET)
    is_chain        = (part_type == PART_CHAIN)
    is_custom_shaft = (shaft_type == SHAFT_CUSTOM)
    is_spacer_shaft = (shaft_type in (SHAFT_HALF_HEX_SPACER, SHAFT_THREE_EIGHTH_SPACER))

    tube_thick      = _s(ATTR_TUBE_THICK,   THICK_1_8)
    is_custom_thick = (tube_thick == THICK_CUSTOM)

    add_holes       = _b(ATTR_ADD_HOLES,    True)
    hole_size       = _s(ATTR_HOLE_SIZE,    HOLE_RIVENUT)
    is_custom_hole  = (hole_size == HOLE_CUSTOM)

    len_expr        = _s(ATTR_LEN_EXPR,          '6 in')
    od_expr         = _s(ATTR_CUSTOM_OD,          '0.75 in')
    id_expr         = _s(ATTR_CUSTOM_ID,          '0.159 in')
    w_expr          = _s(ATTR_TUBE_WIDTH,         '2 in')
    h_expr          = _s(ATTR_TUBE_HEIGHT,        '1 in')
    thick_expr      = _s(ATTR_CUSTOM_THICK,       '0.1 in')
    hole_diam_expr  = _s(ATTR_HOLE_DIAM,          '0.25 in')
    pulley_belt       = _s(ATTR_PULLEY_BELT_TYPE,   'HTD 5mm Pitch')
    pulley_teeth      = _s(ATTR_PULLEY_TOOTH_COUNT, '18')
    pulley_width      = _s(ATTR_PULLEY_BELT_WIDTH,  PULLEY_BELT_WIDTH_ITEMS[0])
    pulley_show_teeth = _b(ATTR_PULLEY_SHOW_TEETH,  False)
    pulley_adapter    = _b(ATTR_PULLEY_ADAPTER,     False)
    pulley_bore_type  = _s(ATTR_PULLEY_BORE_TYPE,   BORE_HALF_HEX)
    pulley_bore_off   = _s(ATTR_PULLEY_BORE_OFFSET, f'{BORE_OFFSET_DEFAULT_IN} in')
    belt_type_val     = _s(ATTR_BELT_TYPE,          'HTD 5mm Pitch')
    belt_width_val    = _s(ATTR_BELT_WIDTH,         '9 mm')
    belt_suppress     = _b(ATTR_BELT_SUPPRESS,       False)
    belt_gen_pulleys  = _b(ATTR_BELT_GEN_PULLEYS,   True)
    belt_pulley_teeth = _b(ATTR_BELT_PULLEY_TEETH,  False)
    belt_bore_off     = _s(ATTR_BELT_BORE_OFFSET,   f'{BORE_OFFSET_DEFAULT_IN} in')
    # Per-pulley bore/adapter; belts saved before that fall back to the one shared value.
    belt_bore_types   = tuple(_s(f'{ATTR_BELT_BORE_TYPE}_{i}', _s(ATTR_BELT_BORE_TYPE, BORE_HALF_HEX))
                              for i in (1, 2))
    belt_adapters     = tuple(_b(f'{ATTR_BELT_ADAPTER}_{i}', _b(ATTR_BELT_ADAPTER, False))
                              for i in (1, 2))
    # Older belts stored a free-typed width expression ('9', '9 mm'); map it onto the
    # Width dropdown (falls back to its first item when it isn't one of the options).
    try:
        belt_width_mm = round(adsk.fusion.Design.cast(app.activeProduct).unitsManager
                              .evaluateExpression(belt_width_val, 'mm') * 10)
        belt_width_name = f'{belt_width_mm} mm'
    except Exception:
        belt_width_name = PULLEY_BELT_WIDTH_ITEMS[0]
    sprocket_teeth    = _s(ATTR_SPROCKET_TOOTH_COUNT, '12')
    sprocket_width_val = _s(ATTR_SPROCKET_WIDTH,     '0.375 in')
    sprocket_show_teeth_val = _b(ATTR_SPROCKET_SHOW_TEETH, False)
    sprocket_chain_type_val = _s(ATTR_SPROCKET_CHAIN_TYPE, '#25 Chain')
    chain_type_val       = _s(ATTR_CHAIN_TYPE,          '25')
    chain_spr_width_val  = _s(ATTR_CHAIN_SPROCKET_WIDTH, '0.375 in')
    chain_gen_spr_val    = _b(ATTR_CHAIN_GEN_SPROCKETS,  True)
    chain_spr_teeth_val  = _b(ATTR_CHAIN_SPROCKET_TEETH, False)
    custom_name_val      = _s(ATTR_CUSTOM_NAME, '')
    create_joint_val     = _b(ATTR_CREATE_JOINT, False)
    bearing_ends_val     = _s(ATTR_BEARING_ENDS, BEARING_NONE)
    joint_type_val       = _s(ATTR_JOINT_TYPE,  JOINT_REVOLUTE)
    joint_flip_val       = _b(ATTR_JOINT_FLIP,  False)
    reverse_dir_val      = _b(ATTR_REVERSE_DIR, False)

    # Recover the original Reference Point (and, for Between-Two-Faces, Face 2) picks. When
    # both Between-Two-Faces picks still resolve the shaft can be rebuilt exactly where it
    # stands, joint and all. A Custom Length shaft only ever stored a Reference Point (no
    # Face 2), so `keep_ref_point` alone covers re-selecting it there. When a pick is gone
    # (the other part was deleted, say) fall back to the old behaviour of editing as a plain
    # Custom Length at the world origin.
    edit_ref_point = _resolve_entity_token(_s(ATTR_REF_POINT_TOKEN, ''), 'Reference Point')
    edit_face2     = _resolve_entity_token(_s(ATTR_FACE2_TOKEN, ''),     'Face 2')
    keep_faces     = is_shaft and edit_ref_point is not None and edit_face2 is not None
    keep_ref_point = is_shaft and edit_ref_point is not None
    _edit_ref_entities = (edit_ref_point, edit_face2) if keep_ref_point else None

    # Same three groups as the create dialog (see command_created).
    has_len = not is_pulley and not is_belt and not is_sprocket and not is_chain
    partInputs, placeInputs, displayInputs = _add_dialog_groups(inputs)
    inputs.itemById('placement_group').isVisible = has_len
    inputs.itemById('display_group').isVisible = has_len

    # --- Part type ---
    partTypeInp = partInputs.addDropDownCommandInput(
        'part_type', 'Part Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    partTypeInp.listItems.add(PART_SHAFT,    is_shaft,    '')
    partTypeInp.listItems.add(PART_TUBE,    is_tube,     '')
    partTypeInp.listItems.add(PART_PULLEY,  is_pulley,   '')
    partTypeInp.listItems.add(PART_BELT,    is_belt,     '')
    partTypeInp.listItems.add(PART_SPROCKET, is_sprocket, '')
    partTypeInp.listItems.add(PART_CHAIN,   is_chain,    '')

    # --- Component name (optional override; blank = auto-generated name) -----
    customNameInp = partInputs.addStringValueInput('custom_name', 'Component Name', custom_name_val)
    customNameInp.tooltip = 'Leave blank to use the automatically generated name.'

    # --- Shaft group ---
    shaftTypeInp = partInputs.addDropDownCommandInput(
        'shaft_type', 'Shaft Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    shaftTypeInp.listItems.add(SHAFT_HALF_HEX,            shaft_type == SHAFT_HALF_HEX,            '')
    shaftTypeInp.listItems.add(SHAFT_THREE_EIGHTH_HEX,    shaft_type == SHAFT_THREE_EIGHTH_HEX,    '')
    shaftTypeInp.listItems.add(SHAFT_MAXSPLINE,           shaft_type == SHAFT_MAXSPLINE,           '')
    shaftTypeInp.listItems.add(SHAFT_HALF_HEX_SPACER,     shaft_type == SHAFT_HALF_HEX_SPACER,     '')
    shaftTypeInp.listItems.add(SHAFT_THREE_EIGHTH_SPACER, shaft_type == SHAFT_THREE_EIGHTH_SPACER, '')
    shaftTypeInp.listItems.add(SHAFT_CUSTOM,              is_custom_shaft,                         '')
    shaftTypeInp.isVisible = is_shaft

    customOD = partInputs.addValueInput(
        'custom_od', 'Outer Diameter', 'in',
        adsk.core.ValueInput.createByString(od_expr)
    )
    customOD.isVisible = is_shaft and (is_custom_shaft or is_spacer_shaft)

    customID = partInputs.addValueInput(
        'custom_id', 'Bore Diameter', 'in',
        adsk.core.ValueInput.createByString(id_expr)
    )
    customID.isVisible = is_shaft and is_custom_shaft

    # --- Tube group ---
    tubeWidthInp = partInputs.addValueInput(
        'tube_width', 'Width', 'in',
        adsk.core.ValueInput.createByString(w_expr)
    )
    tubeWidthInp.isVisible = is_tube

    tubeHeightInp = partInputs.addValueInput(
        'tube_height', 'Height', 'in',
        adsk.core.ValueInput.createByString(h_expr)
    )
    tubeHeightInp.isVisible = is_tube

    tubeThickInp = partInputs.addDropDownCommandInput(
        'tube_thickness', 'Wall Thickness', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    tubeThickInp.listItems.add(THICK_1_16,   tube_thick == THICK_1_16,    '')
    tubeThickInp.listItems.add(THICK_1_8,    tube_thick == THICK_1_8,     '')
    tubeThickInp.listItems.add(THICK_CUSTOM, is_custom_thick,             '')
    tubeThickInp.isVisible = is_tube

    customThickInp = partInputs.addValueInput(
        'custom_thickness', 'Custom Thickness', 'in',
        adsk.core.ValueInput.createByString(thick_expr)
    )
    customThickInp.isVisible = is_tube and is_custom_thick

    # --- Tube face holes ---
    tubeHolesInp = partInputs.addBoolValueInput('tube_add_holes', 'Add Corner Holes', True, '', add_holes)
    tubeHolesInp.isVisible = is_tube

    holeSizeInp = partInputs.addDropDownCommandInput(
        'hole_size', 'Hole Size', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    holeSizeInp.listItems.add(HOLE_RIVENUT, hole_size == HOLE_RIVENUT, '')
    holeSizeInp.listItems.add(HOLE_10_32,   hole_size == HOLE_10_32,   '')
    holeSizeInp.listItems.add(HOLE_CUSTOM,  is_custom_hole,            '')
    holeSizeInp.isVisible = is_tube and add_holes

    holeDiamInp = partInputs.addValueInput(
        'hole_diameter', 'Custom Hole Diameter', 'in',
        adsk.core.ValueInput.createByString(hole_diam_expr)
    )
    holeDiamInp.isVisible = is_tube and add_holes and is_custom_hole

    # --- Pulley group — values pre-filled from stored attributes ---
    beltTypeInp = partInputs.addDropDownCommandInput(
        'belt_type', 'Timing Belt Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    beltTypeInp.listItems.add('HTD 5mm Pitch', pulley_belt == 'HTD 5mm Pitch', '')
    beltTypeInp.listItems.add('GT2 3mm Pitch', pulley_belt == 'GT2 3mm Pitch', '')
    beltTypeInp.isVisible = is_pulley

    toothCountInp = partInputs.addValueInput(
        'tooth_count', 'Tooth Count', '',
        adsk.core.ValueInput.createByString(pulley_teeth)
    )
    toothCountInp.isVisible = is_pulley

    _add_pulley_belt_width_input(partInputs, pulley_width, is_pulley)

    pulleyShowTeethInp = partInputs.addBoolValueInput(
        'pulley_show_teeth', 'Show Teeth', True, '', pulley_show_teeth)
    pulleyShowTeethInp.isVisible = is_pulley

    _add_pulley_bore_inputs(partInputs, pulley_bore_type, pulley_bore_off, is_pulley,
                            pulley_adapter)

    # --- Timing Belt groups — circles must be re-selected; other values pre-filled ---
    # Belt type is always enabled in edit mode (no CCLine auto-lock).
    _add_belt_groups(inputs, is_belt, belt_type_val, belt_width_name, belt_suppress, True,
                     belt_gen_pulleys, belt_pulley_teeth, belt_bore_off, belt_bore_types,
                     belt_adapters)

    # --- Chain Sprocket group ---
    sprocketToothCountInp = partInputs.addValueInput(
        'sprocket_tooth_count', 'Tooth Count', '',
        adsk.core.ValueInput.createByString(sprocket_teeth)
    )
    sprocketToothCountInp.isVisible = is_sprocket

    sprocketWidthInpEdit = partInputs.addValueInput(
        'sprocket_width', 'Sprocket Width', 'in',
        adsk.core.ValueInput.createByString(sprocket_width_val)
    )
    sprocketWidthInpEdit.isVisible = is_sprocket

    sprocketShowTeethInpEdit = partInputs.addBoolValueInput(
        'sprocket_show_teeth', 'Show Teeth', True, '', sprocket_show_teeth_val)
    sprocketShowTeethInpEdit.isVisible = is_sprocket

    sprocketChainTypeInpEdit = partInputs.addDropDownCommandInput(
        'sprocket_chain_type', 'Chain Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    sprocketChainTypeInpEdit.listItems.add('#25 Chain', sprocket_chain_type_val == '#25 Chain', '')
    sprocketChainTypeInpEdit.listItems.add('#35 Chain', sprocket_chain_type_val == '#35 Chain', '')
    sprocketChainTypeInpEdit.isVisible = is_sprocket

    # --- Chain group ---
    chainCirclesInpEdit = partInputs.addSelectionInput(
        'chain_pitch_circles', 'End Circles', 'Select a #25 or #35 Chain C-C Line or two pitch circles'
    )
    chainCirclesInpEdit.addSelectionFilter('SketchCurves')
    chainCirclesInpEdit.setSelectionLimits(0, 2)
    chainCirclesInpEdit.isVisible = is_chain

    chainSprocketWidthInpEdit = partInputs.addValueInput(
        'chain_sprocket_width', 'Chain Width', 'in',
        adsk.core.ValueInput.createByString(chain_spr_width_val)
    )
    chainSprocketWidthInpEdit.isVisible = is_chain

    chainGenSprocketsInpEdit = partInputs.addBoolValueInput(
        'chain_gen_sprockets', 'Generate Sprockets', True, '', chain_gen_spr_val)
    chainGenSprocketsInpEdit.isVisible = is_chain

    chainSprocketTeethInpEdit = partInputs.addBoolValueInput(
        'chain_sprocket_teeth', 'Sprocket Teeth', True, '', chain_spr_teeth_val)
    chainSprocketTeethInpEdit.isVisible = is_chain and chain_gen_spr_val

    # --- Length ---------------------------------------------------------------
    # Between-Two-Faces survives an edit whenever both stored picks still resolve, so the
    # shaft rebuilds where it stands with its joint intact. Otherwise fall back to editing
    # it as a Custom Length, which rebuilds it at the world origin -- the old behaviour,
    # and all that is possible once the referenced geometry is gone.
    lenTypeInp = partInputs.addDropDownCommandInput(
        'length_type', 'Length', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    lenTypeInp.listItems.add(LEN_FACES,  keep_faces,      '')
    lenTypeInp.listItems.add(LEN_CUSTOM, not keep_faces,  '')
    lenTypeInp.isVisible = has_len

    customLenInp = partInputs.addValueInput(
        'custom_length', 'Length', 'in',
        adsk.core.ValueInput.createByString(len_expr)
    )
    customLenInp.isVisible = has_len and not keep_faces

    # Placement order matches the create dialog (see command_created).
    # Reference-face joint -- offered for a Shaft in either length mode (matches the create
    # dialog); actually creating one still needs a Reference Point pick, enforced the same
    # way as the create dialog in command_validate_input.
    createJointInpEdit = placeInputs.addBoolValueInput(
        'create_joint', 'Create Joint at Reference Face', True, '', create_joint_val)
    createJointInpEdit.isVisible = is_shaft

    face1Sel = placeInputs.addSelectionInput(
        'face1_selection', 'Reference Face', 'Select the starting planar face'
    )
    face1Sel.addSelectionFilter('PlanarFaces')
    face1Sel.setSelectionLimits(0, 1)
    face1Sel.isVisible = False

    refPointSelEdit = placeInputs.addSelectionInput(
        'ref_point_selection', 'Reference Point',
        'Select a point, edge, or face that the shaft is built from -- just like picking a '
        'joint origin in the Joint command'
    )
    refPointSelEdit.addSelectionFilter('Vertices')
    refPointSelEdit.addSelectionFilter('SketchPoints')
    refPointSelEdit.addSelectionFilter('ConstructionPoints')
    refPointSelEdit.addSelectionFilter('Edges')
    refPointSelEdit.addSelectionFilter('Faces')
    refPointSelEdit.setSelectionLimits(0, 1)
    # Matches the create dialog's rule (command_input_changed): always shown in
    # Between-Two-Faces, and in Custom Length only while "Create Joint" is checked -- a
    # shaft originally built as a plain Custom Length can still get a Reference Point (and
    # a joint) on Edit by ticking the checkbox.
    refPointSelEdit.isVisible = has_len and is_shaft and (keep_faces or create_joint_val)

    face2Sel = placeInputs.addSelectionInput(
        'face2_selection', 'Face 2', 'Select the ending planar face'
    )
    face2Sel.addSelectionFilter('PlanarFaces')
    face2Sel.setSelectionLimits(0, 1)
    face2Sel.isVisible = keep_faces

    reverseDirInpEdit = placeInputs.addBoolValueInput(
        'reverse_direction', 'Reverse Direction', True, '', reverse_dir_val)
    reverseDirInpEdit.isVisible = has_len and not keep_faces and is_shaft

    jointTypeInpEdit = placeInputs.addDropDownCommandInput(
        'joint_type', 'Joint Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    jointTypeInpEdit.listItems.add(JOINT_REVOLUTE, joint_type_val != JOINT_RIGID, '')
    jointTypeInpEdit.listItems.add(JOINT_RIGID, joint_type_val == JOINT_RIGID, '')
    jointTypeInpEdit.isVisible = is_shaft and create_joint_val

    flipJointInpEdit = placeInputs.addBoolValueInput('flip_joint', 'Flip', True, '', joint_flip_val)
    flipJointInpEdit.isVisible = is_shaft and create_joint_val

    bearingEndsInpEdit = _add_bearing_ends_input(placeInputs, bearing_ends_val, shaft_type)
    bearingEndsInpEdit.isVisible = has_len and is_shaft and len(bearing_choices(shaft_type)) > 1

    highlightRefFaceInpEdit = displayInputs.addBoolValueInput(
        'highlight_ref_face', 'Highlight Reference Face', True, '', True)
    highlightRefFaceInpEdit.isVisible = has_len

    # Wire events — reuse the same input-changed and validate handlers
    futil.add_handler(args.command.execute,        edit_command_execute,   local_handlers=edit_local_handlers)
    futil.add_handler(args.command.activate,       edit_command_activate,  local_handlers=edit_local_handlers)
    futil.add_handler(args.command.inputChanged,   command_input_changed,  local_handlers=edit_local_handlers)
    futil.add_handler(args.command.executePreview, edit_command_preview,   local_handlers=edit_local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=edit_local_handlers)
    futil.add_handler(args.command.destroy,        edit_command_destroy,   local_handlers=edit_local_handlers)


# ===========================================================================
# Edit command — execute (delete old occurrence, recreate with new params)
# ===========================================================================

def edit_command_execute(args: adsk.core.CommandEventArgs):
    global _edit_target_occ, _edit_ref_entities

    if _edit_target_occ:
        # A shaft built with bearings lives in a "<shaft>_Group" with them, so delete the
        # whole group. Older shafts have their bearings as separate root occurrences --
        # deleting the shaft only cascades away their joints -- so remove those first; the
        # rebuild re-adds them either way.
        try:
            delete_shaft_bearings(_edit_target_occ.component)
        except Exception:
            futil.log('PartsGen edit: could not delete old shaft bearings')
        try:
            # A shaft's or pulley's group (bearings / adapter included), else the part.
            outer = pulley_outer_occurrence(shaft_outer_occurrence(_edit_target_occ))
            outer.deleteMe()
        except Exception:
            futil.log('PartsGen edit: could not delete old occurrence')
        _edit_target_occ = None

    inputs = args.command.commandInputs
    partTypeInp: adsk.core.DropDownCommandInput = inputs.itemById('part_type')
    part_type = partTypeInp.selectedItem.name
    if part_type == PART_SHAFT:
        _create_shaft(inputs)
    elif part_type == PART_TUBE:
        _create_tube(inputs)
    elif part_type == PART_PULLEY:
        _create_pulley(inputs)
    elif part_type == PART_SPROCKET:
        _create_sprocket(inputs)
    elif part_type == PART_CHAIN:
        _create_chain(inputs)
    else:
        _create_belt(inputs)


def edit_command_activate(args: adsk.core.CommandEventArgs):
    """Re-select the shaft's original Reference Point and Face 2 in the dialog.

    This has to happen on `activate` rather than in `edit_command_created`:
    `SelectionCommandInput.addSelection()` silently does nothing when the command's inputs
    are still being built. If a selection doesn't take, leave the input empty rather than
    dropping back to Custom Length -- the user can re-pick and still keep the placement.
    """
    if not _edit_ref_entities:
        return
    ref_point, face2 = _edit_ref_entities
    inputs      = args.command.commandInputs
    refPointSel = inputs.itemById('ref_point_selection')
    face2Sel    = inputs.itemById('face2_selection')
    try:
        # Skip a hidden input (Custom Length with "Create Joint" off) -- a pick there would
        # silently place the shaft with nothing in the dialog showing why.
        if refPointSel is not None and ref_point is not None and refPointSel.isVisible:
            refPointSel.addSelection(ref_point)
        if face2Sel is not None and face2 is not None:
            face2Sel.addSelection(face2)
    except Exception:
        futil.log(f'{CMD_NAME} edit: could not re-select the stored reference geometry')


def edit_command_preview(args: adsk.core.CommandEventArgs):
    # No live preview for edit mode — the old component stays visible until OK.
    pass


def edit_command_destroy(args: adsk.core.CommandEventArgs):
    global edit_local_handlers, _edit_target_occ, _edit_ref_entities
    edit_local_handlers = []
    _edit_target_occ    = None
    _edit_ref_entities  = None
