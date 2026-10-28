"""HTTP API the phone calls. Thin: each endpoint loads a run, calls a tool, saves the run."""
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from . import config, costs, frames, models, runs, tools
from .tools import advance_step as advance_mod

import os

from fastapi import Request

from fastapi.middleware.cors import CORSMiddleware

VERSION = "0.2.2"  # bump on every deploy so /health shows which code is live
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
        return tools.run("transcribe", None, audio=data, content_type=audio.content_type or "audio/wav")
    except Exception as e:
        raise HTTPException(502, str(e)[:300])


@app.post("/frames")
async def upload_frame(frame: UploadFile = File(...)):
    """The phone uploads the frame the moment the mic is released, in parallel with transcription."""
    _budget_guard()
    return frames.put(await frame.read())


@app.post("/runs")
async def start_run(question: str = Form(...), frame: UploadFile | None = File(None), frame_id: str = Form("")):
    """Question + frame (uploaded now, or earlier via /frames) -> identify and speak step 1.
    Returns as soon as identify is done; the phone then calls /runs/{id}/outline for the SAM outline so it can start
    speaking while SAM runs."""
    _budget_guard()
    run = runs.new_run(question.strip())
    try:
        stored = frames.get(frame_id) if frame_id else None
        if stored:
            data_url, w, h = stored
        elif frame is not None:
            data_url, w, h = models.frame_from_bytes(await frame.read())
        else:
            raise HTTPException(400, "send a frame file or a valid frame_id")
        frames.keep_for_run(run["id"], data_url, w, h)
        tools.run("identify_part", run, frame=data_url, width=w, height=h)
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
            tools.run("identify_part", run, frame=data_url, width=w, height=h)
            outline = tools.run("outline_part", run, frame=data_url)
            if outline["kind"] == "too_far":
                runs.set_status(run, "needs_input", "Still too far. Get within arm's reach of the part and tap Done.")
            else:
                runs.set_status(run, "guiding", f"Step 1. {run['steps'][0]['instruction']}")
            run["message"] = run["display"]["step"]  # after re-identify, speak only the step line
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
