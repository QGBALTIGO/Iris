from __future__ import annotations

from urllib.parse import urlsplit

from app.models import ResourceType


VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".ts"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".svg"}
DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".epub"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
SUBTITLE_EXT = {".srt", ".vtt", ".ass", ".ssa", ".ttml"}
PLAYLIST_EXT = {".m3u8", ".m3u", ".mpd", ".ism", ".isml"}


def extension(url: str) -> str:
    path = urlsplit(url).path.lower()
    if "." not in path.rsplit("/", 1)[-1]:
        return ""
    return "." + path.rsplit(".", 1)[-1]


def classify_resource(url: str, content_type: str | None = None) -> ResourceType:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    ext = extension(url)

    if "mpegurl" in mime or "dash+xml" in mime or ext in PLAYLIST_EXT:
        return ResourceType.PLAYLIST
    if mime.startswith("video/") or ext in VIDEO_EXT:
        return ResourceType.VIDEO
    if mime.startswith("audio/") or ext in AUDIO_EXT:
        return ResourceType.AUDIO
    if mime.startswith("image/") or ext in IMAGE_EXT:
        return ResourceType.IMAGE
    if mime in {"text/vtt", "application/x-subrip", "application/ttml+xml"} or ext in SUBTITLE_EXT:
        return ResourceType.SUBTITLE
    if mime in {"application/pdf", "application/epub+zip"} or ext in DOC_EXT:
        return ResourceType.DOCUMENT
    if mime in {"application/zip", "application/x-rar-compressed", "application/x-7z-compressed"} or ext in ARCHIVE_EXT:
        return ResourceType.ARCHIVE
    return ResourceType.OTHER
