from app.dedup import deduplicate, normalize_url
from app.models import MediaResource, ResourceType


def test_tracking_parameters_are_removed_for_identity():
    assert normalize_url("https://EXAMPLE.com/a.mp4?utm_source=x&b=2&a=1#x") == "https://example.com/a.mp4?a=1&b=2"


def test_duplicate_resources_merge_metadata():
    items = [
        MediaResource(url="https://x.test/a.mp4?utm_source=a", type=ResourceType.VIDEO, source="html"),
        MediaResource(url="https://x.test/a.mp4", type=ResourceType.VIDEO, source="browser", mime_type="video/mp4", size=123),
    ]
    out = deduplicate(items)
    assert len(out) == 1
    assert out[0].mime_type == "video/mp4"
    assert out[0].size == 123
