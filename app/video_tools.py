from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    codec: str | None = None


async def probe_video(path: Path) -> VideoInfo:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[-500:] or "ffprobe falhou")

    data = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("Nenhuma faixa de vídeo encontrada")

    duration_raw = video.get("duration") or (data.get("format") or {}).get("duration") or 0
    try:
        duration = max(0.0, float(duration_raw))
    except (TypeError, ValueError):
        duration = 0.0

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)

    # Respect rotation metadata so Telegram shows the actual display dimensions.
    rotation = 0
    for side in video.get("side_data_list") or []:
        try:
            rotation = int(side.get("rotation") or 0)
        except (TypeError, ValueError):
            pass
    tags = video.get("tags") or {}
    try:
        rotation = int(tags.get("rotate") or rotation)
    except (TypeError, ValueError):
        pass
    if abs(rotation) % 180 == 90:
        width, height = height, width

    return VideoInfo(
        duration=duration,
        width=max(1, width),
        height=max(1, height),
        codec=video.get("codec_name"),
    )


async def make_thumbnail(path: Path, output: Path, *, second: float = 1.0) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-ss", f"{second:.3f}",
        "-i", str(path),
        "-frames:v", "1",
        "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
        "-q:v", "4",
        str(output),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(stderr.decode(errors="replace")[-500:] or "ffmpeg não gerou thumbnail")
    return output


def _atom_positions(path: Path, max_bytes: int = 8 * 1024 * 1024) -> tuple[int, int]:
    with path.open("rb") as fh:
        data = fh.read(max_bytes)
    return data.find(b"moov"), data.find(b"mdat")


async def ensure_faststart(path: Path) -> Path:
    if path.suffix.lower() not in {".mp4", ".m4v", ".mov"}:
        return path

    moov, mdat = _atom_positions(path)
    if moov >= 0 and (mdat < 0 or moov < mdat):
        return path

    temp = path.with_name(path.stem + ".faststart" + path.suffix)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(path),
        "-map", "0",
        "-c", "copy",
        "-movflags", "+faststart",
        str(temp),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode == 0 and temp.exists() and temp.stat().st_size > 0:
        temp.replace(path)
        return path
    temp.unlink(missing_ok=True)
    return path
