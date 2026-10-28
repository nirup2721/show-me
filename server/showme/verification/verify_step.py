"""verify_step: an independent Spark call that judges one fresh frame against one step's expected evidence.

It sees ONLY the step's instruction, the expected-evidence sentence, and the new frame. It never sees the planning
call's reasoning, earlier frames, earlier verdicts, or anything the user said. It always runs on the backend in
ROUTES["verify_step"] (Meta Spark 1.3), which on the breaker panel is a different model from the one that identified
the part.
"""
from .. import config, models, prompts


def verify_step(step: dict, data_url: str, run_id: str | None = None) -> dict:
    backend = config.route("verify_step", None)
    prompt = prompts.VERIFY_PROMPT.format(instruction=step["instruction"], evidence=step["expected_evidence"])
    result, meta = models.spark_json(backend, prompt, data_url, prompts.VERIFY_SCHEMA, "ShowMeVerify",
                                     run_id=run_id, kind="verify_step", max_tokens=3000,
                                     reasoning_effort=config.REASONING["verify_step"])
    verdict = result.get("verdict", "cannot_tell")
    if verdict not in ("pass", "fail", "cannot_tell"):
        verdict = "cannot_tell"
    return {"verdict": verdict, "evidence_seen": result.get("evidence_seen", ""), "reason": result.get("reason", ""),
            "backend": meta["backend"], "model": meta["model"], "seconds": meta["seconds"], "usd": meta["usd"]}
