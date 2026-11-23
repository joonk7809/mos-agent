"""Dot (local Mac agent): loop, tools, daemon protocol and coordinate mapping, with fakes (no Mac needed)."""
from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dot import daemon, loop, model, stats
from dot.desktop import Element, Shot, key_applescript, to_points, utf16_chunks
from dot.model import Reply, ToolCall, parse_message
from dot.tools import SCHEMAS, Tools


def call(name: str, i: int = 0, **args: Any) -> ToolCall:
    return ToolCall(id=f"c{i}", name=name, arguments=args, raw_arguments=json.dumps(args))


def reply(*calls: ToolCall, content: str = "") -> Reply:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name,
                                                                          "arguments": c.raw_arguments}}
                             for c in calls]
    return Reply(content=content, tool_calls=list(calls), message=msg)


class ScriptedModel:
    def __init__(self, replies: list[Reply], on_call: Any = None):
        self.replies = list(replies)
        self.seen: list[list[dict[str, Any]]] = []
        self.on_call = on_call

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply:
        self.seen.append(json.loads(json.dumps(messages)))
        if self.on_call:
            self.on_call(len(self.seen))
        return self.replies.pop(0)


class FakeDesktop:
    def __init__(self) -> None:
        self.actions: list[tuple[Any, ...]] = []
        self.els = [Element("Button", "Send", 100, 200, 40, 20), Element("TextField", "To", 10, 10, 200, 20)]

    def screenshot(self, window: bool = False) -> Shot:
        self.actions.append(("screenshot", window))
        return Shot(b"\x89PNG", 640, 400, 1280, 800, origin_x=100 if window else 0, origin_y=50 if window else 0)

    def elements(self, limit: int = 150, filter: str = "") -> list[Element]:
        return [e for e in self.els if filter.lower() in (e.title + " " + e.role).lower()]

    def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None:
        self.actions.append(("click", x, y, button, count))

    def move(self, x: float, y: float) -> None:
        self.actions.append(("move", x, y))

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.actions.append(("drag", x1, y1, x2, y2))

    def scroll(self, x: float, y: float, dy: int, dx: int = 0) -> None:
        self.actions.append(("scroll", x, y, dy, dx))

    def type_text(self, text: str) -> None:
        self.actions.append(("type", text))

    def key(self, combo: str) -> None:
        self.actions.append(("key", combo))


