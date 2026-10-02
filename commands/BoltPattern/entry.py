import adsk.core
import adsk.fusion
import os
import math
import typing
from ...lib import fusionAddInUtils as futil
from ... import config
app = adsk.core.Application.get()
ui = app.userInterface


# TODO *** Specify the command identity information. ***
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_BoltPatternDialog'
CMD_NAME = 'FRC Bolt Pattern'
CMD_Description = 'Create a bolt pattern for common FRC motors'

# Specify that the command will be promoted to the panel.
IS_PROMOTED = False

# Resource location for command icons, here we assume a sub folder in this directory named "resources".
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []

# Sketch that was active when the dialog opened, and the sketch-space point the user
# clicked in empty space (None until they click). Used when no point/circle is selected.
_active_sketch: adsk.fusion.Sketch = None
_click_pt: adsk.core.Point3D = None

# How close (in pixels) a click must be to the selected center to count as re-picking it.
CLICK_PICK_TOLERANCE_PX = 10

# Bolt Pattern struct
class BoltPattern(typing.NamedTuple) :
    name: str = ""
    centerDia: float = 0.0
    patternDia: float = 0.0
    holeSize: float = 0.0
    numberOfHoles: int = 0
    suppression: list[int] = []
    outline: str = ''           # '' (none), 'kraken', 'maxplanetary' or 'circle'
    outlineDims: tuple = ()     # outline-specific sizes, in inches

# Selection of Bolt Patterns
# Kraken: the missing 12th hole is at index 6 (opposite the first hole) so the wire bump of the
# outline sits straight across from it -- same 11 holes as the real motor, just clocked.
# Kraken outlineDims = (body diameter, overall height to the bump's flat), from WCP-0940 / WCP-0941.
bolt_patterns: list[BoltPattern] = [
    # Name, center hole radius, pattern radius, hole size, # of holes, suppression, outline, outline dims
    BoltPattern('Kraken X60', 0.75, 2.0, 0.196, 12, [0,0,0,0,0,0,1,0,0,0,0,0], 'kraken', (2.367, 2.498)),
    BoltPattern('Kraken X44', 0.75, 1.375, 0.196, 12, [0,0,0,0,0,0,1,0,0,0,0,0], 'kraken', (1.732, 1.866)),
    # BoltPattern('NEO Vortex', 0.75, 2.0, 0.196, 8, [0,0,0,1,0,0,0,1]),
    # BoltPattern('NEO 550', 0.5118, 0.9843, 0.125, 4, []),
    BoltPattern('REV MAXPlanetary', 1.125, 2.0, 0.196, 8, [0,0,1,1,0,0,1,1], 'maxplanetary'),
    # Thrifty Cycloidal (TTB-0300): 12x 8-32 tapped on each face; inner = output, outer = housing.
    # The 'circle' outline is the plate's outer edge (real geometry, not construction).
    BoltPattern('Thrifty Cycloidal (Inner)', 1.126, 1.5, 0.177, 12, [], 'circle', (2.0,)),
    BoltPattern('Thrifty Cycloidal (Outer)', 2.14, 2.5, 0.177, 12, [], 'circle', (3.0,)),
    # BoltPattern('2" MultiMotor', 0.75, 2.0, 0.196, 24, [0,1,0,0,0,1, 0,1,0,0,0,1, 0,1,0,0,0,1, 0,1,0,0,0,1]),
    BoltPattern('Hex Bearing Retention', 1.125, 1.422, 0.159, 6, [0,0,0,0,0,0]),
]

# REV MAXPlanetary outer profile (inches), from REV-21-2100 / REV-21-2101: a 2.000" square with its
# sides parallel to the 0/180 deg face holes, two opposite corners chamfered 45 deg, and a half-round
# mounting ear on each of the other two corners (flush with the face, 2.44" across the ears).
MAXP_HALF_SIDE = 1.0
MAXP_CHAMFER = 0.34
MAXP_EAR_RADIUS = 0.155
MAXP_EAR_OVERHANG = 0.065     # ear arc center beyond the side face

