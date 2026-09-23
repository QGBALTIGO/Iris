from app.manifest_tracks import dash_track_details, hls_track_details


def test_hls_track_details():
    text = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Português",LANGUAGE="pt-BR",DEFAULT=YES,CHANNELS="2",URI="audio/pt.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",URI="audio/en.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub",NAME="Português",LANGUAGE="pt-BR",FORCED=NO,URI="sub/pt.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,AUDIO="aud",SUBTITLES="sub"
1080.m3u8
"""
    result = hls_track_details(text, "https://cdn.example/master.m3u8")
    assert len(result["audio_tracks"]) == 2
    assert result["audio_tracks"][0]["language"] == "pt-BR"
    assert result["audio_tracks"][0]["default"] is True
    assert result["subtitle_tracks"][0]["url"] == "https://cdn.example/sub/pt.m3u8"
    assert result["drm"] is False


def test_hls_fairplay_details():
    text = '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,KEYFORMAT="com.apple.streamingkeydelivery",URI="skd://license"\n'
    result = hls_track_details(text, "https://cdn.example/master.m3u8")
    assert result["drm"] is True
    assert "FairPlay" in result["drm_systems"]


def test_dash_track_details_and_widevine():
    text = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>
      <AdaptationSet contentType="audio" lang="ja" codecs="mp4a.40.2">
        <AudioChannelConfiguration value="2"/>
        <Representation id="a1"/>
      </AdaptationSet>
      <AdaptationSet contentType="text" lang="pt-BR"><Representation id="s1" mimeType="text/vtt"/></AdaptationSet>
      <AdaptationSet contentType="video">
        <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
        <Representation id="v1" width="1920" height="1080"/>
      </AdaptationSet>
    </Period></MPD>"""
    result = dash_track_details(text, "https://cdn.example/manifest.mpd")
    assert result["audio_tracks"][0]["language"] == "ja"
    assert result["subtitle_tracks"][0]["language"] == "pt-BR"
    assert result["drm"] is True
    assert "Widevine" in result["drm_systems"]
