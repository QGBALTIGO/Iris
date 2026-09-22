from app.classifier import classify_resource
from app.content import annotate_content_roles
from app.dedup import deduplicate
from app.downloads import safe_filename
from app.models import MediaResource, ResourceType


def test_1000_resource_stress_matrix():
    extensions = [
        ("mp4", ResourceType.VIDEO),
        ("webm", ResourceType.VIDEO),
        ("mp3", ResourceType.AUDIO),
        ("jpg", ResourceType.IMAGE),
        ("png", ResourceType.IMAGE),
        ("pdf", ResourceType.DOCUMENT),
        ("zip", ResourceType.ARCHIVE),
        ("srt", ResourceType.SUBTITLE),
        ("m3u8", ResourceType.PLAYLIST),
        ("mpd", ResourceType.PLAYLIST),
    ]
    resources = []
    for i in range(1000):
        ext, expected = extensions[i % len(extensions)]
        url = f"https://cdn.example.test/folder-{i % 37}/file-{i}.{ext}?v={i}"
        resource = MediaResource(
            url=url,
            type=classify_resource(url),
            title=f"../../unsafe:{i}?*.{ext}",
        )
        assert resource.type == expected
        name = safe_filename(resource, i + 1)
        assert "/" not in name
        assert "\\" not in name
        assert ".." not in name
        resources.append(resource)

    annotate_content_roles(resources)
    deduped = deduplicate(resources + resources[:200])
    assert len(deduped) == 1000


def test_1000_manga_page_role_rounds():
    resources = [
        MediaResource(
            url=f"https://cdn.example/secure/title/1/chapter/2/manga_page/high/{i}.jpg",
            type=ResourceType.IMAGE,
        )
        for i in range(1, 1001)
    ]
    annotate_content_roles(resources)
    assert all(r.metadata.get("role") == "chapter_page" for r in resources)
    assert [r.metadata.get("page_number") for r in resources[:3]] == [1, 2, 3]
    assert resources[-1].metadata.get("page_number") == 1000
