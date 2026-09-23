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
_PRIMARY_MEDIA = {ResourceType.VIDEO, ResourceType.AUDIO, ResourceType.PLAYLIST, ResourceType.STREAM}


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
    disable_gpu: bool = False,
) -> list[MediaResource]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []

    found: list[MediaResource] = []
    lock = asyncio.Lock()

    async with async_playwright() as p:
        launch_args: list[str] = []
        if disable_gpu:
            launch_args.extend([
                "--disable-gpu",
                "--disable-gpu-compositing",
                "--disable-accelerated-2d-canvas",
                "--disable-accelerated-video-decode",
                "--disable-accelerated-video-encode",
            ])
        try:
            browser = await p.chromium.launch(headless=True, args=launch_args)
        except Exception:
            executable = Path(p.chromium.executable_path)
            if not executable.exists():
                raise
            browser = await p.chromium.launch(
                headless=True,
                executable_path=str(executable),
                args=launch_args,
            )
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
            lower_url = response.url.lower()
            path_name = lower_url.split("?", 1)[0].rsplit("/", 1)[-1]
            disguised_hls = (
                "/hls/" in lower_url
                and path_name in {"master.txt", "playlist.txt", "index.txt", "video.txt"}
            )
            if kind == ResourceType.OTHER and disguised_hls:
                kind = ResourceType.PLAYLIST
            if kind == ResourceType.OTHER:
                return
            path_lower = Path(response.url.split("?", 1)[0]).suffix.lower()
            hls_segment = (
                path_lower == ".ts"
                and (
                    "/hls/" in response.url.lower()
                    or "/segment" in response.url.lower()
                    or "/video" in response.url.lower()
                )
            )
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
                        metadata={
                            "status": response.status,
                            "hls_segment": hls_segment,
                        },
                    )
                )

        page.on("response", on_response)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            with suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=2_000)

            # Safely nudge common HTML5/custom players so lazy media requests
            # are actually emitted. No navigation or ad clicks are attempted.
            async def nudge_players():
                selectors = [
                    "video",
                    ".player_select_item",
                    "button[aria-label*='play' i]",
                    ".vjs-big-play-button",
                    ".plyr__control--overlaid",
                    ".jw-icon-playback",
                ]
                for frame in page.frames:
                    with suppress(Exception):
                        await frame.evaluate(
                            """() => {
                                for (const v of document.querySelectorAll('video')) {
                                    try {
                                        v.muted = true;
                                        const p = v.play();
                                        if (p && p.catch) p.catch(() => {});
                                    } catch (_) {}
                                }
                            }"""
                        )
                    for selector in selectors[1:]:
                        with suppress(Exception):
                            locator = frame.locator(selector).first
                            if await locator.count():
                                await locator.click(timeout=700, force=True)
                                break

            with suppress(Exception):
                await nudge_players()
                await page.wait_for_timeout(900)
                await nudge_players()
                await page.wait_for_timeout(350)

            rounds = max(0, interaction_rounds)
            previous_count = -1
            stable_rounds = 0
            long_reader = rounds > 24
            for round_index in range(rounds):
                with suppress(Exception):
                    await page.evaluate(
                        """() => {
                            const step = Math.max(window.innerHeight * 1.15, 1000);
                            window.scrollBy(0, step);
                            for (const el of document.querySelectorAll('*')) {
                                const s = getComputedStyle(el);
                                if ((s.overflowY === 'auto' || s.overflowY === 'scroll') &&
                                    el.scrollHeight > el.clientHeight + 100) {
                                    el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + Math.max(el.clientHeight * 1.15, 900));
                                }
                                if ((s.overflowX === 'auto' || s.overflowX === 'scroll') &&
                                    el.scrollWidth > el.clientWidth + 100) {
                                    el.scrollLeft = Math.min(el.scrollWidth, el.scrollLeft + Math.max(el.clientWidth * 1.15, 900));
                                }
                            }
                        }"""
                    )
                with suppress(Exception):
                    await page.mouse.wheel(0, 2200)
                with suppress(Exception):
                    await page.keyboard.press("PageDown")
                with suppress(Exception):
                    await page.wait_for_timeout(180)

                if round_index in {3, 8}:
                    with suppress(Exception):
                        await nudge_players()
                        await page.wait_for_timeout(350)

                async with lock:
                    snapshot = list(found)
                    current_count = len(snapshot)
                    if current_count >= max_requests:
                        break

                # For normal watch pages, once real media appears there is no
                # benefit in spending several extra seconds scrolling the page.
                if not long_reader and round_index >= 3 and any(
                    item.type in _PRIMARY_MEDIA and not item.metadata.get("hls_segment")
                    for item in snapshot
                ):
                    break

                if current_count == previous_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                previous_count = current_count

                if long_reader:
                    if round_index >= 12 and stable_rounds >= 8:
                        break
                elif round_index >= 4 and stable_rounds >= 3:
                    break

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
                    lower_candidate = candidate.lower()
                    path_name = lower_candidate.split("?", 1)[0].rsplit("/", 1)[-1]
                    if (
                        kind == ResourceType.OTHER
                        and "/hls/" in lower_candidate
                        and path_name in {"master.txt", "playlist.txt", "index.txt", "video.txt"}
                    ):
                        kind = ResourceType.PLAYLIST
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
