"""advance_step: the ONLY way a run moves to the next step. The verifier runs inside it.

Given a fresh frame, verify_step judges the current step. pass -> step confirmed by camera, next step is spoken.
fail -> feedback is spoken, same step again. cannot_tell, or MAX_VERIFY_TRIES fails -> needs_input.
"""
from .. import config, runs
from ..verification import verify_step

NAME = "advance_step"
DESCRIPTION = "Check the current step from a fresh frame and move on only if the camera confirms it."
SCHEMA = {"type": "object", "properties": {"frame": {"type": "string"}}, "required": ["frame"]}


def _speak_step(run_record: dict) -> str:
    step = runs.current_step(run_record)
    n = run_record["current_step"] + 1
    return f"Step {n}. {step['instruction']}"


def run(run_record: dict, frame: str) -> dict:
    step = runs.current_step(run_record)
    if step is None or run_record["status"] not in ("guiding", "needs_input"):
        return {"ok": False, "message": run_record.get("message", ""), "status": run_record["status"]}

    runs.set_status(run_record, "verifying")
    verdict = verify_step(step, frame, run_record["id"])
    runs.add_cost(run_record, verdict["usd"])
    step["tries"] += 1
    step["verdicts"].append({k: verdict[k] for k in ("verdict", "evidence_seen", "reason", "backend", "seconds")})
    runs.event(run_record, "verified", step=run_record["current_step"], verdict=verdict["verdict"], seen=verdict["evidence_seen"])

    if verdict["verdict"] == "pass":
        step["status"] = "confirmed"
        step["confirmed_by"] = "camera"
        return _next(run_record, f"Good. {verdict['evidence_seen']}")

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
