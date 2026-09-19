import adsk.core
import adsk.fusion
import os
from ...lib import fusionAddInUtils as futil
from ... import config
from .shaft_gen import _create_shaft, _hex_spacer_bore_dims_cm
from .tube_gen import _create_tube
from .pulley_gen import _create_pulley
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
_selected_partsgen_occ = None   # currently-selected PartsGen occ; tracked by ui_selection_changed

# ---------------------------------------------------------------------------
# Reference-face highlight (Shaft/Tube preview) -- see command_preview /
# command_destroy and _draw_ref_face_highlight below.
# ---------------------------------------------------------------------------
_ref_face_highlight_group = None

_REF_FACE_COLOR   = (230, 30, 180)   # magenta -- distinct from Fusion's blue selection
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
ATTR_BELT_PULLEY_WIDTH = 'belt_pulley_width'

ATTR_SPROCKET_TOOTH_COUNT = 'sprocket_tooth_count'
ATTR_SPROCKET_WIDTH       = 'sprocket_width_expr'
ATTR_SPROCKET_SHOW_TEETH  = 'sprocket_show_teeth'
ATTR_SPROCKET_CHAIN_TYPE  = 'sprocket_chain_type'

ATTR_CHAIN_TYPE           = 'chain_type'
ATTR_CHAIN_SPROCKET_WIDTH = 'chain_sprocket_width_expr'
ATTR_CHAIN_GEN_SPROCKETS  = 'chain_gen_sprockets'
ATTR_CHAIN_SPROCKET_TEETH = 'chain_sprocket_teeth'

ATTR_CUSTOM_NAME          = 'custom_name'


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

