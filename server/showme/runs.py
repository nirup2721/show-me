"""Run state machine and storage. One JSON file per run, rewritten after every change.

Statuses:
  identifying  the first frame is being analysed
  guiding      a step has been spoken; waiting for the user to do it and tap Done
  verifying    a fresh frame is being checked (transient)
  needs_input  the camera could not confirm the step; the user must re-frame or confirm by hand
  done         every step confirmed
  failed       a model or budget error ended the run

Each step records how it was confirmed: "camera" (verifier pass) or "human" (the user's override in needs_input).
The demo video may only use runs whose steps are all "camera" (owner's rule).
"""
import json
import secrets
import time
from pathlib import Path

from . import config

STATUSES = ("identifying", "guiding", "verifying", "needs_input", "done", "failed")


def _dir() -> Path:
    d = config.DATA_DIR / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_run(question: str) -> dict:
    run = {
        "id": time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2),
        "created": now(), "updated": now(),
        "status": "identifying",
        "question": question,
        "object_name": None, "object_class": None, "part_name": None, "matched_label": "",
        "safety_warning": "",
        "frame_size": None,
        "identify": None,          # which backend answered, timing, the discarded answer's summary
        "steps": [],               # {instruction, part, expected_evidence, status, confirmed_by, tries, verdicts[]}
        "current_step": 0,
        "message": "",             # what the phone should say right now (spoken, all lines joined)
        "display": {"warning": "", "object": "", "step": ""},   # the three lines the phone shows: small, medium, large
        "outline": None,           # SAM result for the current step's part, or Spark box fallback
        "events": [],              # [{ts, type, ...}]
        "cost_usd": 0.0,
    }
    save(run)
    return run


def save(run: dict) -> dict:
    run["updated"] = now()
    (_dir() / f"{run['id']}.json").write_text(json.dumps(run, indent=1), encoding="utf-8")
    return run


def load(run_id: str) -> dict | None:
    p = _dir() / f"{run_id}.json"
    if not p.exists() or "/" in run_id or ".." in run_id:
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_runs(limit: int = 50) -> list[dict]:
    files = sorted(_dir().glob("*.json"), reverse=True)[:limit]
    out = []
    for f in files:
        r = json.loads(f.read_text(encoding="utf-8"))
        out.append({k: r.get(k) for k in ("id", "created", "status", "question", "object_name", "current_step", "cost_usd")}
                   | {"steps": len(r.get("steps", [])),
                      "human_confirmed": sum(1 for s in r.get("steps", []) if s.get("confirmed_by") == "human")})
    return out


def event(run: dict, type_: str, **data):
    run["events"].append({"ts": now(), "type": type_, **data})


def set_status(run: dict, status: str, message: str | None = None):
    """Set the status and, when a message is given, the spoken text and the big 'step' line of the display."""
    assert status in STATUSES, status
    run["status"] = status
    if message is not None:
        run["message"] = message
        run.setdefault("display", {"warning": "", "object": "", "step": ""})["step"] = message
    event(run, "status", status=status)


def set_display(run: dict, warning: str | None = None, object_: str | None = None, step: str | None = None):
    """The three lines the phone shows. The spoken message is the non-empty lines joined."""
    d = run.setdefault("display", {"warning": "", "object": "", "step": ""})
    if warning is not None: d["warning"] = warning
    if object_ is not None: d["object"] = object_
    if step is not None: d["step"] = step
    run["message"] = " ".join(x for x in (d["warning"], d["object"], d["step"]) if x)


def add_cost(run: dict, usd: float):
    run["cost_usd"] = round(run.get("cost_usd", 0.0) + (usd or 0.0), 6)


def current_step(run: dict) -> dict | None:
    i = run["current_step"]
    return run["steps"][i] if 0 <= i < len(run["steps"]) else None


def public_view(run: dict) -> dict:
    """What the phone gets: everything except raw usage blobs."""
    return {k: v for k, v in run.items() if k != "events"} | {"events": run["events"][-20:]}
