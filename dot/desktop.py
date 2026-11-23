"""Screen control on macOS: see (screencapture), click/type/scroll/drag (CGEvent via pyobjc Quartz).

Needs Screen Recording and Accessibility permission for the Dot app (or the terminal running it).
Coordinates the model gives are in the pixel space of the last screenshot it was shown; we map
them to screen points. Set DOT_COORDS=norm1000 if the model turns out to emit 0..999 like MAI-UI.
"""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Protocol

# Downscale screenshots to this longest side before sending: fewer image tokens = faster steps.
MAX_SIDE = int(os.environ.get("DOT_MAX_SIDE", "1280"))


@dataclass
class Shot:
    png: bytes
    width: int    # image pixels
    height: int
    screen_w: float  # size of the captured area in screen points
    screen_h: float
    origin_x: float = 0.0  # top-left of the captured area in screen points (non-zero for a window)
    origin_y: float = 0.0


@dataclass
class Element:
    """One accessibility element of the frontmost window, in screen points."""
    role: str
    title: str
    x: float
    y: float
    w: float
    h: float


class Desktop(Protocol):
    def screenshot(self, window: bool = False) -> Shot: ...
    def elements(self, limit: int = 150, filter: str = "") -> list[Element]: ...
    def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None: ...
    def move(self, x: float, y: float) -> None: ...
    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None: ...
    def scroll(self, x: float, y: float, dy: int, dx: int = 0) -> None: ...
    def type_text(self, text: str) -> None: ...
    def key(self, combo: str) -> None: ...


def to_points(x: float, y: float, shot: Shot, space: str | None = None) -> tuple[float, float]:
    """Map model coordinates (image pixels, or 0..999 when space == "norm1000") to screen points, clamped."""
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError(f"coordinates must be finite numbers, got ({x}, {y})")
    space = space or os.environ.get("DOT_COORDS", "pixels")
    if space == "norm1000":
        fx, fy = x / 999.0, y / 999.0
    else:
        fx, fy = x / max(shot.width, 1), y / max(shot.height, 1)
    fx = min(max(fx, 0.0), 1.0)
    fy = min(max(fy, 0.0), 1.0)
    return shot.origin_x + fx * shot.screen_w, shot.origin_y + fy * shot.screen_h


# Key names for combos like "cmd+shift+t", "enter", "esc", "down".
KEYCODES = {"enter": 36, "return": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "esc": 53,
            "escape": 53, "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119,
            "pageup": 116, "pagedown": 121, "forwarddelete": 117,
            **{f"f{i}": c for i, c in zip(range(1, 13), [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111],
                                          strict=True)}}
MODIFIERS = {"cmd": "command down", "command": "command down", "ctrl": "control down", "control": "control down",
             "alt": "option down", "opt": "option down", "option": "option down", "shift": "shift down",
             "fn": "function down"}


def key_applescript(combo: str) -> str:
    """AppleScript for a key combo. Raises ValueError on an unknown key."""
    combo = combo.strip().lower()
    if combo.endswith("++"):  # "cmd++" = cmd and the plus key
        combo = combo[:-2] + "+plus"
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if parts and parts[-1] == "plus":
        parts[-1] = "+"
    if not parts:
        raise ValueError("empty key combo")
    *mods, key = parts
    using = []
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"unknown modifier {m!r}")
        using.append(MODIFIERS[m])
    suffix = f" using {{{', '.join(using)}}}" if using else ""
    if key in KEYCODES:
        return f'tell application "System Events" to key code {KEYCODES[key]}{suffix}'
    if len(key) == 1:
        esc = key.replace("\\", "\\\\").replace('"', '\\"')
        return f'tell application "System Events" to keystroke "{esc}"{suffix}'
    raise ValueError(f"unknown key {key!r}")


def utf16_chunks(text: str, units: int = 16) -> list[str]:
    """Split text into chunks of at most `units` UTF-16 code units (CGEvent's limit is ~20), never
    splitting a character."""
    out, cur, n = [], "", 0
    for ch in text:
        w = 2 if ord(ch) > 0xFFFF else 1
        if n + w > units and cur:
            out.append(cur)
            cur, n = "", 0
        cur += ch
        n += w
    if cur:
        out.append(cur)
    return out


