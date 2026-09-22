import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import app.extractors.browser as browser_module
from app.models import ResourceType


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = b'<html><body><script>fetch("/video.mp4")</script></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/video.mp4":
            body = b"fake-video"
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


@pytest.mark.asyncio
async def test_browser_network_sniffer_detects_media(monkeypatch):
    async def allow_local(url):
        return url

    monkeypatch.setattr(browser_module, "validate_public_url", allow_local)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        try:
            resources = await browser_module.probe_browser(url, timeout_ms=8000, max_requests=20)
        except Exception as exc:
            pytest.skip(f"Chromium indisponível neste runner: {exc}")
        assert any(r.type == ResourceType.VIDEO and r.url.endswith("/video.mp4") for r in resources)
    finally:
        server.shutdown()
        server.server_close()
