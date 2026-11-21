import math
import random
import struct
import wave
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import av
from pydantic import BaseModel, Field, model_validator

from core.transformer import CPU_ENCODER, NVENC_ENCODER, get_ffmpeg_cmd, nvenc_available, run_encoder


# Duration, base frequency, frequency sweep, second harmonic, and noise blend.
EFFECTS = {
    "whoosh": ("Whoosh", .40, 180, 330, .08, .75),
    "pop": ("Pop", .14, 540, -450, .10, .05),
    "chime": ("Chime", .65, 880, 0, .25, 0),
    "impact": ("Impact", .55, 96, -54, .30, .12),
    "swipe": ("Swipe", .25, 880, -740, .15, .55),
    "click": ("Click", .09, 1550, -900, .38, .10),
    "riser": ("Riser", .75, 200, 1200, .20, .20),
    "glitch": ("Glitch", .33, 430, -250, .55, .25),
}


class SoundCue(BaseModel):
    effect: Literal["whoosh", "pop", "chime", "impact", "swipe", "click", "riser", "glitch"]
    at: float = Field(ge=0, allow_inf_nan=False)
    level: int = Field(default=45, ge=0, le=100)


class ClipEdit(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    zoom: int = Field(default=100, ge=100, le=150)
    mirror: bool = False
    look: Literal["original", "vivid", "monochrome"] = "original"
    source_level: int = Field(default=100, ge=0, le=100)
    music_track: str = Field(default="", max_length=255)
    music_level: int = Field(default=20, ge=0, le=100)
    cues: list[SoundCue] = Field(default_factory=list, max_length=12)
    save_mode: Literal["version", "replace"] = "version"
    source_revision: str = Field(default="", pattern=r"^\d{0,24}$")

    @model_validator(mode="after")
    def validate_range(self):
        if self.end - self.start < .24:
            raise ValueError("Keep at least a quarter second between the trim points.")
        if any(cue.at >= self.end - self.start for cue in self.cues):
            raise ValueError("Every sound effect must start before the end of the edit.")
        return self


@lru_cache(maxsize=len(EFFECTS))
def effect_wave(name):
    _, duration, base, sweep, harmonic, noise = EFFECTS[name]
    rate = 24000
    sample_count = round(rate * duration)
    buffer = BytesIO()
    random_samples = random.Random(name)
    smoothed_noise = 0
    frames = bytearray()
    for index in range(sample_count):
        position = index / sample_count
        phase = math.tau * index / rate * (base + sweep * position / 2)
        tone = math.sin(phase) + harmonic * math.sin(phase * 2.01)
        smoothed_noise = .55 * smoothed_noise + .45 * random_samples.uniform(-1, 1)
        if name in {"whoosh", "swipe", "riser"}:
            envelope = math.sin(math.pi * position) ** (1.3 if name == "riser" else 2)
        else:
            envelope = min(1, index / 110) * (1 - position) ** 3
        if name == "glitch":
            envelope *= 1 if index // 540 % 3 < 2 else .12
        sample = .55 * envelope * ((1 - noise) * tone + noise * smoothed_noise)
        frames.extend(struct.pack("<h", round(sample * 32767)))
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, rate, sample_count, "NONE", "not compressed"))
        wav.writeframes(frames)
    return buffer.getvalue()


def render_edit(video, output, edit, music, progress):
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        width, height = stream.width, stream.height
        has_audio = bool(container.streams.audio)
    duration = edit.end - edit.start
    command = [get_ffmpeg_cmd(), "-hide_banner", "-loglevel", "error", "-y",
               "-ss", str(edit.start), "-t", str(duration), "-i", str(video)]
    picture = []
    if edit.zoom != 100:
        crop_width = max(2, int(width / (edit.zoom / 100) / 2) * 2)
        crop_height = max(2, int(height / (edit.zoom / 100) / 2) * 2)
        picture.extend([f"crop={crop_width}:{crop_height}", f"scale={width}:{height}"])
    if edit.mirror:
        picture.append("hflip")
    if edit.look == "vivid":
        picture.append("eq=saturation=1.25")
    elif edit.look == "monochrome":
        picture.append("hue=s=0")
    filters = []
    if picture:
        filters.append("[0:v]" + ",".join(picture) + "[picture]")
    audio = []
    next_input = 1

    def add_audio(index, level, delay=0):
        label = f"mix{len(audio)}"
        chain = f"[{index}:a:0]aresample=48000,aformat=channel_layouts=stereo,volume={level / 100}"
        if delay:
            chain += f",adelay={delay}:all=1"
        filters.append(chain + f"[{label}]")
        audio.append(f"[{label}]")

    if has_audio and edit.source_level:
        add_audio(0, edit.source_level)
    if music and edit.music_level:
        command += ["-stream_loop", "-1", "-t", str(duration), "-i", str(music)]
        add_audio(next_input, edit.music_level)
        next_input += 1

    with TemporaryDirectory(dir=output.parent) as scratch:
        for cue in edit.cues:
            if not cue.level:
                continue
            path = Path(scratch) / f"{cue.effect}.wav"
            if not path.exists():
                path.write_bytes(effect_wave(cue.effect))
            command += ["-i", str(path)]
            add_audio(next_input, cue.level, round(cue.at * 1000))
            next_input += 1
        if audio:
            filters.append("".join(audio) + f"amix=inputs={len(audio)}:duration=longest:normalize=0,"
                           "alimiter=limit=0.95:level=0:latency=1,apad[audio]")
        if filters:
            command += ["-filter_complex_threads", "2", "-filter_complex", ";".join(filters)]
        command += ["-map", "[picture]" if picture else "0:v:0"]
        if audio:
            command += ["-map", "[audio]", "-c:a", "aac", "-b:a", "192k"]
        command += ["-t", str(duration), "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        target = ["-f", "mp4", str(output)]
        use_nvenc = nvenc_available(command[0])
        exit_code, log = run_encoder(command + (NVENC_ENCODER if use_nvenc else CPU_ENCODER) + target,
                                     output.parent, duration, progress)
        if exit_code and use_nvenc:
            exit_code, log = run_encoder(command + CPU_ENCODER + target, output.parent, duration, progress)
        if exit_code:
            raise RuntimeError(f"FFmpeg could not export the edit. {log.strip()[-500:]}")
