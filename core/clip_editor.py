import math
import random
import struct
import wave
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import av
from pydantic import BaseModel, Field, model_validator

from core.editor_visuals import frame_gif, reaction_gif, write_editor_titles
from core.subtitles import generate_ass_subtitles
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


LOOKS = {
    "original": ("Original", "", "none"),
    "vivid": ("Vivid", "eq=saturation=1.25", "saturate(1.25)"),
    "monochrome": ("Black and white", "hue=s=0", "grayscale(1)"),
    "cinema": ("Cinema", "eq=contrast=1.14:saturation=.86", "contrast(1.14) saturate(.86)"),
    "retro": ("Retro warm", "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131", "sepia(1)"),
    "cool": ("Cool night", "colorbalance=rs=-.05:bs=.09:rm=-.04:bm=.07:pl=1", "sepia(.12) hue-rotate(170deg) saturate(.9)"),
}


class TextCue(BaseModel):
    text: str = Field(min_length=1, max_length=120, pattern=r"\S")
    at: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    animation: Literal["pop", "slide", "fade"] = "pop"
    position: Literal["top", "center", "lower"] = "top"
    color: Literal["lime", "white", "pink"] = "lime"


class InsertCue(BaseModel):
    asset: str = Field(min_length=1, max_length=255)
    at: float = Field(ge=0, allow_inf_nan=False)


class SpeechCaption(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=1000)


class PictureSettings(BaseModel):
    zoom: int = Field(default=100, ge=100, le=150)
    mirror: bool = False
    look: Literal["original", "vivid", "monochrome", "cinema", "retro", "cool"] = "original"
    frame: Literal["", "neon", "sparkles", "sweep", "orbit"] = ""
    focus_x: float = Field(default=50, ge=0, le=100, allow_inf_nan=False)
    focus_y: float = Field(default=50, ge=0, le=100, allow_inf_nan=False)


class PictureRange(PictureSettings):
    zoom: int = Field(default=100, ge=100, le=250)
    at: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


class RemovedRange(BaseModel):
    at: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


class AudioRegion(RemovedRange):
    source_level: int = Field(default=0, ge=0, le=100)
    asset: str = Field(default="", max_length=200)
    level: int = Field(default=100, ge=0, le=100)


@dataclass(frozen=True)
class InsertAsset:
    duration: float
    has_audio: bool = False
    file: Path | None = None


class ClipEdit(PictureSettings):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    ranges: list[PictureRange] = Field(default_factory=list, max_length=8)
    cuts: list[RemovedRange] = Field(default_factory=list, max_length=12)
    audio_regions: list[AudioRegion] = Field(default_factory=list, max_length=12)
    cut_fade: bool = False
    titles: list[TextCue] = Field(default_factory=list, max_length=8)
    inserts: list[InsertCue] = Field(default_factory=list, max_length=4)
    captions: list[SpeechCaption] = Field(default_factory=list, max_length=1500)
    caption_style: Literal["clean", "bold"] = "clean"
    source_level: int = Field(default=100, ge=0, le=100)
    music_track: str = Field(default="", max_length=255)
    music_level: int = Field(default=20, ge=0, le=100)
    music_fade_in: float = Field(default=0, ge=0, le=3, allow_inf_nan=False)
    music_fade_out: float = Field(default=0, ge=0, le=3, allow_inf_nan=False)
    duck_music: bool = False
    clean_voice: bool = False
    reduce_rumble: bool = False
    reduce_hiss: bool = False
    hum_filter: Literal[0, 50, 60] = 0
    cues: list[SoundCue] = Field(default_factory=list, max_length=12)
    save_mode: Literal["version", "replace"] = "version"
    source_revision: str = Field(default="", pattern=r"^\d{0,24}$")

    @model_validator(mode="after")
    def validate_range(self):
        validate_timeline(self, self.end - self.start)
        return self


