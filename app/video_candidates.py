from __future__ import annotations

from pathlib import Path

from app.downloads import DownloadEngine
from app.models import MediaResource, ResourceType
from app.video_tools import VideoInfo, normalize_video_mp4, probe_video


_IMAGE_CODECS = {"gif", "png", "apng", "bmp", "webp"}


def video_candidate_rank(resource: MediaResource) -> tuple:
    from urllib.parse import urlsplit

    path = urlsplit(resource.url).path.lower()
    mime = (resource.mime_type or "").lower()
    direct_mp4 = path.endswith(".mp4") or "video/mp4" in mime
    playlist = resource.type == ResourceType.PLAYLIST or path.endswith((".m3u8", ".mpd"))
    network = resource.source.startswith("browser:network")
    direct_video = resource.type == ResourceType.VIDEO
    ytdlp = resource.metadata.get("engine") == "yt-dlp"
    return (
        0 if direct_mp4 else (1 if playlist else 2),
        0 if network else 1,
        0 if direct_video else 1,
        1 if ytdlp else 0,
        -(resource.height or 0),
        resource.url,
    )


def plausible_video(info: VideoInfo, path: Path) -> tuple[bool, str]:
    codec = (info.codec or "").lower()
    fmt = (info.format_name or "").lower()

    if codec in _IMAGE_CODECS:
        return False, f"codec de imagem: {codec}"
    if "gif" in fmt:
        return False, "container GIF"
    if info.width < 16 or info.height < 16:
        return False, f"resolução inválida: {info.width}x{info.height}"
    if path.stat().st_size < 64 * 1024:
        return False, f"arquivo pequeno demais: {path.stat().st_size} bytes"
    return True, "ok"


async def download_first_valid_video(
    resources: list[MediaResource],
    *,
    engine: DownloadEngine | None = None,
    progress=None,
) -> tuple[MediaResource, Path, bool, VideoInfo, list[str]]:
    engine = engine or DownloadEngine()
    candidates = [
        r for r in resources
        if r.type in {ResourceType.VIDEO, ResourceType.PLAYLIST, ResourceType.STREAM}
        and not r.drm
        and r.metadata.get("raw_downloadable") is not False
        and not r.metadata.get("hls_segment")
        and not (
            r.source.startswith("browser:")
            and urlsplit(r.url).path.lower().endswith(".ts")
        )
    ]
    candidates.sort(key=video_candidate_rank)

    errors: list[str] = []
    for index, resource in enumerate(candidates, start=1):
        path: Path | None = None
        normalized: Path | None = None
        generated = False
        try:
            path = await engine.download(resource, filename=None, progress=progress)
            info = await probe_video(path)
            ok, reason = plausible_video(info, path)
            if not ok:
                errors.append(f"{index}. {resource.url}: {reason}")
                path.unlink(missing_ok=True)
                continue

            normalized, generated = await normalize_video_mp4(path)
            normalized_info = await probe_video(normalized)
            ok, reason = plausible_video(normalized_info, normalized)
            if not ok:
                errors.append(f"{index}. {resource.url}: normalizado inválido: {reason}")
                if generated:
                    normalized.unlink(missing_ok=True)
                path.unlink(missing_ok=True)
                continue

            if generated and normalized != path:
                path.unlink(missing_ok=True)
            return resource, normalized, generated, normalized_info, errors
        except Exception as exc:
            errors.append(
                f"{index}. {resource.url}: {type(exc).__name__}: {str(exc)[:240]}"
            )
            if normalized and generated:
                normalized.unlink(missing_ok=True)
            if path:
                path.unlink(missing_ok=True)

    raise RuntimeError(
        "Nenhum candidato resultou em vídeo válido. "
        + " | ".join(errors[-6:])
    )
