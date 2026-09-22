from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from app.models import AnalyzeResult, MediaResource, ResourceType

_PAGE_PATTERNS = (
    re.compile(r"/manga_page/(?:high|low|medium)/(\d+)\.(?:jpe?g|png|webp)$", re.I),
    re.compile(r"/(?:pages?|chapter|chapters?)/(?:[^/]+/)*(\d+)\.(?:jpe?g|png|webp)$", re.I),
)
_ASSET_WORDS = {
    "favicon", "logo", "icon", "sprite", "avatar", "banner", "badge", "pixel", "tracking",
    "spinner", "loading", "placeholder", "emoji", "advert", "ads", "cookie", "onetrust",
}


def _page_number(resource: MediaResource) -> int | None:
    path = urlsplit(resource.url).path
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(path)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def _looks_like_asset(resource: MediaResource) -> bool:
    path = urlsplit(resource.url).path.lower()
    name = Path(path).name
    if any(word in name or f"/{word}" in path for word in _ASSET_WORDS):
        return True
    if resource.width and resource.height and resource.width <= 160 and resource.height <= 160:
        return True
    if resource.size is not None and resource.size <= 4096:
        return True
    return False


def annotate_content_roles(resources: list[MediaResource]) -> list[MediaResource]:
    for resource in resources:
        if resource.type != ResourceType.IMAGE:
            continue
        page_number = _page_number(resource)
        if page_number is not None:
            resource.metadata["role"] = "chapter_page"
            resource.metadata["page_number"] = page_number
            continue
        if _looks_like_asset(resource):
            resource.metadata.setdefault("role", "asset")
        else:
            resource.metadata.setdefault("role", "content_image")
    return resources


def chapter_pages(result: AnalyzeResult) -> list[MediaResource]:
    pages = [r for r in result.resources if r.metadata.get("role") == "chapter_page"]
    return sorted(
        pages,
        key=lambda r: (int(r.metadata.get("page_number") or 10**9), r.url),
    )


def content_images(result: AnalyzeResult) -> list[MediaResource]:
    pages = {r.url for r in chapter_pages(result)}
    images = [
        r for r in result.resources
        if r.type == ResourceType.IMAGE
        and r.url not in pages
        and r.metadata.get("role") != "asset"
    ]
    return images


def content_summary(result: AnalyzeResult) -> dict[str, int]:
    return {
        "videos": sum(r.type in {ResourceType.VIDEO, ResourceType.PLAYLIST, ResourceType.STREAM} for r in result.resources),
        "audio": sum(r.type == ResourceType.AUDIO for r in result.resources),
        "chapter_pages": len(chapter_pages(result)),
        "images": len(content_images(result)),
        "files": sum(r.type in {ResourceType.DOCUMENT, ResourceType.ARCHIVE, ResourceType.SUBTITLE} for r in result.resources),
        "drm": sum(bool(r.drm) for r in result.resources),
    }
