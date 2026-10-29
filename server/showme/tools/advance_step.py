"""advance_step: the ONLY way a run moves to the next step. The verifier runs inside it.

Given a fresh frame, verify_step judges the current step. pass -> step confirmed by camera, next step is spoken.
fail -> feedback is spoken, same step again. cannot_tell, or MAX_VERIFY_TRIES fails -> needs_input.
in_progress (a hand on the part, action under way) -> same step, not counted as a try (station, Oct 29: the slushie
handle step failed 7 times while the owner's hand was on the lever).
"""
import time

from .. import config, runs
from ..verification import verify_step

NAME = "advance_step"
DESCRIPTION = "Check the current step from a fresh frame and move on only if the camera confirms it."
SCHEMA = {"type": "object", "properties": {"frame": {"type": "string"}}, "required": ["frame"]}


def _speak_step(run_record: dict) -> str:
    step = runs.current_step(run_record)
    n = run_record["current_step"] + 1
    return f"Step {n}. {step['instruction']}"


def judge(run_record: dict, frame: str, before_url: str | None = None) -> tuple[int, dict] | None:
    """Auto-check, part 1 (no state change, may run in parallel): verify the frame against the current step.
    Returns (step_index, verdict) or None if the run is not being guided."""
    step = runs.current_step(run_record)
    if step is None or run_record["status"] != "guiding":
        return None
    box = step.get("bbox_px") or run_record.get("bbox_px")
    return run_record["current_step"], verify_step(step, frame, run_record["id"], before_url=before_url, box=box,
                                                   box_frame=tuple(run_record.get("frame_size") or ()) or None)


def record_check(run_record: dict, step_index: int, verdict: dict, seq: int = 0, auto_advance: bool = False) -> dict:
    """Auto-check, part 2 (under the run's lock, on a freshly loaded run): record the verdict and apply the
    consecutive-pass rule. A verdict for a step the run has already left is dropped as stale."""
    if run_record["status"] != "guiding" or run_record["current_step"] != step_index:
        return {"ok": False, "stale": True, "status": run_record["status"], "step": run_record["current_step"]}
    step = run_record["steps"][step_index]
    runs.add_cost(run_record, verdict.get("usd", 0.0))
    rec = {k: verdict.get(k) for k in ("verdict", "evidence_seen", "reason", "change", "mode", "backend", "seconds", "hedged")}
    rec["ts"] = time.time(); rec["seq"] = seq; rec["auto"] = True
    step["verdicts"].append(rec)
    runs.event(run_record, "checked", step=step_index, verdict=verdict["verdict"], seq=seq)
    recent = [v for v in step["verdicts"] if v.get("auto") and time.time() - v.get("ts", 0) <= config.CHECK_WINDOW_S]
    streak = 0
    for v in reversed(recent):
        if v["verdict"] == "pass": streak += 1
        else: break
    out = {"ok": True, "status": run_record["status"], "verdict": rec, "pass_streak": streak,
           "can_advance": streak >= config.CHECK_PASSES, "step": step_index, "advanced": False}
    if auto_advance and out["can_advance"]:
        res = advance_from_checks(run_record)   # the rule is re-applied here, not trusted from the page
        out["advanced"] = bool(res.get("ok"))
    return out


def check(run_record: dict, frame: str, seq: int = 0, auto_advance: bool = False) -> dict:
    """Both parts on one in-memory run (tests and single-threaded use). The API uses judge + record_check."""
    j = judge(run_record, frame)
    if j is None:
        return {"ok": False, "status": run_record["status"], "message": run_record.get("message", "")}
    return record_check(run_record, j[0], j[1], seq, auto_advance)


