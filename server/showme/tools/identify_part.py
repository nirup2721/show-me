"""identify_part: from the first frame and the question, name the object and the first part, box it, plan the steps.

Routing (config.ROUTES): the frame goes to Meta and, when a Baseten key is present, to Baseten at the same time.
Meta's object class decides which answer is kept: breaker panel -> Muse Spark Open (Baseten); anything else ->
Muse Spark 1.3 (Meta). The discarded answer is summarised in the run file so the choice is auditable.
"""
from concurrent.futures import ThreadPoolExecutor

from .. import config, models, prompts, runs

NAME = "identify_part"
DESCRIPTION = "Identify the object and the first part to touch, box it, and write the step plan with expected evidence."
SCHEMA = {"type": "object", "properties": {"frame": {"type": "string", "description": "data URL of the frame"},
                                           "width": {"type": "integer"}, "height": {"type": "integer"}},
          "required": ["frame", "width", "height"]}


def _ask(backend: str, prompt: str, data_url: str, run_id: str):
    try:
        return models.spark_json(backend, prompt, data_url, prompts.IDENTIFY_SCHEMA, "ShowMeIdentify",
                                 run_id=run_id, kind="identify_part")
    except Exception as e:  # a failed secondary backend must never fail the run
        return None, {"backend": backend, "error": str(e)[:200]}


def _box_px(bbox: dict, w: int, h: int) -> list[int]:
    c = lambda v: max(0, min(1000, int(v)))
    return [c(bbox["x_min"]) * w // 1000, c(bbox["y_min"]) * h // 1000, c(bbox["x_max"]) * w // 1000, c(bbox["y_max"]) * h // 1000]


def run(run_record: dict, frame: str, width: int, height: int) -> dict:
    prompt = prompts.IDENTIFY_PROMPT.format(question=run_record["question"], max_steps=config.MAX_STEPS)
    backends = ["meta"] + (["baseten"] if config.has_backend("baseten") else [])
    pool = ThreadPoolExecutor(max_workers=2)
    futures = {b: pool.submit(_ask, b, prompt, frame, run_record["id"]) for b in backends}
    results = {"meta": futures["meta"].result()}       # Meta decides the object class, so wait for it first

    meta_answer, meta_meta = results["meta"]
    if meta_answer is None:
        pool.shutdown(wait=False)
        raise models.SparkError("identify failed on Meta: " + meta_meta.get("error", "?"))
    object_class = config.object_class_of(meta_answer["object_name"])
    chosen = config.route("identify_part", object_class)
    if chosen != "meta" and chosen in futures:
        # Only the breaker panel waits for the open-weights answer (at most BASETEN_WAIT_S more).
        try:
            results[chosen] = futures[chosen].result(timeout=config.BASETEN_WAIT_S)
        except Exception as e:
            results[chosen] = (None, {"backend": chosen, "error": f"timed out or failed: {str(e)[:100]}"})
    else:
        for b in futures:
            if b != "meta":
                results[b] = (None, {"backend": b, "error": "not needed for this object; not awaited"})
    pool.shutdown(wait=False)  # a still-running secondary call finishes and logs its cost on its own

    answer, meta = results.get(chosen, (None, None))
    if answer is None:  # routed backend failed, timed out or absent: fall back to Meta
        chosen, answer, meta = "meta", meta_answer, meta_meta

    for b, (a, m) in results.items():
        runs.add_cost(run_record, (m or {}).get("usd", 0.0))

    steps = []
    for s in answer["steps"][: config.MAX_STEPS]:
        steps.append({"instruction": s["instruction"], "part": s["part"], "expected_evidence": s["expected_evidence"],
                      "status": "pending", "confirmed_by": None, "tries": 0, "verdicts": []})

    run_record.update({
        "object_name": answer["object_name"], "object_class": object_class, "part_name": answer["part_name"],
        "matched_label": answer.get("matched_label", ""), "label_matches_question": answer.get("label_matches_question"),
        "safety_warning": answer.get("safety_warning", ""),
        "prep": answer.get("prep", ""),
        "frame_size": [width, height],
        "bbox_px": _box_px(answer["bbox"], width, height),
        "steps": steps, "current_step": 0,
        "identify": {
            "chosen_backend": chosen, "chosen_model": (meta or {}).get("model"), "seconds": (meta or {}).get("seconds"),
            "answers": {b: ({"object_name": a["object_name"], "part_name": a["part_name"], "seconds": m.get("seconds")}
                            if a else {"error": m.get("error")}) for b, (a, m) in results.items()},
        },
    })
    runs.event(run_record, "identified", backend=chosen, object=answer["object_name"], part=answer["part_name"])
    return answer