class MacDesktop:  # pragma: no cover - needs macOS
    def __init__(self) -> None:
        import Quartz  # pyobjc-framework-Quartz
        self.Q: Any = Quartz

    def _screen_size(self) -> tuple[float, float]:
        b = self.Q.CGDisplayBounds(self.Q.CGMainDisplayID())
        return float(b.size.width), float(b.size.height)

    def _front_window(self) -> tuple[int, dict[str, float]] | None:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        pid = app.processIdentifier()
        Q = self.Q
        infos = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
                                             Q.kCGNullWindowID) or []
        for w in infos:
            if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0:
                b = w.get("kCGWindowBounds")
                return int(w["kCGWindowNumber"]), {k: float(b[k]) for k in ("X", "Y", "Width", "Height")}
        return None

    def screenshot(self, window: bool = False) -> Shot:
        sw, sh = self._screen_size()
        ox = oy = 0.0
        cmd = ["screencapture", "-x", "-t", "png"]
        front = self._front_window() if window else None
        if front is not None:
            wid, b = front
            cmd += [f"-l{wid}", "-o"]
            ox, oy, sw, sh = b["X"], b["Y"], b["Width"], b["Height"]
        else:
            cmd += ["-m"]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.png")
            subprocess.run([*cmd, path], check=True, timeout=20)
            subprocess.run(["sips", "-Z", str(MAX_SIDE), path], check=True, capture_output=True, timeout=20)
            out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                                 check=True, capture_output=True, text=True, timeout=20).stdout
            w = int(out.split("pixelWidth:")[1].split()[0])
            h = int(out.split("pixelHeight:")[1].split()[0])
            with open(path, "rb") as f:
                png = f.read()
        return Shot(png=png, width=w, height=h, screen_w=sw, screen_h=sh, origin_x=ox, origin_y=oy)

    def elements(self, limit: int = 150, filter: str = "") -> list[Element]:
        """Walk the frontmost window's accessibility tree; keep elements with a label and a size
        (and, with `filter`, whose label or role contains it)."""
        import ApplicationServices as AS
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return []
        root = AS.AXUIElementCreateApplication(app.processIdentifier())

        def attr(el: Any, name: str) -> Any:
            err, val = AS.AXUIElementCopyAttributeValue(el, name, None)
            return val if err == 0 else None

        win = attr(root, "AXFocusedWindow") or root
        out: list[Element] = []
        stack, visited = [win], 0
        while stack and len(out) < limit and visited < 3000:
            el = stack.pop()
            visited += 1
            role = str(attr(el, "AXRole") or "")
            label = next((str(v) for v in (attr(el, "AXTitle"), attr(el, "AXDescription"), attr(el, "AXValue"),
                                           attr(el, "AXPlaceholderValue")) if v not in (None, "")), "")
            pos, size = attr(el, "AXPosition"), attr(el, "AXSize")
            if filter and filter.lower() not in (label + " " + role).lower():
                label = ""
            if label and pos is not None and size is not None:
                _, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
                _, z = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
                if z.width > 1 and z.height > 1:
                    out.append(Element(role.removeprefix("AX"), label[:80], p.x, p.y, z.width, z.height))
            stack.extend(reversed(list(attr(el, "AXChildren") or [])))
        return out

    def _post(self, ev: Any) -> None:
        self.Q.CGEventPost(self.Q.kCGHIDEventTap, ev)

    def move(self, x: float, y: float) -> None:
        Q = self.Q
        self._post(Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, (x, y), Q.kCGMouseButtonLeft))

    def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None:
        Q = self.Q
        if button == "right":
            down, up, btn = Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight
        else:
            down, up, btn = Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft
        self.move(x, y)
        for i in range(1, count + 1):
            for kind in (down, up):
                ev = Q.CGEventCreateMouseEvent(None, kind, (x, y), btn)
                Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, i)
                self._post(ev)
            time.sleep(0.05)

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        Q = self.Q
        self._post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDown, (x1, y1), Q.kCGMouseButtonLeft))
        steps = 20
        for i in range(1, steps + 1):
            px, py = x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps
            self._post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDragged, (px, py), Q.kCGMouseButtonLeft))
            time.sleep(0.01)
        self._post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseUp, (x2, y2), Q.kCGMouseButtonLeft))

    def scroll(self, x: float, y: float, dy: int, dx: int = 0) -> None:
        Q = self.Q
        self.move(x, y)
        self._post(Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 2, -dy, -dx))

    def type_text(self, text: str) -> None:
        Q = self.Q
        for chunk in utf16_chunks(text):
            for down in (True, False):
                ev = Q.CGEventCreateKeyboardEvent(None, 0, down)
                Q.CGEventKeyboardSetUnicodeString(ev, len(chunk.encode("utf-16-le")) // 2, chunk)
                self._post(ev)
            time.sleep(0.01)

    def key(self, combo: str) -> None:
        p = subprocess.run(["osascript", "-e", key_applescript(combo)], capture_output=True, text=True, timeout=20)
        if p.returncode != 0:
            raise RuntimeError(f"osascript: {p.stderr.strip() or p.returncode} (does Dot have Accessibility?)")
