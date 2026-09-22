from app.extractors.html import extract_html_resources
from app.models import ResourceType


def test_extracts_html_media_meta_jsonld_and_script():
    html = """
    <html><head><title>Demo</title><meta property="og:video" content="/hero.mp4"></head>
    <body>
      <video src="/movie.mp4"><source src="/movie-720.mp4" type="video/mp4"></video>
      <img src="/cover.jpg" srcset="/cover-2x.jpg 2x">
      <a href="/book.pdf">PDF</a>
      <script type="application/ld+json">{"@type":"VideoObject","contentUrl":"https://cdn.example/video.webm","thumbnailUrl":"/thumb.webp"}</script>
      <script>window.stream="https://cdn.example/master.m3u8?token=abc";</script>
    </body></html>
    """
    title, resources = extract_html_resources(html, "https://example.com/page")
    assert title == "Demo"
    urls = {r.url for r in resources}
    assert "https://example.com/movie.mp4" in urls
    assert "https://example.com/movie-720.mp4" in urls
    assert "https://example.com/cover.jpg" in urls
    assert "https://example.com/cover-2x.jpg" in urls
    assert "https://example.com/book.pdf" in urls
    assert "https://cdn.example/video.webm" in urls
    assert "https://cdn.example/master.m3u8?token=abc" in urls
    assert any(r.type == ResourceType.PLAYLIST for r in resources)


def test_blob_and_data_urls_are_ignored():
    html = '<video src="blob:https://example.com/x"></video><img src="data:image/png;base64,xxx">'
    _, resources = extract_html_resources(html, "https://example.com")
    assert resources == []


def test_embed_meta_is_not_counted_as_downloadable_video():
    html = """
    <html><head>
      <meta property="og:video" content="/embed/23405">
      <meta property="og:video:url" content="/movie.mp4">
    </head></html>
    """
    _, resources = extract_html_resources(html, "https://example.com/page")
    embed = next(r for r in resources if r.url.endswith("/embed/23405"))
    movie = next(r for r in resources if r.url.endswith("/movie.mp4"))
    assert embed.type == ResourceType.OTHER
    assert embed.metadata["navigation_only"] is True
    assert movie.type == ResourceType.VIDEO