# Executed when add-in is run.
def start():
    # Create a command Definition.
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)

    # Define an event handler for the command created event. It will be called when the button is clicked.
    futil.add_handler(cmd_def.commandCreated, command_created)

    # ******** Add a button into the UI so the user can run the command. ********
    # Find the the FRCTools submenu.
    submenu = config.get_sketch_create_submenu()

    # Create the button command control in the UI.
    control = submenu.controls.addCommand(cmd_def)

    # Specify if the command is promoted to the main toolbar. 
    control.isPromoted = IS_PROMOTED

# Executed when add-in is stopped.
def stop():

    # Get the various UI elements for this command
    submenu = config.get_sketch_create_submenu()
    command_control = submenu.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    # Delete the button command control
    if command_control:
        command_control.isPromoted = False
        command_control.deleteMe()

    # Delete the command definition
    if command_definition:
        command_definition.deleteMe()

# Function that is called when a user clicks the corresponding button in the UI.
# This defines the contents of the command dialog and connects to the command related events.
def command_created(args: adsk.core.CommandCreatedEventArgs):

    # General logging for debug.
    # futil.log(f'{CMD_NAME} command Created Event')
    
    # Set dialog size to be wider
    args.command.setDialogInitialSize(250, 300)  # width, height in pixels

    # https://help.autodesk.com/view/fusion360/ENU/?contextId=CommandInputs
    inputs = args.command.commandInputs

    global _active_sketch, _click_pt
    _active_sketch = adsk.fusion.Sketch.cast( app.activeProduct.activeEditObject )
    _click_pt = None

    # Create a selection input. Selecting is optional: clicking empty space in the
    # active sketch places a new (free) center point there instead.
    centerSelection = inputs.addSelectionInput('center_selection', 'Center', 'Click a location, or select a point/circle')
    centerSelection.addSelectionFilter( "SketchPoints" )
    centerSelection.addSelectionFilter( "SketchCircles" )
    centerSelection.setSelectionLimits( 0, 1 )

    # Read-only readout of the clicked location. Changing its value from the mouse
    # click handler is also what makes Fusion re-validate and re-run the preview.
    clickLocation = inputs.addStringValueInput('click_location', 'Location', '')
    clickLocation.isReadOnly = True

    # Bolt Patterns
    boltPattern = inputs.addDropDownCommandInput('bolt_pattern', 'Bolt Pattern', adsk.core.DropDownStyles.TextListDropDownStyle)
    for bp in bolt_patterns:
        boltPattern.listItems.add( bp.name, True, '')
    boltPattern.listItems.item( 0 ).isSelected = True

    # Construction outline of the motor/gearbox housing, for patterns that have one
    showOutline = inputs.addBoolValueInput('show_outline', 'Show Outer Profile', True, '', True)
    showOutline.isVisible = bolt_patterns[0].outline != ''

    # Center hole options
    centerHoleGroup = inputs.addGroupCommandInput('center_hole_group', 'Center Hole Options')
    centerHoleGroup.isExpanded = True
    
    # Checkbox to suppress center hole
    suppressCenterHole = centerHoleGroup.children.addBoolValueInput('suppress_center_hole', 'Suppress Center Hole', True, '', False)
    
    # Value input for center hole diameter (in inches)
    selected_pattern = bolt_patterns[0]  # Default pattern
    centerHoleSize = centerHoleGroup.children.addValueInput('center_hole_size', 'Center Hole Diameter', 'in', adsk.core.ValueInput.createByString(f'{selected_pattern.centerDia} in'))

    # TODO Connect to the events that are needed by this command.
    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.mouseClick, command_mouse_click, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


