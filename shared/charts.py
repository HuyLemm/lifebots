"""Tiny chart renderer (Pillow) for photos attached to messages. Returns JPEG bytes, or None when
Pillow / a font is unavailable - callers then simply send the text without a picture."""

import io
import logging

log = logging.getLogger(__name__)
W, H = 1000, 560
FONTS = [
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # GitHub's ubuntu runners
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "DejaVuSans.ttf",
]
BOLD = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
]


def _font(paths: list[str], size: int):
    from PIL import ImageFont

    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _canvas(title: str, subtitle: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), (250, 251, 253))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 78], fill=(36, 62, 110))
    d.text((28, 12), title, font=_font(BOLD, 30), fill=(255, 255, 255))
    d.text((28, 48), subtitle, font=_font(FONTS, 18), fill=(200, 215, 240))
    return img, d


def _save(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def line_chart(
    values: list[float],
    labels: list[str],
    title: str,
    subtitle: str = "",
    fmt=lambda v: f"{v:,.1f}",
    color: tuple = (214, 120, 12),
) -> bytes | None:
    """values over time (oldest first); labels = x labels for first / middle / last points."""
    try:
        if len(values) < 2:
            return None
        img, d = _canvas(title, subtitle)
        small = _font(FONTS, 17)
        left, right, top, bottom = 110, W - 40, 120, H - 70
        lo, hi = min(values), max(values)
        pad = (hi - lo) * 0.1 or abs(hi) * 0.01 or 1
        lo, hi = lo - pad, hi + pad
        for i in range(5):  # grid + y labels
            y = top + (bottom - top) * i / 4
            d.line([(left, y), (right, y)], fill=(225, 228, 235), width=1)
            d.text((10, y - 9), fmt(hi - (hi - lo) * i / 4), font=small, fill=(90, 98, 115))
        n = len(values)
        pts = [
            (left + (right - left) * i / (n - 1), bottom - (bottom - top) * (v - lo) / (hi - lo))
            for i, v in enumerate(values)
        ]
        d.line(pts, fill=color, width=4, joint="curve")
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=color)
        d.text((pts[-1][0] - 90, pts[-1][1] - 34), fmt(values[-1]), font=_font(BOLD, 22), fill=color)
        for i, lab in zip((0, n // 2, n - 1), labels[:3], strict=False):
            d.text((pts[i][0] - 30, bottom + 14), lab, font=small, fill=(90, 98, 115))
        return _save(img)
    except Exception:  # noqa: BLE001 - a chart is a bonus, never a reason to lose the message
        log.exception("line_chart failed")
        return None


def bar_chart(
    values: list[float],
    labels: list[str],
    title: str,
    subtitle: str = "",
    unit: str = "",
    color: tuple = (47, 128, 237),
    highlight: set[int] | None = None,
    vmax: float | None = None,
) -> bytes | None:
    """One bar per label (e.g. rain chance per hour). `highlight` = indices drawn in red."""
    try:
        if not values:
            return None
        img, d = _canvas(title, subtitle)
        small, tiny = _font(FONTS, 17), _font(FONTS, 14)
        left, right, top, bottom = 70, W - 30, 120, H - 70
        hi = vmax or max(max(values), 1)
        for i in range(5):
            y = top + (bottom - top) * i / 4
            d.line([(left, y), (right, y)], fill=(225, 228, 235), width=1)
            d.text((12, y - 9), f"{hi - hi * i / 4:.0f}{unit}", font=small, fill=(90, 98, 115))
        n = len(values)
        slot = (right - left) / n
        for i, v in enumerate(values):
            x0 = left + slot * i + slot * 0.15
            x1 = left + slot * (i + 1) - slot * 0.15
            y = bottom - (bottom - top) * min(v, hi) / hi
            hot = highlight and i in highlight
            d.rectangle([x0, y, x1, bottom], fill=(220, 70, 60) if hot else color)
            if n <= 14 or i % 2 == 0:
                d.text((x0 - 2, bottom + 10), labels[i], font=tiny, fill=(90, 98, 115))
        return _save(img)
    except Exception:  # noqa: BLE001
        log.exception("bar_chart failed")
        return None
