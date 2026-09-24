import shutil
from pathlib import Path

import pytest

from app.fast_mtproto import connection_count
from app.video_tools import ensure_faststart, make_thumbnail, normalize_video_mp4, probe_video


def test_mtproto_connection_scaling():
    mib = 1024 * 1024
    assert connection_count(1 * mib) == 2
    assert connection_count(4 * mib) == 4
    assert connection_count(24 * mib) == 8
    assert connection_count(96 * mib) == 10
    assert connection_count(248 * mib) == 10
    assert connection_count(300 * mib) == 12


@pytest.mark.asyncio
async def test_ffprobe_and_thumbnail_pipeline(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe indisponível no runner")

    source = tmp_path / "sample.mp4"
    proc = await __import__("asyncio").create_subprocess_exec(
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100",
        "-t", "2",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        "-y", str(source),
    )
    assert await proc.wait() == 0

    await ensure_faststart(source)
    info = await probe_video(source)
    assert 1.8 <= info.duration <= 2.2
    assert info.width == 640
    assert info.height == 360

    thumb = await make_thumbnail(source, tmp_path / "thumb.jpg", second=1.0)
    assert thumb.exists()
    data = thumb.read_bytes()
    assert data.startswith(b"\xff\xd8\xff")
    assert 0 < len(data) < 200 * 1024


@pytest.mark.asyncio
async def test_normalizes_webm_to_real_mp4(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe indisponível no runner")

    source = tmp_path / "sample.webm"
    proc = await __import__("asyncio").create_subprocess_exec(
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=44100",
        "-t", "1.5",
        "-c:v", "libvpx-vp9",
        "-c:a", "libopus",
        "-y", str(source),
    )
    assert await proc.wait() == 0

    output, generated = await normalize_video_mp4(source)
    try:
        assert generated is True
        assert output.suffix == ".mp4"
        info = await probe_video(output)
        assert info.codec == "h264"
        assert info.audio_codec == "aac"
        assert "mp4" in (info.format_name or "")
    finally:
        if generated:
            output.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_normalizes_odd_dimensions_and_weird_codec(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe indisponível no runner")

    source = tmp_path / "odd.mkv"
    proc = await __import__("asyncio").create_subprocess_exec(
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=405x719:rate=17",
        "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=32000",
        "-t", "1.2",
        "-c:v", "ffv1",
        "-pix_fmt", "yuv444p",
        "-c:a", "pcm_s16le",
        "-y", str(source),
    )
    assert await proc.wait() == 0

    output, generated = await normalize_video_mp4(source)
    try:
        assert generated is True
        assert output.suffix == ".mp4"
        info = await probe_video(output)
        assert info.width % 2 == 0
        assert info.height % 2 == 0
        assert info.audio_codec == "aac"
        assert "mp4" in (info.format_name or "")
        assert info.codec in {"h264", "mpeg4"}
    finally:
        if generated:
            output.unlink(missing_ok=True)
