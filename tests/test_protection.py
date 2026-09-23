from app.protection import (
    detect_dash_protection,
    detect_hls_protection,
    detect_streaming_service,
)


def test_streaming_service_detection():
    assert detect_streaming_service("https://www.crunchyroll.com/watch/ABC") == "Crunchyroll"
    assert detect_streaming_service("https://www.netflix.com/watch/123") == "Netflix"
    assert detect_streaming_service("https://www.primevideo.com/detail/0ABC") == "Prime Video"
    assert detect_streaming_service("https://www.disneyplus.com/video/abc") == "Disney+"
    assert detect_streaming_service("https://www.max.com/video/watch/abc") == "Max"
    assert detect_streaming_service("https://www.paramountplus.com/shows/test") == "Paramount+"
    assert detect_streaming_service("https://tv.apple.com/br/show/test/umc.cmc.123") == "Apple TV+"
    assert detect_streaming_service("https://globoplay.globo.com/v/123") == "Globoplay"
    assert detect_streaming_service("https://example.com/video") is None


def test_hls_widevine_detection():
    text = (
        '#EXTM3U\n'
        '#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,'
        'KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",'
        'URI="data:text/plain;base64,AAAA"\n'
    )
    details = detect_hls_protection(text)
    assert details.encrypted is True
    assert details.drm is True
    assert details.systems == ("Widevine",)


def test_hls_aes128_is_encrypted_but_not_drm():
    text = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="https://cdn.example/key.bin"\n'
    details = detect_hls_protection(text)
    assert details.encrypted is True
    assert details.drm is False
    assert details.systems == ("AES-128",)


def test_hls_fairplay_detection():
    text = (
        '#EXTM3U\n'
        '#EXT-X-KEY:METHOD=SAMPLE-AES,'
        'KEYFORMAT="com.apple.streamingkeydelivery",URI="skd://asset"\n'
    )
    details = detect_hls_protection(text)
    assert details.drm is True
    assert details.systems == ("FairPlay",)


def test_dash_widevine_and_playready_detection():
    text = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
      <Period><AdaptationSet>
        <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed" />
        <ContentProtection schemeIdUri="urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95" />
      </AdaptationSet></Period>
    </MPD>"""
    details = detect_dash_protection(text)
    assert details.encrypted is True
    assert details.drm is True
    assert details.systems == ("Widevine", "PlayReady")


def test_dash_unknown_content_protection_stays_blocked():
    text = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
      <Period><AdaptationSet>
        <ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc" />
      </AdaptationSet></Period>
    </MPD>"""
    details = detect_dash_protection(text)
    assert details.drm is True
    assert details.systems == ("CENC/DRM",)
