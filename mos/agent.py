"""The agent loop: observe -> Claude picks a tool -> act -> observe ..."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import anthropic

from .device import KEYCODES, Device
from .observe import Observation, observe

MODEL = "claude-opus-5-5"

SYSTEM = """You operate an Android phone to complete the user's task.

After every action you get a fresh observation of the screen. Work in small steps:
look at the observation, choose ONE action, then check the next observation to see
whether it worked before moving on. If an action had no visible effect, try a
different approach (scroll, back, a different element) instead of repeating it.

Targeting: prefer `element_id` from the element list when one matches; otherwise
give x,y in the coordinate space described in the observation.

Safety: set `irreversible: true` on any action that sends, posts, pays, buys,
deletes, books, or otherwise commits something on the user's behalf. The user
will be asked to approve it. Never enter passwords or payment details; call
`ask_user` instead. If the task is ambiguous, ask before guessing.

When the task is done (or impossible), call `finish` with a short summary."""

_TARGET = {
    "element_id": {"type": "integer", "description": "id from the element list"},
    "x": {"type": "number", "description": "x coordinate, if no element id fits"},
    "y": {"type": "number", "description": "y coordinate, if no element id fits"},
}
_IRREV = {"irreversible": {"type": "boolean", "description": "true if this commits something (send, pay, delete, post...)"}}
_WHY = {"reason": {"type": "string", "description": "one short sentence: why this action"}}


def _tool(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": desc, "input_schema": {
        "type": "object", "properties": {**props, **_WHY}, "required": (required or []) + ["reason"],
        "additionalProperties": False}}


TOOLS = [
    _tool("tap", "Tap an element or a point.", {**_TARGET, **_IRREV}),
    _tool("long_press", "Long-press an element or a point.", {**_TARGET}),
    _tool("swipe", "Scroll/swipe the screen. 'up' moves content up (reveals what is below).",
          {"direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
           "element_id": {"type": "integer", "description": "optional scrollable container to swipe inside"}},
          ["direction"]),
    _tool("type_text", "Type text into the focused field (tap the field first).",
          {"text": {"type": "string"}, **_IRREV}, ["text"]),
    _tool("press_key", "Press a system key.",
          {"key": {"type": "string", "enum": sorted(KEYCODES)}, **_IRREV}, ["key"]),
    _tool("open_app", "Launch an installed app by package name.",
          {"package": {"type": "string"}}, ["package"]),
    _tool("list_apps", "List installed (non-system) app package names.", {}),
    _tool("ask_user", "Ask the user a question and wait for the answer.",
          {"question": {"type": "string"}}, ["question"]),
    _tool("finish", "End the task.",
          {"success": {"type": "boolean"}, "summary": {"type": "string"}}, ["success", "summary"]),
]


@dataclass
class RunResult:
    success: bool
    summary: str
    steps: int
    run_dir: Path
    usage: dict = field(default_factory=dict)


class Agent:
    def __init__(self, device: Device, *, mode: str = "both", max_steps: int = 30,
                 effort: str = "medium", run_root: Path = Path("runs"),
                 confirm: Callable[[str], bool] | None = None,
                 ask: Callable[[str], str] | None = None,
                 client: anthropic.Anthropic | None = None, log: Callable[[str], None] = print):
        self.device, self.mode, self.max_steps, self.effort = device, mode, max_steps, effort
        self.run_root = run_root
        self.confirm = confirm or (lambda msg: input(f"\n⚠️  {msg}\nAllow? [y/N] ").strip().lower() == "y")
        self.ask = ask or (lambda q: input(f"\n❓ {q}\n> "))
        self.client = client or anthropic.Anthropic()
        self.log = log

    # --- main loop ---
    def run(self, task: str) -> RunResult:
        run_dir = self.run_root / time.strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        traj = (run_dir / "trajectory.jsonl").open("w")
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}

        obs = observe(self.device, self.mode)
        self._save(run_dir, 0, obs)
        messages: list[dict] = [{"role": "user", "content": [
            {"type": "text", "text": f"Task: {task}"}, *obs.content_blocks(self.mode)]}]

        for step in range(1, self.max_steps + 1):
            t0 = time.time()
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            latency = time.time() - t0
            for k in usage:
                usage[k] += getattr(response.usage, k, 0) or 0

            if response.stop_reason == "refusal":
                return self._done(traj, run_dir, False, "Model declined the request.", step, usage)

            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            thoughts = " ".join(b.text for b in response.content if b.type == "text").strip()
            if not calls:
                if response.stop_reason == "end_turn":
                    return self._done(traj, run_dir, False, thoughts or "Stopped without finish().", step, usage)
                continue

            results = []
            finished = None
            for call in calls:
                self.log(f"[{step}] {call.name} {json.dumps(call.input)}")
                if call.name == "finish":
                    finished = call.input
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": "ok"})
                    continue
                try:
                    note = self._act(call.name, call.input, obs)
                    is_error = False
                except Exception as e:  # report the failure to the model, don't crash
                    note, is_error = f"Error: {e}", True
                traj.write(json.dumps({"step": step, "tool": call.name, "input": call.input,
                                       "result": note, "error": is_error, "latency_s": round(latency, 2),
                                       "thoughts": thoughts}) + "\n")
                traj.flush()
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": note, "is_error": is_error})

            if finished is not None:
                return self._done(traj, run_dir, bool(finished.get("success")),
                                  finished.get("summary", ""), step, usage)

            # Fresh observation goes with the last tool result.
            obs = observe(self.device, self.mode)
            self._save(run_dir, step, obs)
            last = results[-1]
            last["content"] = [{"type": "text", "text": str(last["content"])}, *obs.content_blocks(self.mode)]
            messages.append({"role": "user", "content": results})

        return self._done(traj, run_dir, False, f"Hit max_steps={self.max_steps}.", self.max_steps, usage)

    # --- actions ---
    def _point(self, inp: dict, obs: Observation) -> tuple[int, int]:
        if inp.get("element_id") is not None:
            eid = int(inp["element_id"])
            if not 0 <= eid < len(obs.elements):
                raise ValueError(f"no element with id {eid}")
            return obs.elements[eid].center
        if inp.get("x") is None or inp.get("y") is None:
            raise ValueError("give element_id or both x and y")
        # Tree coordinates are device px; screenshot-only coordinates are image px.
        if self.mode == "screenshot":
            return obs.to_device(inp["x"], inp["y"])
        return round(inp["x"]), round(inp["y"])

    def _gate(self, name: str, inp: dict) -> None:
        if inp.get("irreversible"):
            if not self.confirm(f"Agent wants to {name}: {inp.get('reason', '')} {json.dumps(inp)}"):
                raise PermissionError("user declined this action; choose another approach or ask_user")

    def _act(self, name: str, inp: dict, obs: Observation) -> str:
        d = self.device
        if name == "tap":
            x, y = self._point(inp, obs)
            self._gate(name, inp)
            d.tap(x, y)
            return f"tapped ({x},{y})"
        if name == "long_press":
            x, y = self._point(inp, obs)
            d.long_press(x, y)
            return f"long-pressed ({x},{y})"
        if name == "swipe":
            w, h = d.screen_size()
            if inp.get("element_id") is not None:
                x1, y1, x2, y2 = obs.elements[int(inp["element_id"])].bounds
            else:
                x1, y1, x2, y2 = 0, 0, w, h
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            dx, dy = (x2 - x1) * 0.35, (y2 - y1) * 0.35
            vec = {"up": (0, -dy), "down": (0, dy), "left": (-dx, 0), "right": (dx, 0)}[inp["direction"]]
            d.swipe(int(cx - vec[0]), int(cy - vec[1]), int(cx + vec[0]), int(cy + vec[1]))
            return f"swiped {inp['direction']}"
        if name == "type_text":
            self._gate(name, inp)
            d.type_text(inp["text"])
            return f"typed {len(inp['text'])} chars"
        if name == "press_key":
            self._gate(name, inp)
            d.key(inp["key"])
            return f"pressed {inp['key']}"
        if name == "open_app":
            d.open_app(inp["package"])
            return f"launched {inp['package']}"
        if name == "list_apps":
            return "\n".join(d.list_packages())
        if name == "ask_user":
            return "User says: " + self.ask(inp["question"])
        raise ValueError(f"unknown tool {name}")

    # --- bookkeeping ---
    def _save(self, run_dir: Path, step: int, obs: Observation) -> None:
        (run_dir / f"step_{step:02d}.png").write_bytes(obs.png)

    def _done(self, traj, run_dir, success, summary, steps, usage) -> RunResult:
        traj.write(json.dumps({"final": True, "success": success, "summary": summary,
                               "steps": steps, "usage": usage}) + "\n")
        traj.close()
        self.log(f"{'✅' if success else '❌'} {summary}  ({steps} steps, {usage})")
        return RunResult(success, summary, steps, run_dir, usage)
