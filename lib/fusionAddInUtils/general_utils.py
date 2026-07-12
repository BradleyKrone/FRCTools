#  Copyright 2022 by Autodesk, Inc.
#  Permission to use, copy, modify, and distribute this software in object code form
#  for any purpose and without fee is hereby granted, provided that the above copyright
#  notice appears in all copies and that both that copyright notice and the limited
#  warranty and restricted rights notice below appear in all supporting documentation.
#
#  AUTODESK PROVIDES THIS PROGRAM "AS IS" AND WITH ALL FAULTS. AUTODESK SPECIFICALLY
#  DISCLAIMS ANY IMPLIED WARRANTY OF MERCHANTABILITY OR FITNESS FOR A PARTICULAR USE.
#  AUTODESK, INC. DOES NOT WARRANT THAT THE OPERATION OF THE PROGRAM WILL BE
#  UNINTERRUPTED OR ERROR FREE.

import traceback
import adsk.core
import adsk.fusion

app = adsk.core.Application.get()
ui = app.userInterface

# Attempt to read DEBUG flag from parent config.
try:
    from ... import config
    DEBUG = config.DEBUG
except:
    DEBUG = False


def log(message: str, level: adsk.core.LogLevels = adsk.core.LogLevels.InfoLogLevel, force_console: bool = False):
    """Utility function to easily handle logging in your app.

    Arguments:
    message -- The message to log.
    level -- The logging severity level.
    force_console -- Forces the message to be written to the Text Command window. 
    """    
    # Always print to console, only seen through IDE.
    print(message)  

    # Log all errors to Fusion log file.
    if level == adsk.core.LogLevels.ErrorLogLevel:
        log_type = adsk.core.LogTypes.FileLogType
        app.log(message, level, log_type)

    # If config.DEBUG is True write all log messages to the console.
    if DEBUG or force_console:
        log_type = adsk.core.LogTypes.ConsoleLogType
        app.log(message, level, log_type)


def handle_error(name: str, show_message_box: bool = False):
    """Utility function to simplify error handling.

    Arguments:
    name -- A name used to label the error.
    show_message_box -- Indicates if the error should be shown in the message box.
                        If False, it will only be shown in the Text Command window
                        and logged to the log file.                        
    """    

    log('===== Error =====', adsk.core.LogLevels.ErrorLogLevel)
    log(f'{name}\n{traceback.format_exc()}', adsk.core.LogLevels.ErrorLogLevel)

    # If desired you could show an error as a message box.
    if show_message_box:
        ui.messageBox(f'{name}\n{traceback.format_exc()}')

def popup_error( message: str ):
    log(message)
    ui.messageBox( message )

def print_SketchObjectCollection( col: adsk.core.ObjectCollection ) :
    log(f'print_SketchObjectCollection() -- Collection has {col.count} items...')
    for item in col.asArray():
        item: adsk.fusion.SketchEntity = item
        log(f'item type = {item.objectType}:')
        log(f'   isFixed({item.isFixed}), is2D({item.is2D}), isFullyConstr({item.isFullyConstrained}), isRef({item.isReference})')
        print_SketchCurve( item )

def print_OrientedBB( orientedBB: adsk.core.OrientedBoundingBox3D ) :
    log(f'item type = {orientedBB.objectType}, isValid={orientedBB.isValid}')
    log(f'   height = {orientedBB.height}, length={orientedBB.length}, width={orientedBB.width}')
    print_Point3D( orientedBB.centerPoint, "   centerPt: ")

def format_Vector2D( v: adsk.core.Vector2D ) :
    return f'({v.x:.4},{v.y:.4})'

def print_Point3D( pt: adsk.core.Point3D, prefix: str = "" ) :
    log( f'{prefix} {format_Point3D( pt )}' )

def format_Point3D( pt: adsk.core.Point3D ) :
    return f'({pt.x:.4},{pt.y:.4},{pt.z:.4})'

def format_Vector3D( v: adsk.core.Vector3D ) :
    return f'({v.x:.4},{v.y:.4},{v.z:.4})'

