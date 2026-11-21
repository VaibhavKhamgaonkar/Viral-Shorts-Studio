import re
import os
import sys
from pathlib import Path
from typing import List, Dict

# CRITICAL FIX for PyAV 14+ removing metadata_errors argument
try:
    import av
    _orig_av_open = av.open
    def _patched_av_open(*args, **kwargs):
        kwargs.pop("metadata_errors", None)
        return _orig_av_open(*args, **kwargs)
    av.open = _patched_av_open
except Exception:
    pass

def parse_vtt_timestamp(ts_str: str) -> float:
    parts = ts_str.strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        seconds, ms = s.split(".") if "." in s else (s, "0")
        return int(h) * 3600 + int(m) * 60 + int(seconds) + int(ms) / 1000.0
    elif len(parts) == 2:
        m, s = parts
        seconds, ms = s.split(".") if "." in s else (s, "0")
        return int(m) * 60 + int(seconds) + int(ms) / 1000.0
    return 0.0

def parse_vtt_subtitles(vtt_file_path: str) -> List[Dict]:
    segments = []
    if not os.path.exists(vtt_file_path):
        return segments

    with open(vtt_file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    pattern = re.compile(r"(\d{2}:)?\d{2}:\d{2}\.\d{3}\s+-->\s+(\d{2}:)?\d{2}:\d{2}\.\d{3}")
    lines = content.splitlines()
    
    i = 0
    current_start = None
    current_end = None
    text_buffer = []

    while i < len(lines):
        line = lines[i].strip()
        match = pattern.search(line)
        if match:
            if current_start is not None and text_buffer:
                raw_text = " ".join(text_buffer).strip()
                clean_text = re.sub(r"<[^>]+>", "", raw_text)
                clean_text = re.sub(r"\s+", " ", clean_text).strip()
                if clean_text:
                    segments.append({
                        "start": round(current_start, 2),
                        "end": round(current_end, 2),
                        "text": clean_text,
                        "words": []
                    })
                text_buffer = []

            times = line.split("-->")
            current_start = parse_vtt_timestamp(times[0])
            current_end = parse_vtt_timestamp(times[1].split()[0])
        elif current_start is not None and line and not line.startswith("WEBVTT") and not line.startswith("Kind:") and not line.startswith("Language:"):
            text_buffer.append(line)
        i += 1

    if current_start is not None and text_buffer:
        raw_text = " ".join(text_buffer).strip()
        clean_text = re.sub(r"<[^>]+>", "", raw_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()
        if clean_text:
            segments.append({
                "start": round(current_start, 2),
                "end": round(current_end, 2),
                "text": clean_text,
                "words": []
            })

    deduped = []
    last_text = ""
    for seg in segments:
        if seg["text"] != last_text:
            deduped.append(seg)
            last_text = seg["text"]
            
    return deduped

def get_audio_ready_media_file(media_path: str) -> str:
    """Verifies media has an audio stream. If not, auto-detects companion audio file in same folder."""
    import av
    if not os.path.exists(media_path):
        return media_path

    try:
        with av.open(media_path, "r") as container:
            if len(container.streams.audio) > 0:
                return media_path
    except Exception:
        pass

    # Video stream has no audio: search for matching audio file (.m4a, .mp3, etc.)
    p = Path(media_path)
    parent_dir = p.parent
    base_stem = p.stem.split(".f")[0]  # strip format code like .f399
    
    for candidate in parent_dir.glob(f"{base_stem}*"):
        if candidate.suffix.lower() in [".m4a", ".mp3", ".wav", ".aac"]:
            try:
                with av.open(str(candidate), "r") as c:
                    if len(c.streams.audio) > 0:
                        print(f"[*] Switching to companion audio track for transcription: {candidate.name}")
                        return str(candidate)
            except Exception:
                pass

    # If still not found, check any audio file in parent_dir
    for candidate in parent_dir.glob("*.m4a"):
        try:
            with av.open(str(candidate), "r") as c:
                if len(c.streams.audio) > 0:
                    print(f"[*] Fallback to available audio track: {candidate.name}")
                    return str(candidate)
        except Exception:
            pass

    return media_path

# CRITICAL FIX for faster_whisper audio decoding on video-only streams
try:
    import faster_whisper.audio as fwa
    import faster_whisper.transcribe as fwt
    _orig_fwa_decode = fwa.decode_audio

    def _safe_decode_audio(input_file, *args, **kwargs):
        if isinstance(input_file, (str, Path)):
            ready = get_audio_ready_media_file(str(input_file))
            return _orig_fwa_decode(ready, *args, **kwargs)
        return _orig_fwa_decode(input_file, *args, **kwargs)

    fwa.decode_audio = _safe_decode_audio
    fwt.decode_audio = _safe_decode_audio
except Exception:
    pass

def transcribe_with_whisper(media_path: str, model_size: str = "base", device: str = "auto", *, progress=None) -> List[Dict]:
    target_media = get_audio_ready_media_file(media_path)
    
    import av
    if not hasattr(av, "_patched_for_whisper"):
        _orig_open = av.open
        def _safe_open(*args, **kwargs):
            kwargs.pop("metadata_errors", None)
            return _orig_open(*args, **kwargs)
        av.open = _safe_open
        av._patched_for_whisper = True

    from faster_whisper import WhisperModel
    
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
            
    compute_type = "float16" if device == "cuda" else "int8"
    cpu_threads = min(8, os.cpu_count() or 4)
    print(f"[*] Running local Whisper ({model_size}) on {device} ({cpu_threads} CPU threads) for: {Path(target_media).name}...")
    
    model = WhisperModel(model_size, device=device, compute_type=compute_type, cpu_threads=cpu_threads)
    whisper_segments, info = model.transcribe(target_media, word_timestamps=True, vad_filter=True)
    
    results = []
    for s in whisper_segments:
        if progress and info.duration:
            progress(min(99, int(100 * s.end / info.duration)))
        words = []
        if s.words:
            words = [{"word": w.word, "start": round(w.start, 2), "end": round(w.end, 2)} for w in s.words]
            
        results.append({
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": s.text.strip(),
            "words": words
        })
    return results

def get_transcript(video_path: str, subtitle_path: str = None, audio_path: str = None, model_size: str = "base", device: str = "auto", *, progress=None) -> List[Dict]:
    if subtitle_path and os.path.exists(subtitle_path):
        print(f"[*] Found native YouTube captions: {subtitle_path}. Parsing directly...")
        parsed = parse_vtt_subtitles(subtitle_path)
        if len(parsed) > 5:
            return parsed
        print("[!] Subtitles were too sparse, falling back to local Whisper...")

    target_for_whisper = audio_path if (audio_path and os.path.exists(audio_path)) else video_path
    return transcribe_with_whisper(target_for_whisper, model_size=model_size, device=device, progress=progress)
