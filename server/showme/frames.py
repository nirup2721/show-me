"""Uploaded frames, kept briefly on disk so the phone can upload the picture while the voice is still being transcribed.

The phone posts the frame to /frames the moment the mic is released, gets a frame_id back, and later starts the run
with that id instead of re-uploading. Frames older than FRAME_TTL_S are swept on every write.
"""
import json
import secrets
import time
from pathlib import Path

from . import config, models, store


def _dir() -> Path:
    d = config.DATA_DIR / "frames"
    d.mkdir(parents=True, exist_ok=True)
    return d


def put(raw: bytes) -> dict:
    """Downsize once, store the data URL, return {frame_id, w, h, bytes_in}."""
    data_url, w, h = models.frame_from_bytes(raw)
    fid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    p = _dir() / f"{fid}.json"
    p.write_text(json.dumps({"data_url": data_url, "w": w, "h": h, "bytes_in": len(raw), "ts": time.time()}), encoding="utf-8")
    store.mark_dirty(p)
    sweep()
    return {"frame_id": fid, "w": w, "h": h, "bytes_in": len(raw)}


def put_data_url(data_url: str, w: int, h: int) -> str:
    """Store an already-downsized frame (e.g. one that arrived with /survey) and return its id."""
    fid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    p = _dir() / f"{fid}.json"
    p.write_text(json.dumps({"data_url": data_url, "w": w, "h": h, "ts": time.time()}), encoding="utf-8")
    store.mark_dirty(p)
    return fid


def save_survey(fid: str, result: dict):
    """Keep the look-around result beside its frame, for the run log and the console."""
    p = _dir() / f"survey-{fid}.json"
    p.write_text(json.dumps({k: v for k, v in result.items() if k != "frame_size"} | {"frame_size": result.get("frame_size")}), encoding="utf-8")
    store.mark_dirty(p)


def get(fid: str) -> tuple[str, int, int] | None:
    if not fid or "/" in fid or ".." in fid:
        return None
    p = _dir() / f"{fid}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["data_url"], d["w"], d["h"]


def keep_for_run(run_id: str, data_url: str, w: int, h: int):
    """The frame a run started from, so /outline can segment it without a second upload."""
    p = _dir() / f"run-{run_id}.json"
    p.write_text(json.dumps({"data_url": data_url, "w": w, "h": h, "ts": time.time()}), encoding="utf-8")
    store.mark_dirty(p)


def for_run(run_id: str) -> tuple[str, int, int] | None:
    return get(f"run-{run_id}")


def keep_step_before(run_id: str, step_index: int, data_url: str, w: int, h: int):
    """The view when a step was spoken: the verifier compares the current frame against it."""
    p = _dir() / f"run-{run_id}-step{step_index}.json"
    p.write_text(json.dumps({"data_url": data_url, "w": w, "h": h, "ts": time.time()}), encoding="utf-8")
    store.mark_dirty(p)


def step_before(run_id: str, step_index: int) -> tuple[str, int, int] | None:
    return get(f"run-{run_id}-step{step_index}")


def sweep():
    cutoff = time.time() - config.FRAME_TTL_S
    run_cutoff = time.time() - 4 * config.FRAME_TTL_S   # a run's start and per-step frames live an hour
    for p in _dir().glob("*.json"):
        try:
            if p.stat().st_mtime < (run_cutoff if p.name.startswith("run-") else cutoff):
                p.unlink()
                store.mark_dirty(p)
        except OSError:
            pass
