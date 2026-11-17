"""Turn logged server sessions into fine-tuning data (Tier 1 of the plan).

    python -m mos.export_sft runs/server --out data/sft.jsonl [--include-failed]

One sample per step of each successful session, in the multimodal chat format
used by ms-swift / LLaMA-Factory: {"messages": [...], "images": [...]}, where each
"<image>" in a message is filled by the next path in "images". The context is
built exactly as at inference time (mos.maiui.build_messages), so the model is
trained on what it will actually see.

Sessions you fixed by hand: edit steps.jsonl (the "args" field) before exporting,
or record a demonstration and log it in the same format.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import maiui


def load_session(d: Path) -> tuple[dict, list[dict]] | None:
    final = d / "final.json"
    if not final.exists():
        return None
    steps = [json.loads(l) for l in (d / "steps.jsonl").read_text().splitlines() if l.strip()]
    texts: dict[int, str] = {}  # step index -> user text the model saw after that step
    for s in steps:
        if s.get("event") == "user_text":
            texts[s["step"]] = s["text"]
    actions = [s for s in steps if "args" in s]
    for s in actions:
        if s["step"] in texts:
            s["user_text"] = texts[s["step"]]
    return json.loads(final.read_text()), actions


def session_samples(d: Path, history_n: int = 3) -> list[dict]:
    loaded = load_session(d)
    if not loaded:
        return []
    final, steps = loaded
    system = maiui.system_prompt(final.get("apps", []))
    task = (d / "task.txt").read_text()
    samples = []
    past: list[maiui.Step] = []
    for s in steps:
        img = d / f"step_{s['step']:02d}.png"
        thinking, args = maiui.parse_response(s["raw"]) if "raw" in s else ("", s["args"])
        msgs = maiui.build_messages(system, task, past, b"", history_n)
        images: list[str] = []
        # replace inline image payloads with placeholders, in order
        hist_imgs = [str(d / f"step_{p_idx:02d}.png") for p_idx in range(len(past))]
        first_with_image = max(0, len(past) - (history_n - 1))
        images += hist_imgs[first_with_image:] + [str(img)]
        out = []
        for m in msgs:
            parts = []
            for c in m["content"]:
                parts.append("<image>" if c["type"] == "image_url" else c["text"])
            out.append({"role": m["role"], "content": "".join(parts)})
        out.append({"role": "assistant", "content": maiui.format_response(thinking, args)})
        samples.append({"messages": out, "images": images})
        past.append(maiui.Step(png=b"", thinking=thinking, args=args, user_text=s.get("user_text")))
    return samples


def main() -> None:
    p = argparse.ArgumentParser(prog="mos.export_sft")
    p.add_argument("runs", type=Path)
    p.add_argument("--out", type=Path, default=Path("data/sft.jsonl"))
    p.add_argument("--include-failed", action="store_true")
    a = p.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    n_sessions = n_samples = 0
    with a.out.open("w") as f:
        for d in sorted(x for x in a.runs.iterdir() if x.is_dir()):
            loaded = load_session(d)
            if not loaded or (loaded[0]["status"] != "success" and not a.include_failed):
                continue
            for sample in session_samples(d):
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                n_samples += 1
            n_sessions += 1
    print(f"wrote {n_samples} samples from {n_sessions} sessions to {a.out}")


if __name__ == "__main__":
    main()
