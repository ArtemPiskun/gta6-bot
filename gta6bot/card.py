"""Branded cover image for posts that have no picture of their own (Pillow)."""
from __future__ import annotations

import io
import random
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
W, H = 1280, 720
CAT_LABEL = {"news": "НОВОСТИ", "update": "ОБНОВЛЕНИЕ", "guide": "ГАЙД", "video": "ВИДЕО", "leak": "СЛУХ", "site": "NEW WEBSITE"}
PALETTES = [  # Vice City skies: sunset, dusk, night, neon, tropical day
    ((26, 11, 46), (255, 46, 136), (255, 154, 60)),
    ((10, 18, 48), (0, 180, 216), (255, 46, 136)),
    ((30, 8, 40), (155, 39, 176), (255, 200, 87)),
    ((6, 10, 30), (24, 40, 92), (120, 60, 160)),
    ((14, 6, 36), (90, 20, 140), (0, 210, 200)),
    ((40, 120, 190), (255, 160, 120), (255, 214, 150)),
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
    scene = pick_scene(category, rnd)
    top, mid, bot = PALETTES[rnd.choice(SCENE_PALETTES.get(scene.__name__, range(len(PALETTES))))]
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
    img = scene(img, rnd)
    d = ImageDraw.Draw(img, "RGBA")
    # darken the text area
    d.rectangle((0, 0, W, H), fill=(0, 0, 0, 35))
    start = int(H * 0.45)
    for y in range(start, H):
        a = int(160 * (y - start) / (H - start))
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


# ---------------------------------------------------------------- scenes
def _sun(img, rnd, cx, cy, r, stripes=True, colors=((255, 220, 110), (255, 60, 140))):
    from PIL import Image, ImageDraw
    sun = Image.new("RGBA", (2 * r, 2 * r))
    sd = ImageDraw.Draw(sun)
    for yy in range(2 * r):
        sd.line((0, yy, 2 * r, yy), fill=(*_lerp(colors[0], colors[1], yy / (2 * r)), 220))
    mask = Image.new("L", (2 * r, 2 * r))
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
    if stripes:
        for k in range(6):
            yy = r + 10 + k * int(r * 0.15)
            md.rectangle((0, yy, 2 * r, yy + 4 + k * 2), fill=0)
    img.paste(sun, (cx - r, cy - r), mask)


def scene_sunset(img, rnd):
    from PIL import ImageDraw
    _sun(img, rnd, rnd.choice([W - 230, W // 2 + 260]), 290, rnd.randint(140, 190))
    d = ImageDraw.Draw(img, "RGBA")
    for i in range(14):
        y = 380 + i * i * 2.2
        if y < H:
            d.line((0, y, W, y), fill=(255, 255, 255, 18), width=2)
    return img


def _palm(d, x, base, h, lean, color, rnd):
    pts, cx, cy = [], x, base
    for i in range(21):  # curved trunk
        t = i / 20
        pts.append((x + lean * t * t, base - t * h))
    for i in range(len(pts) - 1):
        w = int(22 - 12 * i / len(pts))
        d.line((pts[i], pts[i + 1]), fill=color, width=w)
    tx, ty = pts[-1]
    import math
    for k in range(8):  # fronds
        ang = -math.pi / 2 + (k - 3.5) * 0.42 + rnd.uniform(-0.1, 0.1)
        L = h * rnd.uniform(0.38, 0.5)
        frond = []
        for j in range(12):
            t = j / 11
            fx = tx + math.cos(ang) * L * t
            fy = ty + math.sin(ang) * L * t + (L * 0.55) * t * t
            frond.append((fx, fy))
        upper = [(fx, fy - 14 * (1 - abs(2 * j / 11 - 1))) for j, (fx, fy) in enumerate(frond)]
        d.polygon(upper + frond[::-1], fill=color)
    d.ellipse((tx - 16, ty - 10, tx + 16, ty + 18), fill=color)


def scene_palms(img, rnd):
    from PIL import ImageDraw
    _sun(img, rnd, rnd.randint(W // 2, W - 260), rnd.randint(220, 320), rnd.randint(110, 160),
         stripes=rnd.random() < 0.5)
    d = ImageDraw.Draw(img, "RGBA")
    color = (10, 4, 20, 255)
    d.rectangle((0, 600, W, H), fill=(10, 4, 20, 230))  # shoreline
    for x, h, lean in ((W - 120, 560, -160), (W - 300, 430, 90), (90, 380, 120)):
        _palm(d, x + rnd.randint(-30, 30), H + 10, h * rnd.uniform(0.9, 1.1), lean, color, rnd)
    return img


def scene_skyline(img, rnd):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img, "RGBA")
    mx, my = rnd.randint(W // 2, W - 200), rnd.randint(110, 200)  # moon
    d.ellipse((mx - 60, my - 60, mx + 60, my + 60), fill=(255, 240, 220, 200))
    for _ in range(70):  # stars
        x, y = rnd.randint(0, W), rnd.randint(0, 320)
        d.point((x, y), fill=(255, 255, 255, rnd.randint(80, 200)))
    for layer, (base_h, col, alpha) in enumerate(((330, (40, 20, 70), 255), (250, (14, 8, 30), 255))):
        x = -20
        while x < W:
            bw = rnd.randint(50, 120)
            bh = rnd.randint(int(base_h * 0.4), base_h) + (160 if rnd.random() < 0.08 else 0)
            top = H - bh
            d.rectangle((x, top, x + bw, H), fill=(*col, alpha))
            if layer == 1:
                for wy in range(top + 12, H - 10, 22):
                    for wx in range(x + 8, x + bw - 10, 16):
                        if rnd.random() < 0.35:
                            d.rectangle((wx, wy, wx + 7, wy + 10),
                                        fill=rnd.choice([(255, 210, 120, 220), (255, 90, 170, 200),
                                                         (90, 230, 255, 200)]))
            x += bw + rnd.randint(0, 8)
    return img


def scene_grid(img, rnd):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img, "RGBA")
    hz = 400
    _sun(img, rnd, W // 2 + rnd.randint(-200, 300), hz - 40, rnd.randint(120, 170))
    d = ImageDraw.Draw(img, "RGBA")
    pts = [(0, hz)]  # mountains
    x = 0
    while x < W:
        x += rnd.randint(60, 140)
        pts.append((x, hz - rnd.randint(30, 120)))
    pts += [(W, hz)]
    d.polygon(pts, fill=(20, 8, 40, 255))
    d.rectangle((0, hz, W, H), fill=(14, 4, 30, 255))
    neon = rnd.choice([(255, 46, 200), (0, 230, 255), (180, 80, 255)])
    vx = W // 2
    for k in range(-14, 15):
        d.line((vx + k * 18, hz, vx + k * 160, H), fill=(*neon, 170), width=2)
    for i in range(1, 12):
        y = hz + (i ** 2) * 2.4
        if y < H:
            d.line((0, y, W, y), fill=(*neon, 170), width=2)
    d.line((0, hz, W, hz), fill=(*neon, 230), width=3)
    return img


def scene_ocean(img, rnd):
    import math
    from PIL import ImageDraw
    hz = rnd.randint(330, 400)
    sx = rnd.randint(W // 3, W - 250)
    _sun(img, rnd, sx, hz - 30, rnd.randint(110, 160), stripes=False)
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle((0, hz, W, H), fill=(10, 30, 70, 230))
    for i in range(18):  # sun reflection
        y = hz + 8 + i * 14
        w = 160 - i * 6 + rnd.randint(-20, 20)
        d.rectangle((sx - w // 2, y, sx + w // 2, y + 4), fill=(255, 190, 120, 150 - i * 6))
    for row in range(10):  # waves
        y0 = hz + 20 + row * row * 3.2
        amp, ph = 3 + row, rnd.random() * 6
        pts = [(x, y0 + amp * math.sin(x / (40 + row * 6) + ph)) for x in range(0, W + 10, 10)]
        d.line(pts, fill=(160, 220, 255, 60 + row * 6), width=2)
    return img


def scene_road(img, rnd):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img, "RGBA")
    hz = 380
    _sun(img, rnd, W // 2 + rnd.randint(-120, 120), hz - 20, rnd.randint(100, 140))
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle((0, hz, W, H), fill=(28, 16, 44, 255))
    vx = W // 2 + rnd.randint(-80, 80)
    d.polygon([(vx - 6, hz), (vx + 6, hz), (W // 2 + 520, H), (W // 2 - 520, H)], fill=(40, 34, 60, 255))
    for side in (-1, 1):  # edge lines
        d.line((vx + side * 6, hz, W // 2 + side * 520, H), fill=(255, 210, 120, 200), width=4)
    for i in range(10):  # center dashes
        t0, t1 = (i / 10) ** 2, ((i + 0.5) / 10) ** 2
        y0, y1 = hz + (H - hz) * t0, hz + (H - hz) * t1
        x0, x1 = vx + (W // 2 - vx) * t0, vx + (W // 2 - vx) * t1
        d.line((x0, y0, x1, y1), fill=(255, 255, 255, 220), width=max(2, int(2 + 10 * t1)))
    for side in (-1, 1):  # street lights
        for i in range(1, 6):
            t = (i / 6) ** 1.6
            x = vx + side * (20 + 700 * t)
            y = hz + (H - hz) * t
            h = 30 + 260 * t
            d.line((x, y, x, y - h), fill=(10, 6, 20, 255), width=max(2, int(8 * t)))
            d.ellipse((x - 4 - 10 * t, y - h - 4 - 6 * t, x + 4 + 10 * t, y - h + 4 + 6 * t),
                      fill=(255, 220, 140, 230))
    return img


SCENE_PALETTES = {  # bright skies so silhouettes read, neon for the grid
    "scene_palms": [0, 2, 5], "scene_road": [0, 1, 2, 5], "scene_ocean": [0, 2, 5],
    "scene_grid": [1, 3, 4], "scene_skyline": [0, 1, 3, 4], "scene_sunset": [0, 1, 2, 4],
}
SCENES = {"sunset": scene_sunset, "palms": scene_palms, "skyline": scene_skyline,
          "grid": scene_grid, "ocean": scene_ocean, "road": scene_road}
# rubric leans: news/leak at night in the city, guides on the road, updates on the grid
LEAN = {"news": ["skyline", "palms", "ocean", "sunset"], "leak": ["skyline", "grid"],
        "guide": ["road", "palms", "ocean"], "update": ["grid", "road"], "video": ["grid", "sunset", "palms"],
        "site": ["palms", "skyline"]}


def pick_scene(category: str, rnd: random.Random):
    if rnd.random() < 0.7 and category in LEAN:
        return SCENES[rnd.choice(LEAN[category])]
    return SCENES[rnd.choice(list(SCENES))]
