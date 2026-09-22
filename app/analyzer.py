from __future__ import annotations

from app.dedup import deduplicate
from app.extractors.browser import probe_browser
from app.extractors.dash import inspect_mpd
from app.extractors.hls import inspect_hls
from app.extractors.html import extract_html_resources
from app.extractors.ytdlp import probe_ytdlp
from app.fetcher import SafeFetcher
from app.models import AnalyzeResult, MediaResource, ResourceType
from app.settings import Settings, settings


class Analyzer:
    def __init__(self, config: Settings = settings, fetcher: SafeFetcher | None = None):
        self.config = config
        self.fetcher = fetcher or SafeFetcher(config)

    async def analyze(self, url: str, deep: bool = False) -> AnalyzeResult:
        page = await self.fetcher.fetch(url)
        ctype = (page.content_type or "").lower()
        title = None
        resources: list[MediaResource] = []
        warnings: list[str] = []

        if "text/html" in ctype or page.body.lstrip().startswith((b"<!DOCTYPE html", b"<html", b"<HTML")):
            html = page.body.decode(_charset(ctype), errors="replace")
            title, resources = extract_html_resources(html, page.url)
        else:
            from app.classifier import classify_resource
            resources.append(MediaResource(url=page.url, type=classify_resource(page.url, ctype), source="direct", mime_type=page.content_type, size=page.content_length))

        resources = deduplicate(resources)
        await self._inspect_manifests(resources, warnings)

        if deep and self.config.ytdlp_enabled:
            resources.extend(await probe_ytdlp(page.url))
        if deep and self.config.browser_enabled:
            try:
                resources.extend(await probe_browser(page.url, max_requests=self.config.max_browser_requests))
            except Exception as exc:
                warnings.append(f"Browser profundo indisponível: {type(exc).__name__}")

        resources = [r for r in deduplicate(resources) if not r.metadata.get("navigation_only")]
        await self._inspect_manifests(resources, warnings)
        return AnalyzeResult(url=str(url), final_url=page.url, title=title, content_type=page.content_type, resources=resources, warnings=warnings)

    async def _inspect_manifests(self, resources: list[MediaResource], warnings: list[str]) -> None:
        candidates = [r for r in resources if r.type == ResourceType.PLAYLIST][: self.config.max_manifest_probes]
        for resource in candidates:
            if resource.variants or resource.drm:
                continue
            try:
                result = await self.fetcher.fetch(resource.url, max_bytes=2 * 1024 * 1024)
            except Exception:
                continue
            text = result.body.decode("utf-8", errors="replace")
            lower_url = resource.url.lower()
            if ".m3u8" in lower_url or "mpegurl" in (result.content_type or "").lower():
                variants, encrypted, drm = inspect_hls(text, resource.url)
                resource.variants = variants
                resource.encrypted = encrypted
                resource.drm = drm
            elif ".mpd" in lower_url or "dash+xml" in (result.content_type or "").lower():
                variants, drm = inspect_mpd(text, resource.url)
                resource.variants = variants
                resource.drm = drm
            if resource.drm:
                warnings.append(f"Mídia protegida por DRM detectada: {resource.url}")


def _charset(content_type: str) -> str:
    if "charset=" in content_type:
        return content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
    return "utf-8"
