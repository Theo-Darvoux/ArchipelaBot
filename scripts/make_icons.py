"""Generates the bot's icons into src/archipelabot/assets/icons (run after changing this file).

Glyphs come from Google's Material Symbols Rounded (Apache 2.0), downloaded into .dev/fonts:
    https://github.com/google/material-design-icons/tree/master/variablefont
Colours follow the Archipelago text client, plus Discord's own greys.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parents[1]
FONT = ROOT / ".dev" / "fonts" / "MaterialSymbolsRounded.ttf"
CODEPOINTS = ROOT / ".dev" / "fonts" / "MaterialSymbolsRounded.codepoints"
OUT = ROOT / "src" / "archipelabot" / "assets" / "icons"

SIZE = 128
SCALE = 4  # draw big, then downsample: cheap anti-aliasing

# Archipelago's text client
PROGRESSION = "#AF99EF"
USEFUL = "#6D8BE8"
FILLER = "#00EEEE"
TRAP = "#FA8072"
# Discord
GREEN = "#23A55A"
GREY = "#80848E"
LIGHT = "#DBDEE1"
RED = "#F23F43"
ORANGE = "#F0A030"
GOLD = "#F0B232"
BLURPLE = "#949CF7"
TRACK = "#4E505899"

RING_STEPS = 8

# name -> (Material Symbols glyph, colour)
GLYPHS = {
    "release": ("upload", PROGRESSION),
    "collect": ("download", FILLER),
    "ping": ("notifications", GOLD),
    "trophy": ("trophy", GOLD),
    "death": ("skull", LIGHT),
    "hint": ("lightbulb", GOLD),
    "chat": ("chat_bubble", USEFUL),
    "priority": ("star", GOLD),
    "no_priority": ("arrow_downward", GREY),
    "avoid": ("block", TRAP),
    "connecting": ("hourglass_top", GREY),
    "reconnecting": ("sync", ORANGE),
    "asleep": ("bedtime", BLURPLE),
    "failed": ("error", RED),
    "stopped": ("stop_circle", GREY),
    "claim": ("front_hand", PROGRESSION),
    "settings": ("settings", LIGHT),
    "notif_thread": ("notifications", GOLD),
    "notif_dm": ("mail", USEFUL),
    "notif_off": ("notifications_off", GREY),
    "success": ("check_circle", GREEN),
    "error": ("cancel", RED),
    "warning": ("warning", ORANGE),
    "info": ("info", BLURPLE),
}


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", (SIZE * SCALE, SIZE * SCALE), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def finish(image: Image.Image) -> Image.Image:
    return image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def dot(colour: str, diameter: float = 0.42) -> Image.Image:
    image, draw = canvas()
    radius, centre = SIZE * SCALE * diameter / 2, SIZE * SCALE / 2
    draw.ellipse((centre - radius, centre - radius, centre + radius, centre + radius), fill=colour)
    return finish(image)


def ring(step: int) -> Image.Image:
    image, draw = canvas()
    margin, width = SIZE * SCALE * 0.12, int(SIZE * SCALE * 0.13)
    box = (margin, margin, SIZE * SCALE - margin, SIZE * SCALE - margin)
    draw.ellipse(box, outline=TRACK, width=width)
    if step:
        draw.arc(box, -90, -90 + 360 * step / RING_STEPS, fill=PROGRESSION, width=width)
    return finish(image)


def goal() -> Image.Image:
    image, draw = canvas()
    full = SIZE * SCALE
    draw.ellipse((full * 0.12, full * 0.12, full * 0.88, full * 0.88), fill=GOLD)
    check = [(full * 0.32, full * 0.52), (full * 0.45, full * 0.65), (full * 0.70, full * 0.38)]
    draw.line(check, fill="#FFFFFF", width=int(full * 0.09), joint="curve")
    return finish(image)


def pill(colour: str) -> Image.Image:
    image, draw = canvas()
    full = SIZE * SCALE
    draw.rounded_rectangle((full * 0.04, full * 0.30, full * 0.96, full * 0.70), radius=full * 0.2, fill=colour)
    return finish(image)


def glyph(name: str, colour: str, codepoints: dict[str, str]) -> Image.Image:
    font = ImageFont.truetype(str(FONT), int(SIZE * SCALE * 0.86))
    font.set_variation_by_axes([1, 0, 48, 500])  # filled, normal grade, 48 px optical size, medium weight
    image, draw = canvas()
    centre = SIZE * SCALE / 2
    draw.text((centre, centre), chr(int(codepoints[name], 16)), font=font, fill=colour, anchor="mm")
    return finish(image)


def main() -> None:
    codepoints = dict(line.split() for line in CODEPOINTS.read_text().splitlines())
    icons: dict[str, Image.Image] = {
        "prog": dot(PROGRESSION),
        "useful": dot(USEFUL),
        "filler": dot(FILLER),
        "trap": dot(TRAP),
        "online": dot(GREEN, 0.34),
        "offline": dot(GREY, 0.34),
        "goal": goal(),
        "bar_on": pill(PROGRESSION),
        "bar_off": pill(TRACK),
        **{f"ring_{i}": ring(i) for i in range(RING_STEPS + 1)},
        **{name: glyph(symbol, colour, codepoints) for name, (symbol, colour) in GLYPHS.items()},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    for name, image in icons.items():
        image.save(OUT / f"{name}.png", optimize=True)
    print(f"{len(icons)} icons written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
