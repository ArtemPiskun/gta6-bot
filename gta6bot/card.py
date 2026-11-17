"""Branded cover image for posts that have no picture of their own (Pillow)."""
from __future__ import annotations

import io
import random
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
W, H = 1280, 720
CAT_LABEL = {"news": "НОВОСТИ", "update": "ОБНОВЛЕНИЕ", "guide": "ГАЙД", "video": "ВИДЕО", "leak": "СЛУХ"}
PALETTES = [  # Vice City sunsets
    ((26, 11, 46), (255, 46, 136), (255, 154, 60)),
    ((10, 18, 48), (0, 180, 216), (255, 46, 136)),
    ((30, 8, 40), (155, 39, 176), (255, 200, 87)),
]


def available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=font) <= width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_card(title: str, category: str = "news", source: str = "", channel: str = "",
              background: bytes | None = None, seed: str = "") -> bytes:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    rnd = random.Random(seed or title)
    top, mid, bot = rnd.choice(PALETTES)
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(0, W, 4):
            t = min(1, max(0, (y / H) * 0.75 + (x / W) * 0.35))
            c = _lerp(top, mid, t / 0.6) if t < 0.6 else _lerp(mid, bot, (t - 0.6) / 0.4)
            for dx in range(4):
                px[x + dx, y] = c
    if background:
        try:
            bg = Image.open(io.BytesIO(background)).convert("RGB")
            scale = max(W / bg.width, H / bg.height)
            bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1))
            bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
            bg = bg.filter(ImageFilter.GaussianBlur(6))
            img = Image.blend(img, bg, 0.55)
        except Exception:
            pass
    d = ImageDraw.Draw(img, "RGBA")
    # retro sun + horizon lines
    cx, cy, r = W - 230, 290, 170
    sun = Image.new("RGBA", (2 * r, 2 * r))
    sd = ImageDraw.Draw(sun)
    for yy in range(2 * r):
        sd.line((0, yy, 2 * r, yy), fill=(*_lerp((255, 220, 110), (255, 60, 140), yy / (2 * r)), 210))
    mask = Image.new("L", (2 * r, 2 * r))
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
    for k in range(6):  # retro stripes in the lower half
        yy = r + 10 + k * 26
        md.rectangle((0, yy, 2 * r, yy + 4 + k * 2), fill=0)
    img.paste(sun, (cx - r, cy - r), mask)
    d = ImageDraw.Draw(img, "RGBA")
    for i in range(14):
        y = 380 + i * i * 2.2
        if y < H:
            d.line((0, y, W, y), fill=(255, 255, 255, 18), width=2)
    # darken the text area
    d.rectangle((0, 0, W, H), fill=(0, 0, 0, 70))
    for y in range(H // 3, H):
        a = int(170 * (y - H // 3) / (H - H // 3))
        d.line((0, y, W, y), fill=(8, 4, 20, a))

    bold = ImageFont.truetype(str(ASSETS / "DejaVuSans-Bold.ttf"), 30)
    small = ImageFont.truetype(str(ASSETS / "DejaVuSans.ttf"), 28)
    pad = 64
    label = f"GTA 6  ·  {CAT_LABEL.get(category, 'НОВОСТИ')}"
    lw = d.textlength(label, font=bold)
    d.rounded_rectangle((pad, pad, pad + lw + 40, pad + 54), radius=27, fill=(255, 46, 136, 235))
    d.text((pad + 20, pad + 10), label, font=bold, fill="white")

    size = 72
    while size > 38:
        font = ImageFont.truetype(str(ASSETS / "DejaVuSans-Bold.ttf"), size)
        lines = _wrap(d, title, font, W - 2 * pad)
        if len(lines) <= 4:
            break
        size -= 6
    lines = lines[:4]
    lh = int(size * 1.18)
    y = H - pad - 50 - lh * len(lines)
    for ln in lines:
        d.text((pad + 3, y + 3), ln, font=font, fill=(0, 0, 0, 160))
        d.text((pad, y), ln, font=font, fill="white")
        y += lh
    foot = "  ·  ".join(x for x in (source, channel) if x)
    if foot:
        d.text((pad, H - pad - 30), foot, font=small, fill=(255, 220, 235, 230))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue()