def fake_run(*a: Any, **k: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(a[0], 0, stdout="ok\n", stderr="")


def agent(replies: list[Reply], tmp_path: Any, desk: FakeDesktop | None = None, **kw: Any) -> tuple[loop.Agent, Any]:
    m = ScriptedModel(replies, kw.pop("on_call", None))
    a = loop.Agent(m, Tools(desk or FakeDesktop(), run=fake_run), loop.ActionLog(str(tmp_path)), **kw)
    return a, m


def log_records(tmp_path: Any) -> list[dict[str, Any]]:
    out = []
    for p in tmp_path.glob("*.jsonl"):
        out += [json.loads(line) for line in p.read_text().splitlines()]
    return out


# --- loop

def test_answers_without_tools(tmp_path: Any) -> None:
    a, _ = agent([reply(content="42")], tmp_path)
    out = a.run("what is 6*7")
    assert (out.status, out.text, out.turns) == ("answered", "42", 1)


def test_runs_tools_then_answers_and_feeds_results_back(tmp_path: Any) -> None:
    a, m = agent([reply(call("run_shell", command="ls")), reply(content="done")], tmp_path)
    out = a.run("list home", context={"app": "Finder"})
    assert out.status == "answered"
    tool_msgs = [x for x in m.seen[1] if x["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c0" and "exit 0" in tool_msgs[0]["content"]
    assert "app: Finder" in m.seen[0][1]["content"]


def test_several_actions_in_one_reply_run_in_order(tmp_path: Any) -> None:
    d = FakeDesktop()
    a, _ = agent([reply(call("screenshot", 0), call("click", 1, x=320, y=200), call("type_text", 2, text="hi"),
                        call("key", 3, combo="enter")), reply(content="ok")], tmp_path, d)
    a.run("t")
    assert [x[0] for x in d.actions] == ["screenshot", "click", "type", "key"]


def test_stop_halts_before_next_action(tmp_path: Any) -> None:
    d = FakeDesktop()
    holder: dict[str, loop.Agent] = {}
    a, _ = agent([reply(call("click", 0, x=1, y=1)), reply(content="never")], tmp_path, d,
                 on_call=lambda n: holder["a"].stop.set())
    holder["a"] = a
    a.tools.last_shot = Shot(b"", 10, 10, 10, 10)
    out = a.run("t")
    assert out.status == "stopped" and d.actions == []


def test_stop_between_calls_in_one_reply(tmp_path: Any) -> None:
    d = FakeDesktop()
    a, _ = agent([reply(call("screenshot", 0), call("key", 1, combo="enter"))], tmp_path, d)
    orig = a.tools.call

    def stopping(name: str, args: dict[str, Any]) -> Any:
        r = orig(name, args)
        a.stop.set()
        return r
    a.tools.call = stopping  # type: ignore[method-assign]
    assert a.run("t").status == "stopped"
    assert [x[0] for x in d.actions] == ["screenshot"]


def test_repeat_guard(tmp_path: Any) -> None:
    a, _ = agent([reply(call("run_shell", i, command="ls")) for i in range(5)], tmp_path)
    assert a.run("t").status == "stuck"


def test_same_action_several_times_in_one_reply_is_fine(tmp_path: Any) -> None:
    d = FakeDesktop()
    a, _ = agent([reply(*[call("key", i, combo="tab") for i in range(4)]), reply(content="ok")], tmp_path, d)
    assert a.run("t").status == "answered"
    assert d.actions == [("key", "tab")] * 4


def test_any_failure_ends_with_done_event(tmp_path: Any) -> None:
    class BadLog(loop.ActionLog):
        def write(self, *a: Any, **k: Any) -> None:
            raise OSError("disk full")
    evs: list[dict[str, Any]] = []
    a = loop.Agent(ScriptedModel([reply(content="x")]), Tools(None, run=fake_run), BadLog(str(tmp_path)))
    out = a.run("t", on_event=evs.append)
    assert out.status == "error" and "disk full" in out.text
    assert evs[-1]["type"] == "done" and evs[-1]["status"] == "error"


def test_max_turns(tmp_path: Any) -> None:
    a, _ = agent([reply(call("run_shell", i, command=f"echo {i}")) for i in range(3)], tmp_path, max_turns=3)
    assert a.run("t").status == "max_turns"


def test_model_failure_ends_task(tmp_path: Any) -> None:
    class Boom:
        def chat(self, *a: Any) -> Reply:
            raise ConnectionError("down")
    a = loop.Agent(Boom(), Tools(None, run=fake_run), loop.ActionLog(str(tmp_path)))
    out = a.run("t")
    assert out.status == "error" and "down" in out.text


def test_bad_tool_json_goes_back_as_error_not_run(tmp_path: Any) -> None:
    bad = ToolCall(id="c0", name="run_shell", arguments={}, raw_arguments="{oops", parse_error="not JSON")
    ran: list[Any] = []
    a, m = agent([reply(bad), reply(content="ok")], tmp_path)
    a.tools.run = lambda *x, **k: ran.append(x)  # type: ignore[assignment,misc]
    a.run("t")
    assert ran == [] and "not JSON" in [x for x in m.seen[1] if x["role"] == "tool"][0]["content"]


def test_screenshot_attached_as_image_and_pruned_in_batches(tmp_path: Any) -> None:
    n = loop.PRUNE_AT + 1
    a, m = agent([reply(call("screenshot", i, whole=bool(i % 2))) for i in range(n)] + [reply(content="ok")],
                 tmp_path)
    a.run("t")
    counts = [loop._count_images(msgs) for msgs in m.seen]
    assert counts[1] == 1 and counts[2] == 2
    # Append-only until the batch prune, then down to KEEP_SCREENSHOTS.
    assert max(counts) == loop.PRUNE_AT - 1
    assert loop.KEEP_SCREENSHOTS in counts[loop.PRUNE_AT:]
    # Between prunes, each request extends the previous one (prefix cache reuse).
    assert m.seen[2][:len(m.seen[1])] == m.seen[1]


def test_action_log_has_timings(tmp_path: Any) -> None:
    a, _ = agent([reply(call("run_shell", command="ls")), reply(content="done")], tmp_path)
    a.run("t")
    recs = log_records(tmp_path)
    kinds = [r["kind"] for r in recs]
    assert kinds == ["task", "model", "tool", "model", "end"]
    assert all("seconds" in r for r in recs if r["kind"] in ("model", "tool"))
    s = stats.summarize(recs)
    assert s["steps"] == 2 and s["tasks"] == 1 and s["median_step_s"] is not None


# --- tools

def test_unknown_tool_and_bad_args() -> None:
    t = Tools(FakeDesktop(), run=fake_run)
    assert t.call("rm_rf", {}).error
    assert t.call("run_shell", {"nope": 1}).error


def test_click_needs_a_screenshot_first() -> None:
    t = Tools(FakeDesktop(), run=fake_run)
    assert t.call("click", {"x": 1, "y": 1}).error


def test_click_maps_window_screenshot_pixels_to_screen_points() -> None:
    d = FakeDesktop()
    t = Tools(d, run=fake_run)
    t.call("screenshot", {})
    t.call("click", {"x": 320, "y": 200})
    assert d.actions[-1][:3] == ("click", 100 + 640.0, 50 + 400.0)


def test_ui_elements_and_click_element() -> None:
    d = FakeDesktop()
    t = Tools(d, run=fake_run)
    r = t.call("ui_elements", {"filter": "send"})
    assert '[0] Button "Send"' in r.text
    t.call("click_element", {"id": 0})
    assert d.actions[-1][:3] == ("click", 120.0, 210.0)
    assert t.call("click_element", {"id": 9}).error


def test_files_roundtrip(tmp_path: Any) -> None:
    t = Tools(None, run=fake_run)
    p = str(tmp_path / "a" / "b.txt")
    assert not t.call("write_file", {"path": p, "content": "hello"}).error
    assert t.call("read_file", {"path": p}).text == "hello"
    assert "b.txt" in t.call("list_dir", {"path": str(tmp_path / "a")}).text


def test_screen_tools_without_desktop_fail_cleanly() -> None:
    r = Tools(None, run=fake_run).call("screenshot", {})
    assert r.error and "unavailable" in r.text


def test_web_fetch_rejects_non_http() -> None:
    assert Tools(None, run=fake_run).call("web_fetch", {"url": "file:///etc/passwd"}).error


def test_schemas_are_well_formed() -> None:
    names = [s["function"]["name"] for s in SCHEMAS]
    assert len(names) == len(set(names))
    for s in SCHEMAS:
        assert hasattr(Tools, "t_" + s["function"]["name"])
        params = s["function"]["parameters"]
        assert set(params["required"]) <= set(params["properties"])


# --- coordinates and keys

@given(st.floats(-1e6, 1e6), st.floats(-1e6, 1e6), st.integers(1, 5000), st.integers(1, 5000),
       st.sampled_from(["pixels", "norm1000"]))
def test_to_points_always_lands_inside_the_captured_area(x: float, y: float, w: int, h: int, space: str) -> None:
    shot = Shot(b"", w, h, 1512.0, 982.0, origin_x=10.0, origin_y=20.0)
    px, py = to_points(x, y, shot, space)
    assert 10.0 <= px <= 1522.0 and 20.0 <= py <= 1002.0


def test_nan_coordinates_rejected() -> None:
    with pytest.raises(ValueError):
        to_points(float("nan"), 1, Shot(b"", 10, 10, 10, 10))
    t = Tools(FakeDesktop(), run=fake_run)
    t.call("screenshot", {})
    assert t.call("click", {"x": float("inf"), "y": 1}).error


@given(st.text(max_size=60))
def test_utf16_chunks_roundtrip_and_fit(text: str) -> None:
    chunks = utf16_chunks(text)
    assert "".join(chunks) == text
    assert all(0 < len(c.encode("utf-16-le")) // 2 <= 16 for c in chunks)


def test_key_applescript() -> None:
    assert key_applescript("+") == 'tell application "System Events" to keystroke "+"'
    assert parse_message({"tool_calls": [{"function": {"name": "screenshot", "arguments": None}}]}
                         ).tool_calls[0].parse_error is None
    assert key_applescript("cmd++") == 'tell application "System Events" to keystroke "+" using {command down}'
    assert key_applescript("cmd+shift+t") == \
        'tell application "System Events" to keystroke "t" using {command down, shift down}'
    assert key_applescript("enter") == 'tell application "System Events" to key code 36'
    assert key_applescript('"') == 'tell application "System Events" to keystroke "\\""'
    for bad in ["", "hyper+x", "notakey"]:
        with pytest.raises(ValueError):
            key_applescript(bad)


# --- model parsing

def test_parse_message_handles_dict_and_bad_arguments() -> None:
    r = parse_message({"content": None, "tool_calls": [
        {"id": "a", "function": {"name": "click", "arguments": {"x": 1, "y": 2}}},
        {"function": {"name": "key", "arguments": "{bad"}},
        {"id": "c", "function": {"name": "key", "arguments": "[1]"}},
        {"id": "d", "function": {"name": "key", "arguments": [1]}}]})
    assert r.tool_calls[0].arguments == {"x": 1, "y": 2} and r.tool_calls[0].parse_error is None
    assert r.tool_calls[1].parse_error and r.tool_calls[1].id == "call_1"
    assert r.tool_calls[2].parse_error and r.tool_calls[3].parse_error
    assert len(r.message["tool_calls"]) == 4
    # Malformed arguments are not echoed back into the history (servers would reject the next request).
    assert r.message["tool_calls"][1]["function"]["arguments"] == "{}"


@given(st.dictionaries(st.text(max_size=5), st.one_of(st.none(), st.text(max_size=5), st.integers(),
                                                      st.lists(st.integers(), max_size=2)), max_size=4))
def test_parse_message_never_raises(msg: dict[str, Any]) -> None:
    parse_message(msg)


def test_model_client_sends_reasoning_and_cap(monkeypatch: Any) -> None:
    sent: dict[str, Any] = {}

    class Resp(io.BytesIO):
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

    def fake_urlopen(req: Any, timeout: float) -> Resp:
        sent["body"] = json.loads(req.data)
        sent["url"] = req.full_url
        return Resp(json.dumps({"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 7}}).encode())

    monkeypatch.delenv("DOT_REASONING", raising=False)
    monkeypatch.setattr(model.urllib.request, "urlopen", fake_urlopen)
    r = model.OpenAICompatModel(base_url="http://127.0.0.1:11434/v1").chat([{"role": "user", "content": "x"}], SCHEMAS)
    assert r.content == "hi" and r.usage == {"prompt_tokens": 7}
    assert sent["url"].endswith("/chat/completions")
    assert sent["body"]["reasoning_effort"] == "low" and sent["body"]["max_tokens"] == 2048


# --- daemon protocol

def test_daemon_protocol(tmp_path: Any) -> None:
    gate = threading.Event()

    def slow(n: int) -> None:
        gate.wait(5)
    a, _ = agent([reply(content="answer")], tmp_path, on_call=slow)
    out = io.StringIO()
    d = daemon.Daemon(a, out)
    assert d.handle("not json") and d.handle('{"type":"ask","text":""}') and d.handle('{"type":"wat"}')
    d.handle('{"type":"ask","text":"hi","context":{"app":"Mail"}}')
    d.handle('{"type":"ask","text":"again"}')  # busy
    gate.set()
    assert d.worker is not None
    d.worker.join(5)
    assert d.handle('{"type":"quit"}') is False
    evs = [json.loads(line) for line in out.getvalue().splitlines()]
    errors = [e["text"] for e in evs if e["type"] == "error"]
    assert errors[:3] == ["bad message", "empty request", "unknown type 'wat'"]
    assert any("busy" in e for e in errors)
    assert evs[-1] == {"type": "done", "status": "answered", "text": "answer"}


def test_daemon_stop_message_sets_stop(tmp_path: Any) -> None:
    a, _ = agent([], tmp_path)
    d = daemon.Daemon(a, io.StringIO())
    d.handle('{"type":"stop"}')
    assert a.stop.is_set()


def test_daemon_stdin_eof_stops_agent(tmp_path: Any) -> None:
    started = threading.Event()

    def slow(n: int) -> None:
        started.set()
        time.sleep(0.3)
    d = FakeDesktop()
    a, _ = agent([reply(call("key", 0, combo="enter")), reply(content="x")], tmp_path, d, on_call=slow)
    dm = daemon.Daemon(a, io.StringIO())
    dm.serve(io.StringIO('{"type":"ask","text":"go"}\n'))  # EOF right after the ask
    assert a.stop.is_set()
    assert dm.worker is not None
    dm.worker.join(5)
    assert d.actions == []  # the pending key press never ran


# --- model server errors say what to do

class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None


def _http_error(code: int, body: str) -> Exception:
    import urllib.error
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))  # type: ignore[arg-type]


def _fake_server(monkeypatch: Any, handler: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_urlopen(req: Any, timeout: float) -> _Resp:
        body = json.loads(req.data) if req.data else None
        calls.append({"url": req.full_url, "body": body})
        return _Resp(json.dumps(handler(req.full_url, body)).encode())
    monkeypatch.setattr(model.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_missing_model_names_what_exists(monkeypatch: Any) -> None:
    def h(url: str, body: Any) -> Any:
        if url.endswith("/models"):
            return {"data": [{"id": "qwen3:8b"}]}
        raise _http_error(404, '{"error":"model \\"inkling-2-mini\\" not found"}')
    _fake_server(monkeypatch, h)
    m = model.OpenAICompatModel(base_url="http://127.0.0.1:11434/v1", model="inkling-2-mini")
    with pytest.raises(model.ModelError) as e:
        m.chat([{"role": "user", "content": "x"}], [])
    assert "not found" in str(e.value) and "qwen3:8b" in str(e.value) and "ollama pull inkling-2-mini" in str(e.value)
    assert m.check() is not None and "qwen3:8b" in str(m.check())


def test_no_server_says_start_it(monkeypatch: Any) -> None:
    import urllib.error

    def h(url: str, body: Any) -> Any:
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    _fake_server(monkeypatch, h)
    m = model.OpenAICompatModel(base_url="http://127.0.0.1:11434/v1")
    with pytest.raises(model.ModelError) as e:
        m.chat([{"role": "user", "content": "x"}], [])
    assert "No model server answering at http://127.0.0.1:11434/v1" in str(e.value) and "ollama serve" in str(e.value)
    assert "ollama serve" in str(m.check())


def test_reasoning_rejected_is_dropped_and_retried(monkeypatch: Any) -> None:
    def h(url: str, body: Any) -> Any:
        if "reasoning_effort" in body:
            raise _http_error(400, '{"error":"model does not support thinking"}')
        return {"choices": [{"message": {"content": "ok"}}]}
    calls = _fake_server(monkeypatch, h)
    monkeypatch.delenv("DOT_REASONING", raising=False)
    m = model.OpenAICompatModel(base_url="http://h/v1")
    assert m.chat([{"role": "user", "content": "x"}], []).content == "ok"
    assert m.chat([{"role": "user", "content": "x"}], []).content == "ok"
    assert len(calls) == 3 and m.reasoning == ""  # retried once, then never sent again


def test_check_ok_with_ollama_tag(monkeypatch: Any) -> None:
    _fake_server(monkeypatch, lambda url, body: {"data": [{"id": "inkling-2-mini:latest"}]})
    assert model.OpenAICompatModel(base_url="http://h/v1", model="inkling-2-mini").check() is None


def test_model_error_text_reaches_the_user(tmp_path: Any) -> None:
    class Down:
        def chat(self, *a: Any) -> Reply:
            raise model.ModelError("No model server answering at X. Start it.")
    out = loop.Agent(Down(), Tools(None, run=fake_run), loop.ActionLog(str(tmp_path))).run("t")
    assert out.text == "No model server answering at X. Start it."


def test_check_survives_odd_models_replies(monkeypatch: Any) -> None:
    for reply_body in ([], "x", {"data": None}, {"data": [1, None]}):
        _fake_server(monkeypatch, lambda url, body, r=reply_body: r)
        assert model.OpenAICompatModel(base_url="http://h/v1").check() is None


def test_check_ignores_missing_models_route(monkeypatch: Any) -> None:
    def h(url: str, body: Any) -> Any:
        raise _http_error(404, "404 page not found")
    _fake_server(monkeypatch, h)
    assert model.OpenAICompatModel(base_url="http://h/v1").check() is None


def test_default_model_is_inkling_4bit_and_never_swapped(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.delenv("DOT_MODEL", raising=False)
    calls = _fake_server(monkeypatch, lambda url, body: {"model": body["model"],
                                                         "choices": [{"message": {"content": "hi"}}]})
    m = model.OpenAICompatModel(base_url="http://h/v1")
    assert m.model == model.DEFAULT_MODEL == "hf.co/ggml-org/Inkling-2-Mini-GGUF:IQ4_NL"
    a = loop.Agent(m, Tools(None, run=fake_run), loop.ActionLog(str(tmp_path)))
    a.run("hello")
    assert [c["body"]["model"] for c in calls] == [model.DEFAULT_MODEL]
    rec = [r for r in log_records(tmp_path) if r["kind"] == "model"][0]
    assert rec["served_model"] == model.DEFAULT_MODEL


def test_check_flags_missing_inkling_even_if_other_models_exist(monkeypatch: Any) -> None:
    _fake_server(monkeypatch, lambda url, body: {"data": [{"id": "qwen3:8b"}, {"id": "llama3:8b"}]})
    problem = model.OpenAICompatModel(base_url="http://h/v1", model=model.DEFAULT_MODEL).check()
    assert problem is not None and model.DEFAULT_MODEL in problem and "qwen3:8b" in problem
