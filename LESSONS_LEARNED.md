# Lessons Learned

Fusion 360 API gotchas and good patterns found while working on **FRCTools**. Keep entries terse —
this file is read in full at the start of every Fusion-API task, so verbosity costs tokens on every
session.

## How to use this file

- **Read it before** starting any Fusion-API work.
- **Add an entry whenever** you fix a non-obvious bug or find a reusable pattern.
- **Keep it short**: 1-line problem, 1-2 line fix, one file reference. No Date/Context fields —
  git history already has that. Newest entries at the top.

### Entry format

```
### <Short title>
<Problem in one line>. **Fix:** <what works>, 1-2 lines max.
`path/to/file.py`
```

---

## Lessons

### "Joints on this linked component" means boundary-crossing joints, not joints whose side *is* it
A pick inside a linked/external-reference component should answer "what mounts this link into my
design?", so Joint Inspector rolls the target up to the outermost ancestor with
`Occurrence.isReferencedComponent` True. Matching joints against that container occurrence exactly is
wrong both ways: the real mount usually hangs off a *child* of the link (confirmed live -- "FOV Off
v6:3" has **zero** joints on the container yet is jointed to a frame rail through its Camera_Mount
child), while joints whose side *is* the container are the linked document's own internal joints to
that document's root component, proxied into this context. **Fix:** match joints where exactly one
side is in the link's subtree (`fullPathName == root or startswith(root + '+')`) and take the inside
side as "this side".
`commands/JointInspector/entry.py` (`_linked_root`, `_find_boundary_joint_matches`)

### `Occurrence.bRepBodies` is not recursive -- a sub-assembly occurrence usually reports zero bodies
Every top-level linked sub-assembly in the user's robot reports `bRepBodies.count == 0`; all its
geometry belongs to child occurrences. Code that washes/inspects "the bodies of an occurrence" gets
nothing for exactly the assemblies it matters most for. **Fix:** walk `Occurrence.childOccurrences`
recursively and collect `bRepBodies` at each level -- and cap the result, since one CustomGraphics
mesh per body over a few hundred bodies stalls the viewport on every preview rebuild.
`commands/JointInspector/entry.py` (`_occurrence_bodies`)

### `Component.allJoints`/`allAsBuiltJoints` throws outright -- for the *whole design* -- once a linked/external-reference component is in the tree
Selecting a body inside a linked component (the chain-link icon in the browser, e.g. an "Insert
linked" reference to another document) made Joint Inspector's `_find_joint_matches` crash with
`RuntimeError: 3 : object does not belong to the occurrence's component`, raised directly from
Fusion's own `Component__get_allJoints` getter -- confirmed live via the Fusion MCP script tool that
`list(design.rootComponent.allJoints)` fails this way on a real 1206-occurrence assembly containing
such a link, with **zero** joints returned, not just the ones touching the link. The bug isn't
target-specific: it reproduces for *any* selection once the design contains such a component, because
the failure is in flattening the whole design's joints, not in matching a particular occurrence.
**Fix:** don't rely on the bulk `allJoints`/`allAsBuiltJoints` properties alone. Try them first (fast
path, works for every design without a link), and on `RuntimeError` fall back to building the same
flattened, root-proxied list by hand: walk `root.allOccurrences`, read each occurrence's *native*
`comp.joints`/`comp.asBuiltJoints`, and proxy each one individually via
`joint.createForAssemblyContext(occ)` inside its own try/except -- a joint that can't be proxied
through one bad occurrence is just skipped and logged, instead of taking down every other joint in the
design with it. Confirmed live on the same assembly: the fallback recovered 734 of 747 joints (13 more
came straight off the root component), with only the 59 genuinely unproxyable ones skipped.
`commands/JointInspector/entry.py` (`_all_joints_flat`)

### Don't drive `DcEditJointAssembleCmd` (or terminate it) from a script -- it crashed Fusion
Tried, with a joint selected, to invoke the internal "double-click edit joint" command definition
(`ui.commandDefinitions.itemById('DcEditJointAssembleCmd')`) via the MCP script tool to see if it
would open the real Edit Joint dialog pre-bound to that joint (hoping it behaved differently than
`EditJointAssembleCmd`, which was already confirmed to open blank -- see `_joint_to_edit` in
`commands/JointInspector/entry.py`). It did open a dialog, but bound to nothing (same blank/new-joint
state, confirmed via screenshot), and calling `ui.terminateActiveCommand()` on it right after crashed
Fusion outright. **Fix: don't.** There is no known API path to open the native Edit Joint dialog
pre-loaded with an existing joint -- stick with select + `FindInBrowser` (previous entry) and let the
user double-click it themselves.
`commands/JointInspector/entry.py`

### Selecting an entity doesn't expand its collapsed browser ancestors -- run `FindInBrowser` too
`ui.activeSelections.add(entity)` highlights the entity in the browser tree only if its parent
folders (component, Joints folder, etc.) are already expanded; if they're collapsed nothing visibly
happens. **Fix:** select the entity first, then execute the built-in command definition
`ui.commandDefinitions.itemById('FindInBrowser')` -- this is exactly what right-click > "Find in
Browser" runs, and it expands every collapsed ancestor and highlights the item. It acts on the
current selection, so the select-then-execute order matters.
`commands/JointInspector/entry.py`

### `activeViewport.refresh()` inside a command event can crash Fusion
`refresh()` pumps Fusion's message loop, so calling it from `executePreview`/`inputChanged` lets a
queued event run **re-entrantly** on top of the design writes the current pass is still holding --
Fusion dies outright rather than raising. **Fix:** never refresh from inside a command event that
modifies the design; `executePreview` is already part of the redraw cycle, so graphics built there
paint without asking. Refresh only from `destroy`, and keep a re-entrancy flag on `executePreview`.
`commands/JointInspector/entry.py`

### `bestMesh` on a hidden/stale body hands back a mesh that crashes `addMesh`
A body that is invisible, suppressed or mid-recompute returns an empty or inconsistent display mesh,
and `CustomGraphicsGroup.addMesh` takes the process down with it instead of raising. **Fix:** skip
`not body.isVisible`, and validate the mesh before drawing -- non-zero node count,
`len(normals) == len(points)`, `max(indices) < nodeCount`. Same for anything cached across preview
rebuilds: check `.isValid` first, because reading a rolled-back entity crashes rather than raises.
`commands/JointInspector/entry.py`

### Table rows must be created from `TableCommandInput.commandInputs`, and need unique ids
`TableCommandInput.clear()` only detaches rows; inputs built from the *dialog's* `CommandInputs`
stay alive there forever, so rebuilding the table reuses ids like `joint_row_0`. Fusion doesn't
reject the duplicate -- `itemById()` then returns the stale input while events arrive from the new
one. **Fix:** create rows from `tableInput.commandInputs` and stamp a generation counter into every
id (`joint_row_<gen>_<i>`), ignoring events whose generation is stale.
`commands/JointInspector/entry.py`

### Grouping occurrences by `Component` is O(n^2) unless you bucket by name first
`Component` has no usable hash and a colliding `entityToken`, so identity needs a linear `==` scan --
which on a 194-occurrence assembly is tens of thousands of COM calls *per preview rebuild*.
**Fix:** bucket by `comp.name` (a plain string) first and run the `==` scan only inside the bucket;
walk `root.allOccurrences` flat instead of recursing `childOccurrences`.
`commands/JointInspector/entry.py`

