import pytest
from app.classifier import classify_resource
from app.models import ResourceType


@pytest.mark.parametrize(
    "url,mime,expected",
    [
        ("https://x/a.mp4", None, ResourceType.VIDEO),
        ("https://x/a", "video/mp4", ResourceType.VIDEO),
        ("https://x/master.m3u8?token=1", None, ResourceType.PLAYLIST),
        ("https://x/manifest.mpd", "application/dash+xml", ResourceType.PLAYLIST),
        ("https://x/a.mp3", None, ResourceType.AUDIO),
        ("https://x/a.webp", None, ResourceType.IMAGE),
        ("https://x/a.vtt", None, ResourceType.SUBTITLE),
        ("https://x/a.pdf", None, ResourceType.DOCUMENT),
        ("https://x/a.zip", None, ResourceType.ARCHIVE),
    ],
)
def test_classifier(url, mime, expected):
    assert classify_resource(url, mime) == expected
