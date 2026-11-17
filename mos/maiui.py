"""MAI-UI policy: prompt format, response parsing and an OpenAI-compatible client.

Mirrors the official MAI-UI navigation agent (github.com/Tongyi-MAI/MAI-UI,
MAI-UI/src/mai_naivigation_agent.py and prompt.py, Apache-2.0):
- the model sees a system prompt, the task, the last `history_n` screenshots and
  its own previous responses;
- it answers with <thinking>...</thinking><tool_call>{"name": "mobile_use",
  "arguments": {...}}</tool_call>;
- coordinates are integers in a 0..999 space over the screenshot.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.request
from dataclasses import dataclass, field

SCALE = 999
RETRY_TEMPERATURE = 0.7

_SYSTEM_TEMPLATE = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
For each function call, return the thinking process in <thinking> </thinking> tags, and a json object with function name and arguments within <tool_call></tool_call> XML tags:
```
<thinking>
...
</thinking>
<tool_call>
{"name": "mobile_use", "arguments": <args-json-object>}
</tool_call>
```

## Action Space

{"action": "click", "coordinate": [x, y]}
{"action": "long_press", "coordinate": [x, y]}
{"action": "type", "text": ""}
{"action": "swipe", "direction": "up or down or left or right", "coordinate": [x, y]} # "coordinate" is optional. Use the "coordinate" if you want to swipe a specific UI element.
{"action": "open", "text": "app_name"}
{"action": "drag", "start_coordinate": [x1, y1], "end_coordinate": [x2, y2]}
{"action": "system_button", "button": "button_name"} # Options: back, home, menu, enter
{"action": "wait"}
{"action": "terminate", "status": "success or fail"}
{"action": "answer", "text": "xxx"} # Use escape characters \\', \\", and \\n in text part to ensure we can parse the text in normal python string format.
{"action": "ask_user", "text": "xxx"} # you can ask user for more information to complete the task.


## Note
- Write a small plan and finally summarize your next action (with its target element) in one sentence in <thinking></thinking> part.
- Available Apps: `__APPS__`.
You should use the `open` action to open the app as possible as you can, because it is the fast way to open the app.
- You must follow the Action Space strictly, and return the correct json object within <thinking> </thinking> and <tool_call></tool_call> XML tags.
"""


def system_prompt(app_labels: list[str]) -> str:
    return _SYSTEM_TEMPLATE.replace("__APPS__", json.dumps(app_labels, ensure_ascii=False)).strip()


class ParseError(ValueError):
    pass


# Arguments each action must carry; anything else is a malformed answer worth retrying.
REQUIRED = {
    "click": ("coordinate",), "long_press": ("coordinate",), "type": ("text",),
    "swipe": ("direction",), "open": ("text",), "drag": ("start_coordinate", "end_coordinate"),
    "system_button": ("button",), "wait": (), "terminate": (), "answer": ("text",),
    "ask_user": ("text",),
}


def parse_response(text: str) -> tuple[str, dict]:
    """Return (thinking, arguments) from a raw model response. Coordinates stay in 0..999."""
    text = text.strip()
    if "</think>" in text and "</thinking>" not in text:  # thinking-model variant
        text = "<thinking>" + text.replace("</think>", "</thinking>")
    m = re.search(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL)
    if not m:
        raise ParseError("no <tool_call> block in response")
    t = re.search(r"<thinking>(.*?)</thinking>", text, re.DOTALL)
    thinking = t.group(1).strip() if t else ""
    try:
        call = json.loads(m.group(1).strip().strip('"'))
    except json.JSONDecodeError as e:
        raise ParseError(f"invalid JSON in tool_call: {e}") from e
    args = call.get("arguments") if isinstance(call, dict) else None
    if not isinstance(args, dict) or "action" not in args:
        raise ParseError("tool_call has no arguments.action")
    kind = args["action"]
    if kind not in REQUIRED:
        raise ParseError(f"unknown action {kind!r}")
    missing = [k for k in REQUIRED[kind] if k not in args]
    if missing:
        raise ParseError(f"{kind} is missing {', '.join(missing)}")
    for key in ("coordinate", "start_coordinate", "end_coordinate"):
        if key in args:
            args[key] = _point(args[key])
    return thinking, args