# Returns the center SketchPoint to build the pattern on: the selected point/circle if
# there is one, otherwise a new point at the clicked location (left free for the user
# to dimension). Returns None if neither is available.
def _resolve_center(inputs: adsk.core.CommandInputs) -> adsk.fusion.SketchPoint:
    centerSelection: adsk.core.SelectionCommandInput = inputs.itemById('center_selection')
    if centerSelection.selectionCount > 0:
        selectedEntity = centerSelection.selection(0).entity
        if selectedEntity.objectType == adsk.fusion.SketchCircle.classType() :
            return selectedEntity.centerSketchPoint
        elif selectedEntity.objectType == adsk.fusion.SketchPoint.classType() :
            return selectedEntity
        futil.popup_error( f'  Cannot handle object type = {selectedEntity.objectType}')
        return None

    if _click_pt is not None and _active_sketch is not None and _active_sketch.isValid:
        return _active_sketch.sketchPoints.add( _click_pt )
    return None


# Called when the user clicks in the graphics window while the dialog is open.
def command_mouse_click(args: adsk.core.MouseEventArgs):
    global _click_pt
    if _active_sketch is None or not _active_sketch.isValid:
        return

    inputs = args.firingEvent.sender.commandInputs
    centerSelection: adsk.core.SelectionCommandInput = inputs.itemById('center_selection')
    viewport = args.viewport
    viewPt = args.viewportPosition

    # A click on the already-selected point/circle is the selection itself,
    # not a request to place a new point.
    if centerSelection.selectionCount > 0:
        entity = centerSelection.selection(0).entity
        if futil.sketchEntityViewDistance( entity, viewport, viewPt ) <= CLICK_PICK_TOLERANCE_PX:
            return

    sketchPt = futil.viewClickToSketchPoint( _active_sketch, viewport, viewPt )
    if sketchPt is None:
        return

    _click_pt = sketchPt
    centerSelection.clearSelection()
    clickLocation: adsk.core.StringValueCommandInput = inputs.itemById('click_location')
    clickLocation.value = f'{sketchPt.x / 2.54:.3f}, {sketchPt.y / 2.54:.3f} in'


# This event handler is called when the user clicks the OK button in the command dialog or 
# is immediately called after the created event not command inputs were created for the dialog.
def command_execute(args: adsk.core.CommandEventArgs):
    # General logging for debug.
    # futil.log(f'{CMD_NAME} Command Execute Event')

    _build_pattern( args.command.commandInputs, constrain=True )


