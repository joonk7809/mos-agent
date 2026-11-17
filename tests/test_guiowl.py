"""GUI-Owl-1.5 policy: parsing, action mapping, context layout vs the official agent, server + export."""
import base64
import copy
import json

import pytest

from mos import guiowl, maiui, policies
from mos.export_sft import session_samples
from mos.fake import FakeDevice
from mos.remote import RemoteRunner
from tests.test_fnf import APPS, PNG, ScriptedLLM
from mos.server import create_app
from fastapi.testclient import TestClient


def owl(args: dict, action: str = "Tap the button") -> str:
    return f'Action: {action}\n<tool_call>\n{json.dumps({"name": "mobile_use", "arguments": args})}\n</tool_call>'


@pytest.mark.parametrize("native, canonical", [
    ({"action": "click", "coordinate": [500, 250]}, {"action": "click", "coordinate": [500.0, 250.0]}),
    ({"action": "tap", "coordinate": [1, 2]}, {"action": "click", "coordinate": [1.0, 2.0]}),
    ({"action": "long_press", "coordinate": [5, 6], "time": 2}, {"action": "long_press", "coordinate": [5.0, 6.0]}),
    ({"action": "swipe", "coordinate": [500, 800], "coordinate2": [500, 200]},
     {"action": "drag", "start_coordinate": [500.0, 800.0], "end_coordinate": [500.0, 200.0]}),
    ({"action": "system_button", "button": "Back"}, {"action": "system_button", "button": "back"}),
    ({"action": "key", "text": "KEYCODE_ENTER"}, {"action": "system_button", "button": "enter"}),
    ({"action": "type", "text": "hi"}, {"action": "type", "text": "hi"}),
    ({"action": "open", "text": "Settings"}, {"action": "open", "text": "Settings"}),
    ({"action": "wait", "time": 3}, {"action": "wait"}),
    ({"action": "terminate", "status": "failure"}, {"action": "terminate", "status": "fail"}),
    ({"action": "terminate", "status": "success"}, {"action": "terminate", "status": "success"}),
    ({"action": "answer", "text": "42"}, {"action": "answer", "text": "42"}),
])
def test_parse_maps_to_canonical_and_back(native, canonical):
    desc, args = guiowl.parse_response(owl(native))
    assert desc == "Tap the button" and args == canonical
    # replaying history reproduces an equivalent native call
    again = guiowl.parse_response(guiowl.format_response(desc, args))[1]
    assert again == canonical


@pytest.mark.parametrize("bad", [
    "no tool call here",
    owl({"action": "key", "text": "volume_up"}),
    owl({"action": "swipe", "coordinate": [1, 2]}),
    owl({"action": "system_button", "button": "Power"}),
    owl({"action": "fly"}),
])
def test_parse_rejects_what_mos_cannot_do(bad):
    with pytest.raises(maiui.ParseError):
        guiowl.parse_response(bad)


def test_parse_tolerates_missing_close_tag_and_string_arguments():
    raw = 'Action: wait\n<tool_call>\n{"name": "mobile_use", "arguments": "{\\"action\\": \\"wait\\"}"}'
    assert guiowl.parse_response(raw) == ("wait", {"action": "wait"})


# --- context layout matches the official agent ---------------------------------------
# Reference: cut_current_messages + convert_format from X-PLUG/MobileAgent
# Mobile-Agent-v3.5/android_world_v3.5/android_world/agents/gui_owl.py (Apache-2.0), lightly trimmed.

def _official_cut(messages, last_image):
    idx = [i for i, m in enumerate(messages) if m.get("role") == "user" and m.get("content")]
    for i in (idx[:-last_image] if len(idx) > last_image else []):
        messages[i]["content"] = [messages[i]["content"][0]] if i == 1 else []
    return messages


def _official_convert(goal, messages):
    new = copy.deepcopy(messages[:1])
    history = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "user" and (msg["content"] == [] or (len(msg["content"]) == 1 and msg["content"][0]["type"] == "text")):
            history.append(messages[i + 1]["content"][0]["text"].split("Action:")[-1].split("<tool_call>")[0].strip())
        if i != 1 and msg.get("role") == "user" and msg["content"] != []:
            if not history:
                new = copy.deepcopy(messages)
                new[1]["content"][0]["text"] = guiowl._instruction(goal, [])
                return new
            new.append({"role": "user", "content": [{"type": "text", "text": guiowl._instruction(goal, history)},
                                                    {"type": "image_url", "image_url": {"url": msg["content"][0]["image_url"]["url"]}}]})
            new += copy.deepcopy(messages[i + 1:])
            return new
    return copy.deepcopy(messages)


