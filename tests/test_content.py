from app.content import annotate_content_roles, chapter_pages, content_images
from app.models import AnalyzeResult, MediaResource, ResourceType


def test_manga_pages_are_separated_from_assets():
    resources = [
        MediaResource(url="https://cdn.test/secure/title/1/chapter/2/manga_page/high/2.jpg", type=ResourceType.IMAGE),
        MediaResource(url="https://cdn.test/secure/title/1/chapter/2/manga_page/high/1.jpg", type=ResourceType.IMAGE),
        MediaResource(url="https://cdn.test/secure/title/1/chapter/500/chapter_thumbnail/613865.jpg", type=ResourceType.IMAGE),
        MediaResource(url="https://site.test/logo.png", type=ResourceType.IMAGE),
        MediaResource(url="https://site.test/cover.jpg", type=ResourceType.IMAGE),
    ]
    annotate_content_roles(resources)
    result = AnalyzeResult(url="https://site.test", final_url="https://site.test", resources=resources)
    assert [r.metadata["page_number"] for r in chapter_pages(result)] == [1, 2]
    assert [r.url for r in content_images(result)] == ["https://site.test/cover.jpg"]
