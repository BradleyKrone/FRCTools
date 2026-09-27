import os
import time
import adsk.core
from ...lib import fusionAddInUtils as futil

app = adsk.core.Application.get()

# ---------------------------------------------------------------------------
# Hardware thumbnails: each library part's own cloud thumbnail (DataFile.thumbnail, a
# 256x256 PNG), cached as resources/hardware/<URN lineage id>/preview.png at 128px for the
# thumbnail grid (hardware_picker.html). The cache is committed, so a fresh install needs
# no cloud round trip; a part added to HARDWARE_CATEGORIES is fetched the first time the
# dialog opens.
# ---------------------------------------------------------------------------
THUMB_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'hardware')
PREVIEW_PX   = 128
NO_THUMBNAIL = os.path.join(THUMB_DIR, '_none.png')


def _preview_path(part: dict) -> str:
    # The lineage id is filesystem-safe and stable; `file` names contain '/'.
    return os.path.join(THUMB_DIR, part['urn'].rsplit(':', 1)[-1], 'preview.png')


def preview_file(part: dict) -> str:
    """The part's thumbnail PNG, or the "no picture" placeholder."""
    if part is not None and os.path.isfile(_preview_path(part)):
        return _preview_path(part)
    return NO_THUMBNAIL


def ensure_thumbnails(parts, timeout_s: float = 3.0):
    """Fetch and cache the thumbnail of every part in `parts` (an iterable of part dicts)
    that isn't cached yet. Waits at most `timeout_s` for the cloud; anything that fails or
    times out is logged and tried again next time. A no-op once all are cached."""
    pending = []
    for part in parts:
        if os.path.isfile(_preview_path(part)):
            continue
        try:
            data_file = app.data.findFileById(part['urn'])
            if data_file is None:
                futil.log(f'PartsGen: no file for {part["file"]}, so no thumbnail')
                continue
            pending.append((part, data_file.thumbnail))
        except Exception:
            futil.handle_error(f'PartsGen: thumbnail for {part["file"]}')
    if not pending:
        return

    deadline = time.time() + timeout_s
    while pending and time.time() < deadline:
        still = []
        for part, future in pending:
            state = future.state
            if state == adsk.core.FutureStates.ProcessingFutureState:
                still.append((part, future))
                continue
            if state != adsk.core.FutureStates.FinishedFutureState or future.dataObject is None:
                futil.log(f'PartsGen: {part["file"]} has no thumbnail')
                continue
            dst = _preview_path(part)
            tmp = os.path.join(THUMB_DIR, '_download.png')
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                future.dataObject.saveToFile(tmp)
                futil.downscale_png(tmp, dst, PREVIEW_PX)
            except Exception:
                futil.handle_error(f'PartsGen: saving the {part["file"]} thumbnail')
            finally:
                if os.path.isfile(tmp):
                    os.remove(tmp)
        pending = still
        if pending:
            adsk.doEvents()
            time.sleep(0.05)
    for part, _ in pending:
        futil.log(f'PartsGen: timed out fetching the {part["file"]} thumbnail')
