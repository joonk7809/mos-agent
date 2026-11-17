"""Turn logged server sessions into fine-tuning data (Tier 1 of the plan).

    python -m mos.export_sft runs/server --out data/sft.jsonl [--include-failed]

One sample per step of each successful session, in the multimodal chat format
used by ms-swift / LLaMA-Factory: {"messages": [...], "images": [...]}, where each
"<image>" in a message is filled by the next path in "images". The context is
built exactly as at inference time (the policy's build_messages), so the model is
trained on what it will actually see.

Sessions you fixed by hand: edit steps.jsonl (the "args" field) before exporting,
or record a demonstration and log it in the same format.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from . import maiui, policies


def load_session(d: Path) -> tuple[dict, list[dict]] | None:
    final = d / "final.json"
    if not final.exists():
        return None
    steps = [json.loads(l) for l in (d / "steps.jsonl").read_text().splitlines() if l.strip()]
    texts: dict[int, str] = {}  # step index -> user text the model saw after that step
    for s in steps:
        if s.get("event") == "user_text":
            texts[s["step"]] = s["text"]
    final_info = json.loads(final.read_text())
    # Replies typed by a person can hold private details (codes, addresses).
    final_info["has_user_replies"] = any(
        s.get("event") == "user_text" and s.get("source", "user") == "user" for s in steps)
    actions = [s for s in steps if "args" in s]
    for s in actions:
        if s["step"] in texts:
            s["user_text"] = texts[s["step"]]
    return final_info, actions


def session_samples(d: Path, history_n: int | None = None, include_replies: bool = False) -> list[dict]:
    """Samples for one session. Sessions where a person answered the agent are skipped unless
    include_replies is set (only with that person's OK: the replies go into the training data)."""
    loaded = load_session(d)
    if not loaded:
        return []
    final, steps = loaded
    if final["has_user_replies"] and not include_replies:
        return []
    pol = policies.get(final.get("policy"))  # sessions logged before policies existed are MAI-UI
    system = pol.system_prompt(final.get("apps", []))
    task = (d / "task.txt").read_text()
    samples = []
    past: list[maiui.Step] = []
    for s in steps:
        # The thinking comes from the model's raw reply; the action from "args", so a step you
        # corrected by hand in steps.jsonl is what gets trained on.
        thinking = pol.parse_response(s["raw"])[0] if "raw" in s else s.get("thinking", "")
        args = s["args"]
        # Build the context exactly as at inference, with marker "images" standing in for
        # screenshots, then swap each marker for an <image> placeholder plus its file path.
        msgs = pol.build_messages(system, task, past, _marker(s["step"]), history_n or pol.HISTORY_N)
        images: list[str] = []
        out = []
        for m in msgs:
            parts = []
            for c in m["content"]:
                if c["type"] == "image_url":
                    idx = int(base64.b64decode(c["image_url"]["url"].split(",", 1)[1]).decode().split(":")[1])
                    images.append(str(d / f"step_{idx:02d}.png"))
                    parts.append("<image>")
                else:
                    parts.append(c["text"])
            out.append({"role": m["role"], "content": "".join(parts)})
        out.append({"role": "assistant", "content": pol.format_response(thinking, args)})
        samples.append({"messages": out, "images": images})
        past.append(maiui.Step(png=_marker(s["step"]), thinking=thinking, args=args, user_text=s.get("user_text")))
    return samples


def _marker(step: int) -> bytes:
    return f"MOSIMG:{step}".encode()


def main() -> None:
    p = argparse.ArgumentParser(prog="mos.export_sft")
    p.add_argument("runs", type=Path)
    p.add_argument("--out", type=Path, default=Path("data/sft.jsonl"))
    p.add_argument("--include-failed", action="store_true")
    p.add_argument("--policy", default=os.environ.get("MOS_POLICY", "maiui"),
                   help="only export sessions run with this policy (each model needs its own format)")
    p.add_argument("--include-replies", action="store_true",
                   help="also export sessions where someone answered the agent; their replies go into "
                        "the data, so only with that person's OK")
    a = p.parse_args()
    want = policies.get(a.policy).NAME
    a.out.parent.mkdir(parents=True, exist_ok=True)
    n_sessions = n_samples = 0
    with a.out.open("w") as f:
        for d in sorted(x for x in a.runs.iterdir() if x.is_dir()):
            loaded = load_session(d)
            if not loaded or (loaded[0]["status"] != "success" and not a.include_failed):
                continue
            if want != policies.get(loaded[0].get("policy")).NAME:
                continue
            for sample in session_samples(d, include_replies=a.include_replies):
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                n_samples += 1
            n_sessions += 1
    print(f"wrote {n_samples} samples from {n_sessions} sessions to {a.out}")


if __name__ == "__main__":
    main()
