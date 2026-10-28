"""Offline tests of the state machine with a fake model. No network, no spend.

Run:  python -m pytest server/tests -q
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SHOWME_DATA"] = tempfile.mkdtemp()
os.environ["META_API_KEY"] = "test"

from showme import config, runs, tools  # noqa: E402
from showme.tools import advance_step  # noqa: E402

FRAME = "data:image/jpeg;base64,xx"


def fake_identify(monkeypatch, object_name="coffee machine"):
    def spark_json(backend, prompt, data_url, schema, name, **kw):
        return ({"object_name": object_name, "part_name": "touchscreen", "bbox": {"x_min": 100, "y_min": 100, "x_max": 400, "y_max": 400},
                 "matched_label": "", "label_matches_question": False, "safety_warning": "",
                 "steps": [{"instruction": "Place a cup.", "part": "drip tray", "expected_evidence": "a cup on the tray"},
                           {"instruction": "Press brew.", "part": "screen", "expected_evidence": "progress bar shown"}]},
                {"backend": backend, "model": "fake", "seconds": 0.1, "usd": 0.005, "usage": {}})
    monkeypatch.setattr("showme.models.spark_json", spark_json)
    monkeypatch.setattr("showme.models.sam_outline", lambda *a, **k: {"svg_path": "M0 0", "mask_w": 2, "mask_h": 2, "bounds": [100, 100, 400, 400], "confidence": 0.9, "seconds": 0.1})


def fake_verifier(monkeypatch, verdicts):
    it = iter(verdicts)

    def verify_step(step, frame, run_id=None):
        v = next(it)
        return {"verdict": v, "evidence_seen": f"saw {v}", "reason": "because", "backend": "meta", "model": "fake", "seconds": 0.1, "usd": 0.003}
    monkeypatch.setattr("showme.tools.advance_step.verify_step", verify_step)


def start(monkeypatch):
    fake_identify(monkeypatch)
    run = runs.new_run("how do I start a brew?")
    tools.run("identify_part", run, frame=FRAME, width=768, height=1024)
    tools.run("outline_part", run, frame=FRAME)
    runs.set_status(run, "guiding", "Step 1. Place a cup.")
    runs.save(run)
    return run


def test_happy_path_all_camera(monkeypatch):
    run = start(monkeypatch)
    fake_verifier(monkeypatch, ["pass", "pass"])
    tools.run("advance_step", run, frame=FRAME)
    assert run["status"] == "guiding" and run["current_step"] == 1
    tools.run("advance_step", run, frame=FRAME)
    assert run["status"] == "done"
    assert all(s["confirmed_by"] == "camera" for s in run["steps"])
    assert run["cost_usd"] > 0


def test_fail_then_pass(monkeypatch):
    run = start(monkeypatch)
    fake_verifier(monkeypatch, ["fail", "pass"])
    tools.run("advance_step", run, frame=FRAME)
    assert run["status"] == "guiding" and run["current_step"] == 0 and run["message"].startswith("Not yet")
    tools.run("advance_step", run, frame=FRAME)
    assert run["current_step"] == 1


def test_three_fails_needs_input_then_human_confirm(monkeypatch):
    run = start(monkeypatch)
    fake_verifier(monkeypatch, ["fail"] * config.MAX_VERIFY_TRIES)
    for _ in range(config.MAX_VERIFY_TRIES):
        tools.run("advance_step", run, frame=FRAME)
    assert run["status"] == "needs_input"
    advance_step.confirm_by_human(run)
    runs.save(run)
    assert run["current_step"] == 1 and run["steps"][0]["confirmed_by"] == "human"
    listed = next(r for r in runs.list_runs() if r["id"] == run["id"])
    assert listed["human_confirmed"] == 1


def test_cannot_tell_goes_to_needs_input_immediately(monkeypatch):
    run = start(monkeypatch)
    fake_verifier(monkeypatch, ["cannot_tell"])
    tools.run("advance_step", run, frame=FRAME)
    assert run["status"] == "needs_input" and run["steps"][0]["tries"] == 1


def test_too_far_box_blocks_sam(monkeypatch):
    fake_identify(monkeypatch)
    run = runs.new_run("q")
    tools.run("identify_part", run, frame=FRAME, width=768, height=1024)
    run["bbox_px"] = [10, 10, 40, 40]  # 30 px: under MIN_PART_PX
    out = tools.run("outline_part", run, frame=FRAME)
    assert out["kind"] == "too_far"


def test_routing_table():
    assert config.object_class_of("electrical breaker panel") == "breaker panel"
    assert config.object_class_of("MINI-STARBUCKS coffee machine") == "*"
    assert config.route("verify_step", "breaker panel") == "meta"
    os.environ.pop("BASETEN_API_KEY", None)
    assert config.route("identify_part", "breaker panel") == "meta"  # no key -> fallback
    os.environ["BASETEN_API_KEY"] = "x"
    assert config.route("identify_part", "breaker panel") == "baseten"
    assert config.route("identify_part", "*") == "meta"


def test_run_persists_and_reloads(monkeypatch):
    run = start(monkeypatch)
    again = runs.load(run["id"])
    assert again["status"] == "guiding" and again["steps"][0]["expected_evidence"] == "a cup on the tray"
