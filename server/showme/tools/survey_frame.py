"""survey_frame: "look around". List every recognizable object in the frame (2-3 word names, a box each, at most
SURVEY_MAX) and outline each with SAM, so the user can pick the one they need help with. No steps, no task: a
general question never invents a task (owner's rule).

Cost per frame: one Spark call (~$0.004-0.006) + one SAM call per object ($0.0025), so $0.006-0.02 for 1-6 objects.
"""
from concurrent.futures import ThreadPoolExecutor

from .. import config, costs, models, prompts

NAME = "survey_frame"
DESCRIPTION = "List the objects in view with boxes and outlines so the user can choose one."
SCHEMA = {"type": "object", "properties": {"frame": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"}},
          "required": ["frame", "width", "height"]}


def _box_px(bbox: dict, w: int, h: int) -> list[int]:
    c = lambda v: max(0, min(1000, int(v)))
    return [c(bbox["x_min"]) * w // 1000, c(bbox["y_min"]) * h // 1000, c(bbox["x_max"]) * w // 1000, c(bbox["y_max"]) * h // 1000]


def run(run_record: dict | None, frame: str, width: int, height: int) -> dict:
    rid = run_record["id"] if run_record else None
    answer, meta = models.spark_json("meta", prompts.SURVEY_PROMPT.format(max_objects=config.SURVEY_MAX), frame,
                                     prompts.SURVEY_SCHEMA, "ShowMeSurvey", run_id=rid, kind="survey",
                                     max_tokens=1500, reasoning_effort=config.REASONING["identify_part"])
    objects = []
    for o in answer.get("objects", [])[: config.SURVEY_MAX]:
        box = _box_px(o["bbox"], width, height)
        if max(box[2] - box[0], box[3] - box[1]) < 24:
            continue
        objects.append({"name": o["name"].strip(), "box": box, "outline": None})

    def outline(obj):
        try:
            sam = models.sam_outline(tuple(obj["box"]), width, height, frame, rid)
            obj["outline"] = {"kind": "mask", "box": obj["box"], **sam} if sam else {"kind": "box", "box": obj["box"]}
        except Exception as e:
            obj["outline"] = {"kind": "box", "box": obj["box"], "error": str(e)[:120]}

    if objects:
        with ThreadPoolExecutor(max_workers=min(6, len(objects))) as pool:
            list(pool.map(outline, objects))
    usd = meta["usd"] + config.SAM_PER_IMAGE * len(objects)
    return {"objects": objects, "frame_size": [width, height], "seconds": meta["seconds"], "usd": round(usd, 6),
            "scene": answer.get("scene", "")}
