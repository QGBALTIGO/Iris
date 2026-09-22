import shutil
from pathlib import Path

import pytest

from app.fast_mtproto import connection_count
from app.video_tools import ensure_faststart, make_thumbnail, probe_video


def test_mtproto_connection_scaling():
    mib = 1024 * 1024
    assert connection_count(1 * mib) == 2
    assert connection_count(4 * mib) == 4
    assert connection_count(24 * mib) == 8
    assert connection_count(96 * mib) == 12
    assert connection_count(248 * mib) == 16


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
