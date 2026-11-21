import math
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from core.subtitles import ass_text, format_ass_time


REACTIONS = {
    "laugh": ("Laugh", "Punchline", "#be77df", "#f8ce7b", "HA!"),
    "cry": ("Cry", "Sad turn", "#496fa3", "#bad9ff", "OH NO"),
    "surprise": ("Surprise", "Unexpected reveal", "#8d5fd4", "#e3f88b", "WHAT?!"),
    "cheer": ("Celebrate", "Win or payoff", "#387e6d", "#e5f789", "YES!"),
    "confused": ("Confused", "Odd moment", "#a46d42", "#ffe094", "HUH?"),
    "facepalm": ("Facepalm", "Funny mistake", "#a85577", "#ffd0ad", "OOPS"),
}
FRAMES = {"neon": "Neon pulse", "sparkles": "Corner sparkles", "sweep": "Light sweep", "orbit": "Orbit backdrop"}
REACTION_DURATION = .96
TEXT_COLORS = {"lime": ("#ddf76d", "6DF7DD"), "white": ("#fdf8f3", "F3F8FD"), "pink": ("#ffa8dd", "DDA8FF")}


@lru_cache(maxsize=6)
def reaction_gif(name):
    _, _, backdrop, face, caption = REACTIONS[name]
    frames = []
    font = ImageFont.load_default(size=26)
    for index in range(24):
        phase = index / 24 * math.tau
        image = Image.new("RGB", (240, 426), backdrop)
        draw = ImageDraw.Draw(image)
        for ray in range(12):
            turn = ray * math.tau / 12 + phase / 5
            draw.line((120, 200, 120 + 260 * math.cos(turn), 200 + 260 * math.sin(turn)),
                      fill="#301f48", width=13)
        y = 193 + round(9 * math.sin(phase * 2))
        draw.ellipse((47, y - 63, 197, y + 83), fill="#251b31")
        draw.ellipse((42, y - 71, 192, y + 75), fill=face, outline="#251b31", width=5)
        for x in (91, 144):
            if name in {"laugh", "cheer"}:
                draw.arc((x - 13, y - 20, x + 13, y + 3), 180, 360, fill="#251b31", width=5)
            else:
                draw.ellipse((x - 5, y - 17, x + 5, y + 1), fill="#251b31")
        if name in {"laugh", "cheer", "surprise"}:
            spread = 22 if name == "surprise" else 29
            draw.ellipse((117 - spread, y + 9, 117 + spread, y + 35 + round(5 * math.sin(phase))),
                         fill="#251b31")
        else:
            draw.arc((98, y + 15, 137, y + 40), 180 if name == "cry" else 0,
                     360 if name == "cry" else 180, fill="#251b31", width=5)
        if name == "cry":
            for x in (88, 147):
                drop = y + index % 12 * 4
                draw.ellipse((x - 4, drop, x + 4, drop + 13), fill="#3d91ec")
        if name == "facepalm":
            draw.rounded_rectangle((55, y - 30, 158, y + 11), 18, fill="#ffae9f", outline="#251b31", width=4)
        if name in {"cheer", "surprise", "confused"}:
            for mark in range(13):
                angle = mark * 2.4
                distance = 86 + (index * 3 + mark * 11) % 67
                x, yy = 120 + distance * math.cos(angle), y + distance * math.sin(angle)
                draw.ellipse((x - 3, yy - 4, x + 3, yy + 4), fill="#fff4cb")
        draw.rounded_rectangle((34, 305, 206, 362), 18, fill="#251b31")
        draw.text((120, 333), caption, anchor="mm", font=font, fill="#fff9ec")
        frames.append(image)
    return animated_gif(frames)


def animated_gif(frames, **options):
    buffer = BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=40,
                   loop=0, optimize=False, **options)
    return buffer.getvalue()


@lru_cache(maxsize=len(FRAMES))
def frame_gif(name):
    palette = [0, 0, 0, 214, 130, 248, 221, 247, 109, 255, 172, 219, 149, 215, 242, 255, 244, 216, 38, 25, 53]
    frames = []
    for index in range(32):
        image = Image.new("P", (240, 426), 6 if name == "orbit" else 0)
        image.putpalette(palette + [0] * (768 - len(palette)))
        draw = ImageDraw.Draw(image)
        if name == "neon":
            for offset in range(3):
                color = 1 + (index // 8 + offset) % 4
                draw.rounded_rectangle((6 + offset * 3, 6 + offset * 3, 233 - offset * 3, 419 - offset * 3),
                                       radius=18, outline=color, width=2)
        elif name == "sparkles":
            for spark in range(23):
                x = 12 + (spark * 29) % 43
                y = 18 + (spark * 57 + index * 3) % 105
                x, y = (239 - x if spark % 2 else x), (425 - y if spark % 3 else y)
                radius = 2 + (spark + index // 3) % 5
                draw.line((x - radius, y, x + radius, y), fill=1 + spark % 5, width=2)
                draw.line((x, y - radius, x, y + radius), fill=1 + spark % 5, width=2)
        elif name == "sweep":
            travel = index * 12
            for shift, color in enumerate((1, 3, 5, 4)):
                position = travel - 34 + shift * 5
                draw.line((2, position, 28, position + 62), fill=color, width=3)
                draw.line((211, 425 - position, 237, 363 - position), fill=color, width=3)
        else:
            angle = index * 360 / 32
            for orbit in range(8):
                inset = 20 - orbit * 12
                draw.arc((inset, 37 + inset * 2, 239 - inset, 388 - inset * 2),
                         angle + orbit * 42, angle + orbit * 42 + 85, fill=1 + orbit % 5, width=4)
        frames.append(image)
    return animated_gif(frames, transparency=0, disposal=2)


def write_editor_titles(path, titles, width, height):
    font_size = max(16, round(min(width * .079, height * .058)))
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Title,Segoe UI,{font_size},&H00FFFFFF,&H00FFFFFF,&H00271935,&H90271935,-1,0,0,0,100,100,0,0,1,{max(2, width // 160)},1,5,{width // 10},{width // 10},0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for number, title in enumerate(titles):
        x = round(width * .48)
        y = round(height * {"top": .23, "center": .48, "lower": .67}[title.position])
        millis = round((title.end - title.at) * 1000)
        intro = min(240, millis // 2)
        outro = min(150, millis // 3)
        position = rf"\pos({x},{y})"
        movement = ""
        if title.animation == "slide":
            position = rf"\move({x},{y + round(height * .07)},{x},{y},0,{intro})"
        if title.animation == "pop":
            movement = rf"\fscx65\fscy65\t(0,{intro},\fscx100\fscy100)"
        color = TEXT_COLORS[title.color][1]
        tags = rf"{{{position}{movement}\1c&H{color}&\fad({intro if title.animation == 'fade' else 60},{outro})}}"
        text = r"\N".join(ass_text(line) for line in title.text.splitlines())
        lines.append(f"Dialogue: {number},{format_ass_time(title.at)},{format_ass_time(title.end)},Title,,0,0,0,,{tags}{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
