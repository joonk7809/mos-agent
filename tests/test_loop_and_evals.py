"""Tests for stuck detection, stricter parsing/retries, SFT user text, and the eval scoring."""
import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from evals import run_evals, stats
from mos import loopguard, maiui, server
from mos.export_sft import session_samples
from mos.fake import FakeDevice, sample_png
from mos.remote import RemoteRunner
from tests.test_fnf import APPS, PNG, ScriptedLLM, make_client, resp, start

A = sample_png()


def png_with(text: str, box: bool = False, xy=(60, 500)) -> bytes:
    img = Image.open(io.BytesIO(A)).convert("RGB")
    d = ImageDraw.Draw(img)
    if box:
        d.rectangle((100, 600, 980, 1400), fill=(30, 30, 30))
    d.text(xy, text, fill=(0, 0, 0), font=ImageFont.load_default(size=42))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


B = png_with("dialog", box=True)
CLICK = {"action": "click", "coordinate": [500, 500]}


# --- loop guard ---------------------------------------------------------------------

def test_same_screen_ignores_tiny_changes_but_not_new_content():
    sa = loopguard.signature(A)
    assert loopguard.same_screen(sa, loopguard.signature(png_with("12:01", xy=(40, 20))))  # status bar clock
    assert loopguard.same_screen(loopguard.signature(_jpeg(A, 60)), loopguard.signature(_jpeg(A, 90)))  # JPEG noise
    assert not loopguard.same_screen(sa, loopguard.signature(png_with("hi")))           # two typed characters
    assert not loopguard.same_screen(sa, loopguard.signature(B))                        # a dialog opened


def _jpeg(png: bytes, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _phone_screen(draw) -> bytes:
    """A 720x1600 JPEG at quality 90, like the Android app sends."""
    img = Image.new("RGB", (720, 1600), (250, 250, 250))
    d = ImageDraw.Draw(img)
    d.text((40, 300), "Timer", fill=(0, 0, 0), font=ImageFont.load_default(size=30))
    draw(d)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_small_counter_steps_and_colour_only_changes_count_as_changes():
    font = ImageFont.load_default(size=20)  # ~12sp on a 720-wide screenshot
    for n in range(1, 26):
        a = _phone_screen(lambda d, n=n: d.text((300, 800), str(n), fill=(0, 0, 0), font=font))
        b = _phone_screen(lambda d, n=n: d.text((300, 800), str(n + 1), fill=(0, 0, 0), font=font))
        assert not loopguard.same_screen(loopguard.signature(a), loopguard.signature(b)), n
    red = _phone_screen(lambda d: d.rounded_rectangle((40, 1000, 240, 1060), 20, fill=(200, 80, 80)))
    green = _phone_screen(lambda d: d.rounded_rectangle((40, 1000, 240, 1060), 20, fill=(80, 140, 80)))
    assert not loopguard.same_screen(loopguard.signature(red), loopguard.signature(green))


def test_guard_flags_no_effect_and_stuck_after_repeats():
    g = loopguard.LoopGuard(max_repeats=3)
    assert g.observe(A) == {"no_effect": False, "stuck": None}
    for i in range(1, 3):
        g.acted(dict(CLICK, coordinate=[500 + i, 500]))  # jitter counts as the same tap
        r = g.observe(A)
        assert r["no_effect"] and r["stuck"] is None
    g.acted(CLICK)
    assert "3 times" in g.observe(A)["stuck"]
    assert g.no_effect_total == 3


def test_guard_resets_when_action_works_or_changes():
    g = loopguard.LoopGuard(max_repeats=2)
    g.observe(A)
    g.acted(CLICK)
    assert g.observe(A)["no_effect"]
    g.acted(CLICK)
    assert g.observe(B)["stuck"] is None             # this time the tap worked
    g.acted(CLICK)
    assert g.observe(B)["no_effect"]
    g.acted({"action": "click", "coordinate": [100, 900]})  # a different target restarts the count
    assert g.observe(B)["stuck"] is None


def test_guard_stops_endless_waiting_on_a_frozen_screen():
    g = loopguard.LoopGuard(max_waits=3)
    g.observe(A)
    results = []
    for _ in range(3):
        g.acted({"action": "wait"})
        results.append(g.observe(A))
    assert [r["stuck"] is not None for r in results] == [False, False, True]
    assert not any(r["no_effect"] for r in results)  # waiting isn't counted as a failed action


# --- server integration ------------------------------------------------------------

def test_server_ends_stuck_session_early(tmp_path):
    c, llm = make_client(tmp_path, [resp(CLICK)] * 5)
    sid = start(c)
    statuses = [c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG}).json() for _ in range(4)]
    assert [r["status"] for r in statuses] == ["running", "running", "running", "stuck"]
    assert statuses[-1]["done"] and len(llm.calls) == 3   # no model call wasted on the stuck step
    final = json.loads((tmp_path / sid / "final.json").read_text())
    assert final["status"] == "stuck" and final["no_effect_actions"] == 3


