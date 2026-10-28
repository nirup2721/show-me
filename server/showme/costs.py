"""Every paid call is appended to costs.jsonl. The hard stop refuses new paid calls past HARD_STOP_USD on Meta."""
import json
import threading
import time

from . import config

_lock = threading.Lock()


class BudgetExhausted(RuntimeError):
    pass


def _path():
    """This container's own cost file; totals() sums every costs*.jsonl, including other containers' mirrored ones."""
    from . import store
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR / f"costs-{store.CONTAINER_TAG}.jsonl"


_cache = {"mtimes": None, "totals": None}


def totals() -> dict:
    files = sorted(config.DATA_DIR.glob("costs*.jsonl")) if config.DATA_DIR.exists() else []
    sig = tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    if _cache["mtimes"] == sig and _cache["totals"] is not None:
        return dict(_cache["totals"])
    out = {"meta": 0.0, "baseten": 0.0, "calls": 0}
    for p in files:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            out[rec.get("backend", "meta")] = out.get(rec.get("backend", "meta"), 0.0) + float(rec.get("usd", 0))
            out["calls"] += 1
    out["meta"] = round(out["meta"], 6)
    out["baseten"] = round(out["baseten"], 6)
    out["warn"] = out["meta"] >= config.WARN_USD
    out["hard_stop"] = out["meta"] >= config.HARD_STOP_USD
    _cache["mtimes"], _cache["totals"] = sig, dict(out)
    return out


def check_budget(backend: str = "meta"):
    """Call before any paid call. Baseten has its own credits, so only Meta spend trips the stop."""
    if backend == "meta" and totals()["meta"] >= config.HARD_STOP_USD:
        raise BudgetExhausted(f"Meta spend reached the ${config.HARD_STOP_USD:.0f} hard stop; paid calls are off")


def spark_cost(usage: dict, backend: str) -> float:
    p = config.PRICES[backend]
    pin = usage.get("prompt_tokens") or 0
    pout = usage.get("completion_tokens") or 0
    cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
    return (pin - cached) / 1e6 * p["in"] + cached / 1e6 * p["cached"] + pout / 1e6 * p["out"]


def log(kind: str, backend: str, usd: float, run_id: str | None = None, **detail) -> float:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "backend": backend,
           "usd": round(usd, 6), "run_id": run_id, **detail}
    from . import store
    with _lock:
        p = _path()
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    store.mark_dirty(p)
    return rec["usd"]