### Writing to the design from `inputChanged` suspends a command's whole compute pipeline
**Root cause of "Joint Inspector ghosts correctly but never shows the orange/green highlight."**
Ghosting applied `Component.opacity`/`Occurrence.isLightBulbOn` from `inputChanged`. The writes
themselves succeed (101 components really did dim), but afterwards Fusion **stops firing
`validateInputs` and `executePreview` for the rest of that dialog's life** while `inputChanged`
keeps working -- so the dialog looks alive and responsive while the OK button greys itself out,
`target_entity` silently loses its selection (`activeCommand` reported `selectionCount: 0` with the
joint table still populated and no `inputChanged` fired for it), and every CustomGraphic silently
never draws. **Nothing is logged, because the draw is never reached** -- this is not an exception
being swallowed. Confirmed by A/B: with `ghost_others` unchecked *before* picking, the identical
code path draws the highlight perfectly and OK is enabled. It is also why unchecking the box
mid-session didn't recover -- `_clear_ghosting()` is itself another `inputChanged` design write.
**Fix:** `inputChanged` may only *record intent* (an index, a flag); `executePreview` is the one
handler that may modify the design, and it re-applies both the ghosting and the graphics on every
rebuild. Two corollaries: (1) a preview's design writes can be rolled back before the next rebuild,
so the "already applied, skip" guards must re-assert the value when it has drifted and record the
original only once, or the ghost silently stops re-applying; (2) don't gate `areInputsValid` on
anything optional in a command whose `executePreview` owns on-screen state -- an invalid command
gets no `executePreview`, stranding whatever was last drawn with no handler left to clean it up.
**Diagnosing this class of bug:** empty log + `customGraphicsGroups.count == 0` +
`_highlight_group is None` means the draw never ran -- look at what stopped the *handler* firing,
not at the renderer. All three are readable from a `readOnly: true` MCP script while the dialog is
open, which is safe (unlike walking a live command's `commandInputs`).
`commands/JointInspector/entry.py` (`command_preview`, `_update_ghosting`, `command_validate_input`)

### There is no per-occurrence opacity at all -- `Occurrence.isLightBulbOn` is the only genuinely per-instance display control
Chasing "Joint Inspector ghosts the entire model": `Component.opacity` is the **only** settable
opacity in the API. `Occurrence.visibleOpacity` is read-only and its own doc says so outright ("To set
the opacity use the opacity property of the Component object"), and `Occurrence.appearance` -- the one
thing that *could* dim a single instance -- is unusable near a live dialog (entry below).
`Occurrence.isIsolated` is no help either: only one occurrence can be isolated at a time. That leaves
`Occurrence.isLightBulbOn`, confirmed live to be genuinely independent per instance (hid
`Thunder Hex 0.375 v7:1` of 4; `:2`/`:3`/`:4` stayed visible, `Component.opacity` still 1.0, explicit
restore came back clean). **Consequence for any "dim everything except X" feature:** a per-*component*
ghost rule is unusable in a real FRC assembly -- 132 of that design's 194 occurrences belong to
multi-instance components, so "ghost if any occurrence is unrelated" ghosted both sides of the joint
too and washed out the whole model, while "keep if any occurrence is related" left every instance of a
shared part lit. **Fix:** decide per component in three cases, not two -- all occurrences related ->
leave lit; none related -> dim via `Component.opacity` (keeps context); **mixed** -> leave the
component lit and hide only its unrelated occurrences via `isLightBulbOn`. Track hidden ones by
`Occurrence.fullPathName` and restore explicitly.
`commands/JointInspector/entry.py` (`_apply_ghosting`, `_collect_component_keep_status`, `_hide_occurrence`)

### `adsk.fusion.Component` objects can't be dict keys or set members, and `entityToken` is unsafe too
`Component` overrides `==` (two separate `occ.component` accesses for the same underlying component
compare equal) but has no matching `__hash__`, so Python makes it unhashable -- `some_dict[comp] = x`
raises `TypeError: unhashable type: 'Component'` at runtime, confirmed live. The obvious fix, keying
by `comp.entityToken` instead, is **also broken**: confirmed live in a real 194-occurrence assembly
that `Component.entityToken` collides constantly across totally unrelated components (57 collisions,
e.g. "44x v9" and "4inch_Fairline v2" reporting the identical token) -- silently breaking any
"already handled this one" check keyed by it. `Occurrence.entityToken` *is* reliably unique (0
collisions, same assembly), but keying by it doesn't help either when the goal is deduplicating by
*component*. **Fix:** `Occurrence.fullPathName` (a plain string) is a reliable, hashable identity for
per-occurrence tracking -- see the ghosting entries below, which key off it instead of any
Component/Occurrence object or token.
`commands/JointInspector/entry.py` (`_hidden_occurrences`)

### `Component.opacity`/`BRepBody.opacity` are shared per shared definition, not per occurrence -- the API doc's wording for `BRepBody.opacity` is misleading
Setting `Component.opacity` dims every occurrence that references it *and* everything nested inside
those. Confirmed live via the Fusion MCP server that `BRepBody.opacity`, even read/written through an
occurrence-scoped `occ.bRepBodies.item(i)` proxy, behaves the same way: setting it on one occurrence's
proxy also changed the opacity read back from a *different* occurrence's proxy of the same body --
both for two sibling occurrences under the same parent and for two occurrences of the same component
nested under entirely different top-level parents. This directly contradicts the literal
`BRepBody.opacity` API doc text ("different instances of the same body can display using different
opacity levels") -- **do not trust that wording without live-testing it first**. Practical
consequence: an FRC assembly reuses the same bearing/spacer/fastener/pulley `Component` at many
occurrences, so no opacity-based mechanism (`Component` or `BRepBody`) can tell occurrences of the
same shared part apart -- ghosting one unrelated instance either also ghosts every other instance
(including a joint's own occurrence, if it happens to be one of them), or has to exempt the whole
component from ghosting, leaving every instance lit up together (a real reported bug: selecting one
of four `Thunder Hex 0.375` instances kept all four un-ghosted; later, ghosting a joint whose own side
was a 10-instance pulley dimmed that side too). Neither is acceptable when a joint's own part is a
shared/patterned component, which is common in an FRC assembly (gears, pulleys, spacers). **Fix:**
`Occurrence.appearance` was tried next and turned out to be unusable too (next entry). What actually
works is splitting the decision three ways and hiding -- not dimming -- the unrelated instances of a
shared part via `Occurrence.isLightBulbOn`; see the top entry of this file.
`commands/JointInspector/entry.py`

### `Occurrence.appearance` is genuinely independent per occurrence, but is unsafe to write anywhere near this command's live dialog -- even deferred
An appearance *override* set via `occ.appearance = someAppearance` is confirmed live to be genuinely
independent per occurrence (an override on one sibling occurrence of a shared Component does not
affect another occurrence of it, even across different top-level parents) while still cascading down
to every body nested inside that occurrence's subtree -- exactly the per-occurrence granularity
`Component.opacity` can't provide (previous entry). That part works -- **but only from a plain script
outside any command, or once Fusion is idle with no command open.** Two attempts to use it for
ghosting while Joint Inspector's dialog is open were each confirmed live to break something different:
1. Calling it *directly* from `command_input_changed`/`command_destroy` throws `RuntimeError: 3 :
   Cannot modify the design from a read-only context` -- silently leaving the ghost appearance stuck
   on every previously-ghosted occurrence forever (the tracking dict resets on the next
   `command_created`, orphaning them).
2. Deferring it to a `CustomEvent` handler (mirroring `_EDIT_JOINT_EVENT_ID`'s already-proven pattern
   for a *different* "can't do X from inside this event" restriction -- there, `ui.
   terminateActiveCommand()` raising "can not terminate command during a command event") escapes error
   #1, but introduces a worse problem: the write still happens while Joint Inspector's dialog is open,
   and doing so destabilizes the still-live `target_entity` `SelectionCommandInput` -- confirmed live
   (reported by the user, reproduced from their screenshots) that the selection browser shows the pick
   succeeding for a moment ("1 joint(s) found") and then reverts to "Select a body" a second later, as
   if the selection were silently cleared.
**Conclusion:** don't write `Occurrence.appearance` from anywhere reachable while this command's
dialog is open, deferred or not -- use `Component.opacity` plus per-occurrence `isLightBulbOn` for
ghosting instead (top entry of this file). Before trusting *any* property write inside an interactive
command handler, verify it live in that *exact* context if at all possible -- a script run outside a
command, or a direct call to the same function bypassing the dialog entirely, proves nothing about
what's allowed while a command's dialog is actually open, and this bug shipped twice in a row because
of exactly that gap in testing.
`commands/JointInspector/entry.py`

### `importlib.reload()` on a live add-in module doesn't remove functions/globals the new source deleted
Confirmed live via the Fusion MCP server: after editing `entry.py` to delete a function (`_get_ghost_
appearance`) and reload the already-imported module with `importlib.reload(mod)`, `hasattr(mod, '_get_
ghost_appearance')` still returned `True` -- the stale function object from the previous version was
still bound in the module's namespace, since `reload()` only re-executes the module's top-level code
and overwrites/adds names the new source defines, it doesn't clear names that are simply absent from
it now. **Fix:** don't trust `importlib.reload()` plus `hasattr`/behavior checks as proof that old code
is gone -- a full add-in **Stop -> Run** does a fresh import and is the only way to get a truly clean
module state matching the current file on disk.
`commands/JointInspector/entry.py`

### Driving Fusion's UI via script (opening a real command with `cmdDef.execute()`) can crash the app -- don't do it for live verification
Tried, while debugging the entry above, to open a real command dialog via `ui.commandDefinitions.
itemById(CMD_ID).execute()` from an MCP script (to get a genuine `command_input_changed` context to
test property writes in) and then kept issuing further script calls into Fusion while that dialog sat
open. This left the Fusion MCP server unresponsive and Fusion itself crashed shortly after, confirmed
by the user's own Fusion crash-reporter dialog. **Fix:** never call `.execute()` on a command
definition from a script for verification purposes, and never issue additional script calls while any
command dialog might be open as a result of a prior script -- there is no safe way found yet to
reproduce a real interactive-command context from outside Fusion's own UI thread. When a fix depends
on behavior that only manifests inside a live command's event handlers, ship the change based on
sound reasoning from already-proven patterns in the codebase, then ask the user to test it themselves
through the normal UI rather than trying to script-drive the dialog.
`commands/JointInspector/entry.py`

### Live MCP script writes to `.opacity`/`.appearance` aren't reliably reverted by the Fusion MCP server's `undo`
Confirmed live: setting `BRepBody.opacity` via a script, then calling the Fusion MCP server's `undo`
feature (even twice in a row), left the value unchanged -- unlike `Component.opacity`, which a
previous test confirmed IS undo-stack tracked (one undo reverted it). **Fix:** when a live-testing
script mutates opacity/appearance for verification, explicitly write the property back to its
original value in a follow-up script rather than trusting `undo` to clean up after it, and read the
value back afterward to confirm the design is actually clean before moving on.
`commands/JointInspector/entry.py`

### `AsBuiltJoint` has no `geometryOrOriginOne`/`Two` and its shared `geometry` can be `None`
`adsk.fusion.AsBuiltJoint` is a distinct class from `adsk.fusion.Joint` (check with
`joint.objectType == adsk.fusion.AsBuiltJoint.classType()`), found in a separate
`Component.allAsBuiltJoints` collection (not `allJoints`) -- has to be unioned with `allJoints` to
find every joint touching a body. It shares `occurrenceOne`/`occurrenceTwo` (same `_joint_occurrence`
ground-handling applies), but instead of two per-side `geometryOrOriginOne`/`Two` it has one shared
`geometry` (the joint's computed coordinate system, since an as-built joint is captured from parts
already in position rather than assembled by picking two geometries) -- and confirmed live that
`.geometry` can be `None` even on a real as-built joint between two solid bodies, so guard it. **Fix:**
for as-built joints, highlight by washing every body in each side's occurrence (there's no per-side
geometry entity to trace back to one specific body) and only draw the exact face/edge/marker when
`.geometry` isn't `None`.
`commands/JointInspector/entry.py` (`_draw_asbuilt_highlight`, `_occurrence_bodies`)

### There is no API way to open Fusion's native "Edit Joint" dialog pre-loaded for an existing joint
Selecting a `Joint` (`ui.activeSelections.add(joint)`) -- with or without first rolling the timeline
to it via `joint.timelineObject.rollTo(False)` -- and then executing `EditJointAssembleCmd` (the
built-in "Edit Joint " command definition) always opens the dialog blank, with both Component 1/2
"Snap" pickers empty, exactly as if creating a brand new joint. Confirmed live repeatedly, with and
without the timeline roll, and neither changed the result. Fusion's real double-click/right-click
"Edit Joint" in the browser isn't exposed as an equivalent scriptable call -- Autodesk's own docs and
forums only describe editing an existing joint's *values* directly through its API properties/methods
(e.g. `joint.setAsRigidJointMotion()`), never popping the creation dialog pre-bound to it. **Fix:**
don't try to auto-open it. The best an add-in can do is select and reveal the joint (roll the
timeline to it, select it) and leave the actual dialog-opening double-click to the user.
`commands/JointInspector/entry.py` (`_reveal_joint_for_edit`)

### Don't script `timelineObject.rollTo()` immediately followed by executing a command definition
Rolling the timeline then, in the same `fusion_mcp_execute` script, immediately capturing a
`commandCreated` handler and enumerating the new command's `commandInputs` (`inputs.item(idx)`,
`ci.selection(0).entity`, etc.) crashed the whole Fusion application once (native crash reporter, not
a Python exception). Doing the same rollTo + select + execute sequence *without* that inputs
enumeration afterward, or checking state via the `activeCommand` read query instead, did not
reproduce it -- so the risky part looks like introspecting a freshly-created command's inputs from a
script, not the rollTo/execute themselves. **Fix:** to inspect a live command's dialog from a script,
use the `fusion_mcp_read` `activeCommand` query (safe, confirmed live) rather than walking
`args.command.commandInputs` yourself in the script that created it.
`commands/JointInspector/entry.py` (`_reveal_joint_for_edit`)

### `Command.doExecute()` can't be called from inside that command's own event handler
Calling `args.firingEvent.sender.doExecute(True)` directly inside an `inputChanged` handler (to close
the dialog when a button is clicked) raises `RuntimeError: 3 : can not terminate command during a
command event` -- confirmed live. Same family of restriction as `ui.terminateActiveCommand()`, which
is explicitly documented as unsupported inside command-related events. **Fix:** defer it with a
`CustomEvent`: `app.registerCustomEvent(id)` once at add-in `start()`, fire it with
`app.fireCustomEvent(id)` from inside the input handler instead of calling `doExecute`/
`terminateActiveCommand` directly, and do the actual termination + follow-up work in the custom
event's handler -- per the API docs, a fired custom event is queued and only runs "when the
application is idle", i.e. outside the original command event's call stack.
`commands/JointInspector/entry.py` (`_EDIT_JOINT_EVENT_ID`, `_on_edit_joint_event`)

### `fusion_mcp_execute` scripts can't drive a live interactive command dialog
Opening a command via `ui.commandDefinitions.itemById(id).execute()` from an MCP `script`, then
pumping `adsk.doEvents()` in a loop to interact with its `commandInputs` (select something, click a
row, read a textbox), looks like it should work -- `commandCreated` fires and `args.command.isValid`
is `True` at that instant -- but the command is torn down (`isValid` flips to `False`) within a
couple of `doEvents()` calls, before any further interaction happens. Confirmed this is not specific
to any one command: an untouched, unmodified command (`Team4698_FRCTools_TubifyDialog`) dies the
same way. **Fix:** there isn't one from the script side -- treat this as a hard limitation of the
MCP script harness and fall back to manual interactive testing (Scripts and Add-ins reload, then
click through the command by hand) for anything that needs a dialog to stay open across more than
one round of events. Live-testing *pure logic* (helper functions, geometry math) via a script is
still fine; it's specifically a multi-step *interactive dialog* session that can't be automated
this way.
`commands/JointInspector/entry.py`

### Single-select list in a dialog: `TableCommandInput` + one checkbox `BoolValueCommandInput` per row
This codebase had no precedent for a persistently-visible, clickable list (every other command uses
`DropDownCommandInput`/`TextListDropDownStyle`). Built one for Joint Inspector's joint list instead
of a dropdown: `addTableCommandInput(id, name, 1, '1')`, then one
`addBoolValueInput(f'row_{i}', label, True, '', i == 0)` per row placed via
`table.addCommandInput(rowInput, i, 0)`. Checkboxes are independent by default -- nothing stops more
than one being checked -- so `inputChanged` for a row that just got checked must manually set every
other row's `.value = False` to fake single-select/radio behaviour. Guard that unchecking loop with a
module-level re-entrancy flag in case Fusion re-fires `inputChanged` synchronously for each
programmatic `.value` write it causes.
`commands/JointInspector/entry.py` (`_set_selected_row`)

### Draw CustomGraphics from `executePreview`, never from `inputChanged`
**This is the root cause of JointInspector's "only the first joint ever shows" bug** -- three earlier
diagnoses below (missing `Viewport.refresh()`, solid-body occlusion, selection-highlight colour clash)
were all real defects but none of them were it. Fusion destroys every `CustomGraphicsGroup` on the
design each time it rebuilds a command's preview. Proven live by logging state from `validateInputs`:
right after a draw the group was `isValid=True, count=7, cgGroups=1`, and on the very next
`validateInputs` it was `isValid=False, cgGroups=0` -- with no `_clear_highlight()` of ours in between.
Graphics drawn from `inputChanged` therefore flash up and are wiped on the next rebuild (the user's
report of "a very fast flicker of the correct faces, then it goes away" was the whole diagnosis).
**Fix:** have `inputChanged` record *what* should be shown (an index) and add an `executePreview`
handler that does the drawing -- Fusion re-invokes it after every rebuild, so the highlight is
re-created as fast as it is destroyed. Set `args.isValidResult = False` since nothing is being built.
A read-only command still needs `executePreview`; "creates no geometry" is not a reason to skip it.
`commands/JointInspector/entry.py` (`command_preview`, `_selected_idx`)

### Diagnose "it flickers" as a lifetime problem, not a drawing problem
Chasing JointInspector's invisible highlight, several rounds went into *how* it was drawn (colour,
opacity, depth, occlusion, camera) because a script that called the draw helpers directly always
produced correct output. The giveaway was the user saying the right thing appeared for a split second
first -- that rules out every drawing theory at once and means something is deleting the graphics
afterwards. **Fix:** when output is briefly correct then disappears, stop tuning the renderer and
instrument *lifetime* instead -- log object `isValid`/collection counts from a handler that fires
repeatedly (`validateInputs` is ideal) and dump `traceback.extract_stack()` in the teardown path to
prove whether your own code or Fusion is doing the deleting.
`commands/JointInspector/entry.py`

