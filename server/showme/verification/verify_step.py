"""verify_step: an independent Spark call that judges one fresh frame against one step's expected evidence.

It sees ONLY the step's instruction, the expected-evidence sentence, and the new frame. It never sees the planning
call's reasoning, earlier frames, earlier verdicts, or anything the user said. It always runs on the backend in
ROUTES["verify_step"] (Meta Spark 1.3), which on the breaker panel is a different model from the one that identified
the part.
"""
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .. import config, models, prompts


def _call(backend, prompt, data_url, run_id):
    try:
        return models.spark_json(backend, prompt, data_url, prompts.VERIFY_SCHEMA, "ShowMeVerify",
                                 run_id=run_id, kind="verify_step", max_tokens=3000,
                                 reasoning_effort=config.REASONING["verify_step"])
    except Exception as e:
        return None, {"error": str(e)[:200]}


def verify_step(step: dict, data_url: str, run_id: str | None = None) -> dict:
    """One independent judgement of a frame. Hedged: if the first call is silent for VERIFY_HEDGE_S a second
    identical call is fired and the first answer wins (owner: speed over cost)."""
    backend = config.route("verify_step", None)
    prompt = prompts.VERIFY_PROMPT.format(instruction=step["instruction"], evidence=step["expected_evidence"])
    pool = ThreadPoolExecutor(max_workers=2)
    t0 = time.time()
    futs = [pool.submit(_call, backend, prompt, data_url, run_id)]
    hedged = False
    done, _ = wait(futs, timeout=config.VERIFY_HEDGE_S, return_when=FIRST_COMPLETED)
    if not done:
        hedged = True
        futs.append(pool.submit(_call, backend, prompt, data_url, run_id))
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
            "backend": meta["backend"], "model": meta["model"], "seconds": meta["seconds"], "usd": meta["usd"],
            "hedged": hedged, "waited_s": round(time.time() - t0, 2)}
