"""Fast local data with a slow durable mirror.

Every file read or write on a Modal Volume costs hundreds of milliseconds, and a commit after each request cost
up to a second; a single run start touched the volume a dozen times and lost ~10 s (measured Oct 28). So the app
reads and writes DATA_DIR on the container's local disk, and this module mirrors changed files to the volume in a
background thread and commits every SYNC_EVERY_S seconds. On startup the volume is copied to local disk, so the cost
total (and the hard stop) survive restarts. Locally (no volume) nothing here does anything.
"""
import os
import shutil
import threading
import time
from pathlib import Path

from . import config

SYNC_EVERY_S = 2.0
_dirty: set[Path] = set()
_lock = threading.Lock()
_volume_dir: Path | None = None
_commit = None          # callable that commits the volume, injected by app.py
_thread: threading.Thread | None = None


def mark_dirty(path: Path):
    if _volume_dir is None:
        return
    with _lock:
        _dirty.add(Path(path))


def start(volume_dir: str, commit):
    """Copy the volume to local disk once, then mirror changes back in the background."""
    global _volume_dir, _commit, _thread
    _volume_dir, _commit = Path(volume_dir), commit
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    n = 0
    if _volume_dir.exists():
        for src in _volume_dir.rglob("*"):
            if src.is_file():
                dst = config.DATA_DIR / src.relative_to(_volume_dir)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                    shutil.copy2(src, dst)
                    n += 1
    print(f"[store] loaded {n} files from the volume in {time.time() - t0:.1f}s")
    _thread = threading.Thread(target=_loop, name="store-sync", daemon=True)
    _thread.start()


def _loop():
    while True:
        time.sleep(SYNC_EVERY_S)
        try:
            flush()
        except Exception as e:  # never let the mirror kill the server
            print("[store] sync error:", str(e)[:200])


def flush():
    with _lock:
        paths = list(_dirty)
        _dirty.clear()
    if not paths:
        return
    for p in paths:
        try:
            rel = p.relative_to(config.DATA_DIR)
        except ValueError:
            continue
        dst = _volume_dir / rel
        if p.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
        elif dst.exists():
            dst.unlink()
    if _commit:
        _commit()


CONTAINER_TAG = os.environ.get("MODAL_TASK_ID", "local")[-8:]
