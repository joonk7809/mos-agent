"""The agent's tools. v0 is unbound by joon's decision (ADR 0011): no gates, full user-level access."""
from __future__ import annotations

import html
import os
import re
import subprocess
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dot.desktop import Desktop, Element, Shot, to_points

MAX_OUT = 8000


@dataclass
class ToolResult:
    text: str
    image: Shot | None = None
    error: bool = False


def _clip(s: str, n: int = MAX_OUT) -> str:
    return s if len(s) <= n else s[:n] + f"\n... [{len(s) - n} more chars cut]"


def _fn(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


_S = {"type": "string"}
_I = {"type": "integer"}
_N = {"type": "number"}

SCHEMAS = [
    _fn("run_shell", "Run a zsh command on the Mac and return stdout/stderr and the exit code.",
        {"command": _S, "timeout": _I, "cwd": _S}, ["command"]),
    _fn("read_file", "Read a text file.", {"path": _S, "max_chars": _I}, ["path"]),
    _fn("write_file", "Write (or append to) a text file, creating folders as needed.",
        {"path": _S, "content": _S, "append": {"type": "boolean"}}, ["path", "content"]),
    _fn("list_dir", "List a folder.", {"path": _S}, ["path"]),
    _fn("applescript", "Run AppleScript (osascript) to control Mac apps: Mail, Calendar, Finder, Safari, etc.",
        {"script": _S}, ["script"]),
    _fn("open", "Open a file, folder, URL or app (macOS `open`; use app for `open -a`).",
        {"target": _S, "app": _S}, []),
    _fn("web_fetch", "HTTP GET a URL and return its text.", {"url": _S}, ["url"]),
    _fn("ui_elements", "List the frontmost window's buttons, fields and labels as text, with ids. Much faster than a "
        "screenshot: try this first, then click_element.", {"filter": _S}, []),
    _fn("click_element", "Click the centre of an element from the last ui_elements list.",
        {"id": _I, "count": _I}, ["id"]),
    _fn("screenshot", "See the screen as an image (front window by default; whole=true for all screens' main "
        "display). Give click coordinates in that image's pixels.", {"whole": {"type": "boolean"}}, []),
    _fn("click", "Click at (x, y) in the last screenshot's pixels. count=2 for double click.",
        {"x": _N, "y": _N, "button": {"type": "string", "enum": ["left", "right"]}, "count": _I}, ["x", "y"]),
    _fn("scroll", "Scroll at (x, y): dy>0 scrolls down, dy<0 up (lines).", {"x": _N, "y": _N, "dy": _I, "dx": _I},
        ["x", "y", "dy"]),
    _fn("drag", "Drag from (x1, y1) to (x2, y2), in screenshot pixels.",
        {"x1": _N, "y1": _N, "x2": _N, "y2": _N}, ["x1", "y1", "x2", "y2"]),
    _fn("type_text", "Type text at the current focus.", {"text": _S}, ["text"]),
    _fn("key", "Press a key or combo, e.g. enter, esc, cmd+c, cmd+shift+t, down.", {"combo": _S}, ["combo"]),
]
NAMES = {s["function"]["name"] for s in SCHEMAS}


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


class Tools:
    def __init__(self, desktop: Desktop | None = None,
                 run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self.desktop = desktop
        self.run = run
        self.last_shot: Shot | None = None
        self.last_elements: list[Element] = []

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in NAMES:
            return ToolResult(f"unknown tool {name!r}; available: {', '.join(sorted(NAMES))}", error=True)
        try:
            return getattr(self, f"t_{name}")(**args)
        except TypeError as e:
            return ToolResult(f"bad arguments for {name}: {e}", error=True)
        except Exception as e:  # noqa: BLE001 - every tool failure goes back to the model as text
            return ToolResult(f"{name} failed: {type(e).__name__}: {e}", error=True)

    # --- shell and files
    def t_run_shell(self, command: str, timeout: int = 120, cwd: str | None = None) -> ToolResult:
        p = self.run(["/bin/zsh", "-lc", command], capture_output=True, text=True, timeout=timeout,
                     cwd=os.path.expanduser(cwd) if cwd else os.path.expanduser("~"))
        out = f"exit {p.returncode}\n{p.stdout}" + (f"\n[stderr]\n{p.stderr}" if p.stderr else "")
        return ToolResult(_clip(out), error=p.returncode != 0)

    def t_read_file(self, path: str, max_chars: int = MAX_OUT) -> ToolResult:
        with open(os.path.expanduser(path), encoding="utf-8", errors="replace") as f:
            return ToolResult(_clip(f.read(max_chars + 1), max_chars))

    def t_write_file(self, path: str, content: str, append: bool = False) -> ToolResult:
        p = os.path.expanduser(path)
        if os.path.dirname(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a" if append else "w", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(f"wrote {len(content)} chars to {p}")

    def t_list_dir(self, path: str) -> ToolResult:
        p = os.path.expanduser(path)
        rows = []
        for e in sorted(os.scandir(p), key=lambda e: e.name):
            rows.append(e.name + ("/" if e.is_dir() else ""))
        return ToolResult(_clip("\n".join(rows) or "(empty)"))

    # --- apps and web
    def t_applescript(self, script: str) -> ToolResult:
        p = self.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
        return ToolResult(_clip(p.stdout + (f"\n[stderr]\n{p.stderr}" if p.stderr else "")), error=p.returncode != 0)

    def t_open(self, target: str = "", app: str = "") -> ToolResult:
        if not target and not app:
            return ToolResult("give target and/or app", error=True)
        cmd = ["open"] + (["-a", app] if app else []) + ([os.path.expanduser(target)] if target else [])
        p = self.run(cmd, capture_output=True, text=True, timeout=30)
        return ToolResult(f"exit {p.returncode} {p.stderr}".strip(), error=p.returncode != 0)

    def t_web_fetch(self, url: str) -> ToolResult:
        if not re.match(r"(?i)^https?://", url):
            return ToolResult("only http(s) URLs", error=True)
        req = urllib.request.Request(url, headers={"User-Agent": "dot-agent/0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", errors="replace")
            ctype = r.headers.get("Content-Type", "")
        return ToolResult(_clip(_html_to_text(body) if "html" in ctype else body))

    # --- screen
    def _desk(self) -> Desktop:
        if self.desktop is None:
            raise RuntimeError("screen control unavailable (needs macOS and pyobjc-framework-Quartz)")
        return self.desktop

    def _pt(self, x: float, y: float) -> tuple[float, float]:
        if self.last_shot is None:
            raise RuntimeError("take a screenshot first, so coordinates have a reference")
        return to_points(float(x), float(y), self.last_shot)

    def t_ui_elements(self, filter: str = "") -> ToolResult:
        els = self._desk().elements(filter=filter)
        self.last_elements = els
        if not els:
            return ToolResult("no labelled elements found; use screenshot instead")
        rows = [f"[{i}] {e.role} \"{e.title}\"" for i, e in enumerate(els)]
        return ToolResult(_clip("\n".join(rows)))

    def t_click_element(self, id: int, count: int = 1) -> ToolResult:
        i = int(id)
        if not 0 <= i < len(self.last_elements):
            return ToolResult(f"no element {i}; call ui_elements again", error=True)
        e = self.last_elements[i]
        self._desk().click(e.x + e.w / 2, e.y + e.h / 2, count=max(1, min(int(count), 3)))
        return ToolResult(f"clicked [{i}] {e.role} \"{e.title}\"")

    def t_screenshot(self, whole: bool = False) -> ToolResult:
        shot = self._desk().screenshot(window=not whole)
        self.last_shot = shot
        return ToolResult(f"screenshot {shot.width}x{shot.height} px (attached)", image=shot)

    def t_click(self, x: float, y: float, button: str = "left", count: int = 1) -> ToolResult:
        px, py = self._pt(x, y)
        self._desk().click(px, py, button=button, count=max(1, min(int(count), 3)))
        return ToolResult(f"clicked {button} x{count} at ({x}, {y})")

    def t_scroll(self, x: float, y: float, dy: int, dx: int = 0) -> ToolResult:
        px, py = self._pt(x, y)
        self._desk().scroll(px, py, int(dy), int(dx))
        return ToolResult(f"scrolled dy={dy} dx={dx} at ({x}, {y})")

    def t_drag(self, x1: float, y1: float, x2: float, y2: float) -> ToolResult:
        a, b = self._pt(x1, y1), self._pt(x2, y2)
        self._desk().drag(a[0], a[1], b[0], b[1])
        return ToolResult(f"dragged ({x1}, {y1}) -> ({x2}, {y2})")

    def t_type_text(self, text: str) -> ToolResult:
        self._desk().type_text(text)
        return ToolResult(f"typed {len(text)} chars")

    def t_key(self, combo: str) -> ToolResult:
        self._desk().key(combo)
        return ToolResult(f"pressed {combo}")
