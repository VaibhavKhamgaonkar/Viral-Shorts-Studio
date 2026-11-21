import argparse
import sys
import os
from pathlib import Path

from config.settings import load_settings
from core.downloader import download_video
from core.transcriber import get_transcript
from core.analyzer import analyze_transcript_for_viral_clips, generate_post_copy
from core.subtitles import generate_ass_subtitles
from core.transformer import render_viral_clip
from core.publisher import generate_clip_metadata

def run_clipper(
    url: str,
    mode: str = "split_screen",
    llm_model: str = "qwen2.5:7b",
    llm_base_url: str = "http://localhost:11434/v1",
    enable_hflip: bool = False,
    max_clips: int = 3
):
    print("=" * 65)
    print("🎬 YOUTUBE VIRAL CLIPPER & CONTENT TRANSFORMER")
    print("=" * 65)
    
    settings = load_settings()
    settings.update(llm_base_url=llm_base_url, llm_model=llm_model)
    
    # 1. Download
    print(f"\n[Step 1/5] Fetching video and captions from YouTube...")
    dl_result = download_video(url)
    video_path = dl_result["video_path"]
    sub_path = dl_result["subtitle_path"]
    info = dl_result["info"]
    
    if not video_path or not os.path.exists(video_path):
        print("[!] Error: Video download failed.")
        sys.exit(1)
        
    print(f"[+] Download complete: {video_path}")
    
    # 2. Transcription
    print(f"\n[Step 2/5] Extracting timestamped transcript...")
    transcript = get_transcript(
        video_path=video_path,
        subtitle_path=sub_path,
        model_size=settings.get("whisper_model_size", "base"),
        device=settings.get("whisper_device", "auto")
    )
    
    if not transcript:
        print("[!] Error: No transcript could be extracted.")
        sys.exit(1)
        
    print(f"[+] Extracted {len(transcript)} transcript segments.")
    
    # 3. Viral Moments Detection
    print(f"\n[Step 3/5] Analyzing transcript with local AI ({llm_model})...")
    clips = analyze_transcript_for_viral_clips(
        transcript=transcript,
        total_duration=info.get("duration") or 0,
        llm_base_url=llm_base_url,
        llm_api_key=settings.get("llm_api_key", "ollama"),
        llm_model=llm_model,
        min_dur=settings.get("clip_duration_min", 30),
        max_dur=settings.get("clip_duration_max", 60),
        max_clips=max_clips
    )
    
    if not clips:
        print("[!] No viral moments could be identified.")
        sys.exit(1)
        
    print(f"[+] Identified {len(clips)} candidate viral moments:")
    for idx, c in enumerate(clips, 1):
        print(f"    {idx}. [{c['start_time']}s -> {c['end_time']}s] (Virality: {c.get('virality_score', 85)}) {c.get('title')}")
        
    # 4. Rendering
    print("\n[Step 4/5] Rendering 9:16 clips...")
    rendered_files = []
    video_stem = Path(video_path).stem
    
    for idx, c in enumerate(clips, 1):
        start = float(c["start_time"])
        end = float(c["end_time"])
        clip_name = f"Short_{idx}_{video_stem}.mp4"
        ass_name = f"subtitles_{idx}_{video_stem}.ass"
        ass_path = str(Path(video_path).parent / ass_name)
        
        print(f"\n[*] Processing Clip #{idx}: {c.get('title')}")
        
        # Subtitles
        generate_ass_subtitles(transcript, start, end, ass_path)
        
        # Video Render
        rendered_path = render_viral_clip(
            input_video=video_path,
            start_time=start,
            end_time=end,
            output_filename=clip_name,
            subtitles_ass_path=ass_path,
            mode=mode,
            enable_hflip=enable_hflip,
            audio_tempo=settings.get("audio_tempo", 1.04),
            color_contrast=settings.get("color_contrast", 1.05),
            color_saturation=settings.get("color_saturation", 1.08),
            bg_music_volume=settings.get("bg_music_volume", 0.12)
        )
        
        # 5. Metadata
        copy = generate_post_copy(c, transcript, info["title"], settings)
        meta_path = generate_clip_metadata(c | copy, info["title"], rendered_path, youtube_url=url)
        rendered_files.append((rendered_path, meta_path))
        
    print("\n" + "=" * 65)
    print("🎉 ALL VIRAL SHORTS GENERATED SUCCESSFULLY!")
    print("=" * 65)
    for v, m in rendered_files:
        print(f"🎬 Video:    {v}")
        print(f"📝 Metadata: {m}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube Viral Clipper & Content Transformer")
    parser.add_argument("--url", required=True, help="YouTube video URL")
    parser.add_argument("--mode", default="split_screen", choices=["split_screen", "smart_blur"], help="Clipping layout")
    parser.add_argument("--model", default="qwen2.5:7b", help="Local LLM model name (e.g. qwen2.5:7b, deepseek-r1:7b)")
    parser.add_argument("--endpoint", default="http://localhost:11434/v1", help="Ollama / Local LLM base endpoint")
    parser.add_argument("--mirror", action="store_true", help="Enable horizontal mirror flip")
    parser.add_argument("--clips", type=int, default=3, help="Number of clips to generate")
    
    args = parser.parse_args()
    run_clipper(
        url=args.url,
        mode=args.mode,
        llm_model=args.model,
        llm_base_url=args.endpoint,
        enable_hflip=args.mirror,
        max_clips=args.clips
    )