def print_SketchCurve( curve: adsk.fusion.SketchCurve ) :
    if curve.objectType == adsk.fusion.SketchLine.classType() :
        line: adsk.fusion.SketchLine = curve
        log(f'SketchLine: {format_Point3D(line.startSketchPoint.geometry)} -- {format_Point3D(line.endSketchPoint.geometry)}')
    elif curve.objectType == adsk.fusion.SketchArc.classType() :
        arc: adsk.fusion.SketchArc = curve
        str = f'SketchArc: C{format_Point3D(arc.centerSketchPoint.geometry)}, '
        str += f'{format_Point3D(arc.startSketchPoint.geometry)} -- {format_Point3D(arc.endSketchPoint.geometry)}'
        log(str)
    elif curve.objectType == adsk.fusion.SketchCircle.classType() :
        circle: adsk.fusion.SketchCircle = curve
        str = f'SketchCircle: C{format_Point3D(circle.centerSketchPoint.geometry)}, '
        str += f'radius = {circle.radius}'
        log(str)
    elif curve.objectType == adsk.fusion.SketchPoint.classType() :
        pt: adsk.fusion.SketchPoint = curve
        log(f'SketchPoint: {format_Point3D(pt.geometry)}')
    else :
        log(f'print_SketchCurve() --> {curve.objectType} Not handled.')
    log(f'    is2D({curve.is2D}), isDeletable({curve.isDeletable}), isFixed({curve.isFixed}), isFullyConstrained({curve.isFullyConstrained})')
    log(f'    isLinked({curve.isLinked}), isReference({curve.isReference}), isValid({curve.isValid}), isVisible({curve.isVisible})')

def print_Curve3D( curve: adsk.core.Curve3D ) :
    if curve.objectType == adsk.core.Line3D.classType() :
        line: adsk.core.Line3D = curve
        log(f'Line3D: {format_Point3D(line.startPoint)} -- {format_Point3D(line.endPoint)}')
    elif curve.objectType == adsk.core.Arc3D.classType() :
        arc: adsk.core.Arc3D = curve
        str = f'Arc3D: C{format_Point3D(arc.center)}, {format_Point3D(arc.startPoint)} -- {format_Point3D(arc.endPoint)}'
        str += f', R={arc.radius:.4}'
        log(str)
    elif curve.objectType == adsk.core.Circle3D.classType() :
        circle: adsk.core.Circle3D = curve
        str = f'Circle3D: C{format_Point3D(circle.center)}, '
        str += f'radius = {circle.radius}, normal={format_Vector3D(circle.normal)}'
        log(str)
    else :
        log(f'print_Curve3D() --> {curve.objectType} Not handled.')
    # log(f'    is2D({curve.is2D}), isDeletable({curve.isDeletable}), isFixed({curve.isFixed}), isFullyConstrained({curve.isFullyConstrained})')
    # log(f'    isLinked({curve.isLinked}), isReference({curve.isReference}), isValid({curve.isValid}), isVisible({curve.isVisible})')

def print_Profiles( profiles: adsk.fusion.Profiles ) :
    log(f'========= Listing for {profiles.count} Profiles =========')
    pidx = 0
    while pidx < profiles.count:
        p = profiles.item( pidx )
        log(f'    Profile #{pidx+1}, number of loops={p.profileLoops.count}:')
        lidx = 0
        while lidx < p.profileLoops.count:
            l = p.profileLoops.item(lidx)
            log(f'        Loop #{lidx+1}, number of curves = {l.profileCurves.count}')
            cidx = 0
            while cidx < l.profileCurves.count:
                c = l.profileCurves.item(cidx)
                log(f'            Sketch Entity for Curve #{cidx+1}:')
                print_SketchCurve( c.sketchEntity )
                log(f'            geometry for Curve #{cidx+1}:')
                print_Curve3D( c.geometry )
                cidx += 1
            lidx += 1
        pidx += 1


def print_Attributes( entity: adsk.fusion.SketchEntity ) :
    log(f'Entity {entity.objectType} has {len(entity.attributes)} attributes')
    for attr in entity.attributes:
        log(f'   {attr.name} ==> {attr.value}')

def inchValue( inches: float ) -> adsk.core.ValueInput :
    return adsk.core.ValueInput.createByReal( inches * 2.54 )

def Value( number: float ) -> adsk.core.ValueInput :
    return adsk.core.ValueInput.createByReal( number )