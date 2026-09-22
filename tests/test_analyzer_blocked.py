import httpx
import pytest

import app.analyzer as analyzer_module
from app.analyzer import Analyzer
from app.models import MediaResource, ResourceType
from app.settings import Settings


class BlockedFetcher:
    async def fetch(self, url, max_bytes=None, headers=None):
        request = httpx.Request("GET", str(url))
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("blocked", request=request, response=response)


@pytest.mark.asyncio
async def test_403_falls_back_to_browser_even_without_deep(monkeypatch, tmp_path):
    async def fake_browser(url, timeout_ms=18000, max_requests=1200, interaction_rounds=8):
        return [MediaResource(url="https://cdn.example/video.mp4", type=ResourceType.VIDEO, source="browser:network")]

    monkeypatch.setattr(analyzer_module, "probe_browser", fake_browser)
    config = Settings(browser_enabled=True, ytdlp_enabled=False, downloads_dir=tmp_path)
    result = await Analyzer(config, fetcher=BlockedFetcher()).analyze("https://example.com/watch", deep=False)

    assert len(result.resources) == 1
    assert result.resources[0].type == ResourceType.VIDEO
    assert any("403" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_deep_continues_after_blocked_http(monkeypatch, tmp_path):
    async def fake_browser(url, timeout_ms=18000, max_requests=1200, interaction_rounds=8):
        return []

    async def fake_ytdlp(url):
        return [MediaResource(url="https://example.com/watch", type=ResourceType.VIDEO, source="yt-dlp")]

    monkeypatch.setattr(analyzer_module, "probe_browser", fake_browser)
    monkeypatch.setattr(analyzer_module, "probe_ytdlp", fake_ytdlp)
    config = Settings(browser_enabled=True, ytdlp_enabled=True, downloads_dir=tmp_path)
    result = await Analyzer(config, fetcher=BlockedFetcher()).analyze("https://example.com/watch", deep=True)

    assert any(item.source == "yt-dlp" for item in result.resources)
