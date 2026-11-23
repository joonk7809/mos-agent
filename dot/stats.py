"""Seconds per step from the action log: `python -m dot.stats [log.jsonl ...]` (default: today's log)."""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
import sys
from typing import Any


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    model = [r["seconds"] for r in records if r.get("kind") == "model" and "seconds" in r]
    shot = [r["seconds"] for r in records if r.get("kind") == "tool" and r.get("name") == "screenshot"]
    tools = [r["seconds"] for r in records if r.get("kind") == "tool" and "seconds" in r]
    steps: dict[tuple[str, int], float] = {}
    for r in records:
        if r.get("kind") in ("model", "tool") and "seconds" in r:
            k = (r["task"], r["turn"])
            steps[k] = steps.get(k, 0.0) + r["seconds"]
    prompt = [r["usage"]["prompt_tokens"] for r in records
              if r.get("kind") == "model" and isinstance(r.get("usage"), dict) and "prompt_tokens" in r["usage"]]

    def med(xs: list[float]) -> float | None:
        return round(statistics.median(xs), 2) if xs else None

    return {"steps": len(steps), "median_step_s": med(list(steps.values())), "median_model_s": med(model),
            "median_tool_s": med(tools), "median_screenshot_s": med(shot), "median_prompt_tokens": med(prompt),
            "tasks": len({r["task"] for r in records if r.get("kind") == "task"})}


def main() -> None:  # pragma: no cover - wiring
    paths = sys.argv[1:] or [os.path.expanduser(
        os.path.join(os.environ.get("DOT_LOG_DIR") or "~/Library/Logs/Dot", f"{dt.date.today().isoformat()}.jsonl"))]
    recs = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            recs += [json.loads(line) for line in f if line.strip()]
    print(json.dumps(summarize(recs), indent=2))


if __name__ == "__main__":
    main()
