from app.service_registry import detect_service


def test_known_services():
    assert detect_service("https://www.crunchyroll.com/watch/x").key == "crunchyroll"
    assert detect_service("https://animefire.io/anime/x").key == "animefire"
    assert detect_service("https://www.pobrenow.com/filme/x").key == "pobreflix"
    assert detect_service("https://tubepussy.org/x").key == "tubepussy"
    assert detect_service("https://xvideosputaria.com/x").key == "xvideosputaria"


def test_generic_service():
    profile = detect_service("https://example.com/video")
    assert profile.key == "generic"
    assert "browser" in profile.strategies