def validate_timeline(edit, duration):
    if duration < .24:
        raise ValueError("Keep at least a quarter second between the trim points.")
    if any(cue.at >= duration for cue in edit.cues):
        raise ValueError("Every sound effect must start before the end of the edit.")
    if any(title.end - title.at < .19 or title.end > duration + .01 for title in edit.titles):
        raise ValueError("Each title needs at least 0.2 seconds inside the trim range.")
    if any(cut.at >= duration for cut in edit.inserts):
        raise ValueError("Insert each reaction before the end of the trimmed clip.")
    cursor = 0
    removed = 0
    for cut in sorted(edit.cuts, key=lambda item: item.at):
        if cut.at < cursor or cut.end - cut.at < .099 or cut.end > duration + .005:
            raise ValueError("Removed sections must be at least 0.1 seconds, inside the trim, and cannot overlap.")
        if 0 < cut.at - cursor < .049 or 0 < duration - cut.end < .049:
            raise ValueError("Leave at least 0.05 seconds between removed sections and adjacent trim points.")
        if any(cut.at <= insert.at < cut.end for insert in edit.inserts):
            raise ValueError("Move any insert out of the section being removed.")
        removed += min(cut.end, duration) - cut.at
        cursor = cut.end
    if duration - removed < .24:
        raise ValueError("Keep at least 0.24 seconds of video after removing sections.")
    cursor = 0
    for region in sorted(edit.audio_regions, key=lambda item: item.at):
        if region.at < cursor or region.end - region.at < .049 or region.end > duration + .005:
            raise ValueError("Timed audio must be at least 0.05 seconds, inside the trim, and cannot overlap.")
        cursor = region.end
    cursor = 0
    for picture in sorted(edit.ranges, key=lambda item: item.at):
        if picture.end <= picture.at or picture.end > duration + .002:
            raise ValueError("Picture ranges must start and end inside the trim.")
        if picture.at < cursor:
            raise ValueError("Picture ranges cannot overlap.")
        cursor = picture.end


def frame_rate(stream):
    return min(120, max(1, float(stream.average_rate or 30)))


def edit_duration(edit, assets):
    length = edit.end - edit.start
    removed = sum(min(cut.end, length) - cut.at for cut in edit.cuts)
    return length - removed + sum(assets[insert.asset].duration for insert in edit.inserts)


def kept_source_ranges(edit):
    cursor = 0
    for cut in sorted(edit.cuts, key=lambda item: item.at):
        if cut.at > cursor:
            yield cursor, cut.at
        cursor = cut.end
    if edit.end - edit.start > cursor:
        yield cursor, edit.end - edit.start


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