# Draws the selected bolt pattern (and its outer profile, if enabled). `constrain=False`
# skips constraining the outline -- too slow for preview; execute rebuilds it constrained.
# Returns True if an outline was drawn.
def _build_pattern(inputs: adsk.core.CommandInputs, constrain: bool) -> bool:
    boltPatternInp: adsk.core.DropDownCommandInput = inputs.itemById('bolt_pattern')
    suppressCenterHoleInp: adsk.core.BoolValueCommandInput = inputs.itemById('suppress_center_hole')
    centerHoleSizeInp: adsk.core.ValueCommandInput = inputs.itemById('center_hole_size')
    showOutlineInp: adsk.core.BoolValueCommandInput = inputs.itemById('show_outline')

    centerPt = _resolve_center( inputs )
    if centerPt is None:
        return False

    boltPattern = bolt_patterns[ boltPatternInp.selectedItem.index ]
    sketch = centerPt.parentSketch

    # Create the bolt pattern center hole (only if not suppressed)
    if not suppressCenterHoleInp.value:
        # Use user-specified size instead of pattern default
        centerHoleDia = centerHoleSizeInp.value
        centerHole = sketch.sketchCurves.sketchCircles.addByCenterRadius( centerPt, centerHoleDia / 2 )
        textPt = futil.offsetPoint3D( centerHole.centerSketchPoint.geometry, centerHoleDia/4, centerHoleDia/4, 0 )
        centerDim = sketch.sketchDimensions.addDiameterDimension( centerHole, textPt )
        centerDim.value = centerHoleDia

    # Create the bolt pattern bolt circle
    patternDiaCm = boltPattern.patternDia * 2.54
    holeSizeCm = boltPattern.holeSize * 2.54
    boltCircle = sketch.sketchCurves.sketchCircles.addByCenterRadius( centerPt, patternDiaCm / 2 )
    boltCircle.isConstruction = True
    textPt = futil.offsetPoint3D( boltCircle.centerSketchPoint.geometry, -patternDiaCm/4, patternDiaCm/4, 0 )
    boltCirDim = sketch.sketchDimensions.addDiameterDimension( boltCircle, textPt )
    boltCirDim.value = patternDiaCm

    # Create a single bolt hole, drawn already on the bolt circle so the solver
    # doesn't drag a free (clicked) center point toward it.
    boltCenter = futil.offsetPoint3D( centerPt.geometry, patternDiaCm / 2, 0, 0 )
    boltHole = sketch.sketchCurves.sketchCircles.addByCenterRadius( boltCenter, holeSizeCm / 2 )
    sketch.geometricConstraints.addCoincident( boltHole.centerSketchPoint, boltCircle )
    textPt = futil.offsetPoint3D( boltHole.centerSketchPoint.geometry, holeSizeCm/4, holeSizeCm/4, 0 )
    boltDim = sketch.sketchDimensions.addDiameterDimension( boltHole, textPt )
    boltDim.value = holeSizeCm

    # Create the hole pattern
    cirPattern = sketch.geometricConstraints.createCircularPatternInput( [boltHole], centerPt )
    # cirPattern.totalAngle = 2 * math.pi
    cirPattern.quantity = futil.Value(boltPattern.numberOfHoles)

    if len(boltPattern.suppression) > 0:
        boolSuppression = []
        for s in boltPattern.suppression :
            boolSuppression.append( s == 1 )
        # Remove first element bc it cannot be suppressed
        boolSuppression.pop(0)
        cirPattern.isSuppressed = boolSuppression

    sketch.geometricConstraints.addCircularPattern( cirPattern )

    if boltPattern.outline == '' or not showOutlineInp.value:
        return False

    # Plain round plate edge: cheap to constrain, so the preview can stand as the result.
    if boltPattern.outline == 'circle':
        _draw_circle_outline( sketch, centerPt, boltPattern.outlineDims[0] )
        return False

    # Clocking line from the center to the first bolt hole: the outline is oriented off it,
    # so the outline turns with the pattern.
    clockLine = sketch.sketchCurves.sketchLines.addByTwoPoints( centerPt.geometry, boltHole.centerSketchPoint.geometry )
    clockLine.isConstruction = True
    sketch.geometricConstraints.addCoincident( clockLine.startSketchPoint, centerPt )
    sketch.geometricConstraints.addCoincident( clockLine.endSketchPoint, boltHole.centerSketchPoint )

    if boltPattern.outline == 'kraken':
        _draw_kraken_outline( sketch, centerPt, clockLine, boltPattern.outlineDims[0], boltPattern.outlineDims[1], constrain )
    elif boltPattern.outline == 'maxplanetary':
        _draw_maxplanetary_outline( sketch, centerPt, clockLine, constrain )
    return True


# Round plate outer edge (e.g. Thrifty Cycloidal plates), as real geometry so the plate can be
# extruded straight from the sketch.
def _draw_circle_outline(sketch: adsk.fusion.Sketch, centerPt: adsk.fusion.SketchPoint, diaIn: float):
    diaCm = diaIn * 2.54
    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius( centerPt, diaCm / 2 )
    if circle.centerSketchPoint != centerPt:
        sketch.geometricConstraints.addCoincident( circle.centerSketchPoint, centerPt )
    textPt = futil.offsetPoint3D( centerPt.geometry, diaCm / 3, -diaCm / 3, 0 )
    sketch.sketchDimensions.addDiameterDimension( circle, textPt ).value = diaCm


