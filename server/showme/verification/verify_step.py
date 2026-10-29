"""verify_step: an independent Spark call that judges whether one step is done.

Two input modes (config.VERIFY_MODE):
  single     one small frame of the whole view (Day 3 morning).
  pair_crop  a close-up of the step's part BEFORE the step was given, the same close-up NOW, and the small whole
             frame for context. Small movements (a lever, a handle, a button) are what the single frame missed.

It sees only the step's instruction, its expected-evidence sentence and the images. Never the planner's reasoning,
earlier verdicts, or anything the user said. Always the backend in ROUTES["verify_step"] (Meta Spark 1.3).
Hedged: a second identical call after VERIFY_HEDGE_S if the first is silent; first answer wins.
"""
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .. import config, models, prompts


def _call(backend, prompt, images, schema, run_id):
    try:
        return models.spark_json(backend, prompt, images, schema, "ShowMeVerify",
                                 run_id=run_id, kind="verify_step", max_tokens=3000,
                                 reasoning_effort=config.REASONING["verify_step"])
    except Exception as e:
        return None, {"error": str(e)[:200]}


def build_inputs(step: dict, after_url: str, before_url: str | None, box: list | None, mode: str | None = None,
                 box_frame: tuple[int, int] | None = None):
    """Prompt + image list for the chosen mode. Falls back to single when there is no box or no before frame.
    box is in pixels of the frame `box_frame` (the identify frame); crops scale it to each image's own size."""
    mode = mode or config.VERIFY_MODE
    if mode == "pair_crop" and box and before_url:
        images = [models.crop_data_url(before_url, box, config.CROP_PAD, config.CROP_MAX_SIDE, box_frame),
                  models.crop_data_url(after_url, box, config.CROP_PAD, config.CROP_MAX_SIDE, box_frame),
                  models.shrink_data_url(after_url, config.CONTEXT_MAX_SIDE)]
        prompt = prompts.VERIFY_PAIR_PROMPT.format(instruction=step["instruction"], evidence=step["expected_evidence"])
        return prompt, images, prompts.VERIFY_PAIR_SCHEMA, "pair_crop"
    prompt = prompts.VERIFY_PROMPT.format(instruction=step["instruction"], evidence=step["expected_evidence"])
    return prompt, [models.shrink_data_url(after_url, 512)], prompts.VERIFY_SCHEMA, "single"


def verify_step(step: dict, data_url: str, run_id: str | None = None, before_url: str | None = None,
                box: list | None = None, mode: str | None = None, box_frame: tuple[int, int] | None = None) -> dict:
    backend = config.route("verify_step", None)
    prompt, images, schema, used = build_inputs(step, data_url, before_url, box, mode, box_frame)
    pool = ThreadPoolExecutor(max_workers=2)
    t0 = time.time()
    futs = [pool.submit(_call, backend, prompt, images, schema, run_id)]
    hedged = False
    done, _ = wait(futs, timeout=config.VERIFY_HEDGE_S, return_when=FIRST_COMPLETED)
    if not done:
        hedged = True
        futs.append(pool.submit(_call, backend, prompt, images, schema, run_id))
    result, meta = None, {}
    pending = list(futs)
    while pending and time.time() - t0 < 30:
        done, _ = wait(pending, timeout=30, return_when=FIRST_COMPLETED)
        for f in list(pending):
            if f in done:
                pending.remove(f)
                r, m = f.result()
                if r is not None:
                    result, meta = r, m
                    break
        if result is not None:
            break
    pool.shutdown(wait=False)
    if result is None:
        raise models.SparkError("verify: no answer (" + str(meta.get("error")) + ")")
    verdict = result.get("verdict", "cannot_tell")
    if verdict not in ("pass", "fail", "cannot_tell"):
        verdict = "cannot_tell"
    return {"verdict": verdict, "evidence_seen": result.get("evidence_seen", ""), "reason": result.get("reason", ""),
            "change": result.get("change", ""), "mode": used,
            "backend": meta["backend"], "model": meta["model"], "seconds": meta["seconds"], "usd": meta["usd"],
            "hedged": hedged, "waited_s": round(time.time() - t0, 2)}
