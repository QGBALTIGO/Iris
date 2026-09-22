from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.downloads import DownloadEngine, _run, safe_filename
from app.models import MediaResource, ResourceType
from app.security import UnsafeUrlError
from app.settings import Settings


def test_safe_filename_strips_dangerous_chars():
    resource = MediaResource(url="https://x.test/video.mp4", type=ResourceType.VIDEO, title="../../Meu:Vídeo?*")
    name = safe_filename(resource)
    assert ".." not in name
    assert "/" not in name
    assert "\\" not in name


def test_drm_is_blocked_engine():
    engine = DownloadEngine()
    resource = MediaResource(url="https://example.com/master.m3u8", type=ResourceType.PLAYLIST, drm=True)
    assert engine.choose_engine(resource) == "blocked-drm"


def test_playlist_prefers_stream_engine():
    engine = DownloadEngine()
    resource = MediaResource(url="https://example.com/master.m3u8", type=ResourceType.PLAYLIST)
    with patch("shutil.which", side_effect=lambda x: "/usr/bin/yt-dlp" if x == "yt-dlp" else None):
        assert engine.choose_engine(resource) == "yt-dlp"


@pytest.mark.asyncio
async def test_direct_download_streams_and_reports_progress(tmp_path):
    payload = b"x" * 1024

    async def handler(request):
        return httpx.Response(200, headers={"content-length": str(len(payload)), "content-type": "video/mp4"}, content=payload)

    config = Settings(downloads_dir=tmp_path, max_download_bytes=2048)
    engine = DownloadEngine(config, transport=httpx.MockTransport(handler))
    resource = MediaResource(url="https://93.184.216.34/video.mp4", type=ResourceType.VIDEO)
    seen = []

    async def progress(downloaded, total, value):
        seen.append((downloaded, total, value))

    with patch("shutil.which", return_value=None):
        path = await engine.download(resource, progress=progress)
    assert path.read_bytes() == payload
    assert seen[-1] == (1024, 1024, 1.0)


@pytest.mark.asyncio
async def test_invalid_image_payload_is_rejected(tmp_path):
    payload = b"not-a-real-jpeg" * 100

    async def handler(request):
        return httpx.Response(200, headers={"content-length": str(len(payload)), "content-type": "image/jpeg"}, content=payload)

    engine = DownloadEngine(Settings(downloads_dir=tmp_path), transport=httpx.MockTransport(handler))
    resource = MediaResource(url="https://93.184.216.34/page.jpg", type=ResourceType.IMAGE)
    with pytest.raises(Exception, match="obfuscados"):
        await engine.download(resource)
    assert not (tmp_path / "page.jpg").exists()


@pytest.mark.asyncio
async def test_valid_jpeg_signature_is_accepted(tmp_path):
    payload = b"\xff\xd8\xff" + b"x" * 100

    async def handler(request):
        return httpx.Response(200, headers={"content-length": str(len(payload)), "content-type": "image/jpeg"}, content=payload)

    engine = DownloadEngine(Settings(downloads_dir=tmp_path), transport=httpx.MockTransport(handler))
    resource = MediaResource(url="https://93.184.216.34/page.jpg", type=ResourceType.IMAGE)
    path = await engine.download(resource)
    assert path.read_bytes().startswith(b"\xff\xd8\xff")


@pytest.mark.asyncio
async def test_redirect_to_private_network_is_blocked(tmp_path):
    async def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    engine = DownloadEngine(Settings(downloads_dir=tmp_path), transport=httpx.MockTransport(handler))
    resource = MediaResource(url="https://93.184.216.34/video.mp4", type=ResourceType.VIDEO)
    with patch("shutil.which", return_value=None):
        with pytest.raises(UnsafeUrlError):
            await engine.download(resource)


@pytest.mark.asyncio
async def test_declared_oversize_download_is_rejected(tmp_path):
    async def handler(request):
        return httpx.Response(200, headers={"content-length": "4096"}, content=b"x")

    engine = DownloadEngine(Settings(downloads_dir=tmp_path, max_download_bytes=1024), transport=httpx.MockTransport(handler))
    resource = MediaResource(url="https://93.184.216.34/video.mp4", type=ResourceType.VIDEO)
    with patch("shutil.which", return_value=None):
        with pytest.raises(Exception, match="excede o limite"):
            await engine.download(resource)


@pytest.mark.asyncio
async def test_external_process_progress_parser():
    seen = []

    async def progress(downloaded, total, value):
        seen.append(value)

    await _run(["python", "-c", "print('10%'); print('55%'); print('100%')"], progress=progress)
    assert seen == [0.1, 0.55, 1.0]
