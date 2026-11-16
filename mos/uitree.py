"""Turn a uiautomator XML dump into a compact, numbered element list for the model."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass
class Element:
    id: int
    cls: str
    text: str
    desc: str
    res_id: str
    bounds: tuple[int, int, int, int]
    clickable: bool
    editable: bool
    scrollable: bool
    checked: bool | None
    focused: bool
    password: bool = False

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    def describe(self) -> str:
        kind = self.cls.rsplit(".", 1)[-1]
        label = self.text or self.desc or (self.res_id.rsplit("/", 1)[-1] if self.res_id else "")
        flags = [f for f, on in (("click", self.clickable), ("edit", self.editable),
                                 ("scroll", self.scrollable), ("focused", self.focused)) if on]
        if self.checked is not None:
            flags.append("checked" if self.checked else "unchecked")
        cx, cy = self.center
        return f"[{self.id}] {kind} \"{label[:80]}\" @({cx},{cy}) {' '.join(flags)}".rstrip()

    def to_dict(self) -> dict:
        """The element format phones and the server exchange (see mos/server.py)."""
        return {"text": self.text, "desc": self.desc, "res_id": self.res_id, "cls": self.cls,
                "bounds": list(self.bounds), "clickable": self.clickable, "editable": self.editable,
                "scrollable": self.scrollable, "focused": self.focused, "password": self.password}


def parse(xml: str) -> list[Element]:
    """Keep only nodes a user could perceive or act on, numbered in document order."""
    if not xml.strip():
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out: list[Element] = []
    for node in root.iter("node"):
        a = node.attrib
        m = _BOUNDS.match(a.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x2 - x1 <= 1 or y2 - y1 <= 1:
            continue
        text, desc = a.get("text", "").strip(), a.get("content-desc", "").strip()
        clickable = a.get("clickable") == "true" or a.get("long-clickable") == "true"
        cls = a.get("class", "")
        editable = "EditText" in cls
        scrollable = a.get("scrollable") == "true"
        if not (text or desc or clickable or editable or scrollable):
            continue  # pure layout containers
        checked = (a.get("checked") == "true") if a.get("checkable") == "true" else None
        out.append(Element(
            id=len(out), cls=cls, text=text, desc=desc, res_id=a.get("resource-id", ""),
            bounds=(x1, y1, x2, y2), clickable=clickable, editable=editable,
            scrollable=scrollable, checked=checked, focused=a.get("focused") == "true",
            password=a.get("password") == "true",
        ))
    return out


def render(elements: list[Element], limit: int = 150) -> str:
    if not elements:
        return "(no accessibility tree available; rely on the screenshot)"
    lines = [e.describe() for e in elements[:limit]]
    if len(elements) > limit:
        lines.append(f"... {len(elements) - limit} more elements omitted")
    return "\n".join(lines)
