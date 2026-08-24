# FRCTools — AI Assistant Instructions

> These instructions are shared by **Claude** and **GitHub Copilot**. Read them before making
> changes. Before doing any Fusion-API work, also read **[LESSONS_LEARNED.md](../LESSONS_LEARNED.md)**
> and append to it when you learn something new (see the last section).

## What this is

`FRCTools` is an **Autodesk Fusion 360 add-in** (Python) that adds custom CAD tools to speed up
common tasks in **FIRST Robotics Competition (FRC)** robot design. It is maintained by the user
for **Team 1756**. It was forked from Team 4698's open-source FRCTools and was inspired by the
tools the community built for OnShape (see `README.adoc`).

Fusion loads this add-in from its `AddIns/FRCTools` directory and automatically calls `run()` and
`stop()` in `FRCTools.py`.

## Project structure

- **`FRCTools.py`** — the add-in entry point. `run()` creates the `FRCTools` dropdown submenu in
  the Solid-Create, Sketch-Create, and Sketch-Modify panels, then starts every command; `stop()`
  tears it all down. You rarely need to edit this.
- **`config.py`** — shared globals: `WORKSPACE_ID`, the panel IDs, `FRC_TOOLS_DROPDOWN_ID`,
  `ADDIN_NAME`, `COMPANY_NAME` (still the literal `'Team4698'` — it only builds unique internal UI
  IDs, so leave it unless the user asks to rebrand), a `DEBUG` flag, and the
  `get_solid_submenu()` / `get_sketch_create_submenu()` / `get_sketch_modify_submenu()` helpers
  that commands use to hang their buttons.
- **`commands/__init__.py`** — the command **registry**. Every tool is imported here and listed in
  the `commands[]` array; `start()`/`stop()` iterate over it. **Adding a tool = create a new folder
  under `commands/` and register it in this file.**
- **`commands/<Name>/entry.py`** — one command each. Use **`AutoHole`** and **`Tubify`** as
  reference implementations. Standard shape:
  - `CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_...'` (must be globally unique),
    `CMD_NAME`, `CMD_Description`, `ICON_FOLDER`, and a module-level `local_handlers = []`.
  - `start()` — registers the command definition and adds the button to a submenu.
  - `stop()` — deletes the control/definition and resets `local_handlers`.
  - `command_created(args)` — builds the dialog `commandInputs`, then wires handlers with
    `futil.add_handler(...)`: `command_execute`, `command_preview` (optional), `command_input_changed`,
    `command_validate_input`, `command_destroy`.
  - The `local_handlers` list **must** stay referenced or Fusion garbage-collects the handlers and
    the dialog silently stops responding. Clear it in `command_destroy`.
- **`commands/<Name>/resources/`** — `16x16.svg` / `32x32.svg` toolbar icons.
- **`commands/PartsGen/`** — a multi-tool command whose `entry.py` dispatches to per-part
  generators: `tube_gen.py`, `belt_gen.py`, `chain_gen.py`, `pulley_gen.py`, `sprocket_gen.py`,
  `shaft_gen.py`.
- **`lib/fusionAddInUtils/`** — shared helpers, imported everywhere as
  `from ...lib import fusionAddInUtils as futil`:
  - `general_utils.py` — `log()`, `handle_error()`, `popup_error()`, a family of `print_*` /
    `format_*` debug dumpers, and the `inchValue()` / `Value()` `ValueInput` builders.
  - `event_utils.py` — `add_handler()` / `clear_handlers()` event-handler plumbing (never subclass
    the Fusion handler types by hand; use `add_handler`).
  - `geom_utils.py` — 2D/3D point & vector math: `toPoint2D`, `toPoint3D`, `midPoint3D`,
    `offsetPoint3D`, `multVector2D`, `twoPointUnitVector`, `sketchLineUnitVec`, `sketchLineNormal`,
    `toTheRightOf`, `BBCentroid`, and more.
- **`docs/`, `README.adoc`** — user-facing documentation and screenshots.
- **`bundle.sh`, `win_install.nsi`, `.vscode/tasks.json`** — packaging (the "Create Installer and
  Zip Archive" VS Code task runs `bundle.sh`).

## Conventions & gotchas

- **Units:** Fusion's API works in **centimeters internally**. FRC is imperial, so expose **inch**
  inputs in dialogs but convert to cm before touching geometry — use `IN_TO_CM = 2.54` or
  `futil.inchValue(inches)`.
- **Errors & logging:** wrap risky work in `try/except` and route failures through
  `futil.handle_error(name, show_message_box=...)`. Use `futil.log(...)` for tracing. Set
  `config.DEBUG = True` to mirror logs to Fusion's **Text Command** window while developing.
- **Reuse `futil`** — don't re-implement geometry, logging, or event wiring that already exists in
  `lib/fusionAddInUtils/`.
- **Handler lifetime:** keep each command's `local_handlers` alive; reset it in `command_destroy`.
- **Match the surrounding style** of the file you're editing (naming, spacing, comment density).

## Dev / test workflow

There are **no automated tests** — tools are verified live in Fusion.

1. Edit the Python files in place (this repo *is* the installed add-in directory).
2. In Fusion, open **Scripts and Add-Ins** (`Shift+S`), and **Stop → Run** FRCTools to reload the
   code.
3. Trigger the command from its `FRCTools` submenu and watch the **Text Command** window (with
   `config.DEBUG = True`) for `futil.log` output and tracebacks.
4. To ship, run the VS Code **"Create Installer and Zip Archive"** task (or `bash ./bundle.sh`).

### Always test changes live via the Fusion MCP server

Whenever the Fusion MCP tools (`fusion_mcp_execute`, `fusion_mcp_read`, `fusion_mcp_update`) are
available, **develop and verify changes against a live Fusion session instead of only reading the
code** — this is the closest thing this project has to automated testing:

- After editing a command, reload the add-in (Stop → Run) and drive it end-to-end through the MCP
  server: run the `script` feature type to invoke the command/API path being changed, or to poke at
  the resulting geometry directly (e.g. `adsk.fusion` calls to inspect bodies/sketches/parameters).
- Use the `read` tool's `screenshot` query to visually confirm the resulting geometry looks right,
  and `apiDocumentation` to check exact signatures/enums before calling unfamiliar Fusion API
  members instead of guessing.
- Use `fusion_mcp_update` (`undo`/`redo`) to clean up test artifacts left in the open document after
  a verification run, so exploratory testing doesn't pollute the user's design.
- Prefer this live loop over "looks correct on inspection" — Fusion API behavior (units, parametric
  feature ordering, sketch/timeline side effects) is full of gotchas that only show up at runtime;
  see [LESSONS_LEARNED.md](../LESSONS_LEARNED.md).
- If the MCP server isn't connected/available, fall back to the manual Scripts-and-Add-Ins workflow
  above and say so rather than skipping testing.

## Lessons learned — keep this growing

**[LESSONS_LEARNED.md](../LESSONS_LEARNED.md)** is a running knowledge base of Fusion-specific
gotchas and good solution patterns discovered while working on this add-in.

- **Read it before** starting any Fusion-API work.
- **Append a new entry whenever** you figure out how to fix a non-obvious bug, or find a good/clean
  way to solve a recurring problem — anything a future session would waste time rediscovering.
- Use the entry format defined at the top of that file, and reference the relevant source file when
  it helps.
