"""Drive an emulator through the MOS server, exactly as the phone app does.

    python -m mos.remote "Turn on dark theme" --server http://localhost:8080 --token change-me

This is the dev loop: it lets you test the server and model against the emulator
before installing the app. It speaks the same protocol as android/ (see mos/server.py).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

from . import uitree
from .device import AdbDevice, Device

# Labels for common launcher packages; anything else gets a label from its package name.
KNOWN_LABELS = {
    "com.android.settings": "Settings", "com.android.chrome": "Chrome",
    "com.google.android.deskclock": "Clock", "com.google.android.contacts": "Contacts",
    "com.google.android.calendar": "Calendar", "com.google.android.apps.messaging": "Messages",
    "com.google.android.dialer": "Phone", "com.android.camera2": "Camera",
    "com.google.android.GoogleCamera": "Camera", "com.google.android.apps.nbu.files": "Files",
    "com.google.android.documentsui": "Files", "com.google.android.gm": "Gmail",
    "com.google.android.apps.maps": "Maps", "com.google.android.apps.photos": "Photos",
    "com.google.android.youtube": "YouTube", "com.android.vending": "Play Store",
    "com.google.android.calculator": "Calculator", "com.google.android.keep": "Keep Notes",
}


def launcher_apps(device: AdbDevice) -> list[dict]:
    out = device._shell("cmd package query-activities --brief -a android.intent.action.MAIN "
                        "-c android.intent.category.LAUNCHER")
    pkgs = sorted({line.strip().split("/")[0] for line in out.splitlines() if "/" in line})
    return [{"label": KNOWN_LABELS.get(p, p.rsplit(".", 1)[-1].replace("_", " ").title()), "package": p}
            for p in pkgs]


MAX_SCREENSHOT_EDGE = 1600  # same as the Android app (MosAccessibilityService)


def phone_like(png: bytes) -> bytes:
    """Shrink and JPEG-encode a screenshot the way the phone app does, so the emulator dev
    loop and the evals show the model the same images real phones will."""
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    scale = MAX_SCREENSHOT_EDGE / max(img.size)
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def swipe_vector(direction: str, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    """Start/end points for a swipe whose finger moves in `direction`, centred on (x, y)."""
    dx, dy = int(w * 0.3), int(h * 0.3)
    vx, vy = {"up": (0, -dy), "down": (0, dy), "left": (-dx, 0), "right": (dx, 0)}.get(direction, (0, -dy))
    clamp = lambda v, hi: max(1, min(hi - 2, v))
    return (clamp(x - vx // 2, w), clamp(y - vy // 2, h), clamp(x + vx // 2, w), clamp(y + vy // 2, h))


def execute(device: Device, action: dict, screen: tuple[int, int]) -> str:
    w, h = screen
    t = action["type"]
    if t == "click":
        device.tap(action["x"], action["y"])
    elif t == "long_press":
        device.long_press(action["x"], action["y"])
    elif t == "swipe":
        device.swipe(*swipe_vector(action["direction"], action.get("x", w // 2), action.get("y", h // 2), w, h))
    elif t == "drag":
        device.swipe(action["x1"], action["y1"], action["x2"], action["y2"], 800)
    elif t == "type":
        device.type_text(action["text"])
    elif t == "key":
        device.key(action["name"])
    elif t == "open":
        if action.get("package"):
            device.open_app(action["package"])
        else:
            device.key("home")
            return f"unknown app {action.get('label')!r}; went home"
    elif t == "wait":
        time.sleep(action.get("ms", 2000) / 1000)
    return t


def http_post(base: str, token: str) -> Callable[[str, dict], dict]:
    def post(path: str, body: dict) -> dict:
        req = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    return post


@dataclass
class RemoteRunner:
    device: Device
    post: Callable[[str, dict], dict]
    confirm: Callable[[str], bool] = lambda reason: input(f"\n⚠️  {reason}. Allow? [y/N] ").strip().lower() == "y"
    ask: Callable[[str], str] = lambda q: input(f"\n❓ {q}\n> ")
    log: Callable[[str], None] = print
    apps: list[dict] | None = None
    steps: int = 0            # model steps in the last run
    session_id: str = ""      # server session of the last run (logs: runs/server/<id>/)

    def run(self, task: str) -> str:
        w, h = self.device.screen_size()
        apps = self.apps if self.apps is not None else launcher_apps(self.device)  # type: ignore[arg-type]
        sid = self.post("/v1/sessions", {"task": task, "apps": apps, "screen": {"width": w, "height": h}})["session_id"]
        self.session_id, self.steps = sid, 0
        extra: dict = {}
        while True:
            png = phone_like(self.device.screenshot_png())
            elements = [e.to_dict() for e in uitree.parse(self.device.ui_xml())]
            r = self.post(f"/v1/sessions/{sid}/step", {
                "screenshot_b64": base64.b64encode(png).decode(), "elements": elements, **extra})
            extra = {}
            self.steps = r.get("step", 0) + (1 if r.get("action") else 0)
            a = r.get("action")
            if a:
                self.log(f"[{r['step']}] {r.get('thinking', '')[:120]}\n     -> {json.dumps(a, ensure_ascii=False)}")
            if r["done"]:
                self.log(f"done: {r['status']}  (session {sid})")
                return r["status"]
            if r.get("confirm") and not self.confirm(r["confirm"]):
                extra = {"declined": True}
                continue
            if a["type"] == "ask_user":
                extra = {"reply": self.ask(a["text"])}
                continue
            if a["type"] == "answer":
                self.log(f"answer: {a['text']}")
                continue
            execute(self.device, a, (w, h))


def main() -> None:
    p = argparse.ArgumentParser(prog="mos.remote")
    p.add_argument("task")
    p.add_argument("--server", default=os.environ.get("MOS_SERVER", "http://localhost:8080"))
    p.add_argument("--token", default=os.environ.get("MOS_TOKEN", ""))
    p.add_argument("--serial")
    p.add_argument("--yes", action="store_true", help="auto-approve confirmations (emulator only)")
    a = p.parse_args()
    runner = RemoteRunner(AdbDevice(serial=a.serial), http_post(a.server, a.token))
    if a.yes:
        runner.confirm = lambda _r: True
    runner.run(a.task)


if __name__ == "__main__":
    main()
