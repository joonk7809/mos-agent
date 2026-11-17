"""Offline tests for the friends-and-family stack: MAI-UI policy, server, ADB client, SFT export."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from mos import maiui, safety
from mos.export_sft import session_samples
from mos.fake import FakeDevice
from mos.remote import RemoteRunner, swipe_vector
from mos.server import create_app


def resp(args: dict, thinking: str = "plan") -> str:
    return f'<thinking>\n{thinking}\n</thinking>\n<tool_call>\n{json.dumps({"name": "mobile_use", "arguments": args})}\n</tool_call>'


class ScriptedLLM:
    model = "fake-mai-ui"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


# --- MAI-UI format -------------------------------------------------------------

def test_parse_response_variants():
    t, a = maiui.parse_response(resp({"action": "click", "coordinate": [500, 250]}))
    assert t == "plan" and a == {"action": "click", "coordinate": [500.0, 250.0]}
    # thinking-model variant ends with </think>
    t, a = maiui.parse_response('look first</think><tool_call>{"name":"mobile_use","arguments":{"action":"wait"}}</tool_call>')
    assert t == "look first" and a["action"] == "wait"
    # a box becomes its center
    _, a = maiui.parse_response(resp({"action": "click", "coordinate": [100, 100, 300, 200]}))
    assert a["coordinate"] == [200.0, 150.0]
    with pytest.raises(maiui.ParseError):
        maiui.parse_response("I will click the button")
    with pytest.raises(maiui.ParseError):
        maiui.parse_response("<tool_call>{not json}</tool_call>")


def test_to_pixels_maps_0_999_to_screen():
    assert maiui.to_pixels([0, 0], 1080, 2400) == (0, 0)
    assert maiui.to_pixels([999, 999], 1080, 2400) == (1079, 2399)
    assert maiui.to_pixels([499.5, 499.5], 1080, 2400) == (540, 1200)
    assert maiui.to_pixels([2000, -5], 1080, 2400) == (1079, 0)  # clamped


def test_build_messages_keeps_last_history_images_only():
    steps = [maiui.Step(png=b"p%d" % i, thinking=f"t{i}", args={"action": "wait"}) for i in range(4)]
    steps[1].user_text = "the blue one"
    msgs = maiui.build_messages("SYS", "do it", steps, b"now", history_n=3)
    kinds = [(m["role"], m["content"][0]["type"]) for m in msgs]
    images = [m for m in msgs if m["content"][0]["type"] == "image_url"]
    assert kinds[:2] == [("system", "text"), ("user", "text")]
    assert len(images) == 3  # 2 history screenshots + current
    assert msgs[-1] is images[-1]
    assert sum(m["role"] == "assistant" for m in msgs) == 4
    assert any(m["content"][0].get("text") == "the blue one" for m in msgs)


def test_system_prompt_lists_apps():
    sp = maiui.system_prompt(["Settings", "Chrome"])
    assert '["Settings", "Chrome"]' in sp and '"action": "ask_user"' in sp


# --- safety -----------------------------------------------------------------------

ELEMENTS = [
    {"text": "", "desc": "", "bounds": [0, 0, 1080, 2400]},
    {"text": "Send", "desc": "", "bounds": [900, 2100, 1060, 2260], "clickable": True},
    {"text": "Settings", "desc": "", "bounds": [0, 100, 500, 200], "clickable": True},
    {"text": "", "desc": "Password", "bounds": [0, 300, 1080, 400], "editable": True, "focused": True, "password": True},
]


def test_safety_flags_risky_targets():
    assert safety.needs_confirmation({"type": "click", "x": 950, "y": 2150}, ELEMENTS) == 'Tap "Send"'
    assert safety.needs_confirmation({"type": "click", "x": 100, "y": 150}, ELEMENTS) is None
    assert safety.needs_confirmation({"type": "type", "text": "hunter2"}, ELEMENTS) == "Type into a password field"
    assert safety.needs_confirmation({"type": "type", "text": "4111 1111 1111 1111"}, ELEMENTS[:3])
    assert safety.needs_confirmation({"type": "swipe", "direction": "up"}, ELEMENTS) is None


# --- server -----------------------------------------------------------------------

PNG = base64.b64encode(FakeDevice().png).decode()
APPS = [{"label": "Settings", "package": "com.android.settings"}, {"label": "Messages", "package": "com.google.android.apps.messaging"}]


def make_client(tmp_path, replies):
    llm = ScriptedLLM(replies)
    app = create_app(llm=llm, run_root=tmp_path, token="tok")
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer tok"
    return c, llm


def start(c, task="open settings"):
    r = c.post("/v1/sessions", json={"task": task, "apps": APPS, "screen": {"width": 1080, "height": 2400}})
    assert r.status_code == 200
    return r.json()["session_id"]


def test_server_requires_token(tmp_path):
    c, _ = make_client(tmp_path, [])
    del c.headers["Authorization"]
    assert c.post("/v1/sessions", json={"task": "x", "screen": {"width": 1, "height": 1}}).status_code == 401
    assert c.get("/healthz").json()["ok"]


def test_server_full_session(tmp_path):
    c, llm = make_client(tmp_path, [
        resp({"action": "open", "text": "settings"}),
        resp({"action": "click", "coordinate": [499.5, 499.5]}),
        resp({"action": "swipe", "direction": "up"}),
        resp({"action": "terminate", "status": "success"}),
    ])
    sid = start(c)
    step = lambda **kw: c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG, **kw}).json()
    r = step()
    assert r["action"] == {"type": "open", "label": "settings", "package": "com.android.settings"}
    assert step()["action"] == {"type": "click", "x": 540, "y": 1200}
    assert step()["action"] == {"type": "swipe", "direction": "up"}
    r = step()
    assert r["done"] and r["status"] == "success"
    assert step()["done"]  # further steps are no-ops
    # history replayed to the model: 3 past steps, images only for the last 2 + current
    last = llm.calls[-1]
    assert sum(m["role"] == "assistant" for m in last) == 3
    assert sum(m["content"][0]["type"] == "image_url" for m in last) == 3
    d = tmp_path / sid
    assert json.loads((d / "final.json").read_text())["status"] == "success"
    assert len((d / "steps.jsonl").read_text().splitlines()) == 4


def test_server_confirmation_and_decline(tmp_path):
    c, _ = make_client(tmp_path, [resp({"action": "click", "coordinate": [906, 909]})])
    sid = start(c, "text mom hi")
    r = c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG, "elements": ELEMENTS}).json()
    assert r["confirm"] == 'Tap "Send"' and not r["done"]
    r = c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG, "declined": True}).json()
    assert r["done"] and r["status"] == "stopped"


def test_server_ask_user_reply_goes_into_history(tmp_path):
    c, llm = make_client(tmp_path, [
        resp({"action": "ask_user", "text": "Which alarm?"}),
        resp({"action": "terminate", "status": "fail"}),
    ])
    sid = start(c)
    assert c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG}).json()["action"]["type"] == "ask_user"
    r = c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG, "reply": "7:30"}).json()
    assert r["status"] == "fail"
    assert any(m["content"][0].get("text") == "7:30" for m in llm.calls[-1])


def test_server_stops_on_garbage_model_output(tmp_path):
    c, _ = make_client(tmp_path, ["no idea"] * 3)
    sid = start(c)
    r = c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG}).json()
    assert r["done"] and r["status"] == "error"


# --- ADB client through the server, and SFT export -----------------------------------

def test_remote_runner_end_to_end(tmp_path):
    c, _ = make_client(tmp_path, [
        resp({"action": "click", "coordinate": [876, 919]}),   # the "New note" FAB in the fake screen
        resp({"action": "type", "text": "buy milk"}),
        resp({"action": "system_button", "button": "back"}),
        resp({"action": "terminate", "status": "success"}),
    ])
    dev = FakeDevice()
    runner = RemoteRunner(dev, post=lambda path, body: c.post(path, json=body).json(),
                          apps=APPS, log=lambda *_: None, confirm=lambda _r: True)
    assert runner.run("add a note that says buy milk") == "success"
    assert dev.actions == [("tap", 946, 2207), ("type", "buy milk"), ("key", "back")]

    sid = next(p for p in tmp_path.iterdir() if p.is_dir())
    samples = session_samples(sid)
    assert len(samples) == 4
    s = samples[2]
    assert s["messages"][-1]["role"] == "assistant" and '"button":"back"' in s["messages"][-1]["content"]
    n_placeholders = sum(m["content"].count("<image>") for m in s["messages"])
    assert n_placeholders == len(s["images"]) == 3
    assert all(p.endswith(".png") for p in s["images"])


def test_swipe_vector_moves_finger_in_direction():
    x1, y1, x2, y2 = swipe_vector("up", 540, 1200, 1080, 2400)
    assert x1 == x2 == 540 and y2 < y1
    x1, y1, x2, y2 = swipe_vector("left", 540, 1200, 1080, 2400)
    assert x2 < x1 and y1 == y2


def test_safety_flags_permission_grants():
    # MOS ROM phones inject real input, which reaches permission dialogs accessibility can't.
    # (The bridge also refuses to touch the permission controller and package installer.)
    for label in ["Allow", "Allow all the time", "Grant access"]:
        els = [{"text": label, "bounds": [0, 0, 100, 100], "clickable": True}]
        assert safety.needs_confirmation({"type": "click", "x": 50, "y": 50}, els), label
