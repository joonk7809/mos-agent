"""`python -m dot "task"` runs one task in the terminal; `python -m dot --daemon` is what the Mac app spawns."""
from __future__ import annotations

import sys


def main() -> None:  # pragma: no cover - wiring
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        from dot.daemon import main as daemon_main
        daemon_main()
        return
    from dot.loop import Agent
    from dot.model import OpenAICompatModel
    from dot.tools import Tools
    try:
        from dot.desktop import MacDesktop
        desktop = MacDesktop()
    except Exception:  # noqa: BLE001
        desktop = None
    task = " ".join(sys.argv[1:]) or input("Ask Dot: ")
    out = Agent(OpenAICompatModel(), Tools(desktop)).run(
        task, on_event=lambda e: print(f"  [{e['type']}] {e.get('name') or e.get('text', '')}", flush=True))
    print(f"\n{out.status}: {out.text}")


if __name__ == "__main__":
    main()
