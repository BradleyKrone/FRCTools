# Assuming you have not changed the general structure of the template no modification is needed in this file.
import adsk.core
from . import commands
from .lib import fusionAddInUtils as futil
from . import config

app = adsk.core.Application.get()
ui = app.userInterface

def run(context):
    try:

        # ******** Add a button into the UI so the user can run the command. ********
        # Get the target workspace the button will be created in.
        workspace = ui.workspaces.itemById( config.WORKSPACE_ID )

        # Create our own "FRC" panel in the SOLID tab, next to the stock Create /
        # Modify panels. A leftover panel from a previous run (e.g. after a crash)
        # would make add() fail, so reuse it if it's still there.
        solid_tab = config.get_solid_tab()
        frc_panel = solid_tab.toolbarPanels.itemById( config.FRC_PANEL_ID )
        if not frc_panel:
            frc_panel = solid_tab.toolbarPanels.add(
                config.FRC_PANEL_ID,
                config.FRC_PANEL_NAME,
                config.FRC_PANEL_POSITION,
                config.FRC_PANEL_BEFORE
            )

        # Get the panels the FRCTools dropdowns will be created in.
        sketch_create_panel = workspace.toolbarPanels.itemById( config.SKETCH_CREATE_ID )
        sketch_modify_panel = workspace.toolbarPanels.itemById( config.SKETCH_MODIFY_ID )

        # Create the FRCTool submenu in the sketch-create and sketch-modify panels.
        # (Solid commands go straight into the FRC panel above, no submenu needed.)
        sketch_create_panel.controls.addDropDown( "FRCTools", "", config.FRC_TOOLS_DROPDOWN_ID )
        sketch_modify_panel.controls.addDropDown( "FRCTools", "", config.FRC_TOOLS_DROPDOWN_ID )

        # This will run the start function in each of your commands as defined in commands/__init__.py
        commands.start()

    except:
        futil.handle_error('run', True)


def stop(context):
    try:
        # Remove all of the event handlers your app has created
        futil.clear_handlers()

        # This will run the stop function in each of your commands as defined in commands/__init__.py
        commands.stop()

        workspace = ui.workspaces.itemById(config.WORKSPACE_ID)
        sketch_create_panel = workspace.toolbarPanels.itemById( config.SKETCH_CREATE_ID )
        sketch_modify_panel = workspace.toolbarPanels.itemById( config.SKETCH_MODIFY_ID )

        sketch_create_submenu = sketch_create_panel.controls.itemById( config.FRC_TOOLS_DROPDOWN_ID )
        # Delete Sketch->Create FRCTools submenu
        if sketch_create_submenu:
            sketch_create_submenu.deleteMe()

        sketch_modify_submenu = sketch_modify_panel.controls.itemById( config.FRC_TOOLS_DROPDOWN_ID )
        # Delete Sketch->Modify FRCTools submenu
        if sketch_modify_submenu:
            sketch_modify_submenu.deleteMe()

        # Delete the FRC panel itself -- leaving it behind means a stale empty
        # panel in the ribbon and a duplicate-id failure on the next run().
        frc_panel = config.get_frc_panel()
        if frc_panel:
            frc_panel.deleteMe()

    except:
        futil.handle_error('stop')
