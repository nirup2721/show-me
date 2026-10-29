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

    def verify_step(step, frame, run_id=None, **kw):
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
    assert config.route("identify_part", "breaker panel") == "meta"   # Day 3: the user never waits on Baseten
    assert config.route("identify_part", "*") == "meta"


def test_hedged_identify_takes_the_faster_meta_call(monkeypatch):
    import time
    calls = {"n": 0}

    def spark_json(backend, prompt, data_url, schema, name, **kw):
        calls["n"] += 1
        if backend == "meta" and calls["n"] == 1:
            time.sleep(1.0)  # the slow first call
        answer = {"object_name": "coffee machine", "part_name": f"screen{calls['n']}", "bbox": {"x_min": 100, "y_min": 100, "x_max": 400, "y_max": 400},
                  "matched_label": "", "label_matches_question": False, "safety_warning": "", "prep": "",
                  "steps": [{"instruction": "Tap.", "part": "screen", "expected_evidence": "menu shown"}]}
        return answer, {"backend": backend, "model": "fake", "seconds": 0.1, "usd": 0.005, "usage": {}}
    monkeypatch.setattr("showme.models.spark_json", spark_json)
    monkeypatch.setattr(config, "HEDGE_AFTER_S", 0.2)
    os.environ.pop("BASETEN_API_KEY", None)
    run = runs.new_run("q")
    tools.run("identify_part", run, frame=FRAME, width=768, height=1024)
    assert run["identify"]["hedged"] is True
    assert run["identify"]["winner"] == "meta2"
    assert run["part_name"] == "screen2"
    assert run["identify"]["waited_s"] < 1.0


def test_general_question_detection():
    from showme.api import is_general
    assert is_general("what's here?") and is_general("What can you see") and is_general("which one?") and is_general("")
    assert not is_general("how do I start a new brew?") and not is_general("the register has no power")


def test_survey_lists_objects_and_outlines_each(monkeypatch):
    def spark_json(backend, prompt, data_url, schema, name, **kw):
        return ({"scene": "store counter", "objects": [
            {"name": "coffee machine", "bbox": {"x_min": 50, "y_min": 50, "x_max": 400, "y_max": 600}},
            {"name": "breaker panel", "bbox": {"x_min": 500, "y_min": 100, "x_max": 900, "y_max": 900}},
            {"name": "speck", "bbox": {"x_min": 10, "y_min": 10, "x_max": 12, "y_max": 12}}]},
                {"backend": backend, "model": "fake", "seconds": 0.1, "usd": 0.004, "usage": {}})
    monkeypatch.setattr("showme.models.spark_json", spark_json)
    monkeypatch.setattr("showme.models.sam_outline", lambda box, w, h, f, rid=None: {"svg_path": "M0 0", "mask_w": 2, "mask_h": 2, "bounds": list(box), "confidence": 0.8, "seconds": 0.1})
    out = tools.run("survey_frame", None, frame=FRAME, width=768, height=1024)
    assert [o["name"] for o in out["objects"]] == ["coffee machine", "breaker panel"]   # the 2 px speck is dropped
    assert all(o["outline"]["kind"] == "mask" for o in out["objects"])
    assert abs(out["usd"] - (0.004 + 2 * config.SAM_PER_IMAGE)) < 1e-9


def test_auto_check_needs_two_consecutive_passes(monkeypatch):
    run = start(monkeypatch)
    fake_verifier(monkeypatch, ["pass", "fail", "pass", "pass"])
    assert advance_step.check(run, FRAME, 1)["can_advance"] is False
    assert advance_step.check(run, FRAME, 2)["pass_streak"] == 0
    assert advance_step.advance_from_checks(run)["ok"] is False and run["current_step"] == 0
    advance_step.check(run, FRAME, 3)
    out = advance_step.check(run, FRAME, 4)
    assert out["pass_streak"] == 2 and out["can_advance"] is True
    assert advance_step.advance_from_checks(run)["ok"] is True
    assert run["current_step"] == 1 and run["steps"][0]["confirmed_by"] == "camera"
    assert advance_step.advance_from_checks(run)["ok"] is False   # no checks yet on step 2


def test_stale_check_is_dropped_after_the_step_moved_on(monkeypatch):
    run = start(monkeypatch)
    fake_verifier(monkeypatch, ["pass", "pass", "pass"])
    j1 = advance_step.judge(run, FRAME)            # judged while on step 1
    advance_step.check(run, FRAME, 2); advance_step.check(run, FRAME, 3, auto_advance=True)
    assert run["current_step"] == 1
    out = advance_step.record_check(run, j1[0], j1[1], 1, auto_advance=True)   # lands late
    assert out.get("stale") is True and run["current_step"] == 1 and run["steps"][1]["verdicts"] == []


def test_run_persists_and_reloads(monkeypatch):
    run = start(monkeypatch)
    again = runs.load(run["id"])
    assert again["status"] == "guiding" and again["steps"][0]["expected_evidence"] == "a cup on the tray"
