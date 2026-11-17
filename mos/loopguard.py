"""Notice when the agent is stuck, so a run ends early instead of burning steps.

Small GUI models often repeat an action that does nothing (tapping a disabled
button, swiping a list that's already at the end). Each repeat costs a model
call and a few seconds of the user's phone. The server compares each new
screenshot with the one the previous action was taken on:

- an action "had no effect" if the screen looks the same afterwards;
- the run is "stuck" when the same action had no effect `max_repeats` times in a
  row, or the model waited `max_waits` times in a row on an unchanged screen.

Screens are compared on a grayscale thumbnail without the status bar, so the clock
ticking or image compression noise doesn't count as a change, but a new page, a
dialog, a flipped switch, a changed digit or two typed characters do. The check
errs towards "changed": a blinking text cursor also counts, which can only make
the guard miss a loop, never stop a run that was making progress.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image

THUMB = (180, 320)        # width x height of the comparison thumbnail (~6x7 screen pixels per cell)
STATUS_BAR = 0.05         # top fraction of the screen ignored (clock, notification icons)
PIXEL_DIFF = 24           # a thumbnail pixel "changed" if its gray level moved more than this
MIN_CHANGED = 4           # screens differ if at least this many thumbnail pixels changed
EFFECTFUL = {"click", "long_press", "swipe", "drag", "type", "system_button", "open"}


def signature(png: bytes) -> bytes:
    img = Image.open(io.BytesIO(png)).convert("L")
    w, h = img.size
    img = img.crop((0, int(h * STATUS_BAR), w, h)).resize(THUMB, Image.BOX)
    return img.tobytes()


def same_screen(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    return sum(abs(x - y) > PIXEL_DIFF for x, y in zip(a, b)) < MIN_CHANGED


def action_key(args: dict) -> tuple:
    """Two actions are 'the same' if they do the same thing to roughly the same place.

    Coordinates are in MAI-UI's 0..999 space; a 25-unit grid (~2.5% of the screen)
    absorbs the jitter of a model re-aiming at the same button.
    """
    kind = args.get("action")
    grid = lambda p: tuple(round(v / 25) for v in p) if isinstance(p, (list, tuple)) else None
    return (kind, grid(args.get("coordinate")), grid(args.get("start_coordinate")),
            grid(args.get("end_coordinate")), str(args.get("direction", "")).lower(),
            str(args.get("text", "")), str(args.get("button", "")).lower())


@dataclass
class LoopGuard:
    max_repeats: int = 3
    max_waits: int = 5
    _prev_sig: bytes | None = None
    _prev_args: dict | None = None
    _streak_key: tuple | None = None
    _streak: int = 0
    _waits: int = 0
    no_effect_total: int = field(default=0)

    def observe(self, png: bytes) -> dict:
        """Call with each new screenshot, before asking the model.

        Returns {"no_effect": bool, "stuck": str | None} about the previous action.
        """
        sig = signature(png)
        out = {"no_effect": False, "stuck": None}
        if self._prev_sig is not None and self._prev_args is not None:
            unchanged = same_screen(sig, self._prev_sig)
            kind = self._prev_args.get("action")
            if kind == "wait":
                self._waits = self._waits + 1 if unchanged else 0
                if self._waits >= self.max_waits:
                    out["stuck"] = f"waited {self._waits} times and the screen never changed"
            else:
                self._waits = 0
            if kind in EFFECTFUL and unchanged:
                out["no_effect"] = True
                self.no_effect_total += 1
                key = action_key(self._prev_args)
                self._streak = self._streak + 1 if key == self._streak_key else 1
                self._streak_key = key
                if self._streak >= self.max_repeats:
                    out["stuck"] = f"repeated {kind} {self._streak} times with no visible effect"
            elif kind in EFFECTFUL:
                self._streak, self._streak_key = 0, None
        self._prev_sig = sig
        return out

    def acted(self, args: dict) -> None:
        """Record the action the model chose on the screen last passed to observe()."""
        self._prev_args = args
