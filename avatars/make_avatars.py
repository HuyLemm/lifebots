"""Generate the 4 bot profile pictures (512x512 PNG): gradient background + big emoji.

Run (from LifeBots/):  python avatars/make_avatars.py
Needs Pillow (pip install pillow) and the Windows 'Segoe UI Emoji' font.
Upload each PNG via @BotFather > /setuserpic > pick the bot.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
SIZE = 512
FONT = "C:/Windows/Fonts/seguiemj.ttf"

# name -> (emoji, top colour, bottom colour)
AVATARS = {
    "price": ("🥇", (255, 196, 61), (214, 120, 12)),
    "news": ("📰", (79, 140, 255), (28, 54, 140)),
    "weather": ("⛅", (96, 200, 255), (24, 108, 190)),
    "stuff": ("🛍", (255, 120, 170), (170, 40, 110)),
}


def gradient(top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE))
    px = ImageDraw.Draw(img)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        px.line(
            [(0, y), (SIZE, y)],
            fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)),
        )
    return img


def make(name: str, emoji: str, top: tuple, bottom: tuple) -> Path:
    img = gradient(top, bottom).convert("RGBA")
    font = ImageFont.truetype(FONT, 280)
    draw = ImageDraw.Draw(img)
    box = draw.textbbox((0, 0), emoji, font=font, embedded_color=True)
    x = (SIZE - (box[2] - box[0])) / 2 - box[0]
    y = (SIZE - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), emoji, font=font, embedded_color=True)
    path = OUT / f"{name}.png"
    img.convert("RGB").save(path)
    return path


if __name__ == "__main__":
    for name, (emoji, top, bottom) in AVATARS.items():
        print("wrote", make(name, emoji, top, bottom))
