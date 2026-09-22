from __future__ import annotations

import asyncio
from typing import Any

from app.models import MediaResource, MediaVariant, ResourceType


async def probe_ytdlp(url: str, timeout: float = 35.0) -> list[MediaResource]:
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return []

    def _run() -> list[MediaResource]:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": False,
            "extract_flat": False,
            "socket_timeout": 15,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        return _convert(info)

    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
    except Exception as exc:
        message = str(exc)
        if "DRM" in message.upper() or "digital rights management" in message.lower():
            return [
                MediaResource(
                    url=str(url),
                    type=ResourceType.VIDEO,
                    source="yt-dlp:drm",
                    drm=True,
                    metadata={
                        "engine": "yt-dlp",
                        "status": "protected",
                        "reason": "drm",
                        "error": message[:500],
                    },
                )
            ]
        return []


def _convert(info: Any) -> list[MediaResource]:
    if not isinstance(info, dict):
        return []
    entries = info.get("entries")
    if entries:
        out: list[MediaResource] = []
        for entry in entries:
            out.extend(_convert(entry))
        return out

    formats = [fmt for fmt in (info.get("formats") or []) if isinstance(fmt, dict) and fmt.get("url")]
    title = info.get("title")
    page_url = info.get("webpage_url") or info.get("original_url") or info.get("url")
    if not page_url:
        return []

    variants: list[MediaVariant] = []
    has_video = False
    has_audio = False
    best_width = best_height = None
    best_bandwidth = -1
    for fmt in formats:
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        has_video = has_video or bool(vcodec and vcodec != "none")
        has_audio = has_audio or bool(acodec and acodec != "none")
        height = _int(fmt.get("height"))
        width = _int(fmt.get("width"))
        bandwidth = _bandwidth(fmt)
        if height and (best_height is None or height > best_height):
            best_height, best_width = height, width
        label = f"{height}p" if height else fmt.get("format_note") or fmt.get("format_id")
        variants.append(
            MediaVariant(
                url=str(fmt["url"]),
                label=str(label) if label else None,
                width=width,
                height=height,
                bandwidth=bandwidth if bandwidth >= 0 else None,
                codecs=",".join(x for x in (vcodec, acodec) if x and x != "none") or None,
                mime_type=fmt.get("ext"),
            )
        )
        best_bandwidth = max(best_bandwidth, bandwidth)

    kind = ResourceType.VIDEO if has_video or not has_audio else ResourceType.AUDIO
    return [
        MediaResource(
            url=str(page_url),
            type=kind,
            source="yt-dlp",
            title=title,
            duration=info.get("duration"),
            width=best_width,
            height=best_height,
            quality=f"{best_height}p" if best_height else None,
            variants=variants,
            metadata={
                "engine": "yt-dlp",
                "extractor": info.get("extractor_key") or info.get("extractor"),
                "id": info.get("id"),
                "thumbnail": info.get("thumbnail"),
            },
        )
    ]


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bandwidth(fmt: dict[str, Any]) -> int:
    for key in ("tbr", "vbr", "abr"):
        value = fmt.get(key)
        try:
            if value is not None:
                return int(float(value) * 1000)
        except (TypeError, ValueError):
            pass
    return -1
