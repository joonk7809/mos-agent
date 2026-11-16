import io
import json

import anthropic
import httpx2 as httpx
from PIL import Image

from mos import uitree
from mos.agent import Agent
from mos.device import AdbDevice, escape_input_text
from mos.fake import SAMPLE_XML, FakeDevice
from mos.observe import observe


def test_parse_keeps_actionable_nodes_only():
    els = uitree.parse(SAMPLE_XML)
    labels = [e.text or e.desc for e in els]
    assert labels == ["Notes", "", "Groceries", "Pin note", "Search notes", "New note"]
    fab = els[-1]
    assert fab.center == (946, 2206) and fab.clickable
    assert els[3].checked is False
    assert "EditText" in els[4].cls and els[4].editable
    assert "[5] ImageButton \"New note\" @(946,2206) click" in uitree.render(els)


def test_parse_bad_xml_is_empty():
    assert uitree.parse("") == [] and uitree.parse("<oops") == []


def test_escape_input_text():
    assert escape_input_text("buy milk & eggs") == "buy%smilk%s\\&%seggs"
    assert escape_input_text("it's $5") == "it\\'s%s\\$5"


def test_adb_device_builds_commands():
    calls = []
    dev = AdbDevice(serial="emulator-5554", settle_s=0, runner=lambda c: calls.append(c) or b"")
    dev.tap(10, 20)
    dev.type_text("hi there\nok")
    assert calls[0] == ["adb", "-s", "emulator-5554", "shell", "input tap 10 20"]
    assert calls[1][-1] == "input text hi%sthere"
    assert calls[2][-1] == "input keyevent KEYCODE_ENTER"
    assert calls[3][-1] == "input text ok"


def test_observe_resizes_and_maps_coordinates():
    obs = observe(FakeDevice(), "screenshot")
    img = Image.open(io.BytesIO(obs.png))
    assert max(img.size) == 1568
    x, y = obs.to_device(img.width / 2, img.height / 2)
    assert abs(x - 540) <= 1 and abs(y - 1200) <= 1


def test_som_mode_includes_tree_and_image():
    blocks = observe(FakeDevice(), "som").content_blocks("som")
    assert blocks[0]["type"] == "image" and "New note" in blocks[1]["text"]


# --- full loop against a mocked API (exercises real SDK request building) ---

def _msg(content, stop="tool_use"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20}}


def _tool_use(i, name, inp):
    return {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": inp}


def test_agent_loop_end_to_end():
    script = [
        _msg([_tool_use(1, "tap", {"element_id": 5, "reason": "new note"})]),
        _msg([_tool_use(2, "type_text", {"text": "buy milk", "reason": "content"})]),
        _msg([_tool_use(3, "tap", {"element_id": 1, "irreversible": True, "reason": "save"})]),
        _msg([_tool_use(4, "finish", {"success": True, "summary": "note added", "reason": "done"})]),
    ]
    requests = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        requests.append(body)
        return httpx.Response(200, json=script[len(requests) - 1])

    client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    dev = FakeDevice()
    asked = []
    agent = Agent(dev, mode="both", client=client, confirm=lambda m: asked.append(m) or True,
                  log=lambda *_: None, run_root=__import__("pathlib").Path("/tmp/mos-test-runs"))
    r = agent.run("add a note that says buy milk")

    assert r.success and r.steps == 4 and r.summary == "note added"
    assert dev.actions == [("tap", 946, 2206), ("type", "buy milk"), ("tap", 540, 1170)]
    assert len(asked) == 1  # only the irreversible tap was gated
    first = requests[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "medium"}
    # history is append-only and each tool result carries the new screenshot
    last = requests[-1]["messages"]
    assert [m["role"] for m in last] == ["user", "assistant", "user", "assistant", "user", "assistant", "user"]
    assert last[-1]["content"][0]["content"][1]["type"] == "image"
    assert (r.run_dir / "trajectory.jsonl").exists()


def test_declined_action_is_reported_not_executed():
    script = [
        _msg([_tool_use(1, "press_key", {"key": "enter", "irreversible": True, "reason": "send"})]),
        _msg([_tool_use(2, "finish", {"success": False, "summary": "user declined", "reason": "x"})]),
    ]
    requests = []

    def handler(req):
        requests.append(json.loads(req.content))
        return httpx.Response(200, json=script[len(requests) - 1])

    client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    dev = FakeDevice()
    Agent(dev, client=client, confirm=lambda m: False, log=lambda *_: None,
          run_root=__import__("pathlib").Path("/tmp/mos-test-runs")).run("send it")
    assert dev.actions == []
    result = requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "declined" in result["content"][0]["text"]
