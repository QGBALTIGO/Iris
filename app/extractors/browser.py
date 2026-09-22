from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

from app.classifier import classify_resource
from app.models import MediaResource, ResourceType
from app.security import UnsafeUrlError, validate_public_url


_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
_KEEP_HEADERS = {
    "referer",
    "user-agent",
    "origin",
    "authorization",
    "cookie",
    "accept",
    "accept-language",
    "plus-vw-token",
}


def _useful_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in _KEEP_HEADERS or key.lower().startswith("x-")
    }


async def probe_browser(
    url: str,
    timeout_ms: int = 18_000,
    max_requests: int = 1200,
    interaction_rounds: int = 8,
) -> list[MediaResource]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []

    found: list[MediaResource] = []
    lock = asyncio.Lock()

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception:
            executable = Path(p.chromium.executable_path)
            if not executable.exists():
                raise
            browser = await p.chromium.launch(headless=True, executable_path=str(executable))
        context = await browser.new_context(
            ignore_https_errors=False,
            user_agent=_BROWSER_UA,
            locale="pt-BR",
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
            viewport={"width": 1365, "height": 900},
        )
        page = await context.new_page()

        async def guard(route):
            request_url = route.request.url
            try:
                await validate_public_url(request_url)
            except (UnsafeUrlError, ValueError):
                await route.abort()
                return
            await route.continue_()

        await page.route("**/*", guard)

        async def on_response(response):
            async with lock:
                if len(found) >= max_requests:
                    return
            ctype = response.headers.get("content-type")
            kind = classify_resource(response.url, ctype)
            if kind == ResourceType.OTHER:
                return
            try:
                await validate_public_url(response.url)
            except (UnsafeUrlError, ValueError):
                return
            size = None
            try:
                size = int(response.headers.get("content-length", ""))
            except ValueError:
                pass
            try:
                request_headers = _useful_headers(await response.request.all_headers())
            except Exception:
                request_headers = {"referer": page.url} if page.url else {}
            async with lock:
                found.append(
                    MediaResource(
                        url=response.url,
                        type=kind,
                        source="browser:network",
                        mime_type=ctype,
                        size=size,
                        headers=request_headers,
                        metadata={"status": response.status},
                    )
                )

        page.on("response", on_response)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            with suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=5_000)

            rounds = max(0, interaction_rounds)
            for _ in range(rounds):
                with suppress(Exception):
                    await page.evaluate(
                        """() => {
                            const step = Math.max(window.innerHeight * 0.9, 900);
                            window.scrollBy(0, step);
                            for (const el of document.querySelectorAll('*')) {
                                const s = getComputedStyle(el);
                                if ((s.overflowY === 'auto' || s.overflowY === 'scroll') &&
                                    el.scrollHeight > el.clientHeight + 100) {
                                    el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + Math.max(el.clientHeight * 0.9, 800));
                                }
                                if ((s.overflowX === 'auto' || s.overflowX === 'scroll') &&
                                    el.scrollWidth > el.clientWidth + 100) {
                                    el.scrollLeft = Math.min(el.scrollWidth, el.scrollLeft + Math.max(el.clientWidth * 0.9, 800));
                                }
                            }
                        }"""
                    )
                with suppress(Exception):
                    await page.mouse.wheel(0, 1800)
                with suppress(Exception):
                    await page.keyboard.press("PageDown")
                with suppress(Exception):
                    await page.wait_for_timeout(350)
                async with lock:
                    if len(found) >= max_requests:
                        break

            # Catch cached/lazy resources that may not emit a new response callback.
            with suppress(Exception):
                extra_urls = await page.evaluate(
                    """() => {
                        const values = new Set();
                        for (const img of document.images) {
                            if (img.currentSrc) values.add(img.currentSrc);
                            else if (img.src) values.add(img.src);
                        }
                        for (const v of document.querySelectorAll('video,audio,source')) {
                            if (v.currentSrc) values.add(v.currentSrc);
                            if (v.src) values.add(v.src);
                        }
                        for (const entry of performance.getEntriesByType('resource')) {
                            if (entry && entry.name) values.add(entry.name);
                        }
                        return Array.from(values);
                    }"""
                )
                for candidate in extra_urls[:max_requests]:
                    kind = classify_resource(candidate)
                    if kind == ResourceType.OTHER:
                        continue
                    try:
                        await validate_public_url(candidate)
                    except (UnsafeUrlError, ValueError):
                        continue
                    found.append(
                        MediaResource(
                            url=candidate,
                            type=kind,
                            source="browser:dom",
                            headers={"referer": page.url, "user-agent": _BROWSER_UA},
                        )
                    )
        finally:
            await context.close()
            await browser.close()
    return found