### Fusion's own selection highlight swamps translucent CustomGraphics -- never highlight in blue
Another real defect found while chasing the same symptom, but not its root cause either (see the
executePreview entry above). A `SelectionCommandInput` keeps the picked body selected for as long as
the dialog is open, and Fusion paints that body with its blue selection highlight *over* custom
graphics. A green "this side" wash at 0.35 opacity vanished under it and a blue "other side" wash was
indistinguishable from it. This only reproduces with a live selection -- driving the draw helpers from
a script looked perfect, which is what made it so slippery. **Fix:** reproduce the real condition in a
test script with `ui.activeSelections.add(body)` before screenshotting (safe when no command dialog is
open -- see the `activeSelections` entry below for why it is *not* safe while one is), pick colours
that cannot collide with selection blue (orange reads unambiguously), and push body-wash opacity to
~0.65.
`commands/JointInspector/entry.py` (`_OTHER_SIDE_COLOR`, `_BODY_OPACITY`)

### Don't swallow draw errors in a command's redraw path
`_draw_joint_highlight` failures were routed to `handle_error(show_message_box=False)` with no other
trace, which made a genuinely broken highlight indistinguishable from a correctly-drawn-but-invisible
one -- and sent several rounds of debugging after rendering theories instead. **Fix:** keep the modal
off (it would fire on every preview rebuild) but always route through `futil.handle_error`, which
writes the traceback to the Text Command window and the Fusion log. Note the draw now happens in
`executePreview`, so the error can't be written into a command input from there -- the log is the
channel. When a highlight misbehaves, read the Text Command window first.
`commands/JointInspector/entry.py` (`command_preview`)

