# Application Global Variables
# This module serves as a way to share variables across different
# modules (global variables).

import adsk.core

app = adsk.core.Application.get()
ui = app.userInterface

# Flag that indicates to run in Debug mode or not. When running in Debug mode
# more information is written to the Text Command window. Generally, it's useful
# to set this to True while developing an add-in and set it to False when you
# are ready to distribute it.
DEBUG = False

# Gets the name of the add-in from the name of the folder the py file is in.
# This is used when defining unique internal names for various UI elements 
# that need a unique name. It's also recommended to use a company name as 
# part of the ID to better ensure the ID is unique.
ADDIN_NAME = 'FRCTools'
COMPANY_NAME = 'Team4698'

# Palettes
# sample_palette_id = f'{COMPANY_NAME}_{ADDIN_NAME}_palette_id'

# Toolbar stuff
WORKSPACE_ID = 'FusionSolidEnvironment'
SOLID_CREATE_ID = 'SolidCreatePanel'
SKETCH_CREATE_ID = 'SketchCreatePanel'
SKETCH_MODIFY_ID = 'SketchModifyPanel'
FRC_TOOLS_DROPDOWN_ID = 'FRCToolsSubMenu'

def _get_frc_submenu( panel_id: str ) -> adsk.core.ToolbarControl:
    # Find the FRCTools submenu in the given panel of the workspace.
    workspace = ui.workspaces.itemById( WORKSPACE_ID )
    panel = workspace.toolbarPanels.itemById( panel_id )
    return panel.controls.itemById( FRC_TOOLS_DROPDOWN_ID )

def get_sketch_create_submenu() -> adsk.core.ToolbarControl:
    return _get_frc_submenu( SKETCH_CREATE_ID )

def get_sketch_modify_submenu() -> adsk.core.ToolbarControl:
    return _get_frc_submenu( SKETCH_MODIFY_ID )

def get_solid_submenu() -> adsk.core.ToolbarControl:
    return _get_frc_submenu( SOLID_CREATE_ID )
