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

# Which backend answers which role, by object class. "*" is the default.
# Decided on Day 1 from the 8-photo comparison: Muse Spark Open (Baseten) draws tighter boxes on the
# breaker panel; Muse Spark 1.3 (Meta) is better on everything else and always runs the verifier.
ROUTES = {
    "identify_part": {"breaker panel": "baseten", "*": "meta"},
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
MIN_PART_PX = 60

BASETEN_WAIT_S = 20    # breaker panel only: how long to wait for the open-weights answer after Meta has answered
# Reasoning effort per role. identify at "minimal" was 2.5-2.8 s vs 4-5 s at "low" on Day 2 with the same parts and
# concrete evidence sentences; the verifier stays at "low" (owner: verifier untouched).
REASONING = {"identify_part": os.environ.get("SHOWME_IDENTIFY_REASONING", "minimal"), "verify_step": "low"}
FRAME_TTL_S = 900      # uploaded frames waiting for their transcript are deleted after 15 minutes
MAX_STEPS = 8          # per task
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
