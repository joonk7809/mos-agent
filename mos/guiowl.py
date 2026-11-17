"""GUI-Owl-1.5 policy: prompt, history and action format, mapped onto MOS's actions.

Mirrors the official GUI-Owl-1.5 AndroidWorld agent (github.com/X-PLUG/MobileAgent,
Mobile-Agent-v3.5/android_world_v3.5/android_world/agents/gui_owl.py, Apache-2.0):
- system prompt is a `mobile_use` tool definition; the model answers
  "Action: <short imperative>" then <tool_call>{"name": "mobile_use", "arguments": {...}}</tool_call>;
- coordinates are 0..999 over the screenshot ("qwen-vl" format in their code), the same
  space MAI-UI uses, so mos.maiui.to_pixels applies unchanged;
- context: the last `history_n` screenshots (5 in the official runner) each followed by the
  model's reply; older steps are folded into a "Previous actions: Step1: ..." text list.

Its actions are translated to the canonical (MAI-UI-style) args the server, safety
check and loop guard already understand, and back again when history is replayed:
    swipe{coordinate, coordinate2}  <->  drag{start_coordinate, end_coordinate}
    system_button{"Back"}           <->  system_button{"back"}
    terminate{"failure"}            <->  terminate{"fail"}
    key{text: back|home|enter|menu} ->   system_button (other keys are rejected)
GUI-Owl has no ask_user action; a reply the user typed is still replayed as a user turn.
"""
from __future__ import annotations

import json
import re

from .maiui import ParseError, Step, _image, _point

NAME = "guiowl"
HISTORY_N = 5

SYSTEM_PROMPT = '''# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{"type": "function", "function": {"name_for_human": "mobile_use", "name": "mobile_use", "description": "Use a touchscreen to interact with a mobile device, and take screenshots.
* This is an interface to a mobile device with touchscreen. You can perform actions like clicking, typing, swiping, etc.
* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions.
* The screen's resolution is 1000x1000.
* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges unless asked.", "parameters": {"properties": {"action": {"description": "The action to perform. The available actions are:
* `key`: Perform a key event on the mobile device.
    - This supports adb's `keyevent` syntax.
    - Examples: "volume_up", "volume_down", "power", "camera", "clear".
* `click`: Click the point on the screen with coordinate (x, y).
* `long_press`: Press the point on the screen with coordinate (x, y) for specified seconds.
* `swipe`: Swipe from the starting point with coordinate (x, y) to the end point with coordinates2 (x2, y2).
* `type`: Input the specified text into the activated input box.
* `system_button`: Press the system button.
* `open`: Open an app on the device.
* `wait`: Wait specified seconds for the change to happen.
* `answer`: Terminate the current task and output the answer.
* `terminate`: Terminate the current task and report its completion status.", "enum": ["key", "click", "long_press", "swipe", "type", "system_button", "open", "wait", "answer", "terminate"], "type": "string"}, "coordinate": {"description": "(x, y): The x (pixels from the left edge) and y (pixels from the top edge) coordinates to move the mouse to. Required only by `action=click`, `action=long_press`, and `action=swipe`.", "type": "array"}, "coordinate2": {"description": "(x, y): The x (pixels from the left edge) and y (pixels from the top edge) coordinates to move the mouse to. Required only by `action=swipe`.", "type": "array"}, "text": {"description": "Required only by `action=key`, `action=type`, `action=open`, `action=answer`.", "type": "string"}, "time": {"description": "The seconds to wait. Required only by `action=long_press` and `action=wait`.", "type": "number"}, "button": {"description": "Back means returning to the previous interface, Home means returning to the desktop, Menu means opening the application background menu, and Enter means pressing the enter. Required only by `action=system_button`", "enum": ["Back", "Home", "Menu", "Enter"], "type": "string"}, "status": {"description": "The status of the task. Required only by `action=terminate`.", "type": "string", "enum": ["success", "failure"]}}, "required": ["action"], "type": "object"}, "args_format": "Format the arguments as a JSON object."}}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{"name": <function-name>, "arguments": <args-json-object>}
</tool_call>

# Response format

Response format for every step:
1) Action: a short imperative describing what to do in the UI.
2) A single <tool_call>...</tool_call> block containing only the JSON: {"name": <function-name>, "arguments": <args-json-object>}.

Rules:
- Output exactly in the order: Action, <tool_call>.
- Be brief: one for Action.
- Do not output anything else outside those two parts.
- If finishing, use action=terminate in the tool call.'''

BUTTONS = {"back", "home", "menu", "enter"}


def system_prompt(app_labels: list[str]) -> str:
    return SYSTEM_PROMPT  # the official prompt has no app list; `open` takes the app's name


