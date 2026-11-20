"""MOS home server: phones send screens, the server answers with the next action.

    MOS_TOKEN=change-me MOS_LLM_URL=http://localhost:8000/v1 uvicorn mos.server:app --host 0.0.0.0 --port 8080

Protocol (JSON over HTTP, bearer token):
  POST /v1/sessions              {task, apps:[{label, package}], screen:{width, height}}  -> {session_id}
  POST /v1/sessions/{id}/step    {screenshot_b64, elements:[...], reply?, declined?}      -> StepResponse
  GET  /healthz

Device actions returned to the phone (pixel coordinates):
  click{x,y} long_press{x,y} swipe{direction,x?,y?} drag{x1,y1,x2,y2} type{text}
  open{label,package} key{name: back|home|menu|enter} wait{ms} ask_user{text}
  answer{text} terminate{status}
Swipe direction is the direction the finger moves.

Every session is logged under runs/server/<id>/ (screens + steps.jsonl); successful
ones can be turned into fine-tuning data with `python -m mos.export_sft`.
"""
from __future__ import annotations

import base64
import io
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

from . import loopguard, maiui, policies, safety

MAX_STEPS = int(os.environ.get("MOS_MAX_STEPS", "40"))
# Tell the model when its last action didn't change the screen. Off by default until the
# evals show it helps MAI-UI (it is a message the model never saw in training).
NO_EFFECT_HINT = os.environ.get("MOS_NO_EFFECT_HINT", "0") == "1"
NO_EFFECT_TEXT = "(Your previous action did not change the screen. Try something different.)"


class App(BaseModel):
    label: str
    package: str


class Screen(BaseModel):
    width: int
    height: int


class StartRequest(BaseModel):
    task: str = Field(min_length=1, max_length=2000)
    apps: list[App] = []
    screen: Screen


class StepRequest(BaseModel):
    screenshot_b64: str
    elements: list[dict] = []
    reply: str | None = None      # user's answer to the last ask_user
    declined: bool = False        # user refused the last action that needed confirmation


class StepResponse(BaseModel):
    action: dict | None = None
    thinking: str = ""
    confirm: str | None = None    # if set, ask the user before executing `action`
    done: bool = False
    status: str = "running"       # running | success | fail | answered | stopped | max_steps | stuck | error
    step: int = 0


@dataclass
class Session:
    id: str
    task: str
    apps: list[App]
    screen: Screen
    dir: Path
    steps: list[maiui.Step] = field(default_factory=list)
    status: str = "running"
    guard: loopguard.LoopGuard = field(default_factory=loopguard.LoopGuard)


def _resolve_app(name: str, apps: list[App]) -> App | None:
    n = name.strip().lower()
    for a in apps:
        if a.label.lower() == n:
            return a
    for a in apps:
        if n and (n in a.label.lower() or a.label.lower() in n):
            return a
    return None


def to_device_action(args: dict, s: Session) -> dict:
    """Map a MAI-UI action (0..999 coords) to a device action (pixels)."""
    w, h = s.screen.width, s.screen.height
    kind = args.get("action")
    if kind in ("click", "long_press"):
        x, y = maiui.to_pixels(args["coordinate"], w, h)
        return {"type": kind, "x": x, "y": y}
    if kind == "swipe":
        out: dict = {"type": "swipe", "direction": str(args.get("direction", "up")).lower()}
        if "coordinate" in args:
            out["x"], out["y"] = maiui.to_pixels(args["coordinate"], w, h)
        return out
    if kind == "drag":
        x1, y1 = maiui.to_pixels(args["start_coordinate"], w, h)
        x2, y2 = maiui.to_pixels(args["end_coordinate"], w, h)
        return {"type": "drag", "x1": x1, "y1": y1, "x2": x2, "y2": y2}
    if kind == "type":
        return {"type": "type", "text": str(args.get("text", ""))}
    if kind == "open":
        app = _resolve_app(str(args.get("text", "")), s.apps)
        return {"type": "open", "label": str(args.get("text", "")), "package": app.package if app else None}
    if kind == "system_button":
        return {"type": "key", "name": str(args.get("button", "back")).lower()}
    if kind == "wait":
        return {"type": "wait", "ms": 2000}
    if kind in ("ask_user", "answer"):
        return {"type": kind, "text": str(args.get("text", ""))}
    if kind == "terminate":
        return {"type": "terminate", "status": str(args.get("status", "fail")).lower()}
    raise ValueError(f"unsupported action {kind!r}")


