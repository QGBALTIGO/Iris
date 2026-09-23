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
        key="animeonlinecc",
        label="Animes Online",
        hosts=("animesonlinecc.to",),
        strategies=("html", "browser", "hls", "yt-dlp"),
        browser_first=True,
    ),
    ServiceProfile(
        key="pobreflix",
        label="Pobreflix",
        hosts=("pobreflix",),
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