def render_edit(video, output, edit, music, progress, *, inserts=None, voiceovers=None, preview=False):
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        width, height = stream.width, stream.height
        has_audio = bool(container.streams.audio)
        fps = frame_rate(stream)
    duration = edit.end - edit.start
    insert_assets = inserts or {}
    final_duration = edit_duration(edit, insert_assets)
    out_width, out_height = width, height
    if preview and max(width, height) > 480:
        out_width = max(2, round(width * 240 / max(width, height)) * 2)
        out_height = max(2, round(height * 240 / max(width, height)) * 2)
    command = [get_ffmpeg_cmd(), "-hide_banner", "-loglevel", "error", "-y",
               "-ss", str(edit.start), "-t", str(duration), "-i", str(video)]
    filters = []
    audio = []
    next_input = 1

    def add_input(path, *options):
        nonlocal next_input
        command.extend([*options, "-i", str(path)])
        next_input += 1
        return next_input - 1

    def add_audio(index, level, delay=0, effects=""):
        label = f"mix{index}"
        chain = f"[{index}:a:0]aresample=48000,aformat=channel_layouts=stereo{effects},volume={level / 100}"
        if delay:
            chain += f",adelay={delay}:all=1"
        filters.append(chain + f"[{label}]")
        audio.append(f"[{label}]")

    if has_audio and edit.source_level:
        cleanup = []
        if edit.clean_voice or edit.reduce_rumble:
            cleanup.append(f"highpass=f={110 if edit.reduce_rumble else 75}")
        if edit.reduce_hiss:
            cleanup.append("lowpass=f=6000")
        if edit.hum_filter:
            cleanup.extend(f"equalizer=f={edit.hum_filter * harmonic}:width_type=h:width=12:g=-22" for harmonic in (1, 2, 3))
        if edit.clean_voice:
            cleanup.append("afftdn=nr=9:nf=-40")
        gain = "1"
        for region in reversed(sorted(edit.audio_regions, key=lambda item: item.at)):
            if region.source_level == 100:
                continue
            at = f"{region.at:.6f}"
            end = f"{region.end:.6f}"
            fade = min(.015, (region.end - region.at) / 3)
            attenuation = 1 - region.source_level / 100
            envelope = f"1-{attenuation:.6f}*max(0,min(1,min((t-{at})/{fade:.6f},({end}-t)/{fade:.6f})))"
            gain = f"if(lt(t,{at}),1,if(lt(t,{end}),{envelope},{gain}))"
        suppression = f",asetnsamples=n=256:p=0,volume='{gain}':eval=frame" if gain != "1" else ""
        add_audio(0, edit.source_level, effects="".join("," + item for item in cleanup) + suppression)
    music_index = add_input(music, "-stream_loop", "-1", "-t", str(final_duration)) if music and edit.music_level else None
    with TemporaryDirectory(dir=output.parent) as scratch:
        scratch = Path(scratch)
        for cue in edit.cues:
            if not cue.level:
                continue
            path = scratch / f"{cue.effect}.wav"
            if not path.exists():
                path.write_bytes(effect_wave(cue.effect))
            add_audio(add_input(path), cue.level, round(cue.at * 1000))
        for region in edit.audio_regions:
            if region.asset and region.level:
                path, audio_length = voiceovers[region.asset]
                length = min(region.end - region.at, audio_length)
                fade = min(.02, length / 4)
                taper = f",atrim=duration={length:.6f},afade=t=out:st={length - fade:.6f}:d={fade:.6f}"
                add_audio(add_input(path), region.level, round(region.at * 1000), effects=taper)
        graphics = []
        if edit.titles:
            write_editor_titles(scratch / "titles.ass", edit.titles, out_width, out_height)
            graphics.append("ass=titles.ass")
        if edit.captions:
            generate_ass_subtitles([caption.model_dump() for caption in edit.captions],
                                   edit.start, edit.end, scratch / "speech.ass", caption_style=edit.caption_style,
                                   caption_color="#DDF76D")
            graphics.append("ass=speech.ass")
        geometry = (width, height, out_width, out_height)
        if edit.ranges:
            video_label = render_picture_ranges(edit, duration, fps, geometry, filters, add_input, scratch)
        else:
            video_label = apply_picture("[0:v:0]", edit, 0, geometry, filters, add_input, scratch)
        if graphics:
            filters.append(video_label + ",".join(graphics) + "[graphics]")
            video_label = "[graphics]"
        if edit.inserts or edit.cuts:
            has_mix = bool(audio or music_index is not None or any(asset.has_audio for asset in insert_assets.values()))
            video_label, expanded_audio = compose_timeline(filters, add_input, video_label, audio, has_mix,
                                                            edit, insert_assets, scratch, out_width, out_height, fps)
            audio = [expanded_audio] if has_mix else []
        if music_index is not None:
            fade_in = min(edit.music_fade_in, final_duration / 2)
            fade_out = min(edit.music_fade_out, final_duration / 2)
            fades = f",afade=t=in:d={fade_in:.6f}" if fade_in else ""
            if fade_out:
                fades += f",afade=t=out:st={final_duration - fade_out:.6f}:d={fade_out:.6f}"
            add_audio(music_index, edit.music_level, effects=fades)
            if edit.duck_music and len(audio) > 1:
                music_label = audio.pop()
                filters.append(mix_audio(audio) + "apad,asplit=2[foreground][duck_trigger]")
                filters.append(music_label + "[duck_trigger]sidechaincompress=threshold=0.025:ratio=8:"
                               "attack=20:release=280[ducked_music]")
                audio = ["[foreground]", "[ducked_music]"]
        if audio:
            filters.append(mix_audio(audio) + "alimiter=limit=0.95:level=0:latency=1,apad[audio]")
        if filters:
            command += ["-filter_complex_threads", "2", "-filter_complex", ";".join(filters)]
        command += ["-map", "0:v:0" if video_label == "[0:v:0]" else video_label]
        if audio:
            command += ["-map", "[audio]", "-c:a", "aac", "-b:a", "192k"]
        command += ["-t", str(final_duration), "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        target = ["-f", "mp4", str(output)]
        use_nvenc = not preview and nvenc_available(command[0])
        encoder = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "27"] if preview else CPU_ENCODER
        exit_code, log = run_encoder(command + (NVENC_ENCODER if use_nvenc else encoder) + target,
                                     scratch, final_duration, progress)
        if exit_code and use_nvenc:
            exit_code, log = run_encoder(command + CPU_ENCODER + target, scratch, final_duration, progress)
        if exit_code:
            raise RuntimeError(f"FFmpeg could not export the edit. {log.strip()[-500:]}")