# Kraken X60/X44 face outline:the round body plus the wire-routing bump, as construction.
# The bump points away from the first bolt hole (the gap in the 12-hole pattern). WCP's
# drawing gives only the body diameter and the overall height to the bump's flat; the bump's
# sides are taken as tangent to the body at 45 deg (90 deg apart), which matches the drawing.
def _draw_kraken_outline(sketch: adsk.fusion.Sketch, centerPt: adsk.fusion.SketchPoint,
                         clockLine: adsk.fusion.SketchLine, bodyDiaIn: float, overallIn: float,
                         constrain: bool):
    R = bodyDiaIn * 2.54 / 2
    h = overallIn * 2.54 - R                 # center to the bump's flat
    flatHalf = math.sqrt(2) * R - h

    # Local frame: +u toward the first bolt hole, bump toward -u
    c = centerPt.geometry
    d = clockLine.startSketchPoint.geometry.vectorTo( clockLine.endSketchPoint.geometry )
    d.normalize()
    def _pt(u, v):
        return adsk.core.Point3D.create( c.x + u * d.x - v * d.y, c.y + u * d.y + v * d.x, 0 )
    def _polar(deg):
        a = math.radians(deg)
        return _pt( R * math.cos(a), R * math.sin(a) )

    lines = sketch.sketchCurves.sketchLines
    arc = sketch.sketchCurves.sketchArcs.addByCenterStartSweep( centerPt, _polar(225), math.radians(270) )
    sideUp = lines.addByTwoPoints( _polar(135), _pt(-h, flatHalf) )
    flat = lines.addByTwoPoints( _pt(-h, flatHalf), _pt(-h, -flatHalf) )
    sideDown = lines.addByTwoPoints( _pt(-h, -flatHalf), _polar(225) )
    for curve in (arc, sideUp, flat, sideDown):
        curve.isConstruction = True

    if not constrain:
        return

    gc = sketch.geometricConstraints
    sd = sketch.sketchDimensions
    # Passing centerPt as the arc's center does not share the point -- stitch it explicitly
    if arc.centerSketchPoint != centerPt:
        gc.addCoincident( arc.centerSketchPoint, centerPt )
    gc.addCoincident( arc.endSketchPoint, sideUp.startSketchPoint )
    gc.addCoincident( sideUp.endSketchPoint, flat.startSketchPoint )
    gc.addCoincident( flat.endSketchPoint, sideDown.startSketchPoint )
    gc.addCoincident( sideDown.endSketchPoint, arc.startSketchPoint )
    gc.addTangent( arc, sideUp )
    gc.addTangent( arc, sideDown )
    gc.addPerpendicular( sideUp, sideDown )
    gc.addSymmetry( flat.startSketchPoint, flat.endSketchPoint, clockLine )
    sd.addDiameterDimension( arc, _pt(0.7 * R, 0.7 * R) )
    sd.addOffsetDimension( flat, centerPt, _pt(-h / 2, -0.5 * R) )


