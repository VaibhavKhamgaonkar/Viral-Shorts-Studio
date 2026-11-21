from pathlib import Path
from textwrap import wrap


HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00100C14,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,96,96,425,1
Style: Hook,Arial,69,&H00{color},&H00FFFFFF,&H00100C14,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,8,104,104,370,1
Style: Brand,Arial,37,&H00{color},&H00FFFFFF,&H00100C14,&H80000000,-1,0,0,0,100,100,1,0,1,3,1,7,76,96,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def format_ass_time(seconds):
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"


def ass_text(text):
    return " ".join(text.split()).replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def event(style, start, end, text):
    return f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},{style},,0,0,0,,{text}"


def generate_ass_subtitles(
    transcript_segments,
    clip_start,
    clip_end,
    output_ass_path,
    *,
    captions=True,
    caption_style="bold",
    caption_color="#FFFF00",
    opening_hook="",
    brand_name="",
):
    color = caption_color[5:7] + caption_color[3:5] + caption_color[1:3]
    is_bold = caption_style == "bold"
    duration = clip_end - clip_start
    events = []
    if brand_name:
        events.append(event("Brand", 0, duration, ass_text(brand_name)))
    if opening_hook:
        hook = r"\N".join(wrap(ass_text(opening_hook), width=25))
        events.append(event("Hook", 0, min(3, duration), hook))

    if captions:
        for segment in transcript_segments:
            start = max(0, segment["start"] - clip_start)
            end = min(duration, segment["end"] - clip_start)
            if end - start < 0.2:
                continue
            text = ass_text(segment["text"])
            words = (text.upper() if is_bold else text).split()
            group_size = 4 if is_bold else 7
            groups = [words[idx:idx + group_size] for idx in range(0, len(words), group_size)]
            for idx, group in enumerate(groups):
                chunk_start = start + (end - start) * idx / len(groups)
                chunk_end = start + (end - start) * (idx + 1) / len(groups)
                if is_bold:
                    group[0] = r"{\c&H" + color + r"&}" + group[0] + r"{\c}"
                events.append(event("Caption", chunk_start, chunk_end, " ".join(group)))

    header = HEADER.format(font="Impact" if is_bold else "Arial", size=69 if is_bold else 55, color=color)
    Path(output_ass_path).write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return str(output_ass_path)
