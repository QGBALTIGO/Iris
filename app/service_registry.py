from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class ServiceProfile:
    key: str
    label: str
    hosts: tuple[str, ...]
    strategies: tuple[str, ...]
    drm_expected: bool = False
    browser_first: bool = False


_PROFILES = (
    ServiceProfile(
        key="crunchyroll",
        label="Crunchyroll",
        hosts=("crunchyroll.com",),
        strategies=("metadata", "browser", "yt-dlp"),
        drm_expected=True,
        browser_first=True,
    ),
    ServiceProfile(
        key="netflix",
        label="Netflix",
        hosts=("netflix.com",),
        strategies=("metadata", "browser", "manifest"),
        drm_expected=True,
        browser_first=True,
    ),
    ServiceProfile(
        key="primevideo",
        label="Prime Video",
        hosts=("primevideo.com", "amazon.com.br", "amazon.com"),
        strategies=("metadata", "browser", "manifest"),
        drm_expected=True,
        browser_first=True,
    ),
    ServiceProfile(
        key="disneyplus",
        label="Disney+",
        hosts=("disneyplus.com",),
        strategies=("metadata", "browser", "manifest"),
        drm_expected=True,
        browser_first=True,
    ),
    ServiceProfile(
        key="max",
        label="Max",
        hosts=("max.com", "hbomax.com"),
        strategies=("metadata", "browser", "manifest"),
        drm_expected=True,
        browser_first=True,
    ),
    ServiceProfile(
        key="paramountplus",
        label="Paramount+",
        hosts=("paramountplus.com",),
        strategies=("metadata", "browser", "manifest"),
        drm_expected=True,
        browser_first=True,
    ),
    ServiceProfile(
        key="appletv",
        label="Apple TV+",
        hosts=("tv.apple.com",),
        strategies=("metadata", "browser", "manifest"),
        drm_expected=True,
        browser_first=True,
    ),
    ServiceProfile(
        key="globoplay",
        label="Globoplay",
        hosts=("globoplay.globo.com",),
        strategies=("metadata", "browser", "manifest"),
        drm_expected=True,
        browser_first=True,
    ),
    ServiceProfile(
        key="hidive",
        label="HIDIVE",
        hosts=("hidive.com",),
        strategies=("metadata", "browser", "manifest"),
        drm_expected=True,
        browser_first=True,
    ),
    ServiceProfile(
        key="adn",
        label="Animation Digital Network",
        hosts=("animationdigitalnetwork.com", "animationdigitalnetwork.fr"),
        strategies=("metadata", "browser", "manifest"),
        drm_expected=True,
        browser_first=True,
    ),
    ServiceProfile(
        key="animefire",
        label="AnimeFire",
        hosts=("animefire.io", "animefire.net"),
        strategies=("html", "browser", "player-network"),
        browser_first=True,
    ),
    ServiceProfile(
        key="pobreflix",
        label="Pobreflix",
        hosts=("pobrenow.com", "pobreflix"),
        strategies=("html", "browser", "hls", "yt-dlp"),
        browser_first=True,
    ),
    ServiceProfile(
        key="tubepussy",
        label="TubePussy",
        hosts=("tubepussy.org",),
        strategies=("html", "browser", "direct-mp4", "yt-dlp"),
        browser_first=True,
    ),
    ServiceProfile(
        key="xvideosputaria",
        label="XVideosPutaria",
        hosts=("xvideosputaria.com",),
        strategies=("browser", "hls"),
        browser_first=True,
    ),
    ServiceProfile(
        key="mangaplus",
        label="MANGA Plus",
        hosts=("mangaplus.shueisha.co.jp",),
        strategies=("browser", "reader"),
        browser_first=True,
    ),
)


def detect_service(url: str) -> ServiceProfile:
    host = urlsplit(url).netloc.lower().split(":", 1)[0].removeprefix("www.")
    for profile in _PROFILES:
        for needle in profile.hosts:
            if "." in needle:
                if host == needle or host.endswith("." + needle):
                    return profile
            elif needle in host:
                return profile
    return ServiceProfile(
        key="generic",
        label="Site genérico",
        hosts=(),
        strategies=("http", "html", "yt-dlp", "browser", "manifest"),
    )


def list_services() -> list[ServiceProfile]:
    return list(_PROFILES)