def mix_audio(labels):
    return "".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"


def apply_picture(video, picture, index, geometry, filters, add_input, scratch):
    width, height, out_width, out_height = geometry
    chain = []
    if picture.zoom != 100:
        crop_width = max(2, int(width / (picture.zoom / 100) / 2) * 2)
        crop_height = max(2, int(height / (picture.zoom / 100) / 2) * 2)
        left = min(width - crop_width, max(0, width * picture.focus_x / 100 - crop_width / 2))
        top = min(height - crop_height, max(0, height * picture.focus_y / 100 - crop_height / 2))
        chain.append(f"crop={crop_width}:{crop_height}:{left:.4f}:{top:.4f}")
    if picture.mirror:
        chain.append("hflip")
    if look := LOOKS[picture.look][1]:
        chain.append(look)
    if picture.zoom != 100 or (out_width, out_height) != (width, height):
        chain.append(f"scale={out_width}:{out_height}")
    if chain:
        output = f"[picture{index}]"
        filters.append(video + ",".join(chain) + output)
        video = output
    if picture.frame:
        frame_path = scratch / f"frame-{picture.frame}.gif"
        if not frame_path.exists():
            frame_path.write_bytes(frame_gif(picture.frame))
        frame_input = add_input(frame_path, "-stream_loop", "-1", "-ignore_loop", "1")
        border, inset, output = f"[border{index}]", f"[inset{index}]", f"[framed{index}]"
        filters.append(f"[{frame_input}:v:0]scale={out_width}:{out_height}{border}")
        if picture.frame == "orbit":
            filters.append(f"{video}scale=trunc(iw*.42)*2:trunc(ih*.42)*2{inset}")
            filters.append(f"{border}{inset}overlay=(W-w)/2:(H-h)/2:shortest=1{output}")
        else:
            filters.append(f"{video}{border}overlay=shortest=1{output}")
        video = output
    return video


def render_picture_ranges(edit, duration, fps, geometry, filters, add_input, scratch):
    intervals = []
    cursor = 0
    last_frame = max(1, round(duration * fps))
    for picture in sorted(edit.ranges, key=lambda item: item.at):
        first = min(last_frame - 1, round(picture.at * fps))
        last = min(last_frame, max(first + 1, round(picture.end * fps)))
        if first < cursor:
            raise ValueError("Two picture ranges land on the same frame. Use the frame buttons to separate them.")
        if first > cursor:
            intervals.append((cursor, first, edit))
        intervals.append((first, last, picture))
        cursor = last
    if cursor < last_frame:
        intervals.append((cursor, last_frame, edit))
    feeds = [f"[picture_source{index}]" for index in range(len(intervals))]
    split = f",split={len(feeds)}" if len(feeds) > 1 else ""
    filters.append(f"[0:v:0]fps={fps:.8f}{split}" + "".join(feeds))
    outputs = []
    for index, (first, last, picture) in enumerate(intervals):
        trimmed = f"[picture_trim{index}]"
        filters.append(f"{feeds[index]}trim=start_frame={first}:end_frame={last},setpts=PTS-STARTPTS{trimmed}")
        video = apply_picture(trimmed, picture, index, geometry, filters, add_input, scratch)
        output = f"[picture_range{index}]"
        filters.append(f"{video}fps={fps:.8f},tpad=stop_mode=clone:stop_duration={2 / fps:.8f},"
                       f"trim=end_frame={last - first},setsar=1,format=yuv420p{output}")
        outputs.append(output)
    if len(outputs) == 1:
        return outputs[0]
    filters.append("".join(outputs) + f"concat=n={len(outputs)}:v=1:a=0,setpts=N/({fps:.8f}*TB)[picture_timeline]")
    return "[picture_timeline]"


