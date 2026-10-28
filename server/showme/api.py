"""HTTP API the phone calls. Thin: each endpoint loads a run, calls a tool, saves the run."""
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from . import config, costs, models, runs, tools
from .tools import advance_step as advance_mod

VERSION = "0.1.1"  # bump on every deploy so /health shows which code is live
app = FastAPI(title="Show Me", version=VERSION)


def _budget_guard():
    t = costs.totals()
    if t["hard_stop"]:
        raise HTTPException(503, f"Paid calls are off: Meta spend ${t['meta']:.2f} reached the hard stop")


@app.get("/health")
def health():
    t = costs.totals()
    return {"ok": True, "version": VERSION, "backends": {b: config.has_backend(b) for b in ("meta", "baseten")},
            "spend": t, "tools": [d["name"] for d in tools.definitions()]}


@app.get("/costs")
def spend():
    return costs.totals()


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    _budget_guard()
    data = await audio.read()
    try:
        return tools.run("transcribe", None, audio=data, content_type=audio.content_type or "audio/wav")
    except Exception as e:
        raise HTTPException(502, str(e)[:300])


@app.post("/runs")
async def start_run(question: str = Form(...), frame: UploadFile = File(...)):
    """First frame + question -> identify, outline, speak step 1."""
    _budget_guard()
    run = runs.new_run(question.strip())
    try:
        data_url, w, h = models.frame_from_bytes(await frame.read())
        tools.run("identify_part", run, frame=data_url, width=w, height=h)
        outline = tools.run("outline_part", run, frame=data_url)
        if outline["kind"] == "too_far":
            runs.set_status(run, "needs_input", f"I think this is the {run['object_name']}, but I can't see the "
                                                f"{run['part_name']} well. Move closer so it fills more of the screen, then tap Done.")
        else:
            warn = (run["safety_warning"] + " ") if run["safety_warning"] else ""
            label = f" It's labeled {run['matched_label']}." if run["matched_label"] and run.get("label_matches_question") else ""
            runs.set_status(run, "guiding", f"{warn}This is the {run['object_name']}. Start with the {run['part_name']}.{label} "
                                            f"Step 1. {run['steps'][0]['instruction']}" if run["steps"] else "I couldn't work out the steps.")
    except costs.BudgetExhausted as e:
        runs.set_status(run, "failed", str(e))
    except Exception as e:
        runs.set_status(run, "failed", "Something went wrong: " + str(e)[:200])
        runs.event(run, "error", error=str(e)[:300])
    runs.save(run)
    return runs.public_view(run)


@app.post("/runs/{run_id}/advance")
async def advance(run_id: str, frame: UploadFile = File(...)):
    """Fresh frame after the user taps Done -> verifier -> next step / feedback / needs_input."""
    _budget_guard()
    run = runs.load(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    try:
        data_url, w, h = models.frame_from_bytes(await frame.read())
        if run["status"] == "needs_input" and run.get("outline", {}) and run["outline"].get("kind") == "too_far":
            # The user moved closer: re-identify on this frame, then proceed as a fresh start of step 1.
            tools.run("identify_part", run, frame=data_url, width=w, height=h)
            outline = tools.run("outline_part", run, frame=data_url)
            if outline["kind"] == "too_far":
                runs.set_status(run, "needs_input", "Still too far. Get within arm's reach of the part and tap Done.")
            else:
                runs.set_status(run, "guiding", f"Step 1. {run['steps'][0]['instruction']}")
        else:
            tools.run("advance_step", run, frame=data_url)
            if run["status"] == "guiding" and run.get("outline") is None:
                # Outline the next step's part on this frame if Spark gave us a box for it; we reuse the identify box
                # only for step 1, so later steps show the verifier's feedback without an outline unless re-identified.
                pass
    except costs.BudgetExhausted as e:
        runs.set_status(run, "failed", str(e))
    except Exception as e:
        runs.set_status(run, "needs_input", "I had trouble checking that. Tap Done to try again, or 'I did it' to continue.")
        runs.event(run, "error", error=str(e)[:300])
    runs.save(run)
    return runs.public_view(run)


@app.post("/runs/{run_id}/confirm")
def confirm(run_id: str):
    """The user's 'I did it' override while in needs_input. Logged as human-confirmed."""
    run = runs.load(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    advance_mod.confirm_by_human(run)
    runs.save(run)
    return runs.public_view(run)


@app.post("/runs/{run_id}/help")
def help_(run_id: str, reason: str = Form("")):
    run = runs.load(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    tools.run("request_help", run, reason=reason)
    runs.save(run)
    return runs.public_view(run)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    run = runs.load(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return runs.public_view(run)


@app.get("/runs")
def list_runs():
    return runs.list_runs()
