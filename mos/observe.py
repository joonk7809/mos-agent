"""Build one observation (screenshot and/or element list) in the format the model sees."""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from . import uitree
from .device import Device

# Claude downsizes images whose long edge exceeds ~1568px, which would silently
# break coordinates. Resize ourselves and map coordinates back.
MAX_EDGE = 1568


@dataclass
class Observation:
    png: bytes                      # what the model saw (resized, maybe annotated)
    scale: float                    # device_px = model_px * scale
    elements: list[uitree.Element] = field(default_factory=list)
    app: str = ""

    def to_device(self, x: float, y: float) -> tuple[int, int]:
        return round(x * self.scale), round(y * self.scale)

    def content_blocks(self, mode: str) -> list[dict]:
        blocks: list[dict] = []
        if mode in ("screenshot", "both", "som"):
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.standard_b64encode(self.png).decode()}})
        text = [f"Foreground: {self.app}" if self.app else ""]
        if mode in ("tree", "both", "som"):
            text.append("Elements (id, type, label, center in device px, flags):\n" + uitree.render(self.elements))
        if mode in ("screenshot",):
            text.append(f"Screenshot coordinates are image pixels; the image is {self.width}x{self.height}.")
        blocks.append({"type": "text", "text": "\n".join(t for t in text if t)})
        return blocks

    @property
    def width(self) -> int:
        return Image.open(io.BytesIO(self.png)).width

    @property
    def height(self) -> int:
        return Image.open(io.BytesIO(self.png)).height


def observe(device: Device, mode: str) -> Observation:
    img = Image.open(io.BytesIO(device.screenshot_png())).convert("RGB")
    elements = uitree.parse(device.ui_xml()) if mode in ("tree", "both", "som") else []
    if mode == "som":
        _draw_marks(img, elements)
    scale = max(img.width, img.height) / MAX_EDGE
    if scale > 1:
        img = img.resize((round(img.width / scale), round(img.height / scale)), Image.LANCZOS)
    else:
        scale = 1.0
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    try:
        app = device.current_app()
    except Exception:
        app = ""
    return Observation(png=buf.getvalue(), scale=scale, elements=elements, app=app)


def _draw_marks(img: Image.Image, elements: list[uitree.Element]) -> None:
    """Set-of-marks: draw each element's box and id onto the screenshot."""
    d = ImageDraw.Draw(img)
    for e in elements:
        if not (e.clickable or e.editable or e.scrollable):
            continue
        d.rectangle(e.bounds, outline=(255, 0, 80), width=3)
        x1, y1 = e.bounds[:2]
        label = str(e.id)
        d.rectangle((x1, y1, x1 + 14 * len(label) + 8, y1 + 26), fill=(255, 0, 80))
        d.text((x1 + 4, y1 + 4), label, fill=(255, 255, 255))
