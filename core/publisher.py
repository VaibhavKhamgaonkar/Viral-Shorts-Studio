import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from core.analyzer import POST_KIT_FIELDS


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


def posting_metadata(clip_info, source_title, youtube_url=""):
    """Build posting fields shared by the initial export and a copy-only refresh."""
    hook = clip_info.get("ai_hook", clip_info.get("hook", "") if clip_info.get("copy_source") == "ai" else "")
    description = clip_info.get("description", hook)
    if description and youtube_url:
        description = f"{description}\n\nSource: {youtube_url}".strip()
    return {
        **{field: clip_info.get(field, "") for field in POST_KIT_FIELDS if field not in {"title", "hook"}},
        "title": clip_info.get("title") or source_title,
        "title_variants": clip_info.get("title_variants", []),
        "pinned_comment": clip_info.get("pinned_comment", ""),
        "description": description,
        "tags": clip_info.get("tags", []),
        "ai_hook": hook,
        "copy_source": clip_info.get("copy_source", ""),
        "copy_model": clip_info.get("copy_model", ""),
        "copy_ready": clip_info.get("copy_ready", False),
        "posting_kit_version": clip_info.get("posting_kit_version", 0),
        "posting_context": clip_info.get("posting_context", ""),
        "posting_notes": clip_info.get("posting_notes", ""),
    }


def write_clip_metadata(path, metadata):
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as temporary:
        target = Path(temporary.name)
        try:
            json.dump(metadata, temporary, indent=4, ensure_ascii=False)
            temporary.close()
            os.replace(target, path)
        finally:
            temporary.close()
            target.unlink(missing_ok=True)


def read_caption_context(path):
    """Recover speech captions from older exports, leaving hooks and branding out."""
    if not path.is_file():
        return ""
    lines = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) == 10 and fields[3].strip() in {"Caption", "ShortsDefault", "ShortsHighlight"}:
            text = re.sub(r"\{[^}]*\}", "", fields[9]).replace(r"\N", " ").replace(r"\n", " ").replace(r"\h", " ")
            if text.strip():
                lines.append(text.strip())
    return " ".join(lines)[:6500]


def generate_clip_metadata(clip_info, source_title, output_video_path, youtube_url=""):
    """Write clip information and publishing copy beside the exported video."""
    output = Path(output_video_path)
    meta_path = output.with_suffix(".json")
    metadata = posting_metadata(clip_info, source_title, youtube_url) | {
        "video_file": output.name,
        "virality_score": clip_info.get("virality_score"),
        "hook": clip_info.get("hook", ""),
        "reason": clip_info.get("reason", ""),
        "start_time": clip_info.get("start_time"),
        "end_time": clip_info.get("end_time"),
        "source_url": youtube_url,
        "selection": clip_info.get("selection", ""),
        "applied_music": clip_info.get("applied_music", ""),
    }
    write_clip_metadata(meta_path, metadata)
    return str(meta_path)
