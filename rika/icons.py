"""Tray icon images drawn with Pillow (no image files needed at runtime)."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

STATE_COLORS = {
    "idle": (255, 255, 255),
    "listening": (80, 220, 120),
    "thinking": (255, 190, 60),
    "speaking": (110, 180, 255),
    "working": (255, 190, 60),
    "paused": (150, 150, 150),
    "error": (240, 70, 70),
    "starting": (255, 190, 60),
}


def _font(size: int):
    for name in ("seguibl.ttf", "segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon(state: str = "idle", size: int = 64) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    body = (150, 150, 150) if state == "paused" else (255, 105, 180)
    d.ellipse((1, 1, s - 2, s - 2), fill=body, outline=(255, 255, 255), width=max(1, s // 32))
    font = _font(int(s * 0.62))
    d.text((s / 2, s / 2 + s * 0.02), "R", font=font, fill=(255, 255, 255), anchor="mm")
    if state != "idle":
        r = s * 0.17
        cx, cy = s - r - 1, s - r - 1
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=STATE_COLORS.get(state, (255, 255, 255)), outline=(40, 40, 40))
    return img