def _official_context(goal, replies, last_image=5):
    """Replay the official agent's bookkeeping for len(replies) past steps plus the current screen."""
    url = lambda i: "data:image/png;base64," + base64.b64encode(b"s%d" % i).decode()
    cur = [{"role": "system", "content": [{"type": "text", "text": guiowl.SYSTEM_PROMPT}]},
           {"role": "user", "content": [{"type": "text", "text": guiowl._instruction(goal, [])},
                                        {"type": "image_url", "image_url": {"url": url(0)}}]}]
    for i, r in enumerate(replies):
        cur.append({"role": "assistant", "content": [{"type": "text", "text": r}]})
        cur.append({"role": "user", "content": [{"type": "image_url", "image_url": {"url": url(i + 1)}}]})
    cur = _official_cut(cur, last_image)
    return _official_convert(goal, cur)


@pytest.mark.parametrize("n_past", [0, 1, 4, 5, 9])
def test_context_matches_official_agent(n_past):
    natives = [{"action": "click", "coordinate": [10 * i, 20 * i]} for i in range(n_past)]
    steps = []
    for i, nat in enumerate(natives):
        desc, args = guiowl.parse_response(owl(nat, f"do thing {i}"))
        steps.append(maiui.Step(png=b"s%d" % i, thinking=desc, args=args))
    mine = guiowl.build_messages(guiowl.SYSTEM_PROMPT, "Turn on dark theme", steps, b"s%d" % n_past)
    replies = [guiowl.format_response(s.thinking, s.args) for s in steps]
    assert mine == _official_context("Turn on dark theme", replies)


# --- server and export -------------------------------------------------------------------

def test_server_drives_device_with_guiowl_and_exports_in_its_format(tmp_path):
    llm = ScriptedLLM([
        owl({"action": "swipe", "coordinate": [500, 800], "coordinate2": [500, 200]}, "Scroll down"),
        owl({"action": "system_button", "button": "Home"}, "Go home"),
        owl({"action": "terminate", "status": "success"}, "Done"),
    ])
    c = TestClient(create_app(llm=llm, run_root=tmp_path, token="tok", policy="gui-owl"))
    c.headers["Authorization"] = "Bearer tok"
    assert c.get("/healthz").json()["policy"] == "guiowl"
    dev = FakeDevice()
    runner = RemoteRunner(dev, post=lambda path, body: c.post(path, json=body).json(), apps=APPS, log=lambda *_: None)
    assert runner.run("scroll then go home") == "success"
    assert dev.actions == [("swipe", 540, 1921, 540, 480), ("key", "home")]
    assert llm.calls[0][0]["content"][0]["text"] == guiowl.SYSTEM_PROMPT

    d = tmp_path / runner.session_id
    assert json.loads((d / "final.json").read_text())["policy"] == "guiowl"
    samples = session_samples(d)
    assert len(samples) == 3
    last = samples[2]["messages"]
    assert last[-1]["content"].startswith("Action: Done\n<tool_call>")
    assert any(m["role"] == "assistant" and '"button": "Home"' in m["content"] for m in last[:-1])
    assert sum(m["content"].count("<image>") for m in last) == len(samples[2]["images"]) == 3
    assert samples[2]["images"][-1].endswith("step_02.png")


def test_export_trains_on_hand_corrected_args(tmp_path):
    llm = ScriptedLLM([owl({"action": "click", "coordinate": [100, 100]}), owl({"action": "terminate", "status": "success"})])
    c = TestClient(create_app(llm=llm, run_root=tmp_path, token="tok", policy="guiowl"))
    c.headers["Authorization"] = "Bearer tok"
    sid = c.post("/v1/sessions", json={"task": "t", "apps": APPS, "screen": {"width": 1080, "height": 2400}}).json()["session_id"]
    for _ in range(2):
        c.post(f"/v1/sessions/{sid}/step", json={"screenshot_b64": PNG})
    log = tmp_path / sid / "steps.jsonl"
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    rows[0]["args"]["coordinate"] = [700.0, 900.0]  # the fix a person makes by hand
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    first = session_samples(tmp_path / sid)[0]["messages"][-1]["content"]
    assert '"coordinate": [700, 900]' in first


def test_emulator_screenshots_are_sent_like_the_phone_app_sends_them():
    import io
    from PIL import Image
    from mos.remote import phone_like
    img = Image.open(io.BytesIO(phone_like(FakeDevice().png)))
    assert img.format == "JPEG" and img.size == (720, 1600)


def test_unknown_policy_is_rejected():
    assert policies.get(None) is maiui and policies.get("GUI_Owl") is guiowl
    with pytest.raises(ValueError):
        policies.get("gpt")
