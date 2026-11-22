import json
import math
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone

from core.analyzer import GENERIC_TAGS
from core.downloader import get_yt_dlp_cmd


class YouTubeSearchUnavailable(Exception):
    pass


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
HASHTAG = re.compile(r"(?<![\w/#])#([^\W_][\w]{1,44})(?!\w)")
STOP_WORDS = {
    "an", "and", "at", "best", "by", "clip", "clips", "from", "how", "in", "is", "it", "of", "on", "or",
    "shorts", "that", "the", "this", "to", "video", "what", "why", "with",
}
ROMAN_VERSIONS = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}


def relevant_hashtags(topic, videos, exclude_id=None):
    """Rank explicit, topical hashtags by occurrences and view counts among the sampled videos."""
    words = set(re.findall(r"[^\W_]{2,}", topic.casefold())) - STOP_WORDS
    versions = dict(re.findall(r"\b([a-z]{2,})\s+(ii|iii|iv|v|vi|\d+)\b", topic.casefold()))
    matches = defaultdict(list)
    seen = set()
    for video in videos:
        video_id = video.get("id") or ""
        if video_id == exclude_id or video_id in seen or not VIDEO_ID.fullmatch(video_id):
            continue
        duration = video.get("duration")
        if not isinstance(duration, (int, float)) or not 0 < duration <= 180:
            continue
        seen.add(video_id)
        views = video.get("view_count")
        views = views if isinstance(views, int) and views >= 0 else None
        title = video.get("title") or ""
        snippet = video.get("description") or ""
        tags = {tag.casefold(): tag for tag in HASHTAG.findall(f"{title}\n{snippet}")}
        for key, tag in tags.items():
            if key in GENERIC_TAGS or not any(key.startswith(word) or f"_{word}" in key for word in words):
                continue
            if any(
                (version := re.match(r"\d+|(?:iii|ii|iv|vi|v)(?![a-z])", key[len(subject):]))
                and ROMAN_VERSIONS.get(version[0], version[0]) != ROMAN_VERSIONS.get(expected, expected)
                for subject, expected in versions.items() if key.startswith(subject)
            ):
                continue
            matches[key].append((tag, video_id, views))

    ranked = sorted(matches.values(), key=lambda rows: (
        -len(rows), -sum(math.log1p(row[2] or 0) for row in rows), rows[0][0].casefold(),
    ))
    entries = []
    for rows in ranked[:3]:
        tag, video_id, views = max(rows, key=lambda row: (row[2] is not None, row[2] or 0))
        entries.append({"tag": tag, "video_id": video_id, "views": views, "matches": len(rows)})
    return len(seen), entries


def research_youtube_hashtags(topic, exclude_id=None):
    command = [
        get_yt_dlp_cmd(), "--ignore-config", "--no-cache-dir", "--no-warnings",
        "--socket-timeout", "6", "--retries", "0", "--extractor-retries", "0",
        "--encoding", "utf-8", "--skip-download", "--flat-playlist", "--dump-single-json", "--", f"ytsearch16:{topic} #shorts",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=15, encoding="utf-8")
        videos = json.loads(result.stdout).get("entries") or []
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise YouTubeSearchUnavailable from error
    count, entries = relevant_hashtags(topic, (video for video in videos if isinstance(video, dict)), exclude_id)
    return {
        "platform": "youtube", "topic": topic, "sample_size": count, "entries": entries,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
