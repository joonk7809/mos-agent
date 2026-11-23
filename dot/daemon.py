"""Line-delimited JSON over stdin/stdout, so the Mac app can spawn the agent with no open port.

In:  {"type": "ask", "text": "...", "context": {"app": "...", "window": "..."}}
     {"type": "stop"}            halt before the agent's next action
     {"type": "reset"}           start a new chat (forget earlier requests and replies)
     {"type": "quit"}
Out: {"type": "ready"} | {"type": "status", "text"} | {"type": "tool", "name", "args"}
     {"type": "done", "status", "text"} | {"type": "error", "text"} | {"type": "reset"}
"""
from __future__ import annotations

import json
import sys
import threading
from typing import IO, Any

from dot.loop import Agent


class Daemon:
    def __init__(self, agent: Agent, out: IO[str]):
        self.agent = agent
        self.out = out
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None

    def emit(self, ev: dict[str, Any]) -> None:
        with self.lock:
            self.out.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.out.flush()

    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def handle(self, line: str) -> bool:
        """Handle one input line. Returns False on quit."""
        line = line.strip()
        if not line:
            return True
        try:
            msg = json.loads(line)
            kind = msg["type"]
        except (json.JSONDecodeError, KeyError, TypeError):
            self.emit({"type": "error", "text": "bad message"})
            return True
        if kind == "stop":
            self.agent.stop.set()
        elif kind == "reset":
            if self.busy():
                self.emit({"type": "error", "text": "busy: press Stop first"})
            else:
                self.agent.reset()
                self.emit({"type": "reset"})
        elif kind == "quit":
            self.agent.stop.set()
            return False
        elif kind == "ask":
            text = str(msg.get("text", "")).strip()
            if not text:
                self.emit({"type": "error", "text": "empty request"})
            elif self.busy():
                self.emit({"type": "error", "text": "busy: press Stop first"})
            else:
                ctx = msg.get("context") if isinstance(msg.get("context"), dict) else {}
                self.agent.stop.clear()
                self.worker = threading.Thread(target=self.agent.run, args=(text, ctx, self.emit), daemon=True)
                self.worker.start()
        else:
            self.emit({"type": "error", "text": f"unknown type {kind!r}"})
        return True

    def serve(self, inp: IO[str]) -> None:
        self.emit({"type": "ready"})
        for line in inp:
            if not self.handle(line):
                break
        self.agent.stop.set()  # quit or stdin closed (the app died): stop before the next action
        if self.worker is not None:
            self.worker.join(timeout=5)


def main() -> None:  # pragma: no cover - wiring
    from dot.model import OpenAICompatModel
    from dot.tools import Tools
    desktop = None
    try:
        from dot.desktop import MacDesktop
        desktop = MacDesktop()
    except Exception as e:  # noqa: BLE001 - screen control is optional; tell the user once
        print(json.dumps({"type": "status", "text": f"screen control off: {e}"}), flush=True)
    m = OpenAICompatModel()
    try:
        problem = m.check()
    except Exception as e:  # noqa: BLE001 - a broken check must not keep the agent from starting
        problem = f"Startup check failed: {type(e).__name__}: {e}"
    if problem:  # shown in the bar at launch, so a missing server is obvious before the first ask
        print(json.dumps({"type": "error", "text": problem}), flush=True)
    print(json.dumps({"type": "status", "text": f"model: {m.model} at {m.base_url}"}), flush=True)
    Daemon(Agent(m, Tools(desktop)), sys.stdout).serve(sys.stdin)


if __name__ == "__main__":
    main()
