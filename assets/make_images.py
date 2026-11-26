"""Generate the banner pictures attached to price / weather messages (1280x640 JPEG).

Run (from LifeBots/):  python assets/make_images.py
Needs Pillow and the Windows fonts 'Segoe UI Emoji' + 'Segoe UI Bold'. The JPEGs are committed to
the repo and the bots send them by URL (raw.githubusercontent.com), so runners need no Pillow.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H = 1280, 640
EMOJI_FONT = "C:/Windows/Fonts/seguiemj.ttf"
TEXT_FONT = "C:/Windows/Fonts/segoeuib.ttf"

# file -> (emoji, caption, top colour, bottom colour)
IMAGES = {
    "price_gold": ("💰", "Giá vàng trong nước", (255, 205, 80), (196, 112, 10)),
    "price_world": ("🌍", "Giá vàng thế giới", (255, 215, 110), (150, 95, 20)),
    "price_petrol": ("⛽", "Giá xăng dầu", (255, 130, 90), (150, 30, 30)),
    "price_usd": ("💵", "Tỷ giá USD/VND", (110, 215, 140), (20, 110, 70)),
    "w_sun": ("☀️", "Trời nắng", (110, 200, 255), (30, 120, 215)),
    "w_night": ("🌙", "Trời đêm quang", (40, 50, 120), (10, 15, 50)),
    "w_partly": ("⛅", "Có mây", (120, 200, 255), (40, 120, 200)),
    "w_cloud": ("☁️", "Nhiều mây", (150, 170, 195), (70, 85, 110)),
    "w_rain": ("🌧", "Có mưa", (110, 140, 175), (35, 55, 95)),
    "w_storm": ("⛈", "Có dông", (80, 85, 125), (25, 20, 60)),
    "w_fog": ("🌫", "Sương mù", (190, 200, 210), (110, 120, 135)),
    "w_heat": ("🥵", "Nắng nóng", (255, 170, 80), (200, 50, 20)),
    "w_cold": ("🥶", "Trời lạnh", (150, 210, 255), (50, 100, 190)),
    "w_aqi": ("😷", "Không khí xấu", (190, 170, 140), (95, 75, 55)),
}


def gradient(top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        px.line(
            [(0, y), (W, y)],
            fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)),
        )
    return img


def make(name: str, emoji: str, caption: str, top: tuple, bottom: tuple) -> Path:
    img = gradient(top, bottom).convert("RGBA")
    draw = ImageDraw.Draw(img)
    efont = ImageFont.truetype(EMOJI_FONT, 300)
    layer = Image.new("RGBA", (700, 700), (0, 0, 0, 0))  # roomy canvas: glyph is never clipped
    ImageDraw.Draw(layer).text((100, 100), emoji, font=efont, embedded_color=True)
    bbox = layer.getbbox() or (0, 0, 700, 700)
    glyph = layer.crop(bbox)
    img.alpha_composite(glyph, ((W - glyph.width) // 2, 60 + (390 - glyph.height) // 2))
    tfont = ImageFont.truetype(TEXT_FONT, 76)
    tb = draw.textbbox((0, 0), caption, font=tfont)
    tx, ty = (W - (tb[2] - tb[0])) / 2 - tb[0], 470
    draw.text((tx + 3, ty + 3), caption, font=tfont, fill=(0, 0, 0, 90))  # soft shadow
    draw.text((tx, ty), caption, font=tfont, fill=(255, 255, 255, 255))
    path = OUT / f"{name}.jpg"
    img.convert("RGB").save(path, quality=85, optimize=True)
    return path


if __name__ == "__main__":
    for name, (emoji, caption, top, bottom) in IMAGES.items():
        print("wrote", make(name, emoji, caption, top, bottom))