### CustomGraphics are depth-tested against solids -- there is no "draw on top"
A real defect found while chasing that symptom, though not its root cause (see the executePreview
entry above). A joint highlight can be drawn at perfectly correct world coordinates and still be
completely invisible, because it is buried inside the solid bodies: geometry attached to a face or
edge pointing away from the camera renders hidden, while a camera-facing face looks fine. Proven by
re-rendering the hidden edge from the opposite camera, where it appeared in full. The whole
`CustomGraphicsEntity` API is
`deleteMe`/`get|setOpacity`/`isVisible`/`isSelectable`/`depthPriority`/`viewScale`/`viewPlacement` --
`depthPriority` only orders custom graphics against **each other**, not against model geometry, so it
cannot rescue an occluded highlight. **Fix:** don't rely on exact geometry being visible -- give each
side a translucent whole-body wash (`body.meshManager.displayMeshes.bestMesh` -> `addMesh`) that is
too big to hide, and size point markers with `CustomGraphicsViewScale.create(pixels, anchor)` so they
stay a constant on-screen size instead of shrinking to a speck (a fixed 0.6cm crosshair was ~8px on an
assembly).
`commands/JointInspector/entry.py` (`_highlight_body`, `_add_marker`, `_highlight_side`)

### `Joint.occurrenceOne`/`occurrenceTwo` raise instead of returning None for a grounded side
Reading either property throws `RuntimeError: 2 : InternalValidationError : jointOcc` when that side of
the joint is attached to the root component rather than to an occurrence. Unguarded it aborted the
whole `target_entity` handler, so `_current_matches = _find_joint_matches(...)` never assigned and the
joint dropdown stayed empty for that pick **and every pick after it**. **Fix:** read both through one
helper that catches and returns `None` -- "attached to the root" is exactly the ground case the rest of
the code already means by `None`.
`commands/JointInspector/entry.py` (`_joint_occurrence`)