def _instruction(task: str, history: list[str]) -> str:
    prev = "\n".join(f"Step{i + 1}: {h}" for i, h in enumerate(history)) or "No previous action."
    return ("Please generate the next move according to the UI screenshot, instruction and previous actions.\n\n"
            f"Instruction: {task}\n\nPrevious actions:\n{prev}")


def to_canonical(native: dict) -> dict:
    kind = str(native.get("action", "")).replace("tap", "click")
    if kind in ("click", "long_press"):
        if "coordinate" not in native:
            raise ParseError(f"{kind} is missing coordinate")
        return {"action": kind, "coordinate": _point(native["coordinate"])}
    if kind == "swipe":
        if "coordinate" not in native or "coordinate2" not in native:
            raise ParseError("swipe needs coordinate and coordinate2")
        return {"action": "drag", "start_coordinate": _point(native["coordinate"]),
                "end_coordinate": _point(native["coordinate2"])}
    if kind in ("type", "open", "answer"):
        if "text" not in native:
            raise ParseError(f"{kind} is missing text")
        return {"action": kind, "text": str(native["text"])}
    if kind == "system_button":
        button = str(native.get("button", "")).lower()
        if button not in BUTTONS:
            raise ParseError(f"unknown system button {native.get('button')!r}")
        return {"action": "system_button", "button": button}
    if kind == "key":
        key = str(native.get("text", "")).lower().removeprefix("keycode_")
        if key not in BUTTONS:
            raise ParseError(f"unsupported key {native.get('text')!r}")
        return {"action": "system_button", "button": key}
    if kind == "wait":
        return {"action": "wait"}
    if kind == "terminate":
        status = str(native.get("status", "failure")).lower()
        return {"action": "terminate", "status": "success" if status == "success" else "fail"}
    raise ParseError(f"unknown action {kind!r}")


def to_native(args: dict) -> dict:
    kind = args.get("action")
    rnd = lambda p: [round(v) for v in p]
    if kind in ("click", "long_press"):
        return {"action": kind, "coordinate": rnd(args["coordinate"])}
    if kind == "drag":
        return {"action": "swipe", "coordinate": rnd(args["start_coordinate"]),
                "coordinate2": rnd(args["end_coordinate"])}
    if kind == "system_button":
        return {"action": "system_button", "button": str(args.get("button", "back")).capitalize()}
    if kind == "terminate":
        return {"action": "terminate", "status": "success" if args.get("status") == "success" else "failure"}
    if kind == "wait":
        return {"action": "wait", "time": 2}
    return {k: v for k, v in args.items()}  # type, open, answer (and anything else) as-is


def parse_response(text: str) -> tuple[str, dict]:
    """Return (action_description, canonical args)."""
    m = re.search(r"<tool_call>(.*?)(?:</tool_call>|$)", text, re.DOTALL)
    if not m:
        raise ParseError("no <tool_call> block in response")
    before = text[:m.start()]
    desc = before.split("Action:", 1)[-1].strip() if "Action:" in before else before.strip()
    try:
        call = json.loads(m.group(1).strip())
    except json.JSONDecodeError as e:
        raise ParseError(f"invalid JSON in tool_call: {e}") from e
    native = call.get("arguments") if isinstance(call, dict) else None
    if isinstance(native, str):
        try:
            native = json.loads(native)
        except json.JSONDecodeError as e:
            raise ParseError(f"invalid arguments JSON: {e}") from e
    if not isinstance(native, dict) or "action" not in native:
        raise ParseError("tool_call has no arguments.action")
    return desc, to_canonical(native)


def format_response(thinking: str, args: dict) -> str:
    call = json.dumps({"name": "mobile_use", "arguments": to_native(args)}, ensure_ascii=False)
    return f"Action: {thinking}\n<tool_call>\n{call}\n</tool_call>"


def build_messages(system: str, task: str, steps: list[Step], current_png: bytes,
                   history_n: int = HISTORY_N) -> list[dict]:
    """System, then the oldest kept screenshot with the instruction and a text list of older
    actions, then (reply, screenshot) pairs up to the current screen. Keeps history_n screenshots."""
    first = max(0, len(steps) + 1 - history_n)  # index of the oldest step whose screenshot is kept
    older = [s.thinking or json.dumps(to_native(s.args), ensure_ascii=False) for s in steps[:first]]
    pngs = [s.png for s in steps[first:]] + [current_png]
    msgs: list[dict] = [{"role": "system", "content": [{"type": "text", "text": system}]}]
    head = _image(pngs[0])
    head["content"].insert(0, {"type": "text", "text": _instruction(task, older)})
    msgs.append(head)
    for s, png in zip(steps[first:], pngs[1:]):
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": format_response(s.thinking, s.args)}]})
        if s.user_text:
            msgs.append({"role": "user", "content": [{"type": "text", "text": s.user_text}]})
        msgs.append(_image(png))
    return msgs
