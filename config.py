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
SOLID_TAB_ID = 'SolidTab'
SKETCH_CREATE_ID = 'SketchCreatePanel'
SKETCH_MODIFY_ID = 'SketchModifyPanel'
FRC_TOOLS_DROPDOWN_ID = 'FRCToolsSubMenu'

# Our own "FRC" panel in the SOLID tab, sitting alongside the stock Create /
# Modify / Assemble panels. Fusion remembers ribbon layout per user, so the
# position below only applies the first time the panel is created.
FRC_PANEL_ID = f'{COMPANY_NAME}_{ADDIN_NAME}_SolidPanel'
FRC_PANEL_NAME = 'FRC'
FRC_PANEL_POSITION = 'SolidModifyPanel'   # the panel we get inserted next to
FRC_PANEL_BEFORE = False                  # False = after FRC_PANEL_POSITION

def _get_frc_submenu( panel_id: str ) -> adsk.core.ToolbarControl:
    # Find the FRCTools submenu in the given panel of the workspace.
    workspace = ui.workspaces.itemById( WORKSPACE_ID )
    panel = workspace.toolbarPanels.itemById( panel_id )
    return panel.controls.itemById( FRC_TOOLS_DROPDOWN_ID )

def get_sketch_create_submenu() -> adsk.core.ToolbarControl:
    return _get_frc_submenu( SKETCH_CREATE_ID )

def get_sketch_modify_submenu() -> adsk.core.ToolbarControl:
    return _get_frc_submenu( SKETCH_MODIFY_ID )

def get_solid_tab() -> adsk.core.ToolbarTab:
    # The SOLID tab of the Design workspace, which owns the FRC panel.
    return ui.workspaces.itemById( WORKSPACE_ID ).toolbarTabs.itemById( SOLID_TAB_ID )

def get_frc_panel() -> adsk.core.ToolbarPanel:
    # The FRC panel itself; solid commands hang their buttons directly off this.
    return get_solid_tab().toolbarPanels.itemById( FRC_PANEL_ID )
