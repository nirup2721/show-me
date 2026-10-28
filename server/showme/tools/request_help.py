"""request_help: put the run into needs_input on purpose (the user says 'I'm stuck'), with what the app needs."""
from .. import runs

NAME = "request_help"
DESCRIPTION = "Pause the run and tell the user what the camera needs to see."
SCHEMA = {"type": "object", "properties": {"reason": {"type": "string"}}, "required": []}


def run(run_record: dict, reason: str = "") -> dict:
    step = runs.current_step(run_record)
    part = step["part"] if step else "the part"
    runs.set_status(run_record, "needs_input",
                    f"Hold the camera steady on the {part}, about an arm's length away, and tap Done. "
                    f"If you already did the step, tap 'I did it'.")
    runs.event(run_record, "help_requested", reason=reason)
    return {"ok": True, "status": "needs_input", "message": run_record["message"]}
