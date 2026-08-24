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
