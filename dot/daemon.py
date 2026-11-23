"""Line-delimited JSON over stdin/stdout, so the Mac app can spawn the agent with no open port.

In:  {"type": "ask", "text": "...", "context": {"app": "...", "window": "..."}}
     {"type": "stop"}            halt before the agent's next action
     {"type": "quit"}
Out: {"type": "ready"} | {"type": "status", "text"} | {"type": "tool", "name", "args"}
     {"type": "done", "status", "text"} | {"type": "error", "text"}
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
    Daemon(Agent(OpenAICompatModel(), Tools(desktop)), sys.stdout).serve(sys.stdin)


if __name__ == "__main__":
    main()
