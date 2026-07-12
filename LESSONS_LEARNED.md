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

### Python silently keeps only the last def when a function is defined twice
`geom_utils.py` had two `addPoint2D`/`lineNormal` defs (overload-style); Python has no overloading,
so the first def is dead and callers may hit the wrong signature. **Fix:** one def per name; with
no linter in this repo, grep for `def <name>` before adding a "new" helper to a futil module.
`lib/fusionAddInUtils/geom_utils.py`

### Log the caught exception text whenever an except turns into a silent skip
Swallowing an error with `except RuntimeError: return` (no logging) makes an *expected* skip
(e.g. two hole rows colliding) indistinguishable from a real bug eating every result. **Fix:**
always log the exception text at the point of skip, e.g. `except RuntimeError as err: _dbg(f'...: {err}')`.
`commands/AutoHole/entry.py` (`_build_hole_row`)

### Write a per-run debug_log.txt instead of relying only on the Text Command window
`futil.log()` only reaches Fusion's Text Command window (needs `config.DEBUG=True`, scrolls away,
not visible outside Fusion). **Fix:** add a local `_dbg()`/`_dbg_reset()` pair that also writes to
`debug_log.txt` next to `entry.py`, truncated at the start of each `_run()`. Log every decision
point so the file can just be read directly.
`commands/AutoHole/entry.py` (`_dbg`, `_dbg_reset`)

### Dedupe shared edges across selected corner pairs before cutting hole rows
Selecting all 4 corners of a rectangle double-cuts each shared edge (once per adjoining corner),
and the second pass's seed hole overlaps the first → `EXTRUDE_BOOLEAN_FAIL`. **Fix:** track edges
already claimed (by `entityToken`) in a set shared across the whole run loop and skip them; also
wrap the seed `extrudes.add()` in its own `try/except RuntimeError` as defense-in-depth since an
overlapping hole is an expected outcome, not a crash.
`commands/AutoHole/entry.py` (`_run`, `_process_edge_pair`, `_build_hole_row`)

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
