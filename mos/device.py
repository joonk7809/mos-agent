"""Thin ADB wrapper: everything the agent needs to see and touch an Android device."""
from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Protocol

KEYCODES = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "enter": "KEYCODE_ENTER",
    "app_switch": "KEYCODE_APP_SWITCH",
    "delete": "KEYCODE_DEL",
    "tab": "KEYCODE_TAB",
}


class Device(Protocol):
    def screenshot_png(self) -> bytes: ...
    def ui_xml(self) -> str: ...
    def screen_size(self) -> tuple[int, int]: ...
    def tap(self, x: int, y: int) -> None: ...
    def long_press(self, x: int, y: int, ms: int = 800) -> None: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None: ...
    def type_text(self, text: str) -> None: ...
    def key(self, name: str) -> None: ...
    def open_app(self, package: str) -> None: ...
    def list_packages(self) -> list[str]: ...
    def current_app(self) -> str: ...


def escape_input_text(text: str) -> str:
    """Escape text for `adb shell input text`.

    `input text` treats %s as a space and the device shell re-parses the
    argument, so spaces become %s and shell metacharacters are backslashed.
    """
    out = []
    for ch in text:
        if ch == " ":
            out.append("%s")
        elif ch in "\\'\"`$&|;<>()*?!#~[]{}%":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


@dataclass
class AdbDevice:
    serial: str | None = None
    settle_s: float = 0.8  # wait after each action so the UI can update
    runner: Callable[[list[str]], bytes] | None = None  # injectable for tests

    def _adb(self, *args: str) -> bytes:
        cmd = ["adb"] + (["-s", self.serial] if self.serial else []) + list(args)
        if self.runner:
            return self.runner(cmd)
        return subprocess.run(cmd, check=True, capture_output=True, timeout=30).stdout

    def _shell(self, command: str) -> str:
        return self._adb("shell", command).decode("utf-8", "replace")

    def _settle(self) -> None:
        if self.settle_s:
            time.sleep(self.settle_s)

    # --- perception ---
    def screenshot_png(self) -> bytes:
        return self._adb("exec-out", "screencap", "-p")

    def ui_xml(self) -> str:
        # uiautomator can fail on animating screens; retry once.
        for _ in range(2):
            out = self._shell("uiautomator dump /sdcard/mos_ui.xml >/dev/null && cat /sdcard/mos_ui.xml")
            if "<hierarchy" in out:
                return out[out.index("<?xml") if "<?xml" in out else out.index("<hierarchy"):]
            time.sleep(0.5)
        return ""

    def screen_size(self) -> tuple[int, int]:
        out = self._shell("wm size")  # "Physical size: 1080x2400" (+ maybe Override size)
        line = [l for l in out.splitlines() if "size" in l][-1]
        w, h = line.split(":")[1].strip().split("x")
        return int(w), int(h)

    def current_app(self) -> str:
        out = self._shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' | head -1")
        return out.strip()

    def list_packages(self) -> list[str]:
        out = self._shell("pm list packages -3")
        return sorted(l.removeprefix("package:").strip() for l in out.splitlines() if l.strip())

    # --- actions ---
    def tap(self, x: int, y: int) -> None:
        self._shell(f"input tap {int(x)} {int(y)}")
        self._settle()

    def long_press(self, x: int, y: int, ms: int = 800) -> None:
        self._shell(f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} {int(ms)}")
        self._settle()

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
        self._shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")
        self._settle()

    def type_text(self, text: str) -> None:
        # `input text` can't do newlines; split and press enter between lines.
        for i, line in enumerate(text.split("\n")):
            if i:
                self.key("enter")
            if line:
                self._shell(f"input text {escape_input_text(line)}")
        self._settle()

    def key(self, name: str) -> None:
        code = KEYCODES.get(name.lower())
        if not code:
            raise ValueError(f"unknown key {name!r}; use one of {sorted(KEYCODES)}")
        self._shell(f"input keyevent {code}")
        self._settle()

    def open_app(self, package: str) -> None:
        self._shell(f"monkey -p {shlex.quote(package)} -c android.intent.category.LAUNCHER 1")
        self._settle()
        time.sleep(1.0)  # cold starts are slow
