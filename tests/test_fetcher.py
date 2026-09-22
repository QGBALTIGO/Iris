import httpx
import pytest

from app.fetcher import SafeFetcher
from app.security import UnsafeUrlError
from app.settings import Settings


@pytest.mark.asyncio
async def test_fetcher_follows_safe_redirects():
    async def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/done"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")

    fetcher = SafeFetcher(Settings(), transport=httpx.MockTransport(handler))
    result = await fetcher.fetch("https://93.184.216.34/start")
    assert result.url.endswith("/done")
    assert result.body == b"ok"


@pytest.mark.asyncio
async def test_fetcher_blocks_private_redirect():
    async def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    fetcher = SafeFetcher(Settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeUrlError):
        await fetcher.fetch("https://93.184.216.34/start")


@pytest.mark.asyncio
async def test_fetcher_stops_stream_over_limit_without_content_length():
    async def handler(request):
        return httpx.Response(200, content=b"x" * 2048)

    fetcher = SafeFetcher(Settings(max_html_bytes=1024), transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="excede o limite"):
        await fetcher.fetch("https://93.184.216.34/page")