# REV MAXPlanetary outline (see the MAXP_* constants), as construction. The square's sides are
# parallel to the clocking line. The loop is REV's drawing mirrored in v: the pattern here runs
# CCW, so its face holes sit at 0/45/180/225 deg where the drawing's are at 0/135/180/315.
def _draw_maxplanetary_outline(sketch: adsk.fusion.Sketch, centerPt: adsk.fusion.SketchPoint,
                               clockLine: adsk.fusion.SketchLine, constrain: bool):
    a = MAXP_HALF_SIDE * 2.54
    ch = MAXP_CHAMFER * 2.54
    r = MAXP_EAR_RADIUS * 2.54
    e = MAXP_EAR_OVERHANG * 2.54

    c = centerPt.geometry
    d = clockLine.startSketchPoint.geometry.vectorTo( clockLine.endSketchPoint.geometry )
    d.normalize()
    def _pt(u, v):
        return adsk.core.Point3D.create( c.x + u * d.x - v * d.y, c.y + u * d.y + v * d.x, 0 )

    # Corners going clockwise (CCW in REV's unmirrored view). Ears: lower-right (flush with
    # the bottom face) and upper-left (flush with the top); chamfers: upper-right, lower-left.
    lines = sketch.sketchCurves.sketchLines
    arcs = sketch.sketchCurves.sketchArcs
    top = lines.addByTwoPoints( _pt(-(a + e), a), _pt(a - ch, a) )
    chamferTR = lines.addByTwoPoints( _pt(a - ch, a), _pt(a, a - ch) )
    right = lines.addByTwoPoints( _pt(a, a - ch), _pt(a, -a + 2 * r) )
    returnBR = lines.addByTwoPoints( _pt(a, -a + 2 * r), _pt(a + e, -a + 2 * r) )
    # Arcs always run CCW: this one from the bottom face up to the return line, bulging outward
    earBR = arcs.addByCenterStartSweep( _pt(a + e, -a + r), _pt(a + e, -a), math.pi )
    bottom = lines.addByTwoPoints( _pt(a + e, -a), _pt(-(a - ch), -a) )
    chamferBL = lines.addByTwoPoints( _pt(-(a - ch), -a), _pt(-a, -(a - ch)) )
    left = lines.addByTwoPoints( _pt(-a, -(a - ch)), _pt(-a, a - 2 * r) )
    returnTL = lines.addByTwoPoints( _pt(-a, a - 2 * r), _pt(-(a + e), a - 2 * r) )
    earTL = arcs.addByCenterStartSweep( _pt(-(a + e), a - r), _pt(-(a + e), a), math.pi )
    curves = (top, chamferTR, right, returnBR, earBR, bottom, chamferBL, left, returnTL, earTL)
    for curve in curves:
        curve.isConstruction = True

    if not constrain:
        return

    gc = sketch.geometricConstraints
    sd = sketch.sketchDimensions

    # Stitch the loop (-20 dof). earBR runs bottom -> return line; earTL top -> return line.
    gc.addCoincident( top.endSketchPoint, chamferTR.startSketchPoint )
    gc.addCoincident( chamferTR.endSketchPoint, right.startSketchPoint )
    gc.addCoincident( right.endSketchPoint, returnBR.startSketchPoint )
    gc.addCoincident( returnBR.endSketchPoint, earBR.endSketchPoint )
    gc.addCoincident( earBR.startSketchPoint, bottom.startSketchPoint )
    gc.addCoincident( bottom.endSketchPoint, chamferBL.startSketchPoint )
    gc.addCoincident( chamferBL.endSketchPoint, left.startSketchPoint )
    gc.addCoincident( left.endSketchPoint, returnTL.startSketchPoint )
    gc.addCoincident( returnTL.endSketchPoint, earTL.endSketchPoint )
    gc.addCoincident( earTL.startSketchPoint, top.startSketchPoint )

    # Directions (-6 dof)
    gc.addParallel( bottom, clockLine )
    gc.addPerpendicular( right, bottom )
    gc.addParallel( top, bottom )
    # Not parallel(left, right): with that, Fusion rejects the left offset dimension below
    # as over-constrained even though the left side is still free.
    gc.addPerpendicular( left, bottom )
    gc.addParallel( returnBR, bottom )
    gc.addParallel( returnTL, top )

    # Ears (-4 dof)
    gc.addTangent( earBR, bottom )
    gc.addTangent( earBR, returnBR )
    gc.addTangent( earTL, top )
    gc.addTangent( earTL, returnTL )

    # Dimensions (-12 dof)
    sd.addOffsetDimension( top, centerPt, _pt(-0.3 * a, 0.5 * a) )
    sd.addOffsetDimension( bottom, centerPt, _pt(0.3 * a, -0.5 * a) )
    sd.addOffsetDimension( right, centerPt, _pt(0.5 * a, 0.3 * a) )
    sd.addOffsetDimension( left, centerPt, _pt(-0.5 * a, -0.3 * a) )
    sd.addDiameterDimension( earBR, _pt(a + e + 2 * r, -a - r) )
    sd.addDiameterDimension( earTL, _pt(-(a + e + 2 * r), a + r) )
    sd.addDistanceDimension( returnBR.startSketchPoint, returnBR.endSketchPoint,
                             adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, _pt(a + e / 2, -a + 3 * r) )
    sd.addDistanceDimension( returnTL.startSketchPoint, returnTL.endSketchPoint,
                             adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, _pt(-(a + e / 2), a - 3 * r) )
    sd.addOffsetDimension( right, chamferTR.startSketchPoint, _pt(a - ch / 2, a + r) )
    sd.addOffsetDimension( top, chamferTR.endSketchPoint, _pt(a + r, a - ch / 2) )
    sd.addOffsetDimension( left, chamferBL.startSketchPoint, _pt(-(a - ch / 2), -a - r) )
    sd.addOffsetDimension( bottom, chamferBL.endSketchPoint, _pt(-a - r, -(a - ch / 2)) )


