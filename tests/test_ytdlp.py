from app.extractors.ytdlp import _convert
from app.models import ResourceType


def test_ytdlp_formats_are_grouped_as_one_video():
    info = {
        "id": "abc",
        "title": "Demo",
        "webpage_url": "https://video.example/watch/abc",
        "duration": 120,
        "formats": [
            {"format_id": "1", "url": "https://cdn.example/480.mp4", "height": 480, "width": 854, "vcodec": "avc1", "acodec": "mp4a", "tbr": 800, "ext": "mp4"},
            {"format_id": "2", "url": "https://cdn.example/1080.mp4", "height": 1080, "width": 1920, "vcodec": "avc1", "acodec": "none", "tbr": 4500, "ext": "mp4"},
            {"format_id": "3", "url": "https://cdn.example/audio.m4a", "vcodec": "none", "acodec": "mp4a", "abr": 128, "ext": "m4a"},
        ],
    }
    resources = _convert(info)
    assert len(resources) == 1
    resource = resources[0]
    assert resource.type == ResourceType.VIDEO
    assert resource.url == "https://video.example/watch/abc"
    assert resource.height == 1080
    assert resource.quality == "1080p"
    assert len(resource.variants) == 3
    assert resource.metadata["engine"] == "yt-dlp"


def test_ytdlp_playlist_becomes_distinct_items():
    base = {"formats": [{"format_id": "1", "url": "https://cdn.example/a.mp4", "height": 720, "vcodec": "avc1", "acodec": "mp4a"}]}
    info = {
        "entries": [
            {**base, "id": "1", "title": "One", "webpage_url": "https://example.com/1"},
            {**base, "id": "2", "title": "Two", "webpage_url": "https://example.com/2"},
        ]
    }
    resources = _convert(info)
    assert [r.title for r in resources] == ["One", "Two"]