def advance_from_checks(run_record: dict) -> dict:
    """Advance only if the last CHECK_PASSES auto-check verdicts on the current step are passes within the window.
    The page cannot pass a step on its own: the verdicts it relies on were produced and stored here."""
    step = runs.current_step(run_record)
    if step is None or run_record["status"] != "guiding":
        return {"ok": False, "status": run_record["status"], "message": run_record.get("message", "")}
    recent = [v for v in step["verdicts"] if v.get("auto") and time.time() - v.get("ts", 0) <= config.CHECK_WINDOW_S]
    tail = recent[-config.CHECK_PASSES:]
    if len(tail) < config.CHECK_PASSES or any(v["verdict"] != "pass" for v in tail):
        return {"ok": False, "status": run_record["status"], "message": "not enough consecutive passes yet", "pass_streak": len([v for v in tail if v['verdict'] == 'pass'])}
    step["status"] = "confirmed"
    step["confirmed_by"] = "camera"
    runs.event(run_record, "advanced_from_checks", step=run_record["current_step"], passes=[v["seq"] for v in tail])
    return _next(run_record, "Good.")


def run(run_record: dict, frame: str) -> dict:
    step = runs.current_step(run_record)
    if step is None or run_record["status"] not in ("guiding", "needs_input"):
        return {"ok": False, "message": run_record.get("message", ""), "status": run_record["status"]}

    runs.set_status(run_record, "verifying")
    from .. import frames as _frames
    before = _frames.step_before(run_record["id"], run_record["current_step"])
    verdict = verify_step(step, frame, run_record["id"], before_url=before[0] if before else None,
                          box=step.get("bbox_px") or run_record.get("bbox_px"),
                          box_frame=tuple(run_record.get("frame_size") or ()) or None)
    runs.add_cost(run_record, verdict["usd"])
    step["tries"] += 1
    step["verdicts"].append({k: verdict.get(k) for k in ("verdict", "evidence_seen", "reason", "change", "mode", "backend", "seconds", "hedged")} | {"ts": time.time()})
    runs.event(run_record, "verified", step=run_record["current_step"], verdict=verdict["verdict"], seen=verdict["evidence_seen"])

    if verdict["verdict"] == "pass":
        step["status"] = "confirmed"
        step["confirmed_by"] = "camera"
        return _next(run_record, f"Good. {verdict['evidence_seen']}")

    if verdict["verdict"] == "in_progress":          # hand on the part, action under way: not a failed try
        step["tries"] -= 1
        runs.set_status(run_record, "guiding", f"Keep going. {step['instruction']}")
        return {"ok": True, "status": "guiding", "verdict": verdict, "message": run_record["message"]}
    if verdict["verdict"] == "fail" and step["tries"] < config.MAX_VERIFY_TRIES:
        runs.set_status(run_record, "guiding", f"Not yet. {verdict['reason']} {step['instruction']}")
        return {"ok": True, "status": "guiding", "verdict": verdict, "message": run_record["message"]}

    why = "I can't see it clearly." if verdict["verdict"] == "cannot_tell" else "I still can't confirm that step."
    runs.set_status(run_record, "needs_input",
                    f"{why} {verdict['evidence_seen']} Hold the camera on the {step['part']} and tap Done again, "
                    f"or tap 'I did it' to continue.")
    return {"ok": True, "status": "needs_input", "verdict": verdict, "message": run_record["message"]}


def confirm_by_human(run_record: dict) -> dict:
    """The needs_input override. Recorded as human-confirmed; such runs never go in the demo video."""
    step = runs.current_step(run_record)
    if step is None or run_record["status"] != "needs_input":
        return {"ok": False, "status": run_record["status"], "message": run_record.get("message", "")}
    step["status"] = "confirmed"
    step["confirmed_by"] = "human"
    runs.event(run_record, "human_confirmed", step=run_record["current_step"])
    return _next(run_record, "Okay, continuing.")


def _next(run_record: dict, prefix: str) -> dict:
    run_record["current_step"] += 1
    run_record["outline"] = None  # the next step's part gets outlined on the next frame
    if run_record["current_step"] >= len(run_record["steps"]):
        runs.set_status(run_record, "done", f"{prefix} All done.")
    else:
        runs.set_status(run_record, "guiding", f"{prefix} {_speak_step(run_record)}")
    return {"ok": True, "status": run_record["status"], "message": run_record["message"], "current_step": run_record["current_step"]}
