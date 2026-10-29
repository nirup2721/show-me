"""HTTP API the phone calls. Thin: each endpoint loads a run, calls a tool, saves the run."""
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool   # blocking model calls must not sit on the event loop

from . import config, costs, frames, models, prompts, runs, tools
from .tools import advance_step as advance_mod

import os

from fastapi import Request

from fastapi.middleware.cors import CORSMiddleware

VERSION = "0.6.1"  # bump on every deploy so /health shows which code is live
app = FastAPI(title="Show Me", version=VERSION)

# Access key: every paid endpoint needs it, so strangers who find the address cannot spend credits.
# The phone page gets it once from the owner (URL fragment or typed in) and keeps it in local storage;
# it is never embedded in the page source. Set SHOWME_ACCESS_KEY in the Modal secret; unset = open (local dev only).
_OPEN_PATHS = {"/health", "/", "/docs", "/openapi.json"}


@app.middleware("http")
async def _require_access_key(request: Request, call_next):
    expected = os.environ.get("SHOWME_ACCESS_KEY", "")
    path = request.url.path
    if request.method != "OPTIONS" and expected and path not in _OPEN_PATHS and not path.startswith("/static"):
        given = request.headers.get("x-show-me-key") or request.query_params.get("key", "")
        if given != expected:
            return JSONResponse({"detail": "access key missing or wrong"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def _server_timing(request: Request, call_next):
    """Server-Timing header: total time spent in this server, so the phone can tell server time from network time."""
    import time as _t
    t0 = _t.time()
    response = await call_next(request)
    response.headers["Server-Timing"] = f"app;dur={(_t.time() - t0) * 1000:.0f}"
    return response


# The phone page is hosted on Vercel at askshowme.com and calls this server from the browser.
# Added AFTER the access-key middleware so CORS is the outermost layer: preflights are answered before the
# key check, and 401s still carry CORS headers so the page can read them.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://askshowme.com", "https://www.askshowme.com"],
    allow_origin_regex=r"^https://[a-z0-9-]+(\.[a-z0-9-]+)*\.vercel\.app$|^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-Show-Me-Key", "Content-Type"],
    expose_headers=["Server-Timing"],
    max_age=600,
)


def _budget_guard():
    t = costs.totals()
    if t["hard_stop"]:
        raise HTTPException(503, f"Paid calls are off: Meta spend ${t['meta']:.2f} reached the hard stop")


@app.get("/health")
def health():
    t = costs.totals()
    return {"ok": True, "version": VERSION, "access_key_required": bool(os.environ.get("SHOWME_ACCESS_KEY")),
            "backends": {b: config.has_backend(b) for b in ("meta", "baseten")},
            "spend": t, "tools": [d["name"] for d in tools.definitions()]}


@app.get("/costs")
def spend():
    return costs.totals()


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    _budget_guard()
    data = await audio.read()
    try:
        return await run_in_threadpool(tools.run, "transcribe", None, audio=data, content_type=audio.content_type or "audio/wav")
    except Exception as e:
        raise HTTPException(502, str(e)[:300])


@app.post("/frames")
async def upload_frame(frame: UploadFile = File(...)):
    """The phone uploads the frame the moment the mic is released, in parallel with transcription."""
    _budget_guard()
    return frames.put(await frame.read())


import re as _re


def is_general(question: str) -> bool:
    return not question.strip() or bool(_re.search(prompts.GENERAL_QUESTION, question.strip(), _re.I))


def task_question(question: str, focus_name: str = "") -> tuple[str, str | None]:
    """What the planner is asked. A one-word transcript ("Here", "This", "Help") is a request for help with what is
    in view (station, Oct 29: the planner returned no steps for "Here"). Returns (question, heard_or_None)."""
    q = question.strip()
    if len(q.split()) == 1 and not focus_name:
        return "How do I use this machine?", q
    return q, None


async def _frame_from(frame: UploadFile | None, frame_id: str):
    stored = frames.get(frame_id) if frame_id else None
    if stored:
        return stored
    if frame is not None:
        return models.frame_from_bytes(await frame.read())
    raise HTTPException(400, "send a frame file or a valid frame_id")


@app.post("/survey")
async def survey(frame: UploadFile | None = File(None), frame_id: str = Form("")):
    """Look around: every recognizable object in the frame, boxed and outlined, so the user can pick one. No task."""
    _budget_guard()
    data_url, w, h = await _frame_from(frame, frame_id)
    fid = frame_id or frames.put_data_url(data_url, w, h)
    try:
        result = await run_in_threadpool(tools.run, "survey_frame", None, frame=data_url, width=w, height=h)
    except costs.BudgetExhausted as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, "look around failed: " + str(e)[:200])
    result["frame_id"] = fid
    result["prompt"] = "Which one do you need help with?" if result["objects"] else "I can't make out anything to help with here. Try pointing at the machine."
    frames.save_survey(fid, result)
    return result


