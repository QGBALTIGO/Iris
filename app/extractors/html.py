from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.classifier import classify_resource
from app.models import MediaResource, ResourceType

_MEDIA_URL_RE = re.compile(
    r"https?://[^\s'\"<>\\]+?\.(?:m3u8|mpd|mp4|mkv|webm|mov|m4v|mp3|m4a|aac|ogg|opus|wav|flac|srt|vtt|ass|pdf|zip)(?:\?[^\s'\"<>\\]*)?",
    re.I,
)


def _absolute(base: str, value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.startswith(("data:", "blob:", "javascript:", "mailto:", "tel:")):
        return None
    result = urljoin(base, value)
    if urlsplit(result).scheme not in {"http", "https"}:
        return None
    return result


def _push(out: list[MediaResource], base: str, value: str | None, source: str, mime: str | None = None, title: str | None = None) -> None:
    url = _absolute(base, value)
    if not url:
        return
    out.append(MediaResource(url=url, type=classify_resource(url, mime), source=source, mime_type=mime, title=title))


def _walk_jsonld(value, out: list[tuple[str, str | None]]):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"contentUrl", "embedUrl", "thumbnailUrl", "url"} and isinstance(child, str):
                out.append((child, key))
            else:
                _walk_jsonld(child, out)
    elif isinstance(value, list):
        for child in value:
            _walk_jsonld(child, out)


def extract_html_resources(html: str, base_url: str) -> tuple[str | None, list[MediaResource]]:
    soup = BeautifulSoup(html, "html.parser")
    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og_title = soup.select_one('meta[property="og:title"]')
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()

    resources: list[MediaResource] = []

    for tag_name, attr in (("video", "src"), ("audio", "src"), ("source", "src"), ("img", "src"), ("iframe", "src"), ("a", "href"), ("link", "href")):
        for tag in soup.find_all(tag_name):
            mime = tag.get("type")
            candidate = tag.get(attr)
            _push(resources, base_url, candidate, f"html:{tag_name}", mime, tag.get("title"))
            if tag_name in {"img", "source"}:
                srcset = tag.get("srcset")
                if srcset:
                    for part in srcset.split(","):
                        url_part = part.strip().split(" ", 1)[0]
                        _push(resources, base_url, url_part, f"html:{tag_name}:srcset", mime)

    meta_selectors = {
        'meta[property="og:video"]': ResourceType.VIDEO,
        'meta[property="og:video:url"]': ResourceType.VIDEO,
        'meta[property="og:image"]': ResourceType.IMAGE,
        'meta[name="twitter:player:stream"]': ResourceType.VIDEO,
        'meta[name="twitter:image"]': ResourceType.IMAGE,
    }
    for selector, forced_type in meta_selectors.items():
        for tag in soup.select(selector):
            url = _absolute(base_url, tag.get("content"))
            if url:
                resources.append(MediaResource(url=url, type=forced_type, source="html:meta"))

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        pairs: list[tuple[str, str | None]] = []
        _walk_jsonld(payload, pairs)
        for candidate, key in pairs:
            _push(resources, base_url, candidate, f"html:jsonld:{key or 'url'}")

    for script in soup.find_all("script"):
        text = script.string or script.get_text(" ", strip=False)
        if not text:
            continue
        for match in _MEDIA_URL_RE.findall(text):
            _push(resources, base_url, match.replace("\\/", "/"), "html:script")

    # Iframes and generic links are useful context but should not pollute the media count.
    for item in resources:
        if item.source in {"html:iframe", "html:a", "html:link"} and item.type == ResourceType.OTHER:
            item.metadata["navigation_only"] = True

    return title, resources