def create_app(llm=None, run_root: Path | None = None, token: str | None = None,
               policy: str | None = None) -> FastAPI:
    pol = policies.get(policy or os.environ.get("MOS_POLICY"))
    llm = llm or maiui.ChatClient(
        base_url=os.environ.get("MOS_LLM_URL", "http://localhost:8000/v1"),
        model=os.environ.get("MOS_LLM_MODEL", "MAI-UI-8B" if pol is maiui else "GUI-Owl-1.5-8B-Instruct"),
        api_key=os.environ.get("MOS_LLM_KEY", "EMPTY"), params=dict(pol.SAMPLING))
    run_root = run_root or Path(os.environ.get("MOS_RUNS", "runs/server"))
    token = token if token is not None else os.environ.get("MOS_TOKEN", "")
    if not token:
        raise RuntimeError("Set MOS_TOKEN: phones must present it as a bearer token.")
    sessions: dict[str, Session] = {}
    app = FastAPI(title="MOS server")

    def auth(authorization: str = Header(default="")) -> None:
        if not secrets.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "bad or missing token")

    def log(s: Session, record: dict) -> None:
        with (s.dir / "steps.jsonl").open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def finish(s: Session, status: str) -> None:
        s.status = status
        (s.dir / "final.json").write_text(json.dumps({
            "task": s.task, "status": status, "steps": len(s.steps),
            "no_effect_actions": s.guard.no_effect_total, "policy": pol.NAME,
            "screen": s.screen.model_dump(), "apps": [a.label for a in s.apps]}, ensure_ascii=False))

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "model": getattr(llm, "model", "?"), "policy": pol.NAME}

    @app.post("/v1/sessions", dependencies=[Depends(auth)])
    def start(req: StartRequest):
        sid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        d = run_root / sid
        d.mkdir(parents=True, exist_ok=True)
        sessions[sid] = Session(sid, req.task, req.apps, req.screen, d)
        (d / "task.txt").write_text(req.task)
        return {"session_id": sid}

    @app.post("/v1/sessions/{sid}/step", dependencies=[Depends(auth)], response_model=StepResponse)
    def step(sid: str, req: StepRequest):
        s = sessions.get(sid)
        if not s:
            raise HTTPException(404, "unknown session")
        if s.status != "running":
            return StepResponse(done=True, status=s.status, step=len(s.steps))
        if req.declined:
            log(s, {"step": len(s.steps), "event": "user_declined"})
            finish(s, "stopped")
            return StepResponse(done=True, status="stopped", step=len(s.steps))
        if req.reply is not None and s.steps:
            s.steps[-1].user_text = req.reply
            log(s, {"step": len(s.steps) - 1, "event": "user_text", "source": "user", "text": req.reply})
        if len(s.steps) >= MAX_STEPS:
            finish(s, "max_steps")
            return StepResponse(done=True, status="max_steps", step=len(s.steps))

        n = len(s.steps)
        if pol.ANSWER_ENDS and s.steps and s.steps[-1].args.get("action") == "answer":
            log(s, {"step": n, "event": "answer_ends_task"})
            finish(s, "success")
            return StepResponse(done=True, status="success", step=n)
        png = _normalize_png(req.screenshot_b64)
        seen = s.guard.observe(png)
        if seen["stuck"]:
            log(s, {"step": n, "event": "stuck", "reason": seen["stuck"]})
            finish(s, "stuck")
            return StepResponse(done=True, status="stuck", thinking=seen["stuck"], step=n)
        if seen["no_effect"] and NO_EFFECT_HINT and s.steps and not s.steps[-1].user_text:
            s.steps[-1].user_text = NO_EFFECT_TEXT
            log(s, {"step": n - 1, "event": "user_text", "source": "hint", "text": NO_EFFECT_TEXT})
        (s.dir / f"step_{n:02d}.png").write_bytes(png)
        labels = [a.label for a in s.apps]
        t0 = time.time()
        try:
            raw, thinking, args = maiui.predict(llm, pol.system_prompt(labels), s.task, s.steps, png, policy=pol)
            action = to_device_action(args, s)
        except Exception as e:  # noqa: BLE001 - model or parse failure: stop cleanly, keep the log
            log(s, {"step": n, "error": str(e)})
            finish(s, "error")
            return StepResponse(done=True, status="error", thinking=str(e), step=n)
        prev = s.steps[-1].args if s.steps else {}
        s.steps.append(maiui.Step(png=png, thinking=thinking, args=args))
        if (getattr(pol, "REPEATED_ANSWER_ENDS", False) and args.get("action") == "answer"
                and prev.get("action") == "answer" and prev.get("text") == args.get("text")):
            # The model's report hasn't changed after seeing a fresh screen: it is finished. Its
            # answer may report success or failure, so this is "answered", not a success claim
            # (evals judge by device state; SFT export keeps only "success" sessions).
            log(s, {"step": n, "raw": raw, "args": args, "event": "repeated_answer_ends_task",
                    "latency_s": round(time.time() - t0, 2)})
            finish(s, "answered")
            return StepResponse(done=True, status="answered", thinking=thinking, step=n)
        s.guard.acted(args)
        confirm = safety.needs_confirmation(action, req.elements)
        done = action["type"] == "terminate"
        log(s, {"step": n, "raw": raw, "args": args, "action": action, "confirm": confirm,
                "latency_s": round(time.time() - t0, 2), "n_elements": len(req.elements),
                "prev_no_effect": seen["no_effect"]})
        status = "running"
        if done:
            status = "success" if action.get("status") == "success" else "fail"
            finish(s, status)
        return StepResponse(action=action, thinking=thinking, confirm=confirm, done=done,
                            status=status, step=n)

    return app


def _normalize_png(b64: str) -> bytes:
    """Accept PNG or JPEG from the phone; store and send PNG like the reference agent."""
    data = base64.b64decode(b64)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def __getattr__(name: str):  # `uvicorn mos.server:app` builds the app lazily from env vars
    if name == "app":
        return create_app()
    raise AttributeError(name)
