from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx

from app.content import annotate_content_roles
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
        title = None
        content_type = None
        final_url = str(url)
        resources: list[MediaResource] = []
        warnings: list[str] = []
        page = None
        blocked_status: int | None = None

        try:
            page = await self.fetcher.fetch(url)
        except httpx.HTTPStatusError as exc:
            blocked_status = exc.response.status_code
            warnings.append(f"HTTP {blocked_status} na análise direta; usando navegador/extrator quando possível.")
        except Exception as exc:
            if not deep:
                raise
            warnings.append(f"Falha na análise HTTP: {type(exc).__name__}")

        if page is not None:
            final_url = page.url
            content_type = page.content_type
            ctype = (page.content_type or "").lower()
            if "text/html" in ctype or page.body.lstrip().startswith((b"<!DOCTYPE html", b"<html", b"<HTML")):
                html = page.body.decode(_charset(ctype), errors="replace")
                title, resources = extract_html_resources(html, page.url)
            else:
                from app.classifier import classify_resource
                resources.append(
                    MediaResource(
                        url=page.url,
                        type=classify_resource(page.url, ctype),
                        source="direct",
                        mime_type=page.content_type,
                        size=page.content_length,
                    )
                )

        resources = deduplicate(resources)
        await self._inspect_manifests(resources, warnings)

        browser_fallback = blocked_status in {401, 403, 429}
        if deep:
            await self._deep_probe(final_url, resources, warnings)
        elif self.config.browser_enabled and browser_fallback:
            try:
                resources.extend(
                    await probe_browser(
                        final_url,
                        max_requests=self.config.max_browser_requests,
                        timeout_ms=12_000,
                        interaction_rounds=2,
                    )
                )
            except Exception as exc:
                warnings.append(f"Navegador indisponível: {type(exc).__name__}")

        resources = [r for r in deduplicate(resources) if not r.metadata.get("navigation_only")]
        annotate_content_roles(resources)
        await self._inspect_manifests(resources, warnings)

        if any(item.drm for item in resources):
            warnings.append("Mídia protegida por DRM detectada; o Iris informa a proteção, mas não tenta contorná-la.")

        return AnalyzeResult(
            url=str(url),
            final_url=final_url,
            title=title,
            content_type=content_type,
            resources=resources,
            warnings=list(dict.fromkeys(warnings)),
        )

    async def _deep_probe(self, final_url: str, resources: list[MediaResource], warnings: list[str]) -> None:
        tasks: list[tuple[str, asyncio.Task]] = []
        host = urlsplit(final_url).netloc.lower()
        rounds = 64 if "mangaplus.shueisha.co.jp" in host else 18

        if self.config.browser_enabled:
            tasks.append((
                "browser",
                asyncio.create_task(
                    probe_browser(
                        final_url,
                        max_requests=self.config.max_browser_requests,
                        timeout_ms=18_000,
                        interaction_rounds=rounds,
                    )
                ),
            ))
        if self.config.ytdlp_enabled:
            tasks.append(("yt-dlp", asyncio.create_task(probe_ytdlp(final_url))))

        if not tasks:
            return

        results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for (name, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                warnings.append(f"{name} falhou: {type(result).__name__}")
                continue
            resources.extend(result)

    async def _inspect_manifests(self, resources: list[MediaResource], warnings: list[str]) -> None:
        candidates = [r for r in resources if r.type == ResourceType.PLAYLIST][: self.config.max_manifest_probes]
        for resource in candidates:
            if resource.variants or resource.drm:
                continue
            try:
                result = await self.fetcher.fetch(
                    resource.url,
                    max_bytes=2 * 1024 * 1024,
                    headers=resource.headers,
                )
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
                warnings.append("Uma playlist protegida por DRM foi detectada.")


def _charset(content_type: str) -> str:
    if "charset=" in content_type:
        return content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
    return "utf-8"
