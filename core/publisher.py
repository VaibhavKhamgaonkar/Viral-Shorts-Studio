import json
from pathlib import Path


def save_task_info(
    task_dir: Path,
    task_id: str,
    youtube_url: str,
    source_title: str,
    uploader: str = "Unknown",
    duration: float = 0,
    source_files=(),
):
    task_dir.mkdir(parents=True, exist_ok=True)
    info_path = task_dir / "info.json"
    data = {
        "task_id": task_id,
        "youtube_url": youtube_url,
        "source_title": source_title,
        "uploader": uploader,
        "duration": duration,
        "folder_name": task_dir.name,
        "source_files": list(source_files),
    }
    info_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    return str(info_path)


def generate_clip_metadata(clip_info, source_title, output_video_path, youtube_url=""):
    """Write clip information and publishing copy beside the exported video."""
    output = Path(output_video_path)
    meta_path = output.with_suffix(".json")
    hook = clip_info.get("hook", "")
    description = clip_info.get("description", hook)
    if youtube_url:
        description = f"{description}\n\nSource: {youtube_url}".strip()

    metadata = {
        "title": clip_info.get("title") or source_title,
        "title_variants": clip_info.get("title_variants", []),
        "pinned_comment": clip_info.get("pinned_comment", ""),
        "description": description,
        "tags": clip_info.get("tags", []),
        "video_file": output.name,
        "virality_score": clip_info.get("virality_score"),
        "hook": hook,
        "ai_hook": clip_info.get("ai_hook", hook if clip_info.get("copy_source") == "ai" else ""),
        "reason": clip_info.get("reason", ""),
        "start_time": clip_info.get("start_time"),
        "end_time": clip_info.get("end_time"),
        "source_url": youtube_url,
        "selection": clip_info.get("selection", ""),
        "copy_source": clip_info.get("copy_source", ""),
        "copy_model": clip_info.get("copy_model", ""),
        "editing_tip": clip_info.get("editing_tip", ""),
        "posting_tip": clip_info.get("posting_tip", ""),
        "sound_effect_prompt": clip_info.get("sound_effect_prompt", ""),
        "music_brief": clip_info.get("music_brief", ""),
        "music_track": clip_info.get("music_track", ""),
        "applied_music": clip_info.get("applied_music", ""),
    }
    meta_path.write_text(json.dumps(metadata, indent=4, ensure_ascii=False), encoding="utf-8")
    return str(meta_path)