### CustomGraphics edits need an explicit `Viewport.refresh()` -- and only one per redraw
JointInspector's viewport highlight (see the entry below) worked the first time a joint was picked but
never updated when switching to a different joint in the dropdown -- no error, the graphics were
provably being rebuilt correctly (confirmed live: `design.rootComponent.customGraphicsGroups.count`
and each group's `.count` matched expectations after every switch), they just weren't being repainted.
The first draw happened to get painted for free because picking the body itself causes Fusion to
repaint the view (selection highlight changes); a pure dropdown change doesn't touch selection, so
nothing told the viewport to repaint the new custom graphics. **Fix:** call
`app.activeViewport.refresh()` once after every redraw. Two traps found doing this: (1) calling
`refresh()` from inside `_clear_highlight()` *and* again at the end of the draw that immediately
follows it triggered an exception on the delete call often enough to matter, which a bare
`except: pass` then swallowed -- the deleted group's Python reference was already nulled out before the
exception, so the group silently leaked (confirmed live: `customGraphicsGroups.count` stayed at 1
after a `_clear_highlight()` call that should have brought it to 0). Refresh **once**, only after the
new group is fully built, not once per intermediate step. (2) Null out the module's group reference
*before* calling `deleteMe()` (not after, and not only on success) so a delete that raises can't leave
a stale reference in the way of the next redraw -- log the exception text instead of silently passing.
`commands/JointInspector/entry.py` (`_refresh_viewport`, `_clear_highlight`)

### `CustomGraphics` is the right way to show "what a joint connects to" -- not `ui.activeSelections` or text
JointInspector originally described a joint's attachment geometry (face/edge/point on each side) as an
HTML text block, because an earlier attempt to highlight it via `ui.activeSelections` was abandoned (see
the entry below). `CustomGraphicsGroup` on `design.rootComponent` sidesteps that whole problem -- it's a
separate display layer that never touches `ui.activeSelections` or the undo stack, so it's safe to draw
from inside `command_input_changed` even while `target_entity`'s `SelectionCommandInput` is still on
screen. Recipe per entity type: `BRepFace` -> `face.meshManager.displayMeshes.bestMesh` gives an
already-computed `TriangleMesh` (`nodeCoordinatesAsDouble`/`nodeIndices`/`normalVectorsAsDouble`) to feed
straight into `group.addMesh(...)`, no new tessellation needed; `BRepEdge` -> `group.addCurve(edge.geometry)`;
a bare point (`BRepVertex`/`ConstructionPoint`/`SketchPoint.worldGeometry`) -> a small 3D crosshair built
from `group.addLines(...)`, which (unlike a screen-facing point-image marker) reads correctly from any
camera angle. Gotchas confirmed live via the Fusion MCP script executor: (1) `CustomGraphicsGroups.add()`
raises `RuntimeError: 3 : Cannot modify the design from a read-only context` if the calling script/handler
context is read-only -- a real command handler is never read-only, so this only bites ad hoc test scripts,
not the shipped feature; (2) `CustomGraphicsMesh.setOpacity(value, isOverride)` takes **two** required
positional args, not one. Always `group.deleteMe()` the whole group (not each entity) to tear the highlight
down in one call -- do it on every joint-selection change and in `command_destroy`, mirroring how the
`_current_matches` list itself is reset.
`commands/JointInspector/entry.py` (`_draw_joint_highlight`, `_highlight_face`, `_highlight_edge`, `_add_marker`)

### `ui.activeSelections.clear()`/`.add()` fights a live `SelectionCommandInput`
JointInspector called `ui.activeSelections.clear()` (to reset a "highlight the connected geometry"
feature) from inside its own `target_entity` input's `inputChanged` handler, at the very top before
even reading the new selection. `ui.activeSelections` turned out to be the same underlying store a
focused `SelectionCommandInput` is reading from while it gathers a pick — clearing it there wiped
out the very click that triggered the event, so nothing could ever be selected (confirmed live: an
otherwise-identical command with no `activeSelections` calls, e.g. Tubify, selected the same body
fine). **Fix:** don't mutate `ui.activeSelections` at all while a command's own `SelectionCommandInput`
is still active/gathering picks — there's no known-safe sequencing for it, so the viewport-highlight
feature was dropped rather than chased further.
`commands/JointInspector/entry.py`

### An overlooked one-line side effect can look like an intractable Fusion/environment bug
Debugging JointInspector's "clicking never selects anything," a real-but-irrelevant Fusion quirk
(`ui.activeCommand` reports the generic `SelectCommand` while any `SelectionCommandInput` is actively
armed, not the actual running custom command) led several rounds of live MCP diagnosis toward "this
must be environment/session-wide," including a full Fusion restart, before a plain side-by-side
against a known-working command (Tubify, same filter, same fresh document) isolated it back down to
one line of JointInspector's own code (see the `activeSelections` entry above). **Fix:** when a
custom command misbehaves, A/B it against another already-working command on the exact same input
before trusting an environment-level theory — it's a five-minute check that rules out a whole class
of wrong turns.
`commands/JointInspector/entry.py`

### Fusion MCP `fusion_mcp_execute` script mutations don't persist across separate calls
Building a test assembly in one `fusion_mcp_execute` script call, then reading it back in a
following call, consistently showed 0 occurrences/joints even though the first call's own prints
showed the objects existed and worked. **Fix:** each script execution appears to run in its own
disposable context — do setup *and* assertions inside a single script call when verifying Fusion
API behavior via MCP; don't rely on state created by one call being visible to the next.
`commands/JointInspector/entry.py`

### Joint API: `allJoints`, ground sides, and JointOrigin vs. JointGeometry
`design.rootComponent.allJoints` (not `.joints`, which only returns joints owned directly by that
component) flattens every joint in the whole tree as proxies — the one call needed to enumerate all
joints from the root. `Joint.occurrenceOne`/`occurrenceTwo` can legitimately be `None` (the joint is
grounded to the root component, not another occurrence) — guard for it. `Joint.geometryOrOriginOne`/
`Two` can be either a plain `JointGeometry` or a `JointOrigin` wrapping one — always unwrap via
`JointOrigin.geometry` before touching `.entityOne`/`.entityTwo`/`.origin`. To identify which
occurrence a joint side refers to, compare `.fullPathName` strings rather than `.entityToken`
(Autodesk's own docs warn against treating token equality as an identity check) — verified live
against a real two-component rigid joint plus a duplicate occurrence instance of one of the
components (to confirm same-component-different-instance doesn't false-positive-match).
`commands/JointInspector/entry.py`

### `executePreview` calls the same creation code as `execute`, with no validity gate
PartsGen's `command_preview` handled Shaft/Tube/Pulley/Sprocket by just calling
`command_execute(args)` and then hardcoding `args.isValidResult = True` — regardless of whether
creation actually succeeded. `executePreview` fires continuously as the user types/drags, including
on transient states `command_validate_input` would reject (e.g. a tooth-count field mid-edit), and
the underlying `_create_*` functions already show a blocking `ui.messageBox` on failure via
`futil.handle_error(show_message_box=True)` — so a bad transient value produced a message box on
every preview tick while still reporting success. **Fix:** factor the dispatch into a helper that
returns success/failure and takes a `show_message_box` flag; `command_preview` passes `False` (log
only) and sets `isValidResult` to the real result, `command_execute` keeps `True`. Also add cheap
guard clauses at the top of each `_create_*` function (tooth count, OD/ID, wall thickness) that
silently `return` rather than raise, since preview can call them with values the dialog itself would
never allow through the OK button.
`commands/PartsGen/entry.py` (`_run_part_creation`, `command_preview`)

### A fixed-size feature must be checked against the part's own computed size, not just a tooth-count floor
PartsGen's timing-pulley and sprocket hex bore is always cut at a fixed 0.5in across-flats,
regardless of tooth count. `command_validate_input` only floors the tooth count (pulley ≥8, sprocket
≥9) to keep the *tooth profile itself* from degenerating — it says nothing about whether the
resulting tooth-OD circle is actually bigger than the hex bore. A low but "valid" tooth count (e.g.
an 8-tooth GT2 pulley) has a tooth diameter smaller than the hex bore's circumradius, so the cut
would self-intersect the outer profile. **Fix:** compare the hex bore's circumradius against the
part's own computed outer/tip diameter right before cutting, and skip the cut with a clear message
instead of attempting invalid geometry.
`commands/PartsGen/pulley_gen.py` (`_add_hex_bore`), `commands/PartsGen/sprocket_gen.py`
(`_add_hex_bore_sprocket`)

### Zero-length-vector guards belong in the shared helper, not re-derived at every call site
`geom_utils.twoPointUnitVector`/`lineNormal` divided by the vector's magnitude with no zero check.
PartsGen's belt/chain pitch-loop construction (`_buildPitchLoop`) is reachable from the *initial
creation* path with no distinct-circles check, even though the *rebuild* path (triggered when a CC
distance is edited) already guards the equivalent condition (`cc < abs(r1 - r2)`) — an easy
inconsistency to introduce when a feature grows a "rebuild on edit" path after the fact. **Fix:**
raise a clear `ValueError` in the shared helper itself (so any future caller is protected for free),
and additionally add the same explicit `cc`-distance check used on rebuild right before the initial
build call, so the user gets a clean `popup_error` instead of an unhandled exception.
`lib/fusionAddInUtils/geom_utils.py` (`twoPointUnitVector`, `lineNormal`),
`commands/PartsGen/belt_gen.py`/`chain_gen.py` (`_create_belt`/`_create_chain`)

### A bounding-box center is not a reliable stand-in for "inside the body"
FaceFillet's convex/concave edge test (`_edge_is_convex`) decided which side of an edge's
face-normal bisector was "inside the body" by comparing against the body's *bounding-box*
center. That only works for roughly box-shaped, symmetric solids — confirmed wrong live on
a "staple" shape (a wide short base with two long thin prongs, gap between them): the
bbox center sits out in the open gap between the prongs, and the two corners where the
base meets each prong were misclassified as concave when they're actually convex
(independently confirmed via the polygon's own winding direction). **Fix:** use the body's
real mass centroid (`body.physicalProperties.centerOfMass`) instead, computed once per
call and passed down rather than recomputed per edge (it was previously recomputed from
scratch inside the per-edge function). Note this bug is easy to *miss* with test geometry:
a simple symmetric L-bracket did **not** reproduce it — for a shape symmetric about a
diagonal, the bbox center and mass centroid end up on the same side of every corner's
test plane regardless of the approximation error, so proving the fix required an
intentionally lopsided body and comparing against the pre-fix heuristic directly.
`commands/FaceFillet/entry.py` (`_edge_is_convex`, `_body_centroid`), regression test at
`commands/FaceFillet/dev_scripts/regression_test.py` (`_test_asymmetric_convexity`)

### A rolling-ball tangent-chain fillet is far more forgiving than "radius <= face width"
Tried to force FaceFillet's all-at-once fillet attempt to fail on just *some* edges (to
test the per-edge fallback) by making one edge's adjacent face much narrower than the
requested radius (a 0.05in-wide tab with a 1.0in radius, filleted as part of one
`addConstantRadiusEdgeSet` call with `isRollingBallCorner=True`/`isTangentChain=True`
covering all edges at once) — it still succeeded every time. A single multi-edge rolling-
ball fillet call is evidently able to blend away geometry far more aggressively than a
naive "radius must fit within the adjacent face" model predicts. **Fix, if you need a
guaranteed partial failure for testing:** don't try to construct a geometrically-tight
face; instead just request a radius larger than a plain symmetric box's own half-width
(e.g. a 2x2x2in box with a 3in radius) — confirmed live and deterministic across repeated
runs that the all-at-once attempt fails and the per-edge fallback then succeeds for 2 of
the 4 corners and fails the other 2, which is enough to exercise the fallback path without
needing exotic geometry.
`commands/FaceFillet/entry.py` (`_apply_fillet`), regression test at
`commands/FaceFillet/dev_scripts/regression_test.py` (`_test_fillet_partial_fallback`)

### A copy-pasted range check silently stops validating the second variable
`command_validate_input`'s cog-teeth range check in CCDistance's create/edit dialogs tested
`cog1Teeth.value < 100` twice instead of checking `cog2Teeth.value`'s upper bound — invisible in
normal use because the integer spinner UI itself hard-clamps to 6-100, so it only bites if that
range ever changes or the value is set another way. **Fix:** when a validation condition has the
same shape repeated for two inputs, grep for the second variable name in your own check afterward;
don't trust that "it looks right" once is enough. Caught by directly calling
`command_validate_input`/`edit_command_validate_input` with duck-typed stub inputs (`.value`,
`.itemById()`, `.areInputsValid`) — no real Fusion UI needed to unit-test a validate-handler.
`commands/CCDistance/create_cmd.py`, `commands/CCDistance/edit_cmd.py`

### Regression-test an inverse formula against the equation it solves, not a re-typed copy of itself
CCDistance derives belt/chain center distance by solving a physical equation (belt pitch-length,
chain link-count) for the center distance via a closed-form quadratic. Re-deriving the same
closed-form by hand in a test is easy to get subtly wrong, and re-typing the identical formula
just checks the code against itself. **Fix:** plug the function's returned value back into the
*original* defining equation (e.g. `2*C + pi*(D1+D2)/2 + (D1-D2)**2/(4*C) - beltPitchLengthIN`) and
assert the residual is ~0 — this catches a broken derivation (wrong sign, dropped term, wrong
constant) that a tautological re-implementation would miss.
`commands/CCDistance/dev_scripts/regression_test.py`

### Fill mode's straight-edge row count must subtract the start offset in *both* places it's computed, or it bites a crescent notch off the part
`_build_edge_row_line_fill`'s Fill count was computed from the raw edge length (`length_in / spacing_in`)
instead of the *usable* length after the fixed 0.5" start offset — unlike its sibling
`_build_edge_row_arc`, which already subtracts `HOLE_OFFSET_CM` first. Confirmed live via the Fusion
MCP tools: a 6" edge, 0.5" offset, 0.4" spacing computed `count=15`, and the 15th instance's center
landed at 6.1" — past the edge's far end. Because Fill mode cuts via a feature-level
`RectangularPatternFeature` (translated boolean cuts, not profile-based), that out-of-bounds instance
doesn't just get silently dropped — it bites a real crescent `Arc3D` notch out of the part's boundary
edge. **Fix:** compute `usable_length_in = (length_in * IN_TO_CM - start_offset_cm) / IN_TO_CM` and
derive `count` from that. Only fixing the *initial* count wasn't enough, though — the same function
immediately overwrites `pattern.quantityOne.expression` with a **second**, independently-computed
live formula (`_length_param_name`-driven, for resize support) that had the identical bug baked into
its string template. Both computations needed the same offset subtraction; verified by reading
`pattern.quantityOne.expression`/`.value` back after the fix (`max(1; floor(( d415 - 0.5 in ) / 0.4
in))`) and confirming zero `Arc3D` edges anywhere on a freshly re-cut test body.
`commands/AutoHole/entry.py` (`_build_edge_row_line_fill`)

### `PathPatternFeatureInput.startPoint` breaks the pattern outright when the seed is already positioned correctly — don't set it
Patterning a seed hole cut around a closed offset loop (`_process_loop_fill`) failed on *every*
instance with `PATTERN_FEATURES_NO_PASTE_INT_EDGES — No pattern instances could be intersected with
the original body`, even on a clean test body with no other geometry nearby — confirmed live via the
Fusion MCP tools by reproducing the exact same call against a fresh plate. Bisected it down to one
line: setting `patInput.startPoint` (a 0–1 fraction along the path, meant to phase-match where the
pattern begins) to the seed's own position as a fraction of the total path length. The seed hole was
already physically placed at that exact location before the cut, which is all Fusion needs to align
the pattern to it — also setting `startPoint` creates a conflicting second definition of "where the
pattern starts" that breaks every instance's compute, confirmed by removing just that one line and
rerunning the identical reproduction successfully (all 62 holes, clean wrap around the seam with no
doubling or gap). **Fix:** leave `startPoint` at its default; position the seed feature itself where
you want the pattern to start and nothing else is needed.
`commands/AutoHole/entry.py` (`_process_loop_fill`)

### `PathPatternFeature` can pattern a cut around an arbitrary closed loop, but there's no live "total perimeter" parameter to drive its count
Extending the seed-cut-plus-feature-pattern fix (below) from a straight edge to the outer-edge loop
trace (`_process_loop_fill`) mostly works the same way — `Path.create()` accepts the offset loop's
own `ObjectCollection` of connected lines/arcs directly (multiple curves in one collection skip the
chaining-rule argument entirely, per its docstring), and `comp.features.pathPatternFeatures` patterns
the seed cut around it with `PatternDistanceType.SpacingPatternDistanceType` (see the `startPoint`
entry above for a sharp edge in the same API). Where it *can't* match the straight-edge case: there's
no sketch dimension type for an arbitrary multi-segment path's combined length (unlike one line's
length or one circle's radius), so the pattern's `quantity` has to stay a literal computed once in
Python — resizing the part still needs a re-run of AutoHole, only the immediate cut-vs-sketch
mismatch is fixed. Same suppression trade-off as the straight-edge case applies too.
`commands/AutoHole/entry.py` (`_process_loop_fill`)

### A frozen multi-profile cut extrude can't grow even if its sketch pattern does — needs a feature-level pattern instead
Fill mode's straight-edge row built N sketch circles via a sketch-level `RectangularPatternConstraint`
and cut them all in one multi-profile `ExtrudeFeature`. Upgrading that constraint's `quantityOne` to a
live expression (see the length-parameter trick below) made new circles appear in the sketch on
resize, but the cut never grew to match — an `ExtrudeFeature`'s profile selection is a fixed list of
`Profile` objects captured at creation; Fusion has no "auto-include any future profile" mode, so the
new circles just sat there uncut. **Fix:** cut only the seed hole as its own single-profile extrude,
then replicate *that cut feature* with a feature-level `RectangularPatternFeature`
(`comp.features.rectangularPatternFeatures`) whose `quantityOne` is a live expression — a feature
pattern reruns the underlying cut the given number of times on every recompute, which a sketch
pattern + frozen cut can't do. Trade-off: `PatternElement.isSuppressed`/`suppressedElementsIds`
reliably fails for this feature type (see the entry below), so per-hole "Suppress Holes" can't apply
to a Fill row — hide/disable that input for this specific combination instead of shipping a control
that silently does nothing.
`commands/AutoHole/entry.py` (`_build_edge_row_line_fill`)

### Fill mode's hole count must be re-derived from a live parameter, not baked in as a literal, or it won't track a resized edge
A pattern's count computed once from the edge's length in Python and passed in as a plain int
(`futil.Value(count)`) never updates on a later resize — positions/direction can stay live
(dimensioned off the projected edge), but the count is frozen. **Fix:** after the initial cut, add a
*reference* (`isDriving=False`) distance dimension across the projected edge's own endpoints
(`_length_param_name`, mirrors `_radius_param_name`) to expose its live length as a named parameter,
then rewrite the pattern's `quantityOne.expression` to a formula referencing it
(`max(1; floor(<param> / <spacing> in))`). This alone is necessary but not sufficient for a cut
feature — see the entry above about the frozen-profile-list gotcha it ran into next.
`commands/AutoHole/entry.py` (`_length_param_name`, `_process_circular_edge`, `_build_edge_row_line_fill`)

### A pattern's reference/helper construction line only needs its direction constrained, not its length dimensioned
`_build_edge_row_line` built two invisible construction lines (`dir_line` for the pattern direction,
`ref_line` for the longitudinal-offset dimension to measure from) and pinned each one's length with
its own driving dimension, purely so nothing was left "unconstrained". Those two arbitrary values
(e.g. `1.287`, `6.969`) showed up as real-looking dimensions in the sketch right next to the seed
hole's actual diameter/offset dimensions, making the hole look over-dimensioned to anyone reading
it. Neither dimension was load-bearing: `setDirectionOne` only reads the line's direction, and
`addOffsetDimension` measures perpendicular distance to a line's infinite direction, not its segment
length — both already fully fixed by a parallel/perpendicular constraint plus a coincident start
point. **Fix:** constrain a reference line's direction and start point, and leave its length
undimensioned — don't add a dimension just to make a sketch look "fully constrained" when nothing
downstream actually depends on that length.
`commands/AutoHole/entry.py` (`_build_edge_row_line`)

### Reusing a SketchPoint object as a new curve's endpoint doesn't survive the source being re-projected
Passing an existing `SketchPoint` into `addByTwoPoints` makes the new line start there with no
separate coincident constraint — confirmed via `new_line.startSketchPoint is base_line.startSketchPoint`
→ `True` right after creation. That equality is deceptive: it does **not** survive a later recompute
that regenerates the projected edge it came from. Resizing a part so an edge's start vertex moves
showed the projected edge (`sketch.project()`'s result) correctly tracking the new position, while a
helper line that had merely *reused* its old corner point stayed orphaned at the stale location —
silently corrupting every dimension measured off that helper line (and anything patterned from it).
**Fix:** never rely on point-object reuse for anything that must survive a recompute. Create a fresh
point instead, and add an explicit `sketch.geometricConstraints.addCoincident(newPoint, realPoint)` —
an actual constraint is re-solved against the corner's live identity every recompute, not a snapshot
of "whichever object happened to be passed in."
`commands/AutoHole/entry.py` (`_build_edge_row_line`)

### Sketch-level `RectangularPatternConstraint.isSuppressed` needs the seed fully dimensioned first, and skip the write when nothing's suppressed
Two traps, both confirmed by direct A/B testing on a live pattern:
1. Assigning `pattern.isSuppressed = [...]` while the seed entity still has *any* unresolved
   DOF (i.e. before it's fully dimensioned) makes Fusion's solver settle on a different, still
   technically-valid solution branch — observed as the *entire* pattern silently reversing
   direction. Fix: fully dimension the seed (diameter + position) before ever touching
   `isSuppressed`.
2. Even with the seed fully constrained, assigning `isSuppressed` with **every value False**
   (i.e. nothing actually suppressed) *still* triggers the same reversal — the act of writing the
   property forces a re-solve, not just the values in it. Fix: only assign `isSuppressed` when
   `any(flags)` is true; skip the write entirely otherwise.
Unrelated to the feature-level `PatternElement.isSuppressed` failure below — this sketch-level
constraint's suppression genuinely works (and even removes the suppressed copy's geometry from the
sketch entirely, confirmed via `sketchCircles.count`), it's just order/timing-sensitive.
`commands/AutoHole/entry.py` (`_build_edge_row_line`)

### `RectangularPatternConstraintInput.setDirectionOne`'s distance reads the document's display unit, not cm
Nearly every other `ValueInput.createByReal(x)` call in the Fusion API takes `x` as raw internal
centimeters. This one specific parameter doesn't: passing `createByReal(2.54)` meaning "2.54 cm"
produced a live pattern whose `distanceOne.value` read back as `6.4516` cm — i.e. it had actually
interpreted `2.54` as **inches** in this inch-default document, then stored the cm equivalent.
**Fix:** use `ValueInput.createByString(f'{spacing_cm} cm')` for this parameter specifically, never
`createByReal`, to remove the ambiguity regardless of the document's display unit.
`commands/AutoHole/entry.py` (`_build_edge_row_line`)

### `PatternElement.isSuppressed` / `suppressedElementsIds` failed every time in testing
Tried to programmatically suppress individual instances of a `RectangularPatternFeature` (both via
`element.isSuppressed = True` and via `feature.suppressedElementsIds = [...]`) to give AutoHole's
straight-edge row native per-hole suppression. Every attempt failed with the same
`RuntimeError: 3 : Didn't roll editing feature back.` — on element 0 *and* element 1, in the same
script right after `.add()` and in a separate later script call, regardless of an unrelated
`quantityTwo` default-value quirk (see below) also present at the time. Never found a combination
that worked from script. **Don't spend more time on this path** — if it's ever worth revisiting,
test interactively from the Fusion UI first (right-click → Suppress) before assuming the API will
cooperate; a real user doing that by hand is unaffected by whatever this is.
`commands/AutoHole/entry.py` (`_build_edge_row_line`)

### `RectangularPatternFeatureInput.quantityTwo` silently defaults to a nonzero value
Never calling `setDirectionTwo`/setting `directionTwoEntity` still left `quantityTwo.value` reading
back as `3` (not `1`) and `patternElements.count` as `quantityOne * 3`, even though the resulting
*geometry* only ever reflected `quantityOne` real instances (verified by edge count) — direction two
isn't actually active without a direction entity, this is just a cosmetic/placeholder default.
**Fix, if `patternElements` needs to be enumerated 1:1 with real holes:** explicitly set
`pat_input.quantityTwo = ValueInput.createByReal(1)` before `.add()` rather than trusting the
readback default.
`commands/AutoHole/entry.py`

### Suppressing one hole in a big batch still cost the full batch's work
"Suppress Holes" re-runs the whole row/loop/ring from scratch on every click (Fusion's preview
model rolls back and re-executes the command each time an input changes), so its real cost is per-
click, not one-time. Two anti-patterns made each click pay for the *whole* batch regardless of how
much was already suppressed: (1) every candidate's circle was drawn first and suppressed ones
filtered out *after*, so a suppressed hole still added a curve for every later profile search to
sift through; (2) profile matching called the expensive `profile.areaProperties()` on every profile
in the sketch unconditionally, including the sketch's own large leftover-face profile that can never
match a hole's tiny area. **Fix:** check suppression *before* drawing a hole's circle at all (skip
entirely, don't draw-then-filter), and reject obviously-wrong-sized profiles via the cheap
`profile.boundingBox` before paying for `areaProperties()`. Measured on a 116-hole loop: ~2.2s/click
with nothing suppressed yet, dropping to ~0.3s/click once 100 of 116 were suppressed — before this
fix every click cost the same ~2s+ regardless of suppression count.
`commands/AutoHole/entry.py` (`_match_profiles_to_centers`, `_process_loop_from_edge`,
`_build_edge_row_arc`)

### A "distance-to-center vs radius" inward test only works for a full circle
Deciding which side of a curve has material by comparing a hint point's distance from the curve's
center against its radius is only valid when the circle's inside/outside genuinely spans the whole
local region (a full bore, or the part's own round outer edge). It's wrong for a small arc that's
just one segment of a bigger loop (e.g. a corner fillet) — a hint point far across the face can read
as "outside" that small circle while still being on the *inward* side of the arc's own boundary.
**Fix:** do a local dot-product test at the arc's own midpoint instead (radial direction vs. vector
to the hint point), the same idea `_perp_toward` uses for a line's perpendicular — never compare raw
distances against a size/shape-dependent threshold when a direction (dot-product) test is available.
`commands/AutoHole/entry.py` (`_build_edge_row_arc`)

### `futil.sketchLineNormal`'s `towardPt` hint is a no-op — don't rely on it
It's supposed to flip the returned normal to face `towardPt`, but the flip condition is
`abs(towardUnitVec.angleTo(normal)) > math.pi`, and `Vector2D.angleTo()` only ever returns an
unsigned angle in `[0, pi]` — so the condition can never be true and the hint is silently ignored.
**Fix:** do your own dot-product sign test instead: `perp = Vector2D.create(-dir.y, dir.x)`, then
flip it if `perp.x*(hint.x-from.x) + perp.y*(hint.y-from.y) < 0`.
`lib/fusionAddInUtils/geom_utils.py` (`sketchLineNormal`), worked around in
`commands/AutoHole/entry.py` (`_perp_toward`)

### A non-construction reference curve through a hole's center splits its profile
Placing a hole's center exactly on another real (non-construction) sketch curve — e.g. an offset
curve used only to compute where holes go — makes that curve cut the hole into two crescent
profiles, so area-based profile matching finds zero matches for every hole. **Fix:** set
`curve.isConstruction = True` on any curve that's a computational aid rather than part geometry,
same as the existing pattern-direction `dir_line`.
`commands/AutoHole/entry.py` (`_process_loop_from_edge`)

### An edge on a thin plate can border two planar faces — pick the larger one
A straight/arc edge's `.faces` can include both the flat cap face you want and an incidentally-flat
thin side wall; `next(f for f in edge.faces if isinstance(f.geometry, Plane))` can grab the wrong
one and trace a tiny side-wall loop instead of the part's outer boundary. **Fix:** collect all
planar candidates and take `max(..., key=lambda f: f.area)` — a real part's cap face is normally far
bigger than its edge-band side walls.
`commands/AutoHole/entry.py` (`_process_loop_from_edge`)

### Fusion's expression parser has no `pi` constant
`floor(2 * pi * x / 1 in)` throws `RuntimeError: Expression is invalid` — confirmed by testing
directly against a live parameter's `.expression`. **Fix:** use a numeric literal
(`3.14159265358979`) instead; wrap `.expression` assignments in try/except regardless so a bad
formula degrades to a literal value rather than breaking the feature.
`commands/AutoHole/entry.py` (`_process_circular_edge`)

### RectangularPatternFeatureInput.patternComputeOption default scales terribly with count
Patterning a *cut* feature (not sketch geometry) defaults to `AdjustPatternCompute`, which fully
re-evaluates each instance against the growing body — measured ~30x slower going from 20 to 80
holes in one row, and enough to hang/crash Fusion at ~200 holes across a full perimeter. **Fix:**
set `patInput.patternComputeOption = adsk.fusion.PatternComputeOptions.OptimizedPatternCompute`
before `.add()` — turns superlinear scaling into roughly linear. `IdenticalPatternCompute` is faster
still but failed outright (`PATTERN_FEATURES_NO_PASTE_INT_EDGES`) on these geometrically-identical
translated cuts; `Optimized` is the more tolerant of the two fast options.
`commands/AutoHole/entry.py` (`_build_edge_row_line`)

### Python silently keeps only the last def when a function is defined twice
`geom_utils.py` had two `addPoint2D`/`lineNormal` defs (overload-style); Python has no overloading,
so the first def is dead and callers may hit the wrong signature. **Fix:** one def per name; with
no linter in this repo, grep for `def <name>` before adding a "new" helper to a futil module.
`lib/fusionAddInUtils/geom_utils.py`

### Log the caught exception text whenever an except turns into a silent skip
Swallowing an error with `except RuntimeError: return` (no logging) makes an *expected* skip
(e.g. two hole rows colliding) indistinguishable from a real bug eating every result. **Fix:**
always log the exception text at the point of skip, e.g. `except RuntimeError as err: _dbg(f'...: {err}')`.
`commands/AutoHole/entry.py` (`_build_edge_row_line`)

### Write a per-run debug_log.txt instead of relying only on the Text Command window
`futil.log()` only reaches Fusion's Text Command window (needs `config.DEBUG=True`, scrolls away,
not visible outside Fusion). **Fix:** add a local `_dbg()`/`_dbg_reset()` pair that also writes to
`debug_log.txt` next to `entry.py`, truncated at the start of each `_run()`. Log every decision
point so the file can just be read directly.
`commands/AutoHole/entry.py` (`_dbg`, `_dbg_reset`)

### Fusion expression parser separates function args with `;`, not `,`
`max(0, floor(x / 1 in))` fails to parse. **Fix:** use `max(0; floor(x / 1 in))`. Fall back to a
literal value if setting `.expression` throws, so geometry is still correct (just not parametric).
`commands/AutoHole/entry.py`

### Use a lightweight executePreview and let execute rebuild the real result
Full dimensioning + parameter-linking on every preview recompute is slow and flickery. **Fix:**
in the preview handler build only the geometry needed to show the result (skip dims/formulas) and
set `args.isValidResult = False` so Fusion discards it and reruns the full `command_execute` on OK.
`commands/AutoHole/entry.py`

### Sketch dimension parameters share one global bare-name namespace
A qualified expression like `AutoHole.d15` won't parse. **Fix:** reference any model parameter
(including sketch dims) by its bare name (e.g. `d15`, from `dimension.parameter.name`). Use a
reference/driven dimension (`isDriving=False`) when you only need to report a length without
over-constraining an already-defined edge.
`commands/AutoHole/entry.py` (`_length_param_name`)

### Fusion works in centimeters internally
Passing inch values straight into the API makes parts 2.54x too small. **Fix:** convert with
`IN_TO_CM = 2.54` or `futil.inchValue(inches)`; expose inches in dialogs, store/compute in cm.
`lib/fusionAddInUtils/general_utils.py`
