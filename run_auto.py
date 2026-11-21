import os
import sys
import uuid
from pathlib import Path

from config.settings import load_settings
from core.downloader import download_video, sanitize_filename
from core.transcriber import get_transcript
from core.analyzer import analyze_transcript_for_viral_clips, generate_post_copy
from core.subtitles import generate_ass_subtitles
from core.transformer import render_viral_clip, OUTPUT_DIR
from core.publisher import generate_clip_metadata, save_task_info

def main():
    print("=" * 65)
    print("🎬  YOUTUBE VIRAL SHORTS AUTOMATED CLIPPER")
    print("=" * 65)
    print("• Free & Self-Hosted")
    print("• Local AI (Qwen / DeepSeek)")
    print("• Vertical layouts and styled captions")
    print("=" * 65 + "\n")

    url = ""
    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
    
    while not url:
        try:
            url = input("👉 Enter YouTube URL: ").strip()
            if not url:
                print("Please enter a valid URL.")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            sys.exit(0)

    task_id = str(uuid.uuid4())[:8]
    settings = load_settings()
    llm_model = settings.get("llm_model", "qwen2.5:7b")
    llm_endpoint = settings.get("llm_base_url", "http://localhost:11434/v1")
    mode = settings.get("default_mode", "split_screen")

    print("\n" + "-" * 65)
    print(f"[*] Task ID:      #{task_id}")
    print(f"[*] Target Model: {llm_model} (Local)")
    print(f"[*] Video Mode:   {mode}")
    print("-" * 65)

    # 1. Download
    print("\n[1/4] 📥 Downloading YouTube video and extracting subtitles...")
    dl_res = download_video(url)
    video_path = dl_res["video_path"]
    sub_path = dl_res["subtitle_path"]
    info = dl_res["info"]

    if not video_path or not os.path.exists(video_path):
        print("[!] Download failed. Please verify the URL.")
        input("\nPress Enter to exit...")
        sys.exit(1)

    clean_title = sanitize_filename(info.get("title", "YouTube_Video"))
    task_folder_name = f"{clean_title}_[{task_id}]"
    task_output_dir = OUTPUT_DIR / task_folder_name
    task_output_dir.mkdir(parents=True, exist_ok=True)

    save_task_info(
        task_dir=task_output_dir,
        task_id=task_id,
        youtube_url=url,
        source_title=info.get("title", "YouTube Video"),
        uploader=info.get("uploader", "Unknown"),
        duration=info.get("duration", 0),
        source_files=filter(None, (video_path, dl_res.get("audio_path"))),
    )

    print(f"[+] Downloaded: {info['title']}")
    print(f"[+] Dedicated Task Folder: {task_output_dir}")

    # 2. Transcribe
    print("\n[2/4] 🎙️ Extracting timestamped transcript...")
    transcript = get_transcript(
        video_path=video_path,
        subtitle_path=sub_path,
        model_size=settings.get("whisper_model_size", "base"),
        device=settings.get("whisper_device", "auto")
    )

    if not transcript:
        print("[!] No transcript found or generated.")
        input("\nPress Enter to exit...")
        sys.exit(1)

    # 3. Analyze with Local Qwen / DeepSeek
    print(f"\n[3/4] 🧠 Analyzing transcript for viral moments using {llm_model}...")
    clips = analyze_transcript_for_viral_clips(
        transcript=transcript,
        total_duration=info.get("duration") or 0,
        llm_base_url=llm_endpoint,
        llm_api_key=settings.get("llm_api_key", "ollama"),
        llm_model=llm_model,
        min_dur=settings.get("clip_duration_min", 30),
        max_dur=settings.get("clip_duration_max", 60),
        max_clips=settings.get("max_clips_to_generate", 3)
    )

    if not clips:
        print("[!] Could not isolate clips.")
        input("\nPress Enter to exit...")
        sys.exit(1)

    print(f"[+] Found {len(clips)} viral moments ready to render!")

    # 4. Render clips
    print(f"\n[4/4] ⚡ Rendering 9:16 Shorts into folder: {task_folder_name} ...")
    video_stem = Path(video_path).stem
    rendered_paths = []

    for idx, c in enumerate(clips, 1):
        start = float(c["start_time"])
        end = float(c["end_time"])
        clip_name = f"Short_{idx}_{video_stem}.mp4"
        ass_path = str(task_output_dir / f"subs_{idx}_{video_stem}.ass")

        print(f"\n   -> Rendering Short #{idx}: '{c.get('title')}' ({round(end - start, 1)}s)")
        generate_ass_subtitles(transcript, start, end, ass_path)

        rendered_path = render_viral_clip(
            input_video=video_path,
            start_time=start,
            end_time=end,
            output_filename=clip_name,
            output_folder=str(task_output_dir),
            subtitles_ass_path=ass_path,
            mode=mode,
            enable_hflip=settings.get("enable_hflip", False),
            audio_tempo=settings.get("audio_tempo", 1.04),
            color_contrast=settings.get("color_contrast", 1.05),
            color_saturation=settings.get("color_saturation", 1.08),
            bg_music_volume=settings.get("bg_music_volume", 0.12)
        )

        copy_settings = settings | {"llm_base_url": llm_endpoint, "llm_model": llm_model}
        copy = generate_post_copy(c, transcript, info["title"], copy_settings)
        generate_clip_metadata(c | copy, info["title"], rendered_path, youtube_url=url)
        rendered_paths.append(rendered_path)

    print("\n" + "=" * 65)
    print("🎉 ALL VIRAL SHORTS SUCCESSFULLY GENERATED!")
    print("=" * 65)
    for p in rendered_paths:
        print(f"🎬 {p}")

    try:
        os.startfile(str(task_output_dir))
    except Exception:
        pass

    print(f"\n✅ Opened folder: {task_output_dir}")
    input("\nPress Enter to finish...")

if __name__ == "__main__":
    main()
