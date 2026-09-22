from app.extractors.html import extract_html_resources
from app.models import ResourceType


def test_relative_mp4_in_script_is_extracted():
    html = """
    <html><head><title>Video page</title></head>
    <body><script>
      const source = "/media/videos/example.mp4?token=abc";
    </script></body></html>
    """
    title, resources = extract_html_resources(html, "https://example.com/watch/post")
    assert title == "Video page"
    videos = [r for r in resources if r.type == ResourceType.VIDEO]
    assert len(videos) == 1
    assert videos[0].url == "https://example.com/media/videos/example.mp4?token=abc"
    assert videos[0].source == "html:script-relative"