def compose_timeline(filters, add_input, video, audio, has_mix, edit, assets, scratch, width, height, fps):
    duration = edit.end - edit.start
    timeline = []
    cursor = 0
    events = sorted([(cut.at, cut.end, None) for cut in edit.cuts] +
                    [(insert.at, insert.at, insert.asset) for insert in edit.inserts], key=lambda item: item[0])
    for at, end, asset in events:
        if at > cursor:
            timeline.append((cursor, at, None))
        if asset:
            timeline.append((0, assets[asset].duration, asset))
        cursor = end
    if duration > cursor:
        timeline.append((cursor, duration, None))
    source_parts = sum(asset is None for _, _, asset in timeline)
    video_feeds = [f"[sourcev{number}]" for number in range(source_parts)]
    audio_feeds = [f"[sourcea{number}]" for number in range(source_parts)]
    normalization = f"fps={fps:.8f},setsar=1,format=yuv420p"
    fade_in_at = {cut.end for cut in edit.cuts if cut.at > 0}
    fade_out_at = {cut.at for cut in edit.cuts if cut.end < duration}
    video_split = f",split={source_parts}" if source_parts > 1 else ""
    filters.append(video + normalization + video_split + "".join(video_feeds))
    silence = "anullsrc=r=48000:cl=stereo"
    stereo = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    if has_mix:
        audio_split = f",asplit={source_parts}" if source_parts > 1 else ""
        source_audio = mix_audio(audio) + "apad" if audio else silence
        filters.append(f"{source_audio},atrim=duration={duration},{stereo}{audio_split}" + "".join(audio_feeds))
    segments = []
    for number, (start, end, name) in enumerate(timeline):
        length = end - start
        audio_source = None
        if name is None:
            video_source = video_feeds.pop(0)
            if has_mix:
                audio_source = audio_feeds.pop(0)
        else:
            asset = assets[name]
            path = asset.file
            if path is None:
                path = scratch / f"{name}.gif"
                if not path.exists():
                    path.write_bytes(reaction_gif(name))
            index = add_input(path)
            video_source = f"[{index}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            video_source += f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,{normalization},"
            audio_source = f"[{index}:a:0]{stereo},apad," if asset.has_audio else None
        video_label = f"[segmentv{number}]"
        # Input labels feed their first filter directly. The insert chain already ends in a separator.
        picture = f"{video_source}trim=start={start}:end={end},setpts=PTS-STARTPTS"
        if edit.cut_fade and name is None:
            fade = min(.12, length / 3)
            if start in fade_in_at:
                picture += f",fade=t=in:d={fade:.6f}"
            if end in fade_out_at:
                picture += f",fade=t=out:st={length - fade:.6f}:d={fade:.6f}"
        filters.append(picture + video_label)
        segments.append(video_label)
        if has_mix:
            audio_label = f"[segmenta{number}]"
            chain = f"{audio_source}atrim=start={start}:duration={length}" if audio_source else f"{silence},atrim=duration={length}"
            chain += ",asetpts=PTS-STARTPTS"
            if name is None and start in fade_in_at:
                chain += ",afade=t=in:d=0.008"
            if name is None and end in fade_out_at:
                chain += f",afade=t=out:st={length - .008:.6f}:d=0.008"
            filters.append(chain + audio_label)
            segments.append(audio_label)
    video_out = "[joined_video]"
    audio_out = "[joined_audio]" if has_mix else ""
    filters.append("".join(segments) + f"concat=n={len(timeline)}:v=1:a={int(has_mix)}" + video_out + audio_out)
    return video_out, audio_out