def test_no_effect_hint_reaches_model_and_sft_export(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "NO_EFFECT_HINT", True)
    c, llm = make_client(tmp_path, [resp(CLICK), resp({"action": "terminate", "status": "success"})])
    sid = start(c)
    for _ in range(2):
        c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG})
    assert any(m["content"][0].get("text") == server.NO_EFFECT_TEXT for m in llm.calls[-1])
    sample = session_samples(tmp_path / sid)[1]
    assert any(server.NO_EFFECT_TEXT in m["content"] for m in sample["messages"] if m["role"] == "user")


def test_hint_off_by_default(tmp_path):
    c, llm = make_client(tmp_path, [resp(CLICK), resp(CLICK)])
    sid = start(c)
    for _ in range(2):
        c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG})
    assert not any(m["content"][0].get("text") == server.NO_EFFECT_TEXT for m in llm.calls[-1])


def test_sft_export_keeps_ask_user_replies(tmp_path):
    c, _ = make_client(tmp_path, [resp({"action": "ask_user", "text": "Which alarm?"}),
                                  resp({"action": "terminate", "status": "success"})])
    sid = start(c)
    c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG})
    c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG, "reply": "7:30"})
    assert session_samples(tmp_path / sid) == []  # replies are private: skipped unless opted in
    sample = session_samples(tmp_path / sid, include_replies=True)[1]  # same context the model saw
    assert any(m["role"] == "user" and m["content"] == "7:30" for m in sample["messages"])


# --- parsing and retries -------------------------------------------------------------

@pytest.mark.parametrize("args, msg", [
    ({"action": "fly"}, "unknown action"),
    ({"action": "click"}, "missing coordinate"),
    ({"action": "drag", "start_coordinate": [1, 2]}, "missing end_coordinate"),
])
def test_parse_rejects_malformed_actions(args, msg):
    with pytest.raises(maiui.ParseError, match=msg):
        maiui.parse_response(resp(args))


def test_terminate_without_status_is_accepted():
    assert maiui.parse_response(resp({"action": "terminate"}))[1] == {"action": "terminate"}


def test_retries_sample_at_higher_temperature():
    llm = ScriptedLLM(["garbage", resp(CLICK)])
    raw, _, args = maiui.predict(llm, "SYS", "task", [], A)
    assert args["action"] == "click"
    assert llm.overrides == [None, {"temperature": maiui.RETRY_TEMPERATURE}]
    assert llm.calls[0] is llm.calls[1]  # same prompt, only decoding changes


def test_two_malformed_answers_stop_rather_than_keep_guessing():
    llm = ScriptedLLM(["garbage", resp({"action": "fly"}), resp(CLICK)])
    with pytest.raises(maiui.ParseError):
        maiui.predict(llm, "SYS", "task", [], A)
    assert len(llm.calls) == 2


# --- safety gate for drags and Enter -----------------------------------------------------

SEND = {"text": "Send", "bounds": [900, 2100, 1060, 2260], "clickable": True}
CHAT = {"text": "", "desc": "Message", "bounds": [0, 2100, 880, 2260], "editable": True, "focused": True}
SEARCH = {"text": "", "desc": "Search settings", "bounds": [0, 100, 1080, 200], "editable": True, "focused": True}


def test_drag_starting_on_a_risky_button_asks():
    from mos import safety
    tap_like = {"type": "drag", "x1": 950, "y1": 2150, "x2": 955, "y2": 2152}
    assert safety.needs_confirmation(tap_like, [SEND]) == 'Tap "Send"'
    slide = {"type": "drag", "x1": 950, "y1": 2150, "x2": 400, "y2": 2150}
    assert safety.needs_confirmation(slide, [SEND]) == 'Drag from "Send"'
    scroll = {"type": "drag", "x1": 540, "y1": 1800, "x2": 540, "y2": 600}
    assert safety.needs_confirmation(scroll, [SEND]) is None


def test_enter_in_a_chat_box_asks_but_not_in_search():
    from mos import safety
    enter = {"type": "key", "name": "enter"}
    assert safety.needs_confirmation(enter, [CHAT, SEND]) is not None
    assert safety.needs_confirmation(enter, [SEARCH]) is None
    assert safety.needs_confirmation(enter, [SEND]) is None  # nothing focused
    assert safety.needs_confirmation({"type": "key", "name": "back"}, [CHAT]) is None


def test_remote_runner_reports_steps_and_session(tmp_path):
    c, _ = make_client(tmp_path, [resp({"action": "swipe", "direction": "up"}),
                                  resp({"action": "terminate", "status": "success"})])
    runner = RemoteRunner(FakeDevice(), post=lambda path, body: c.post(path, json=body).json(),
                          apps=APPS, log=lambda *_: None)
    assert runner.run("scroll") == "success"
    assert runner.steps == 2 and (tmp_path / runner.session_id).is_dir()


