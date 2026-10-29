"""identify_part: from the first frame and the question, name the object and the first part, box it, plan the steps.

Routing (config.ROUTES): the frame goes to Meta and, when a Baseten key is present, to Baseten at the same time.
Meta's object class decides which answer is kept: breaker panel -> Muse Spark Open (Baseten); anything else ->
Muse Spark 1.3 (Meta). The discarded answer is summarised in the run file so the choice is auditable.

Speed (owner's rule, Oct 28: speed over cost): Meta's latency has a long tail (same tokens in 2.6 s or 13 s), so if
the first Meta call has not answered after HEDGE_AFTER_S a second identical Meta call is fired and the first answer
wins. If both are slow, the Baseten answer already in flight is used as soon as it arrives. Every hedge is recorded
in the run file (identify.hedged, identify.winner, identify.waited_s).
"""
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .. import config, models, prompts, runs

NAME = "identify_part"
DESCRIPTION = "Identify the object and the first part to touch, box it, and write the step plan with expected evidence."
SCHEMA = {"type": "object", "properties": {"frame": {"type": "string", "description": "data URL of the frame"},
                                           "width": {"type": "integer"}, "height": {"type": "integer"}},
          "required": ["frame", "width", "height"]}


def _ask(backend: str, prompt: str, data_url: str, run_id: str):
    try:
        return models.spark_json(backend, prompt, data_url, prompts.IDENTIFY_SCHEMA, "ShowMeIdentify",
                                 run_id=run_id, kind="identify_part", reasoning_effort=config.REASONING["identify_part"])
    except Exception as e:  # a failed secondary backend must never fail the run
        return None, {"backend": backend, "error": str(e)[:200]}


def _record_late(run_id: str, key: str, fut):
    """A backend answer that arrived after the run moved on: store its summary in the run file."""
    try:
        a, m = fut.result()
        summary = ({"object_name": a["object_name"], "part_name": a["part_name"], "seconds": m.get("seconds"), "late": True}
                   if a else {"error": (m or {}).get("error"), "late": True})
        rec = runs.load(run_id)
        if rec is None:
            return
        rec.setdefault("identify", {}).setdefault("answers", {})[key] = summary
        runs.event(rec, "late_answer", backend=key, seconds=summary.get("seconds"))
        runs.save(rec)
    except Exception:
        pass


def _box_px(bbox: dict, w: int, h: int) -> list[int]:
    c = lambda v: max(0, min(1000, int(v)))
    return [c(bbox["x_min"]) * w // 1000, c(bbox["y_min"]) * h // 1000, c(bbox["x_max"]) * w // 1000, c(bbox["y_max"]) * h // 1000]


def run(run_record: dict, frame: str, width: int, height: int, focus: dict | None = None) -> dict:
    """focus = {"name", "box"} when the user picked an object in look-around mode: identify works on that object only."""
    focus_line = ""
    if focus and focus.get("name"):
        b = focus.get("box") or []
        where = f" inside the box ({b[0]},{b[1]})-({b[2]},{b[3]}) in pixels of this {width}x{height} photo" if len(b) == 4 else ""
        focus_line = f"The user pointed at the {focus['name']}{where}. Answer about that object only; ignore everything else in the photo.\n"
        run_record["focus"] = focus
    prompt = prompts.IDENTIFY_PROMPT.format(question=run_record["question"], max_steps=config.MAX_STEPS, focus=focus_line)
    have_baseten = config.has_backend("baseten")
    pool = ThreadPoolExecutor(max_workers=3)
    t0 = time.time()
    futures = {"meta": pool.submit(_ask, "meta", prompt, frame, run_record["id"])}
    if have_baseten:
        futures["baseten"] = pool.submit(_ask, "baseten", prompt, frame, run_record["id"])
    results: dict = {}
    hedged = False

    # 1. Wait for the first Meta call up to HEDGE_AFTER_S; if it is slow, fire a second one.
    done, _ = wait([futures["meta"]], timeout=config.HEDGE_AFTER_S, return_when=FIRST_COMPLETED)
    if not done:
        hedged = True
        futures["meta2"] = pool.submit(_ask, "meta", prompt, frame, run_record["id"])
        runs.event(run_record, "hedge", after_s=round(time.time() - t0, 2))

    # 2. Take the first good answer from any Meta call; if both are slow, Baseten's answer counts too.
    winner = None
    pending = {k: f for k, f in futures.items()}
    deadline = t0 + config.IDENTIFY_DEADLINE_S
    while pending and winner is None and time.time() < deadline:
        done, _ = wait(list(pending.values()), timeout=max(0.1, deadline - time.time()), return_when=FIRST_COMPLETED)
        for k, f in list(pending.items()):
            if f in done:
                results[k] = f.result()
                del pending[k]
                if results[k][0] is not None and (k.startswith("meta") or hedged):
                    winner = k
                    break
    if winner is None:
        pool.shutdown(wait=False)
        errs = {k: (m or {}).get("error") for k, (a, m) in results.items()}
        raise models.SparkError(f"identify: no answer within {config.IDENTIFY_DEADLINE_S:.0f}s ({errs})")
    waited = round(time.time() - t0, 2)
    answer, meta = results[winner]
    source_backend = "meta" if winner.startswith("meta") else "baseten"

    # 3. Never wait on Baseten (Day 3: the panel's first answer took ~10 s). The first good answer drives the run.
    #    Muse Spark Open's answer, when it lands, is written into the run file by _record_late so the open-weights
    #    claim is backed by the logs without the user waiting for it.
    object_class = config.object_class_of(answer["object_name"])
    chosen = source_backend
    run_id = run_record["id"]
    for k, f in futures.items():
        if k not in results:
            results[k] = (None, {"backend": "meta" if k.startswith("meta") else k, "error": "still running when the run moved on; recorded when it lands"})
            f.add_done_callback(lambda fut, k=k: _record_late(run_id, k, fut))
    pool.shutdown(wait=False)  # losers finish on their own, log their cost, and land in the run file
    meta_answer, meta_meta = answer, meta  # names kept for the summary below

    for b, (a, m) in results.items():
        runs.add_cost(run_record, (m or {}).get("usd", 0.0))

    steps = []
    for s in answer["steps"][: config.MAX_STEPS]:
        steps.append({"instruction": s["instruction"], "part": s["part"], "expected_evidence": s["expected_evidence"],
                      "bbox_px": _box_px(s["bbox"], width, height) if s.get("bbox") else None,
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
            "hedged": hedged, "winner": winner, "waited_s": waited,
            "answers": {b: ({"object_name": a["object_name"], "part_name": a["part_name"], "seconds": m.get("seconds")}
                            if a else {"error": m.get("error")}) for b, (a, m) in results.items()},
        },
    })
    runs.event(run_record, "identified", backend=chosen, winner=winner, hedged=hedged, waited_s=waited,
               object=answer["object_name"], part=answer["part_name"])
    return answer