def command_created(args: adsk.core.CommandCreatedEventArgs):
    inputs = args.command.commandInputs

    # --- Part type -----------------------------------------------------------
    partTypeInp = inputs.addDropDownCommandInput(
        'part_type', 'Part Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    partTypeInp.listItems.add(PART_SHAFT,    True,  '')
    partTypeInp.listItems.add(PART_TUBE,    False, '')
    partTypeInp.listItems.add(PART_PULLEY,  False, '')
    partTypeInp.listItems.add(PART_BELT,    False, '')
    partTypeInp.listItems.add(PART_SPROCKET, False, '')
    partTypeInp.listItems.add(PART_CHAIN,   False, '')

    # --- Component name (optional override; blank = auto-generated name) -----
    customNameInp = inputs.addStringValueInput('custom_name', 'Component Name', '')
    customNameInp.tooltip = 'Leave blank to use the automatically generated name.'

    # --- Shaft group ---------------------------------------------------------
    shaftTypeInp = inputs.addDropDownCommandInput(
        'shaft_type', 'Shaft Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    shaftTypeInp.listItems.add(SHAFT_HALF_HEX, True, '')
    shaftTypeInp.listItems.add(SHAFT_THREE_EIGHTH_HEX, False, '')
    shaftTypeInp.listItems.add(SHAFT_MAXSPLINE, False, '')
    shaftTypeInp.listItems.add(SHAFT_HALF_HEX_SPACER, False, '')
    shaftTypeInp.listItems.add(SHAFT_THREE_EIGHTH_SPACER, False, '')
    shaftTypeInp.listItems.add(SHAFT_CUSTOM, False, '')

    customOD = inputs.addValueInput(
        'custom_od', 'Outer Diameter', 'in',
        adsk.core.ValueInput.createByString('0.75 in')
    )
    customOD.isVisible = False

    customID = inputs.addValueInput(
        'custom_id', 'Bore Diameter', 'in',
        adsk.core.ValueInput.createByString('0.159 in')
    )
    customID.isVisible = False

    # --- Tube group ----------------------------------------------------------
    tubeWidthInp = inputs.addValueInput(
        'tube_width', 'Width', 'in',
        adsk.core.ValueInput.createByString('2 in')
    )
    tubeWidthInp.isVisible = False

    tubeHeightInp = inputs.addValueInput(
        'tube_height', 'Height', 'in',
        adsk.core.ValueInput.createByString('1 in')
    )
    tubeHeightInp.isVisible = False

    tubeThickInp = inputs.addDropDownCommandInput(
        'tube_thickness', 'Wall Thickness', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    tubeThickInp.listItems.add(THICK_1_16, True, '')
    tubeThickInp.listItems.add(THICK_1_8, False, '')
    tubeThickInp.listItems.add(THICK_CUSTOM, False, '')
    tubeThickInp.isVisible = False

    customThickInp = inputs.addValueInput(
        'custom_thickness', 'Custom Thickness', 'in',
        adsk.core.ValueInput.createByString('0.1 in')
    )
    customThickInp.isVisible = False

    # --- Tube face holes -----------------------------------------------------
    tubeHolesInp = inputs.addBoolValueInput('tube_add_holes', 'Add Corner Holes', True, '', True)
    tubeHolesInp.isVisible = False

    holeSizeInp = inputs.addDropDownCommandInput(
        'hole_size', 'Hole Size', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    holeSizeInp.listItems.add(HOLE_RIVENUT, True, '')
    holeSizeInp.listItems.add(HOLE_10_32, False, '')
    holeSizeInp.listItems.add(HOLE_CUSTOM, False, '')
    holeSizeInp.isVisible = False

    holeDiamInp = inputs.addValueInput(
        'hole_diameter', 'Custom Hole Diameter', 'in',
        adsk.core.ValueInput.createByString('0.25 in')
    )
    holeDiamInp.isVisible = False

    # --- Length group (shared) -----------------------------------------------
    lenTypeInp = inputs.addDropDownCommandInput(
        'length_type', 'Length', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    lenTypeInp.listItems.add(LEN_FACES, False, '')
    lenTypeInp.listItems.add(LEN_CUSTOM, True, '')

    face1Sel = inputs.addSelectionInput(
        'face1_selection', 'Face 1', 'Select the starting planar face'
    )
    face1Sel.addSelectionFilter('PlanarFaces')
    face1Sel.setSelectionLimits(0, 1)
    face1Sel.isVisible = False

    face2Sel = inputs.addSelectionInput(
        'face2_selection', 'Face 2', 'Select the ending planar face'
    )
    face2Sel.addSelectionFilter('PlanarFaces')
    face2Sel.setSelectionLimits(0, 1)
    face2Sel.isVisible = False

    customLenInp = inputs.addValueInput(
        'custom_length', 'Length', 'in',
        adsk.core.ValueInput.createByString('6 in')
    )

    highlightRefFaceInp = inputs.addBoolValueInput(
        'highlight_ref_face', 'Highlight Reference Face', True, '', True)
    customLenInp.isVisible = True

    # --- Pulley group --------------------------------------------------------
    beltTypeInp = inputs.addDropDownCommandInput(
        'belt_type', 'Timing Belt Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    beltTypeInp.listItems.add('HTD 5mm Pitch', True,  '')
    beltTypeInp.listItems.add('GT2 3mm Pitch', False, '')
    beltTypeInp.isVisible = False

    defaultLengthUnits = ''
    toothCountInp = inputs.addValueInput(
        'tooth_count', 'Tooth Count', defaultLengthUnits,
        adsk.core.ValueInput.createByString('18')
    )
    toothCountInp.isVisible = False

    beltWidthInp = inputs.addValueInput(
        'belt_width', 'Belt Width', 'mm',
        adsk.core.ValueInput.createByString('0.394 in')
    )
    beltWidthInp.isVisible = False

    pulleyShowTeethInp = inputs.addBoolValueInput('pulley_show_teeth', 'Show Teeth', True, '', False)
    pulleyShowTeethInp.isVisible = False

    # --- Timing Belt group ---------------------------------------------------
    tbCirclesInp = inputs.addSelectionInput(
        'tb_pitch_circles', 'End Circles', 'Select a C-C Line or two pitch circles'
    )
    tbCirclesInp.addSelectionFilter('SketchCurves')
    tbCirclesInp.setSelectionLimits(0, 2)
    tbCirclesInp.isVisible = False

    tbBeltTypeInp = inputs.addDropDownCommandInput(
        'tb_belt_type', 'Timing Belt Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    tbBeltTypeInp.listItems.add('HTD 5mm Pitch', True,  '')
    tbBeltTypeInp.listItems.add('GT2 3mm Pitch', False, '')
    tbBeltTypeInp.isEnabled = False
    tbBeltTypeInp.isVisible = False

    tbBeltWidthInp = inputs.addValueInput(
        'tb_belt_width', 'Belt Width', 'mm',
        adsk.core.ValueInput.createByString('9')
    )
    tbBeltWidthInp.isVisible = False

    tbSuppressInp = inputs.addBoolValueInput('tb_suppress_teeth', 'Toothless Belt', True, '', True)
    tbSuppressInp.isVisible = False

    tbGenPulleysInp = inputs.addBoolValueInput('tb_gen_pulleys', 'Generate Pulleys', True, '', True)
    tbGenPulleysInp.isVisible = False

    tbPulleyTeethInp = inputs.addBoolValueInput('tb_pulley_teeth', 'Pulley Teeth', True, '', False)
    tbPulleyTeethInp.isVisible = False

    tbPulleyWidthInp = inputs.addValueInput(
        'tb_pulley_width', 'Pulley Width', 'mm',
        adsk.core.ValueInput.createByString('0.394 in')
    )
    tbPulleyWidthInp.isVisible = False

    # --- Chain Sprocket group ------------------------------------------------
    sprocketToothCountInp = inputs.addValueInput(
        'sprocket_tooth_count', 'Tooth Count', '',
        adsk.core.ValueInput.createByString('12')
    )
    sprocketToothCountInp.isVisible = False

    sprocketWidthInp = inputs.addValueInput(
        'sprocket_width', 'Sprocket Width', 'in',
        adsk.core.ValueInput.createByString('0.375 in')
    )
    sprocketWidthInp.isVisible = False

    sprocketShowTeethInp = inputs.addBoolValueInput(
        'sprocket_show_teeth', 'Show Teeth', True, '', False)
    sprocketShowTeethInp.isVisible = False

    sprocketChainTypeInp = inputs.addDropDownCommandInput(
        'sprocket_chain_type', 'Chain Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    sprocketChainTypeInp.listItems.add('#25 Chain', True,  '')
    sprocketChainTypeInp.listItems.add('#35 Chain', False, '')
    sprocketChainTypeInp.isVisible = False

    # --- Chain group ---------------------------------------------------------
    chainCirclesInp = inputs.addSelectionInput(
        'chain_pitch_circles', 'End Circles', 'Select a #25 or #35 Chain C-C Line or two pitch circles'
    )
    chainCirclesInp.addSelectionFilter('SketchCurves')
    chainCirclesInp.setSelectionLimits(0, 2)
    chainCirclesInp.isVisible = False

    chainSprocketWidthInp = inputs.addValueInput(
        'chain_sprocket_width', 'Chain Width', 'in',
        adsk.core.ValueInput.createByString('0.375 in')
    )
    chainSprocketWidthInp.isVisible = False

    chainGenSprocketsInp = inputs.addBoolValueInput(
        'chain_gen_sprockets', 'Generate Sprockets', True, '', True)
    chainGenSprocketsInp.isVisible = False

    chainSprocketTeethInp = inputs.addBoolValueInput(
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
    inputs = args.inputs

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
    highlightRefFaceInp: adsk.core.BoolValueCommandInput = inputs.itemById('highlight_ref_face')
    beltTypeInp:    adsk.core.DropDownCommandInput   = inputs.itemById('belt_type')
    toothCountInp:  adsk.core.ValueCommandInput      = inputs.itemById('tooth_count')
    beltWidthInp:   adsk.core.ValueCommandInput      = inputs.itemById('belt_width')
    tbCirclesInp:   adsk.core.SelectionCommandInput  = inputs.itemById('tb_pitch_circles')
    tbBeltTypeInp:  adsk.core.DropDownCommandInput   = inputs.itemById('tb_belt_type')
    tbBeltWidthInp: adsk.core.ValueCommandInput      = inputs.itemById('tb_belt_width')
    tbSuppressInp:       adsk.core.BoolValueCommandInput = inputs.itemById('tb_suppress_teeth')
    tbGenPulleysInp:     adsk.core.BoolValueCommandInput = inputs.itemById('tb_gen_pulleys')
    tbPulleyTeethInp:    adsk.core.BoolValueCommandInput = inputs.itemById('tb_pulley_teeth')
    tbPulleyWidthInp:    adsk.core.ValueCommandInput     = inputs.itemById('tb_pulley_width')
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
    is_between_faces = (lenTypeInp.selectedItem.name   == LEN_FACES)
    hide_length      = part_is_pulley or part_is_belt or part_is_sprocket or part_is_chain

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

    # Timing Belt inputs
    if tbCirclesInp is not None:
        tbCirclesInp.isVisible  = part_is_belt
    if tbBeltTypeInp is not None:
        tbBeltTypeInp.isVisible = part_is_belt
    if tbBeltWidthInp is not None:
        tbBeltWidthInp.isVisible = part_is_belt
    if tbSuppressInp is not None:
        tbSuppressInp.isVisible = part_is_belt
    if tbGenPulleysInp is not None:
        tbGenPulleysInp.isVisible = part_is_belt
    if tbPulleyTeethInp is not None:
        tbPulleyTeethInp.isVisible = part_is_belt and (tbGenPulleysInp is not None and tbGenPulleysInp.value)
    if tbPulleyWidthInp is not None:
        tbPulleyWidthInp.isVisible = part_is_belt and (tbGenPulleysInp is not None and tbGenPulleysInp.value)

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

    # Length inputs — hidden when Pulley or Belt is selected
    lenTypeInp.isVisible   = not hide_length
    face1Sel.isVisible     = not hide_length and is_between_faces
    face2Sel.isVisible     = not hide_length and is_between_faces
    customLenInp.isVisible = not hide_length and not is_between_faces
    if highlightRefFaceInp is not None:
        highlightRefFaceInp.isVisible = not hide_length

    # Sync selection limits with visibility
    if not hide_length and is_between_faces:
        face1Sel.setSelectionLimits(1, 1)
        face2Sel.setSelectionLimits(1, 1)
    else:
        face1Sel.setSelectionLimits(0, 1)
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

    # Auto-focus Face 1 when switching to Between Two Faces mode
    if args.input.id == 'length_type' and is_between_faces:
        face1Sel.hasFocus = True

    # Auto-advance to Face 2 once Face 1 is filled
    if args.input.id == 'face1_selection' and face1Sel.selectionCount >= 1:
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
            _create_pulley(inputs)
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
        suppressTeethInp = inputs.itemById('tb_suppress_teeth')
        if suppressTeethInp and suppressTeethInp.value:
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
        args.isValidResult = ok and part_type not in (PART_SHAFT, PART_TUBE)
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
    customLenInp:   adsk.core.ValueCommandInput     = inputs.itemById('custom_length')

    part_type = partTypeInp.selectedItem.name

    # --- Timing Pulley validation -------------------------------------------
    if part_type == PART_PULLEY:
        toothCountInp:  adsk.core.ValueCommandInput     = inputs.itemById('tooth_count')
        beltWidthInp:   adsk.core.ValueCommandInput     = inputs.itemById('belt_width')
        if toothCountInp is None or toothCountInp.value < 8:
            args.areInputsValid = False
            return
        if beltWidthInp is None or beltWidthInp.value <= 0:
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
        tbBeltWidthInp: adsk.core.ValueCommandInput     = inputs.itemById('tb_belt_width')
        if tbCirclesInp is None or tbCirclesInp.selectionCount < 2:
            args.areInputsValid = False
            return
        if tbBeltWidthInp is None or tbBeltWidthInp.value <= 0:
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

    # Length validation (shared by Shaft and Tube)
    if len_type == LEN_FACES:
        if face1Sel.selectionCount < 1 or face2Sel.selectionCount < 1:
            args.areInputsValid = False
            return
    else:
        if customLenInp.value <= 0:
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

def edit_command_created(args: adsk.core.CommandCreatedEventArgs):
    global _edit_target_occ

    inputs = args.command.commandInputs

    # Read stored attributes from the targeted occurrence's component
    attrs = {}
    if _edit_target_occ:
        comp = _edit_target_occ.component
        for key in (ATTR_PART_TYPE, ATTR_SHAFT_TYPE, ATTR_CUSTOM_OD, ATTR_CUSTOM_ID,
                    ATTR_TUBE_WIDTH, ATTR_TUBE_HEIGHT, ATTR_TUBE_THICK, ATTR_CUSTOM_THICK,
                    ATTR_ADD_HOLES, ATTR_HOLE_SIZE, ATTR_HOLE_DIAM, ATTR_LEN_EXPR,
                    ATTR_PULLEY_BELT_TYPE, ATTR_PULLEY_TOOTH_COUNT, ATTR_PULLEY_BELT_WIDTH,
                    ATTR_PULLEY_SHOW_TEETH,
                    ATTR_BELT_TYPE, ATTR_BELT_WIDTH, ATTR_BELT_SUPPRESS,
                    ATTR_BELT_GEN_PULLEYS, ATTR_BELT_PULLEY_TEETH, ATTR_BELT_PULLEY_WIDTH,
                    ATTR_SPROCKET_TOOTH_COUNT, ATTR_SPROCKET_WIDTH, ATTR_SPROCKET_SHOW_TEETH,
                    ATTR_SPROCKET_CHAIN_TYPE,
                    ATTR_CHAIN_TYPE, ATTR_CHAIN_SPROCKET_WIDTH,
                    ATTR_CHAIN_GEN_SPROCKETS, ATTR_CHAIN_SPROCKET_TEETH,
                    ATTR_CUSTOM_NAME):
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
    pulley_width      = _s(ATTR_PULLEY_BELT_WIDTH,  '0.394 in')
    pulley_show_teeth = _b(ATTR_PULLEY_SHOW_TEETH,  False)
    belt_type_val     = _s(ATTR_BELT_TYPE,          'HTD 5mm Pitch')
    belt_width_val    = _s(ATTR_BELT_WIDTH,         '9 mm')
    belt_suppress     = _b(ATTR_BELT_SUPPRESS,       False)
    belt_gen_pulleys  = _b(ATTR_BELT_GEN_PULLEYS,   True)
    belt_pulley_teeth = _b(ATTR_BELT_PULLEY_TEETH,  False)
    belt_pulley_width = _s(ATTR_BELT_PULLEY_WIDTH,  '0.394 in')
    sprocket_teeth    = _s(ATTR_SPROCKET_TOOTH_COUNT, '12')
    sprocket_width_val = _s(ATTR_SPROCKET_WIDTH,     '0.375 in')
    sprocket_show_teeth_val = _b(ATTR_SPROCKET_SHOW_TEETH, False)
    sprocket_chain_type_val = _s(ATTR_SPROCKET_CHAIN_TYPE, '#25 Chain')
    chain_type_val       = _s(ATTR_CHAIN_TYPE,          '25')
    chain_spr_width_val  = _s(ATTR_CHAIN_SPROCKET_WIDTH, '0.375 in')
    chain_gen_spr_val    = _b(ATTR_CHAIN_GEN_SPROCKETS,  True)
    chain_spr_teeth_val  = _b(ATTR_CHAIN_SPROCKET_TEETH, False)
    custom_name_val      = _s(ATTR_CUSTOM_NAME, '')

    # --- Part type ---
    partTypeInp = inputs.addDropDownCommandInput(
        'part_type', 'Part Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    partTypeInp.listItems.add(PART_SHAFT,    is_shaft,    '')
    partTypeInp.listItems.add(PART_TUBE,    is_tube,     '')
    partTypeInp.listItems.add(PART_PULLEY,  is_pulley,   '')
    partTypeInp.listItems.add(PART_BELT,    is_belt,     '')
    partTypeInp.listItems.add(PART_SPROCKET, is_sprocket, '')
    partTypeInp.listItems.add(PART_CHAIN,   is_chain,    '')

    # --- Component name (optional override; blank = auto-generated name) -----
    customNameInp = inputs.addStringValueInput('custom_name', 'Component Name', custom_name_val)
    customNameInp.tooltip = 'Leave blank to use the automatically generated name.'

    # --- Shaft group ---
    shaftTypeInp = inputs.addDropDownCommandInput(
        'shaft_type', 'Shaft Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    shaftTypeInp.listItems.add(SHAFT_HALF_HEX,            shaft_type == SHAFT_HALF_HEX,            '')
    shaftTypeInp.listItems.add(SHAFT_THREE_EIGHTH_HEX,    shaft_type == SHAFT_THREE_EIGHTH_HEX,    '')
    shaftTypeInp.listItems.add(SHAFT_MAXSPLINE,           shaft_type == SHAFT_MAXSPLINE,           '')
    shaftTypeInp.listItems.add(SHAFT_HALF_HEX_SPACER,     shaft_type == SHAFT_HALF_HEX_SPACER,     '')
    shaftTypeInp.listItems.add(SHAFT_THREE_EIGHTH_SPACER, shaft_type == SHAFT_THREE_EIGHTH_SPACER, '')
    shaftTypeInp.listItems.add(SHAFT_CUSTOM,              is_custom_shaft,                         '')
    shaftTypeInp.isVisible = is_shaft

    customOD = inputs.addValueInput(
        'custom_od', 'Outer Diameter', 'in',
        adsk.core.ValueInput.createByString(od_expr)
    )
    customOD.isVisible = is_shaft and (is_custom_shaft or is_spacer_shaft)

    customID = inputs.addValueInput(
        'custom_id', 'Bore Diameter', 'in',
        adsk.core.ValueInput.createByString(id_expr)
    )
    customID.isVisible = is_shaft and is_custom_shaft

    # --- Tube group ---
    tubeWidthInp = inputs.addValueInput(
        'tube_width', 'Width', 'in',
        adsk.core.ValueInput.createByString(w_expr)
    )
    tubeWidthInp.isVisible = is_tube

    tubeHeightInp = inputs.addValueInput(
        'tube_height', 'Height', 'in',
        adsk.core.ValueInput.createByString(h_expr)
    )
    tubeHeightInp.isVisible = is_tube

    tubeThickInp = inputs.addDropDownCommandInput(
        'tube_thickness', 'Wall Thickness', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    tubeThickInp.listItems.add(THICK_1_16,   tube_thick == THICK_1_16,    '')
    tubeThickInp.listItems.add(THICK_1_8,    tube_thick == THICK_1_8,     '')
    tubeThickInp.listItems.add(THICK_CUSTOM, is_custom_thick,             '')
    tubeThickInp.isVisible = is_tube

    customThickInp = inputs.addValueInput(
        'custom_thickness', 'Custom Thickness', 'in',
        adsk.core.ValueInput.createByString(thick_expr)
    )
    customThickInp.isVisible = is_tube and is_custom_thick

    # --- Tube face holes ---
    tubeHolesInp = inputs.addBoolValueInput('tube_add_holes', 'Add Corner Holes', True, '', add_holes)
    tubeHolesInp.isVisible = is_tube

    holeSizeInp = inputs.addDropDownCommandInput(
        'hole_size', 'Hole Size', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    holeSizeInp.listItems.add(HOLE_RIVENUT, hole_size == HOLE_RIVENUT, '')
    holeSizeInp.listItems.add(HOLE_10_32,   hole_size == HOLE_10_32,   '')
    holeSizeInp.listItems.add(HOLE_CUSTOM,  is_custom_hole,            '')
    holeSizeInp.isVisible = is_tube and add_holes

    holeDiamInp = inputs.addValueInput(
        'hole_diameter', 'Custom Hole Diameter', 'in',
        adsk.core.ValueInput.createByString(hole_diam_expr)
    )
    holeDiamInp.isVisible = is_tube and add_holes and is_custom_hole

    # --- Pulley group — values pre-filled from stored attributes ---
    beltTypeInp = inputs.addDropDownCommandInput(
        'belt_type', 'Timing Belt Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    beltTypeInp.listItems.add('HTD 5mm Pitch', pulley_belt == 'HTD 5mm Pitch', '')
    beltTypeInp.listItems.add('GT2 3mm Pitch', pulley_belt == 'GT2 3mm Pitch', '')
    beltTypeInp.isVisible = is_pulley

    toothCountInp = inputs.addValueInput(
        'tooth_count', 'Tooth Count', '',
        adsk.core.ValueInput.createByString(pulley_teeth)
    )
    toothCountInp.isVisible = is_pulley

    beltWidthInp = inputs.addValueInput(
        'belt_width', 'Belt Width', 'mm',
        adsk.core.ValueInput.createByString(pulley_width)
    )
    beltWidthInp.isVisible = is_pulley

    pulleyShowTeethInp = inputs.addBoolValueInput(
        'pulley_show_teeth', 'Show Teeth', True, '', pulley_show_teeth)
    pulleyShowTeethInp.isVisible = is_pulley

    # --- Timing Belt group — circles must be re-selected; other values pre-filled ---
    tbCirclesInp = inputs.addSelectionInput(
        'tb_pitch_circles', 'End Circles', 'Select a C-C Line or two pitch circles'
    )
    tbCirclesInp.addSelectionFilter('SketchCurves')
    tbCirclesInp.setSelectionLimits(0, 2)
    tbCirclesInp.isVisible = is_belt

    tbBeltTypeInp = inputs.addDropDownCommandInput(
        'tb_belt_type', 'Timing Belt Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    tbBeltTypeInp.listItems.add('HTD 5mm Pitch', belt_type_val == 'HTD 5mm Pitch', '')
    tbBeltTypeInp.listItems.add('GT2 3mm Pitch', belt_type_val == 'GT2 3mm Pitch', '')
    tbBeltTypeInp.isEnabled = True   # always enabled in edit mode (no CCLine auto-lock)
    tbBeltTypeInp.isVisible = is_belt

    tbBeltWidthInp = inputs.addValueInput(
        'tb_belt_width', 'Belt Width', 'mm',
        adsk.core.ValueInput.createByString(belt_width_val)
    )
    tbBeltWidthInp.isVisible = is_belt

    tbSuppressInp = inputs.addBoolValueInput('tb_suppress_teeth', 'Toothless Belt', True, '', belt_suppress)
    tbSuppressInp.isVisible = is_belt

    tbGenPulleysInp = inputs.addBoolValueInput('tb_gen_pulleys', 'Generate Pulleys', True, '', belt_gen_pulleys)
    tbGenPulleysInp.isVisible = is_belt

    tbPulleyTeethInp = inputs.addBoolValueInput('tb_pulley_teeth', 'Pulley Teeth', True, '', belt_pulley_teeth)
    tbPulleyTeethInp.isVisible = is_belt and belt_gen_pulleys

    tbPulleyWidthInp = inputs.addValueInput(
        'tb_pulley_width', 'Pulley Width', 'mm',
        adsk.core.ValueInput.createByString(belt_pulley_width)
    )
    tbPulleyWidthInp.isVisible = is_belt and belt_gen_pulleys

    # --- Chain Sprocket group ---
    sprocketToothCountInp = inputs.addValueInput(
        'sprocket_tooth_count', 'Tooth Count', '',
        adsk.core.ValueInput.createByString(sprocket_teeth)
    )
    sprocketToothCountInp.isVisible = is_sprocket

    sprocketWidthInpEdit = inputs.addValueInput(
        'sprocket_width', 'Sprocket Width', 'in',
        adsk.core.ValueInput.createByString(sprocket_width_val)
    )
    sprocketWidthInpEdit.isVisible = is_sprocket

    sprocketShowTeethInpEdit = inputs.addBoolValueInput(
        'sprocket_show_teeth', 'Show Teeth', True, '', sprocket_show_teeth_val)
    sprocketShowTeethInpEdit.isVisible = is_sprocket

    sprocketChainTypeInpEdit = inputs.addDropDownCommandInput(
        'sprocket_chain_type', 'Chain Type', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    sprocketChainTypeInpEdit.listItems.add('#25 Chain', sprocket_chain_type_val == '#25 Chain', '')
    sprocketChainTypeInpEdit.listItems.add('#35 Chain', sprocket_chain_type_val == '#35 Chain', '')
    sprocketChainTypeInpEdit.isVisible = is_sprocket

    # --- Chain group ---
    chainCirclesInpEdit = inputs.addSelectionInput(
        'chain_pitch_circles', 'End Circles', 'Select a #25 or #35 Chain C-C Line or two pitch circles'
    )
    chainCirclesInpEdit.addSelectionFilter('SketchCurves')
    chainCirclesInpEdit.setSelectionLimits(0, 2)
    chainCirclesInpEdit.isVisible = is_chain

    chainSprocketWidthInpEdit = inputs.addValueInput(
        'chain_sprocket_width', 'Chain Width', 'in',
        adsk.core.ValueInput.createByString(chain_spr_width_val)
    )
    chainSprocketWidthInpEdit.isVisible = is_chain

    chainGenSprocketsInpEdit = inputs.addBoolValueInput(
        'chain_gen_sprockets', 'Generate Sprockets', True, '', chain_gen_spr_val)
    chainGenSprocketsInpEdit.isVisible = is_chain

    chainSprocketTeethInpEdit = inputs.addBoolValueInput(
        'chain_sprocket_teeth', 'Sprocket Teeth', True, '', chain_spr_teeth_val)
    chainSprocketTeethInpEdit.isVisible = is_chain and chain_gen_spr_val

    # --- Length — always use Custom Length in edit mode; face refs are gone ---
    lenTypeInp = inputs.addDropDownCommandInput(
        'length_type', 'Length', adsk.core.DropDownStyles.TextListDropDownStyle
    )
    lenTypeInp.listItems.add(LEN_FACES,  False, '')
    lenTypeInp.listItems.add(LEN_CUSTOM, True,  '')
    lenTypeInp.isVisible = not is_pulley and not is_belt and not is_sprocket and not is_chain

    face1Sel = inputs.addSelectionInput(
        'face1_selection', 'Face 1', 'Select the starting planar face'
    )
    face1Sel.addSelectionFilter('PlanarFaces')
    face1Sel.setSelectionLimits(0, 1)
    face1Sel.isVisible = False

    face2Sel = inputs.addSelectionInput(
        'face2_selection', 'Face 2', 'Select the ending planar face'
    )
    face2Sel.addSelectionFilter('PlanarFaces')
    face2Sel.setSelectionLimits(0, 1)
    face2Sel.isVisible = False

    customLenInp = inputs.addValueInput(
        'custom_length', 'Length', 'in',
        adsk.core.ValueInput.createByString(len_expr)
    )
    customLenInp.isVisible = not is_pulley and not is_belt and not is_sprocket and not is_chain

    highlightRefFaceInpEdit = inputs.addBoolValueInput(
        'highlight_ref_face', 'Highlight Reference Face', True, '', True)
    highlightRefFaceInpEdit.isVisible = not is_pulley and not is_belt and not is_sprocket and not is_chain

    # Wire events — reuse the same input-changed and validate handlers
    futil.add_handler(args.command.execute,        edit_command_execute,   local_handlers=edit_local_handlers)
    futil.add_handler(args.command.inputChanged,   command_input_changed,  local_handlers=edit_local_handlers)
    futil.add_handler(args.command.executePreview, edit_command_preview,   local_handlers=edit_local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=edit_local_handlers)
    futil.add_handler(args.command.destroy,        edit_command_destroy,   local_handlers=edit_local_handlers)


# ===========================================================================
# Edit command — execute (delete old occurrence, recreate with new params)
# ===========================================================================

def edit_command_execute(args: adsk.core.CommandEventArgs):
    global _edit_target_occ

    if _edit_target_occ:
        try:
            _edit_target_occ.deleteMe()
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


def edit_command_preview(args: adsk.core.CommandEventArgs):
    # No live preview for edit mode — the old component stays visible until OK.
    pass


def edit_command_destroy(args: adsk.core.CommandEventArgs):
    global edit_local_handlers, _edit_target_occ
    edit_local_handlers = []
    _edit_target_occ    = None