# --- eval scoring --------------------------------------------------------------------

def test_check_kinds():
    assert run_evals.passes({"expect": "2"}, " 2\n")
    assert run_evals.passes({"expect_contains": "ada"}, "Row: 0 data1=Ada Lovelace")
    assert not run_evals.passes({"expect_nonzero": True}, "0")
    assert run_evals.passes({"expect_changed": True}, "3", before="2")
    assert not run_evals.passes({"expect_changed": True}, "2", before="2")
    assert not run_evals.passes({"expect_changed": True}, "3")  # no baseline: can't claim a change


def test_task_scoring_needs_every_check_and_detects_dirty_snapshots():
    t = {"id": "c", "task": "x", "checks": [{"check": "name", "expect": "Ada"}, {"check": "phone", "expect": "1"}]}
    state = {"name": "Ada", "phone": "0"}
    shell = lambda cmd: state[cmd]
    assert not run_evals.task_passes(shell, t)
    state["phone"] = "1"
    assert run_evals.task_passes(shell, t)
    assert run_evals.already_done(t, run_evals.snapshot_outputs(shell, t))
    changed = {"id": "a", "task": "x", "check": "n", "expect_changed": True}
    assert not run_evals.already_done(changed, ["5"])


def test_task_file_is_well_formed():
    tasks = run_evals.load_tasks(str(Path(run_evals.__file__).with_name("tasks.jsonl")))
    assert len({t["id"] for t in tasks}) == len(tasks) >= 9
    for t in tasks:
        assert t["task"]
        for c in run_evals.checks_of(t):
            assert c["check"]


def test_wilson_interval():
    lo, hi = stats.wilson(7, 10)
    assert 0.39 < lo < 0.40 and 0.89 < hi < 0.90
    assert stats.wilson(0, 0) == (0.0, 0.0)


def test_summary_metrics():
    rows = [
        {"task": "a", "success": True, "self_report": True, "steps": 4},
        {"task": "a", "success": False, "self_report": True, "steps": 6},   # claimed success, wasn't
        {"task": "b", "success": True, "self_report": True, "steps": 2},
        {"task": "b", "success": True, "self_report": False, "steps": 2},
        {"task": "c", "success": False, "invalid": True},
    ]
    s = stats.summarize(rows)
    assert s["runs"] == 4 and s["invalid_runs"] == 1
    assert s["success_rate"] == 0.75 and s["pass_at_k"] == 1.0 and s["pass_hat_k"] == 0.5
    assert s["false_success_rate"] == round(1 / 3, 3) and s["avg_steps"] == 3.5
    assert s["per_task"] == {"a": "1/2", "b": "2/2", "c": "0/0"}
    assert s["k"] == 2 and s["tasks_with_k_valid_runs"] == "2/3"


def test_pass_hat_k_ignores_tasks_missing_valid_repeats():
    rows = [{"task": "a", "success": True}, {"task": "a", "success": False},
            {"task": "b", "success": True}, {"task": "b", "success": False, "invalid": True}]
    s = stats.summarize(rows)
    assert s["pass_hat_k"] == 0.0 and s["pass_at_k"] == 1.0   # only task a has both repeats


def test_malformed_checks_are_rejected():
    for bad in ({"id": "x", "checks": []}, {"id": "x", "checks": [{"check": "ls"}]},
                {"id": "x", "check": "ls", "expect": "1", "expect_nonzero": True}):
        with pytest.raises(ValueError):
            run_evals.checks_of(bad)


def test_compare_calls_only_clear_gaps():
    from evals.compare import paired_bootstrap
    a = {f"t{i}": 0.0 for i in range(10)}
    clear = paired_bootstrap(a, {f"t{i}": 1.0 for i in range(10)})
    assert clear["verdict"] == "B is better" and clear["diff"] == 1.0
    noisy = paired_bootstrap({f"t{i}": (i % 2) * 1.0 for i in range(10)}, {f"t{i}": ((i + 1) % 2) * 1.0 for i in range(10)})
    assert noisy["verdict"] == "too close to call"
    assert paired_bootstrap(a, {"other": 1.0})["tasks"] == 0


def test_compare_cli_end_to_end(tmp_path, capsys, monkeypatch):
    import sys

    from evals import compare
    for name, wins in (("a", 0), ("b", 1)):
        d = tmp_path / name
        d.mkdir()
        (d / "meta.json").write_text(json.dumps({"label": name, "screenshots": "jpeg"}))
        (d / "results.jsonl").write_text("\n".join(json.dumps({"task": f"t{i}", "success": bool(wins)}) for i in range(8)))
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path / "a"), str(tmp_path / "b")])
    compare.main()
    out = capsys.readouterr().out
    assert "b - a: +100%" in out and "B is better" in out
