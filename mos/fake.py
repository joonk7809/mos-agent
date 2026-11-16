"""A fake device for offline tests and demos: serves fixed screens, records actions."""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.example.notes" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,2400]">
    <node index="0" text="Notes" resource-id="com.example.notes:id/title" class="android.widget.TextView" package="com.example.notes" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[48,120][400,220]" />
    <node index="1" text="" resource-id="com.example.notes:id/list" class="androidx.recyclerview.widget.RecyclerView" package="com.example.notes" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="true" focused="false" scrollable="true" long-clickable="false" password="false" selected="false" bounds="[0,240][1080,2100]">
      <node index="0" text="Groceries" resource-id="" class="android.widget.TextView" package="com.example.notes" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="true" password="false" selected="false" bounds="[48,260][1032,400]" />
      <node index="1" text="" resource-id="" class="android.widget.CheckBox" package="com.example.notes" content-desc="Pin note" checkable="true" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[900,420][1032,540]" />
    </node>
    <node index="2" text="" resource-id="com.example.notes:id/search" class="android.widget.EditText" package="com.example.notes" content-desc="Search notes" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[48,2120][800,2260]" />
    <node index="3" text="" resource-id="com.example.notes:id/fab" class="android.widget.ImageButton" package="com.example.notes" content-desc="New note" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[860,2120][1032,2292]" />
    <node index="4" text="" resource-id="" class="android.view.View" package="com.example.notes" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][0,0]" />
  </node>
</hierarchy>"""


def sample_png(w: int = 1080, h: int = 2400) -> bytes:
    img = Image.new("RGB", (w, h), (250, 250, 250))
    d = ImageDraw.Draw(img)
    d.text((60, 150), "Notes", fill=(0, 0, 0))
    d.rectangle((48, 260, 1032, 400), outline=(180, 180, 180), width=2)
    d.text((70, 320), "Groceries", fill=(0, 0, 0))
    d.ellipse((860, 2120, 1032, 2292), fill=(60, 90, 220))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class FakeDevice:
    xml: str = SAMPLE_XML
    png: bytes = field(default_factory=sample_png)
    size: tuple[int, int] = (1080, 2400)
    actions: list[tuple] = field(default_factory=list)

    def screenshot_png(self) -> bytes: return self.png
    def ui_xml(self) -> str: return self.xml
    def screen_size(self) -> tuple[int, int]: return self.size
    def current_app(self) -> str: return "com.example.notes/.MainActivity"
    def list_packages(self) -> list[str]: return ["com.example.notes"]
    def tap(self, x, y): self.actions.append(("tap", x, y))
    def long_press(self, x, y, ms=800): self.actions.append(("long_press", x, y))
    def swipe(self, x1, y1, x2, y2, ms=300): self.actions.append(("swipe", x1, y1, x2, y2))
    def type_text(self, text): self.actions.append(("type", text))
    def key(self, name): self.actions.append(("key", name))
    def open_app(self, package): self.actions.append(("open", package))
