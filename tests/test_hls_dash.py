from app.extractors.dash import inspect_mpd
from app.extractors.hls import inspect_hls


def test_hls_master_variants():
    text = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
1080/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720/index.m3u8
"""
    variants, encrypted, drm = inspect_hls(text, "https://cdn.example/master.m3u8")
    assert [v.label for v in variants] == ["1080p", "720p"]
    assert variants[0].url == "https://cdn.example/1080/index.m3u8"
    assert not encrypted and not drm


def test_hls_drm_detection():
    text = '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,KEYFORMAT="com.apple.streamingkeydelivery",URI="skd://key"\n'
    _, encrypted, drm = inspect_hls(text, "https://cdn.example/master.m3u8")
    assert encrypted and drm


def test_dash_variants_and_drm():
    mpd = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
      <Period><AdaptationSet mimeType="video/mp4">
        <ContentProtection schemeIdUri="urn:uuid:test" />
        <Representation id="720" width="1280" height="720" bandwidth="1500000" codecs="avc1"><BaseURL>video720.mp4</BaseURL></Representation>
      </AdaptationSet></Period></MPD>"""
    variants, drm = inspect_mpd(mpd, "https://cdn.example/manifest.mpd")
    assert drm is True
    assert variants[0].label == "720p"
    assert variants[0].url == "https://cdn.example/video720.mp4"
