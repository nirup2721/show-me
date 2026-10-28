"""outline_part: SAM 3.1 turns the current part's box into a tight outline. Falls back to the box itself.

Arm's-reach rule: if the box's long side is under MIN_PART_PX the user is too far away; no SAM call, the phone says
"move closer" and asks for a new frame.
"""
from .. import config, models, runs

NAME = "outline_part"
DESCRIPTION = "Outline the part the user must touch, on the current frame."
SCHEMA = {"type": "object", "properties": {"frame": {"type": "string"}}, "required": ["frame"]}


def run(run_record: dict, frame: str) -> dict:
    x1, y1, x2, y2 = run_record["bbox_px"]
    w, h = run_record["frame_size"]
    long_side = max(x2 - x1, y2 - y1)
    if long_side < config.MIN_PART_PX:
        run_record["outline"] = {"kind": "too_far", "box": [x1, y1, x2, y2], "long_side_px": long_side}
        runs.event(run_record, "too_far", long_side_px=long_side)
        return run_record["outline"]
    try:
        sam = models.sam_outline((x1, y1, x2, y2), w, h, frame, run_record["id"])
    except Exception as e:
        sam = None
        runs.event(run_record, "sam_error", error=str(e)[:200])
    runs.add_cost(run_record, config.SAM_PER_IMAGE)
    if sam:
        run_record["outline"] = {"kind": "mask", "box": [x1, y1, x2, y2], **sam}
    else:
        run_record["outline"] = {"kind": "box", "box": [x1, y1, x2, y2]}
    runs.event(run_record, "outlined", kind=run_record["outline"]["kind"])
    return run_record["outline"]
