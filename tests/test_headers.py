from app.downloads import _replay_headers
from app.extractors.browser import _useful_headers


def test_browser_preserves_media_replay_headers():
    headers = {
        "Referer": "https://example.com/",
        "User-Agent": "ua",
        "Plus-Vw-Token": "token",
        "X-Custom": "value",
        "Sec-Fetch-Site": "same-site",
    }
    kept = _useful_headers(headers)
    assert kept["Plus-Vw-Token"] == "token"
    assert kept["X-Custom"] == "value"
    assert "Sec-Fetch-Site" not in kept


def test_downloader_replays_custom_media_headers():
    headers = {
        "Referer": "https://example.com/",
        "Plus-Vw-Token": "token",
        "X-Custom": "value",
        "Host": "cdn.example.com",
    }
    kept = _replay_headers(headers)
    assert kept["Plus-Vw-Token"] == "token"
    assert kept["X-Custom"] == "value"
    assert "Host" not in kept