# This event handler is called when the command needs to compute a new preview in the graphics window.
def command_preview(args: adsk.core.CommandEventArgs):
    # General logging for debug.
    # futil.log(f'{CMD_NAME} Command Preview Event')

    # An outline is drawn unconstrained in preview, so let execute rebuild the real result.
    drewOutline = _build_pattern( args.command.commandInputs, constrain=False )
    args.isValidResult = not drewOutline


# This event handler is called when the user changes anything in the command dialog
# allowing you to modify values of other inputs based on that change.
def command_input_changed(args: adsk.core.InputChangedEventArgs):
    global _click_pt
    changed_input = args.input
    inputs = args.inputs

    # General logging for debug.
    # futil.log(f'{CMD_NAME} Input Changed Event fired from a change to {changed_input.id}')
    
    # Selecting an existing point/circle takes over from a clicked location.
    if changed_input.id == 'center_selection':
        centerSelection: adsk.core.SelectionCommandInput = changed_input
        if centerSelection.selectionCount > 0 and _click_pt is not None:
            _click_pt = None
            clickLocation: adsk.core.StringValueCommandInput = changed_input.parentCommand.commandInputs.itemById('click_location')
            clickLocation.value = ''

    # If the bolt pattern selection changed, update the center hole size default
    if changed_input.id == 'bolt_pattern':
        boltPatternInp: adsk.core.DropDownCommandInput = inputs.itemById('bolt_pattern')
        centerHoleSizeInp: adsk.core.ValueCommandInput = inputs.itemById('center_hole_size')
        
        selected_pattern = bolt_patterns[boltPatternInp.selectedItem.index]
        centerHoleSizeInp.expression = f'{selected_pattern.centerDia} in'
        inputs.itemById('show_outline').isVisible = selected_pattern.outline != ''



# This event handler is called when the user interacts with any of the inputs in the dialog
# which allows you to verify that all of the inputs are valid and enables the OK button.
def command_validate_input(args: adsk.core.ValidateInputsEventArgs):

    # futil.log(f'{CMD_NAME} Command Validate Event')

    inputs = args.inputs
    
    # Check if center is selected
    centerSelection: adsk.core.SelectionCommandInput = inputs.itemById('center_selection')
    centerHoleSizeInp: adsk.core.ValueCommandInput = inputs.itemById('center_hole_size')
    
    # Validate inputs
    hasCenter = centerSelection.selectionCount > 0 or _click_pt is not None
    if hasCenter and centerHoleSizeInp.value > 0:
        args.areInputsValid = True
    else:
        args.areInputsValid = False

# This event handler is called when the command terminates.
def command_destroy(args: adsk.core.CommandEventArgs):
    # General logging for debug.
    # futil.log(f'{CMD_NAME} Command Destroy Event')

    global local_handlers, _active_sketch, _click_pt
    local_handlers = []
    _active_sketch = None
    _click_pt = None