@app.post("/runs")
async def start_run(question: str = Form(...), frame: UploadFile | None = File(None), frame_id: str = Form(""),
                    focus_name: str = Form(""), focus_box: str = Form("")):
    """Question + frame (uploaded now, or earlier via /frames) -> identify and speak step 1.
    Returns as soon as identify is done; the phone then calls /runs/{id}/outline for the SAM outline so it can start
    speaking while SAM runs. A general question with no object chosen is sent to look-around instead (no spend)."""
    _budget_guard()
    if is_general(question) and not focus_name:
        return {"status": "survey_needed", "message": "That sounds like a look-around question.", "frame_id": frame_id}
    q, heard = task_question(question, focus_name)
    run = runs.new_run(q or f"how do I use the {focus_name}?")
    if heard is not None:
        run["heard"] = heard
    try:
        data_url, w, h = await _frame_from(frame, frame_id)
        frames.keep_for_run(run["id"], data_url, w, h)
        frames.keep_step_before(run["id"], 0, data_url, w, h)   # the view when step 1 is spoken
        focus = None
        if focus_name:
            try:
                box = [int(float(v)) for v in focus_box.split(",")] if focus_box else None
            except ValueError:
                box = None
            focus = {"name": focus_name.strip(), "box": box if box and len(box) == 4 else None}
        await run_in_threadpool(tools.run, "identify_part", run, frame=data_url, width=w, height=h, focus=focus)
        warning = " ".join(x for x in (run.get("safety_warning", ""), run.get("prep", "")) if x)
        label = f", labeled {run['matched_label']}" if run["matched_label"] and run.get("label_matches_question") else ""
        obj = f"{run['object_name']}{label}. Start with the {run['part_name']}."
        x1, y1, x2, y2 = run["bbox_px"]
        if max(x2 - x1, y2 - y1) < config.MIN_PART_PX:
            run["outline"] = {"kind": "too_far", "box": [x1, y1, x2, y2], "long_side_px": max(x2 - x1, y2 - y1)}
            runs.set_status(run, "needs_input")
            runs.set_display(run, warning=warning, object_=obj,
                             step=f"I can't see the {run['part_name']} well. Move closer so it fills more of the screen, then tap Done.")
        elif run["steps"]:
            runs.set_status(run, "guiding")
            runs.set_display(run, warning=warning, object_=obj, step=f"Step 1. {run['steps'][0]['instruction']}")
        else:
            runs.set_status(run, "failed")
            runs.set_display(run, warning=warning, object_=obj, step="I couldn't work out the steps.")
    except HTTPException:
        raise
    except costs.BudgetExhausted as e:
        runs.set_status(run, "failed", str(e))
    except Exception as e:
        runs.set_status(run, "failed", "Something went wrong: " + str(e)[:200])
        runs.event(run, "error", error=str(e)[:300])
    runs.save(run)
    return runs.public_view(run)


@app.post("/runs/{run_id}/outline")
def outline(run_id: str):
    """SAM outline of the current part on the frame the run started from. Called right after /runs."""
    _budget_guard()
    run = runs.load(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    stored = frames.for_run(run_id)
    if stored and run.get("bbox_px") and (run.get("outline") or {}).get("kind") != "too_far":
        data_url, _, _ = stored
        try:
            tools.run("outline_part", run, frame=data_url)
        except Exception as e:
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
            frames.keep_for_run(run["id"], data_url, w, h)
            await run_in_threadpool(tools.run, "identify_part", run, frame=data_url, width=w, height=h)
            outline = await run_in_threadpool(tools.run, "outline_part", run, frame=data_url)
            if outline["kind"] == "too_far":
                runs.set_status(run, "needs_input", "Still too far. Get within arm's reach of the part and tap Done.")
            else:
                runs.set_status(run, "guiding", f"Step 1. {run['steps'][0]['instruction']}")
            run["message"] = run["display"]["step"]  # after re-identify, speak only the step line
        else:
            before_step = run["current_step"]
            await run_in_threadpool(tools.run, "advance_step", run, frame=data_url)
            if run["status"] == "guiding" and run["current_step"] != before_step:
                frames.keep_step_before(run["id"], run["current_step"], data_url, w, h)
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


@app.post("/runs/{run_id}/check")
async def check(run_id: str, frame: UploadFile = File(...), seq: int = Form(0), auto_advance: int = Form(0)):
    """Auto-check: judge a fresh frame against the current step and record the verdict. With auto_advance=1 the
    server also moves to the next step when its own consecutive-pass rule is met, and returns the run.
    Verification runs in parallel across requests; the record step runs under the run's lock on a fresh copy."""
    _budget_guard()
    run = runs.load(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    try:
        # Check frames arrive at up to 1024 px so the part close-up is sharp; the context view is shrunk server-side.
        data_url, w, h = models.frame_from_bytes(await frame.read(), max_side=1024)
        before = frames.step_before(run_id, run["current_step"])
        judged = await run_in_threadpool(advance_mod.judge, run, data_url, before[0] if before else None)
        if judged is None:
            return {"ok": False, "status": run["status"], "message": run.get("message", "")}
        step_index, verdict = judged

        def record():
            with runs.lock(run_id):
                fresh = runs.load(run_id)
                out = advance_mod.record_check(fresh, step_index, verdict, seq, auto_advance=bool(auto_advance))
                if out.get("advanced") and fresh["status"] == "guiding":
                    frames.keep_step_before(run_id, fresh["current_step"], data_url, w, h)   # this view = next step's "before"
                runs.save(fresh)
                if out.get("advanced"):
                    out["run"] = runs.public_view(fresh)
                return out
        return await run_in_threadpool(record)
    except costs.BudgetExhausted as e:
        with runs.lock(run_id):
            fresh = runs.load(run_id) or run
            runs.set_status(fresh, "failed", str(e)); runs.save(fresh)
        return {"ok": False, "status": "failed"}
    except Exception as e:
        return {"ok": False, "status": run["status"], "error": str(e)[:200]}


@app.post("/runs/{run_id}/advance-from-checks")
def advance_from_checks(run_id: str):
    """Move to the next step on the strength of the recorded auto-check passes (server-side rule)."""
    run = runs.load(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    out = advance_mod.advance_from_checks(run)
    runs.save(run)
    return runs.public_view(run) | {"advanced": bool(out.get("ok"))}


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