def _point(c) -> list[float]:
    if not isinstance(c, (list, tuple)) or len(c) not in (2, 4):
        raise ParseError(f"bad coordinate {c!r}")
    if len(c) == 4:  # a box: use its center
        return [(c[0] + c[2]) / 2, (c[1] + c[3]) / 2]
    return [float(c[0]), float(c[1])]


def to_pixels(point: list[float], width: int, height: int) -> tuple[int, int]:
    x = min(max(point[0], 0), SCALE) / SCALE * (width - 1)
    y = min(max(point[1], 0), SCALE) / SCALE * (height - 1)
    return round(x), round(y)


def format_response(thinking: str, args: dict) -> str:
    """Re-serialize a past step the way the official agent replays history."""
    call = json.dumps({"name": "mobile_use", "arguments": args}, separators=(",", ":"), ensure_ascii=False)
    return f"<thinking>\n{thinking}\n</thinking>\n<tool_call>\n{call}\n</tool_call>"


@dataclass
class Step:
    png: bytes
    thinking: str
    args: dict
    user_text: str | None = None  # e.g. the user's reply to ask_user


def _image(png: bytes) -> dict:
    return {"role": "user", "content": [{"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}


def build_messages(system: str, task: str, steps: list[Step], current_png: bytes,
                   history_n: int = 3) -> list[dict]:
    """System, task, then each past step (screenshot only for the last history_n-1), then the current screen."""
    msgs: list[dict] = [
        {"role": "system", "content": [{"type": "text", "text": system}]},
        {"role": "user", "content": [{"type": "text", "text": task}]},
    ]
    first_with_image = max(0, len(steps) - (history_n - 1))
    for i, s in enumerate(steps):
        if i >= first_with_image:
            msgs.append(_image(s.png))
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": format_response(s.thinking, s.args)}]})
        if s.user_text:
            msgs.append({"role": "user", "content": [{"type": "text", "text": s.user_text}]})
    msgs.append(_image(current_png))
    return msgs


@dataclass
class ChatClient:
    """Minimal OpenAI-compatible /chat/completions client (vLLM, llama.cpp server, SGLang...)."""
    base_url: str = "http://localhost:8000/v1"
    model: str = "MAI-UI-8B"
    api_key: str = "EMPTY"
    timeout_s: float = 120
    params: dict = field(default_factory=lambda: {"max_tokens": 2048, "temperature": 0.0, "top_p": 1.0})

    def complete(self, messages: list[dict], overrides: dict | None = None) -> str:
        body = json.dumps({"model": self.model, "messages": messages, **self.params, **(overrides or {})}).encode()
        req = urllib.request.Request(self.base_url.rstrip("/") + "/chat/completions", data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]


def predict(client, system: str, task: str, steps: list[Step], png: bytes,
            history_n: int = 3, retries: int = 3) -> tuple[str, str, dict]:
    """Ask the model for the next action. Returns (raw_text, thinking, args).

    Decoding is greedy, so asking again with the same settings would give the same
    unparseable answer. Retries sample at a higher temperature instead.
    """
    last_err: Exception | None = None
    messages = build_messages(system, task, steps, png, history_n)
    for attempt in range(retries):
        overrides = {"temperature": RETRY_TEMPERATURE} if attempt else None
        raw = client.complete(messages, overrides) if overrides else client.complete(messages)
        try:
            thinking, args = parse_response(raw)
            return raw, thinking, args
        except ParseError as e:
            last_err = e
            time.sleep(0.2 * attempt)
    raise ParseError(f"model gave no parseable action after {retries} tries: {last_err}")
