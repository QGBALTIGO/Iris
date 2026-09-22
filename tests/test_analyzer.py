import httpx
import pytest

from app.analyzer import Analyzer, _valid_image_payload
from app.fetcher import SafeFetcher
from app.models import ResourceType
from app.settings import Settings


@pytest.mark.asyncio
async def test_analyzer_page_and_manifest(monkeypatch, tmp_path):
    page = b'<html><head><title>Page</title></head><body><video src="https://93.184.216.34/v.mp4"></video><script>"https://93.184.216.34/master.m3u8"</script></body></html>'
    manifest = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=1280x720\n720.m3u8\n'

    async def handler(request):
        if request.url.path.endswith("master.m3u8"):
            return httpx.Response(200, headers={"content-type": "application/vnd.apple.mpegurl"}, content=manifest)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=page)

    transport = httpx.MockTransport(handler)
    config = Settings(browser_enabled=False, ytdlp_enabled=False, downloads_dir=tmp_path)
    fetcher = SafeFetcher(config, transport=transport)
    analyzer = Analyzer(config, fetcher=fetcher)
    result = await analyzer.analyze("https://93.184.216.34/page")
    assert result.title == "Page"
    playlist = next(r for r in result.resources if r.type == ResourceType.PLAYLIST)
    assert playlist.variants[0].label == "720p"


def test_valid_image_payload_signatures():
    assert _valid_image_payload(b"\xff\xd8\xff" + b"x" * 20)
    assert _valid_image_payload(b"\x89PNG\r\n\x1a\n" + b"x" * 20)
    assert _valid_image_payload(b"RIFFxxxxWEBP" + b"x" * 20)
    assert not _valid_image_payload(bytes.fromhex("95a95bd4f2e4f9ced6ca2ef45fa5b585"))
