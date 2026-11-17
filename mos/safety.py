"""Decide which actions need the user's OK before the phone executes them.

MAI-UI doesn't flag risky actions itself, so the server checks what the action
would touch: the label of the element under a tap, or the field being typed in.
This is a heuristic safety net, not a guarantee; the phone app also always
shows what the agent is doing and has a stop button.
"""
from __future__ import annotations

import re

RISKY_WORDS = re.compile(
    r"\b(send|pay|payment|buy|purchase|order|checkout|check out|place order|delete|remove|erase|"
    r"post|publish|share|transfer|submit|confirm|book|reserve|subscribe|call|dial|uninstall|"
    r"reset|factory|sign out|log out|accept|agree|install|allow|grant)\b", re.IGNORECASE)

# A card number as people type it: 12-19 digits in one run, or 4-digit groups (Amex 4-6-5)
# split by one consistent space/dash/dot (or a doubled one). Groups keep dates and phone numbers from matching.
_SEP = r"[ .\-\u2013\u2014]{1,2}"
CARD_NUMBER = re.compile(
    rf"(?<!\d)(?:\d{{12,19}}|\d{{4}}({_SEP})\d{{4}}\1\d{{4}}\1\d{{1,7}}|\d{{4}}({_SEP})\d{{6}}\2\d{{5}})(?!\d)")


def element_at(elements: list[dict], x: int, y: int) -> dict | None:
    """Smallest element whose bounds contain (x, y)."""
    best, best_area = None, None
    for e in elements:
        b = e.get("bounds")
        if not b or len(b) != 4:
            continue
        x1, y1, x2, y2 = b
        if x1 <= x <= x2 and y1 <= y <= y2:
            area = (x2 - x1) * (y2 - y1)
            if best_area is None or area < best_area:
                best, best_area = e, area
    return best


def label(e: dict | None) -> str:
    if not e:
        return ""
    rid = (e.get("res_id") or "").rsplit("/", 1)[-1].replace("_", " ")
    return " ".join(p for p in (e.get("text"), e.get("desc"), rid) if p).strip()


def needs_confirmation(action: dict, elements: list[dict]) -> str | None:
    """Return a short human reason if this device action should be confirmed, else None."""
    kind = action.get("type")
    if kind in ("click", "long_press"):
        target = element_at(elements, action["x"], action["y"])
        text = label(target)
        if text and RISKY_WORDS.search(text):
            return f'Tap "{text[:60]}"'
    if kind == "type":
        focused = next((e for e in elements if e.get("focused") and e.get("editable")), None)
        if focused and focused.get("password"):
            return "Type into a password field"
        if CARD_NUMBER.search(action.get("text", "")):
            return "Type what looks like a card number"
    return None
