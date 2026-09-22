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
    audio_codec: str | None = None
    format_name: str | None = None
    pix_fmt: str | None = None


async def _probe_json(path: Path) -> dict:
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
    return json.loads(stdout.decode("utf-8", errors="replace") or "{}")


async def probe_video(path: Path) -> VideoInfo:
    data = await _probe_json(path)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("Nenhuma faixa de vídeo encontrada")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_raw = video.get("duration") or (data.get("format") or {}).get("duration") or 0
    try:
        duration = max(0.0, float(duration_raw))
    except (TypeError, ValueError):
        duration = 0.0

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)

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
        audio_codec=(audio or {}).get("codec_name"),
        format_name=(data.get("format") or {}).get("format_name"),
        pix_fmt=video.get("pix_fmt"),
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
    _, _ = await proc.communicate()
    if proc.returncode == 0 and temp.exists() and temp.stat().st_size > 0:
        temp.replace(path)
        return path
    temp.unlink(missing_ok=True)
    return path


def _is_mp4_container(info: VideoInfo) -> bool:
    names = {part.strip().lower() for part in (info.format_name or "").split(",")}
    return bool(names & {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"})


async def _run_ffmpeg(args: list[str]) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode == 0, stderr.decode(errors="replace")


async def _try_remux(path: Path, output: Path, info: VideoInfo) -> tuple[bool, str]:
    args = [
        "-i", str(path),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-sn",
        "-dn",
        "-c:v", "copy",
    ]
    if info.audio_codec is None:
        pass
    elif (info.audio_codec or "").lower() == "aac":
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", "aac", "-b:a", "128k"]
    args += [
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "4096",
        "-avoid_negative_ts", "make_zero",
        str(output),
    ]
    return await _run_ffmpeg(args)


async def _try_h264(path: Path, output: Path, preset: str, crf: int) -> tuple[bool, str]:
    vf = "scale=ceil(iw/2)*2:ceil(ih/2)*2:flags=bicubic,setsar=1,format=yuv420p"
    args = [
        "-fflags", "+genpts",
        "-i", str(path),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-sn",
        "-dn",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-fps_mode", "vfr",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "4096",
        "-avoid_negative_ts", "make_zero",
        str(output),
    ]
    return await _run_ffmpeg(args)


async def _try_mpeg4_fallback(path: Path, output: Path) -> tuple[bool, str]:
    vf = "scale=ceil(iw/2)*2:ceil(ih/2)*2:flags=bicubic,setsar=1,format=yuv420p"
    args = [
        "-fflags", "+genpts",
        "-i", str(path),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-sn",
        "-dn",
        "-vf", vf,
        "-c:v", "mpeg4",
        "-q:v", "4",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "4096",
        str(output),
    ]
    return await _run_ffmpeg(args)


async def normalize_video_mp4(path: Path) -> tuple[Path, bool]:
    """Return a Telegram-friendly MP4 and whether it is a temporary derivative."""
    path = Path(path)
    info = await probe_video(path)

    h264 = (info.codec or "").lower() in {"h264", "avc1"}
    aac_or_none = info.audio_codec is None or (info.audio_codec or "").lower() == "aac"

    if path.suffix.lower() == ".mp4" and _is_mp4_container(info) and h264 and aac_or_none:
        await ensure_faststart(path)
        return path, False

    output = path.with_name(path.stem + ".telegram.mp4")
    output.unlink(missing_ok=True)
    errors: list[str] = []

    if h264:
        ok, stderr = await _try_remux(path, output, info)
        if ok and output.exists() and output.stat().st_size > 0:
            normalized = await probe_video(output)
            if _is_mp4_container(normalized):
                return output, True
        errors.append("remux: " + stderr[-1200:])
        output.unlink(missing_ok=True)

    for preset, crf in (("veryfast", 22), ("ultrafast", 24)):
        ok, stderr = await _try_h264(path, output, preset, crf)
        if ok and output.exists() and output.stat().st_size > 0:
            normalized = await probe_video(output)
            if _is_mp4_container(normalized) and normalized.codec == "h264":
                return output, True
        errors.append(f"libx264/{preset}: " + stderr[-1600:])
        output.unlink(missing_ok=True)

    ok, stderr = await _try_mpeg4_fallback(path, output)
    if ok and output.exists() and output.stat().st_size > 0:
        normalized = await probe_video(output)
        if _is_mp4_container(normalized):
            return output, True
    errors.append("mpeg4: " + stderr[-1600:])
    output.unlink(missing_ok=True)

    joined = "\n---\n".join(errors)
    print(
        "IRIS_VIDEO_NORMALIZE_ERROR "
        f"input={path.name} codec={info.codec} audio={info.audio_codec} "
        f"pix_fmt={info.pix_fmt} size={info.width}x{info.height}\n{joined}",
        flush=True,
    )
    raise RuntimeError(
        "Não consegui normalizar este vídeo para MP4. "
        f"Codec={info.codec or '?'} áudio={info.audio_codec or 'sem áudio'} "
        f"resolução={info.width}x{info.height}. "
        + joined[-1200:]
    )
