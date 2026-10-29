"""Configuration: backends, routing table, prices, caps. Everything here is data, not logic."""
import os
from pathlib import Path

META_BASE_URL = "https://api.meta.ai/v1"
BASETEN_BASE_URL = os.environ.get("BASETEN_BASE_URL", "https://inference.baseten.co/v1")

MODELS = {
    "meta": "muse-spark-1.3",
    "baseten": "meta-models/muse-spark-open-1026",
}
SAM_MODEL = "sam-3.1"
VOICE_MODEL = "muse-voice-transcribe-1.0"

# Which backend answers which role. Day 3 (owner): the user never waits on Baseten. Meta Spark 1.3 drives every
# run; Muse Spark Open (Baseten) is run in parallel on every identify and its answer is recorded in the run file
# when it lands (see identify_part._record_late), which is what the README's open-weights claim rests on.
ROUTES = {
    "identify_part": {"*": "meta"},
    "verify_step": {"*": "meta"},
}

# Published prices per 1M tokens (Meta: dev.meta.ai; Baseten: baseten.co/pricing, both read Oct 2026).
PRICES = {
    "meta": {"in": 1.25, "out": 4.25, "cached": 0.15},
    "baseten": {"in": 1.25, "out": 4.25, "cached": 0.15},
}
SAM_PER_IMAGE = 0.0025
VOICE_PER_HOUR = 0.18

# Spend control on the $150 Meta credits. Baseten draws on its own credits and is tracked separately.
WARN_USD = float(os.environ.get("SHOWME_WARN_USD", "75"))
HARD_STOP_USD = float(os.environ.get("SHOWME_HARD_STOP_USD", "100"))

# Frames are downsized to this longest side before any model sees them. Every coordinate is in this frame.
FRAME_MAX_SIDE = 1024
# Arm's-reach rule: a part box narrower than this (long side, px) means "move closer", no SAM, no step.
# Day 3: phones send portrait 432x768 frames, where a card slot at arm's length is ~50 px; 60 refused it. 40 keeps
# the Day-1 "Wrong" case (31 px from 3 m) out while letting close shots of small parts through.
MIN_PART_PX = 40
# Shrink Spark's box inward by this fraction per side before it becomes SAM's prompt (Day 3: at 0 the mask on the
# coffee screen spilled onto the bezel; see LEARNINGS). Only for the part outline, not for look-around.
SAM_BOX_INSET = float(os.environ.get("SHOWME_SAM_INSET", "0.08"))

BASETEN_WAIT_S = 20    # breaker panel only: how long to wait for the open-weights answer after Meta has answered
# Speed over cost (owner, Oct 28 evening): if Meta's identify has not answered after HEDGE_AFTER_S, fire a second
# identical Meta call and take whichever answers first; if both are slow, the Baseten answer (already in flight for
# routing) is used as soon as it arrives. IDENTIFY_DEADLINE_S bounds the whole wait.
HEDGE_AFTER_S = 3.0
IDENTIFY_DEADLINE_S = 30.0
# Reasoning effort per role. identify at "minimal" was 2.5-2.8 s vs 4-5 s at "low" on Day 2 with the same parts and
# concrete evidence sentences; the verifier stays at "low" (owner: verifier untouched).
REASONING = {"identify_part": os.environ.get("SHOWME_IDENTIFY_REASONING", "minimal"),
             # Day 3 matrix on the before/after pairs: minimal + 512 px frames kept every coffee verdict right and gave
             # honest cannot_tell on the panel (minimal + 768 px produced two false passes), at 1.0-1.8 s vs 1.8-2.5 s.
             # The 512 px frame is therefore part of the verifier's safety, not just its speed: the page must send it.
             "verify_step": os.environ.get("SHOWME_VERIFY_REASONING", "minimal")}
# Auto-check (Day 3): the phone sends a frame every ~2 s to /check; a step advances when the last CHECK_PASSES
# verdicts on it are passes within CHECK_WINDOW_S. The verifier is hedged like identify: a second call after
# VERIFY_HEDGE_S if the first has not answered.
CHECK_PASSES = 2
CHECK_WINDOW_S = 10.0
VERIFY_HEDGE_S = 2.0
FRAME_TTL_S = 900      # uploaded frames waiting for their transcript are deleted after 15 minutes
MAX_STEPS = 6          # per task (was 8; fewer, shorter steps mean fewer output tokens and a faster first answer)
SURVEY_MAX = 6         # "look around": at most this many objects per frame, one SAM call each
MAX_VERIFY_TRIES = 3   # per step before needs_input
CONSISTENCY_RUNS = 3   # offline test: same frame, same verdict, 3 of 3, before a step may appear in the video

# Working data lives on fast local disk; store.py mirrors it to the Modal Volume (SHOWME_VOLUME) in the background.
DATA_DIR = Path(os.environ.get("SHOWME_DATA", "./data"))
VOLUME_DIR = os.environ.get("SHOWME_VOLUME", "")


def key(backend: str) -> str:
    name = {"meta": "META_API_KEY", "baseten": "BASETEN_API_KEY"}[backend]
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def has_backend(backend: str) -> bool:
    return bool(os.environ.get({"meta": "META_API_KEY", "baseten": "BASETEN_API_KEY"}[backend]))


def route(role: str, object_class: str | None) -> str:
    table = ROUTES[role]
    if object_class and object_class in table and has_backend(table[object_class]):
        return table[object_class]
    return table["*"]


def object_class_of(object_name: str) -> str:
    n = (object_name or "").lower()
    if "breaker" in n or "fuse box" in n or ("panel" in n and "circuit" in n):
        return "breaker panel"
    return "*"
