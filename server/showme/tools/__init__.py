"""One tool per file. Each tool module exposes NAME, DESCRIPTION, SCHEMA (its inputs) and run(run, **inputs).

`definitions()` lists them in the shape a model would see; `run()` executes one by name. The API endpoints are thin
wrappers over these. Today the loop is fixed (identify, outline, advance...), which keeps the demo predictable; the
registry is what would let a model choose tools later without changing the tools.
"""
from importlib import import_module

_NAMES = ["transcribe", "identify_part", "outline_part", "advance_step", "request_help"]
_MODULES = {n: import_module(f".{n}", __name__) for n in _NAMES}


def definitions() -> list[dict]:
    return [{"name": m.NAME, "description": m.DESCRIPTION, "parameters": m.SCHEMA} for m in _MODULES.values()]


def run(name: str, run_record: dict, **inputs):
    if name not in _MODULES:
        raise KeyError(f"unknown tool {name}")
    return _MODULES[name].run(run_record, **inputs)
