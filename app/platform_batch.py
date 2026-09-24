from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.analyzer import Analyzer
from app.browser_guard import browser_slot
from app.delivery import DeliveryManager
from app.editorial import extract_editorial_metadata, format_video_caption
from app.extractors.browser import probe_browser
from app.settings import settings
from app.video_candidates import download_first_valid_video

_SEEDS = {
    "tubepussy": "https://tubepussy.org/ruiva-isabel-dando-a-bucetinha-e-levando-na-cara/#forward",
    "xvideosputaria": "https://xvideosputaria.com/anao-gabriela-gadotti-mini-gabys-boquetando-com-leite-na-boca/#forward",
}

_HISTORY_PATH = Path("/data/platform_batch_history.json")

# Media confirmed in previous Railway platform-batch logs. Query strings are
# intentionally omitted because these CDNs rotate signed URLs.
_HISTORICAL_MEDIA_KEYS = {
    "tubepussy": {
        "midias.foxvideo.club/23000/23405/23405.mp4",
        "midias.foxvideo.club/27000/27864/27864_shorts.mp4",
        "midias.foxvideo.club/13000/13621/13621_shorts.mp4",
    },
    "xvideosputaria": {
        "vazounudes.net/hls/db4bbebc-710b-445d-8b4b-2ae2cc081d82/480p/video.m3u8",
    },
}

_HISTORICAL_PAGES = {
    "tubepussy": {
        "https://tubepussy.org/ruiva-isabel-dando-a-bucetinha-e-levando-na-cara/",
        "https://tubepussy.org/en/redhead-isabel-gets-pussy-pounded-and-facialized/",
        "https://tubepussy.org/shorts/27864/",
        "https://tubepussy.org/shorts/13621/",
    },
    "xvideosputaria": {
        "https://xvideosputaria.com/anao-gabriela-gadotti-mini-gabys-boquetando-com-leite-na-boca/",
    },
}


def _load_history() -> dict[str, dict[str, list[str]]]:
    data: dict[str, dict[str, list[str]]] = {}
    try:
        raw = json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for platform, value in raw.items():
                if not isinstance(value, dict):
                    continue
                data[str(platform)] = {
                    "media": [str(x) for x in value.get("media", []) if x],
                    "pages": [str(x) for x in value.get("pages", []) if x],
                }
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return data


def _save_history(data: dict[str, dict[str, list[str]]]) -> None:
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = _HISTORY_PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(_HISTORY_PATH)


_SKIP_PREFIXES = (
    "/tag/",
    "/tags/",
    "/category/",
    "/categories/",
    "/author/",
    "/search/",
    "/page/",
    "/wp-",
    "/feed/",
    "/models/",
    "/model/",
    "/pornstar/",
    "/pornstars/",
    "/atriz/",
    "/ator/",
    "/performer/",
)


def _canonical(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _media_key(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.netloc.lower()}{parsed.path}".rstrip("/")


def _looks_like_post(url: str, host: str) -> bool:
    parsed = urlsplit(url)
    if parsed.netloc.lower().removeprefix("www.") != host:
        return False
    path = parsed.path or "/"
    if path == "/":
        return False
    lower = path.lower()
    if host == "tubepussy.org":
        # Keep only actual video pages: root-level slugs and /shorts/<id>/.
        # Ignore locale mirrors, listings, profiles, auth/community pages, etc.
        if re.match(r"^/[a-z]{2}/", lower):
            return False
        segments = [segment for segment in lower.split("/") if segment]
        if len(segments) == 2 and segments[0] == "shorts" and segments[1].isdigit():
            pass
        elif len(segments) == 1:
            if segments[0] in {
                "latest-updates",
                "top-rated",
                "most-popular",
                "shorts",
                "community",
                "login",
                "login-required",
                "register",
                "profile",
                "profiles",
                "models",
                "categories",
                "tags",
                "search",
                "about",
                "contact",
                "dmca",
                "privacy",
                "terms",
            }:
                return False
        else:
            return False
    if host == "xvideosputaria.com":
        segments = [segment for segment in lower.split("/") if segment]
        if len(segments) != 1:
            return False
        if segments[0] in {
            "porno-novo-hdd",
            "mais-populares",
            "login",
            "contato",
            "dmca",
            "privacy-policy",
            "politica-de-privacidade",
        }:
            return False
    if lower.startswith(_SKIP_PREFIXES):
        return False
    if any(lower.endswith(ext) for ext in (
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg",
        ".mp4", ".m3u8", ".mpd", ".ts", ".css", ".js", ".xml", ".json",
    )):
        return False
    return True


async def _discover_related(
    seed: str,
    limit: int | None = 30,
    *,
    max_listing_pages: int | None = None,
) -> tuple[list[str], dict | None]:
    """Discover post URLs from a listing and follow its pagination.

    limit=None means crawl the complete reachable pagination. Existing callers
    keep the old bounded behavior by passing an integer limit.
    """
    from playwright.async_api import async_playwright

    host = urlsplit(seed).netloc.lower().removeprefix("www.")
    seed_path = urlsplit(seed).path.rstrip("/")
    launch_args: list[str] = []
    if settings.browser_disable_gpu:
        launch_args.extend([
            "--disable-gpu",
            "--disable-gpu-compositing",
            "--disable-accelerated-2d-canvas",
            "--disable-accelerated-video-decode",
            "--disable-accelerated-video-encode",
        ])

    candidates: list[str] = []
    seen_posts: set[str] = set()
    visited_listings: set[str] = set()
    listing_queue: list[str] = [seed]

    def _listing_key(value: str) -> str:
        parsed = urlsplit(value)
        path = parsed.path or "/"
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))

    def _under_seed(path: str) -> bool:
        if not seed_path:
            return True
        return path == seed_path or path.startswith(seed_path + "/")

    def _is_pagination_link(anchor: dict) -> bool:
        value = str(anchor.get("href") or "")
        parsed = urlsplit(value)
        if parsed.netloc.lower().removeprefix("www.") != host:
            return False

        path = (parsed.path or "/").rstrip("/")
        query = parsed.query.lower()
        label = str(anchor.get("text") or "").strip().lower()
        rel = str(anchor.get("rel") or "").lower()
        classes = (
            str(anchor.get("className") or "") + " "
            + str(anchor.get("parentClass") or "")
        ).lower()

        if "next" in rel:
            return True
        if re.search(r"/page/\d+$", path, re.I):
            return _under_seed(path) or not seed_path
        if re.search(r"/\d+$", path):
            if not seed_path:
                return True
            if path.startswith(seed_path + "/"):
                return True
            # TubePussy-style listing pagination can live below a named
            # listing route even when discovery starts at the home page.
            if any(
                token in path.lower()
                for token in ("/latest-updates/", "/most-popular/", "/top-rated/")
            ):
                return True
        if any(k in query for k in ("page=", "paged=", "p=", "start=", "offset=")):
            return _under_seed(path) or not seed_path
        if any(token in classes for token in ("pagination", "page-numbers", "pager", "paginator")):
            if label.isdigit() or label in {"próximo", "proximo", "next", "›", "»", "→"}:
                return True
        return label in {"próximo", "proximo", "next", "›", "»", "→"} and (
            _under_seed(path) or not seed_path
        )

    async with browser_slot("platform_batch_1"), async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=launch_args)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
            viewport={"width": 1365, "height": 900},
        )
        page = await context.new_page()

        async def keep_listing_navigation(route):
            request = route.request
            if request.is_navigation_request() and request.frame == page.main_frame:
                target_host = urlsplit(request.url).netloc.lower().removeprefix("www.")
                if target_host and target_host != host:
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", keep_listing_navigation)

        pages_scanned = 0
        if max_listing_pages is None:
            if limit is None:
                page_cap = 5000
            else:
                page_cap = max(1, (int(limit) + 19) // 20 + 2)
        else:
            page_cap = max(1, int(max_listing_pages))

        while listing_queue and pages_scanned < page_cap:
            if limit is not None and len(candidates) >= limit:
                break

            listing_url = listing_queue.pop(0)
            listing_key = _listing_key(listing_url)
            if listing_key in visited_listings:
                continue
            visited_listings.add(listing_key)
            pages_scanned += 1

            try:
                await page.goto(
                    listing_url,
                    wait_until="domcontentloaded",
                    timeout=22_000,
                )
                await page.wait_for_timeout(450 if host == "tubepussy.org" else 1_250)

                anchors = None
                last_anchor_error = None
                for _ in range(4):
                    try:
                        anchors = await page.eval_on_selector_all(
                            "a[href]",
                            """(els) => els.map(a => ({
                                href: a.href,
                                text: (a.textContent || '').trim(),
                                rel: a.rel || '',
                                className: a.className || '',
                                parentClass: (a.parentElement && a.parentElement.className) || ''
                            })).filter(x => x.href)""",
                        )
                        break
                    except Exception as exc:
                        last_anchor_error = exc
                        await page.wait_for_timeout(750)

                if anchors is None:
                    raise last_anchor_error or RuntimeError("não consegui ler os links")
            except Exception as exc:
                print(
                    f"IRIS_DISCOVERY_PAGE_ERROR host={host} page={listing_url} "
                    f"current={page.url} error={type(exc).__name__}:{str(exc)[:180]}",
                    flush=True,
                )
                continue

            queued_keys = {_listing_key(x) for x in listing_queue}
            for anchor in anchors:
                href = str(anchor.get("href") or "")
                value = _canonical(href)
                is_post = _looks_like_post(value, host)

                if is_post:
                    if value not in seen_posts:
                        seen_posts.add(value)
                        candidates.append(value)
                        if limit is not None and len(candidates) >= limit:
                            break
                    # A post URL (e.g. /shorts/27864/) must never be treated
                    # as numeric pagination just because it ends in digits.
                    continue

                if _is_pagination_link(anchor):
                    nav_key = _listing_key(href)
                    if (
                        nav_key not in visited_listings
                        and nav_key not in queued_keys
                        and len(visited_listings) + len(listing_queue) < page_cap
                    ):
                        listing_queue.append(href)
                        queued_keys.add(nav_key)

            if pages_scanned % 25 == 0:
                print(
                    f"IRIS_DISCOVERY_PROGRESS host={host} scanned={pages_scanned} "
                    f"queued={len(listing_queue)} candidates={len(candidates)}",
                    flush=True,
                )

        storage_state = await context.storage_state()
        await context.close()
        await browser.close()

    print(
        f"IRIS_DISCOVERY_PAGES host={host} scanned={len(visited_listings)} "
        f"candidates={len(candidates)} exhausted={not bool(listing_queue)}",
        flush=True,
    )
    return (candidates if limit is None else candidates[:limit]), storage_state


async def _editorial_from_result(result, selected) -> dict | None:
    meta = selected.metadata.get("editorial")
    if meta:
        return dict(meta)
    for resource in result.resources:
        meta = resource.metadata.get("editorial")
        if meta:
            return dict(meta)
    return None


def _metadata_is_useful(meta: dict | None) -> bool:
    if not meta:
        return False
    return bool(
        meta.get("person")
        or meta.get("categories")
        or meta.get("tags")
    )


async def _editorial_from_browser(page_url: str) -> dict | None:
    """Fallback for pages whose direct HTML is blocked or incomplete."""
    from playwright.async_api import async_playwright

    launch_args: list[str] = []
    if settings.browser_disable_gpu:
        launch_args.extend([
            "--disable-gpu",
            "--disable-gpu-compositing",
            "--disable-accelerated-2d-canvas",
            "--disable-accelerated-video-decode",
            "--disable-accelerated-video-encode",
        ])

    async with browser_slot("platform_batch_2"), async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=launch_args)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
            viewport={"width": 1365, "height": 900},
        )
        page = await context.new_page()
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=20_000)
            await page.wait_for_timeout(1_500)
            html_text = await page.content()
            meta = extract_editorial_metadata(html_text, page.url).as_dict()
            return meta if _metadata_is_useful(meta) else None
        finally:
            await context.close()
            await browser.close()


def _file_fingerprint(path: Path) -> str:
    """Stable enough to reject the same video served from another signed URL."""
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as fh:
        digest.update(fh.read(1024 * 1024))
        if size > 1024 * 1024:
            fh.seek(max(0, size - 1024 * 1024))
            digest.update(fh.read(1024 * 1024))
    return digest.hexdigest()


async def run_platform_batch(
    bot,
    *,
    platforms: set[str] | None = None,
    candidate_overrides: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    if not settings.admin_id:
        raise RuntimeError("IRIS_ADMIN_ID não configurado")

    analyzer = Analyzer()
    delivery = DeliveryManager()
    report: dict[str, object] = {}
    history = _load_history()

    tube_correction = settings.platform_batch_nonce == "editorial-six-v3-correction"
    xvp_correction = settings.platform_batch_nonce == "editorial-xvp-v4-correction"

    for platform, seed in _SEEDS.items():
        if platforms is not None and platform not in platforms:
            continue
        discovered, discovery_state = await _discover_related(seed, limit=60)
        override = (candidate_overrides or {}).get(platform)
        candidates = [_canonical(url) for url in override] if override else discovered
        print(
            "IRIS_PLATFORM_BATCH_START "
            + json.dumps(
                {
                    "platform": platform,
                    "candidates": candidates[:10],
                    "override": bool(override),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        sent = 0
        attempted = 0
        failures: list[str] = []
        rows: list[dict] = []
        platform_history = history.setdefault(platform, {"media": [], "pages": []})
        seen_media: set[str] = set(_HISTORICAL_MEDIA_KEYS.get(platform, set()))
        seen_media.update(str(x) for x in platform_history.get("media", []))
        seen_pages: set[str] = set(_HISTORICAL_PAGES.get(platform, set()))
        seen_pages.update(str(x) for x in platform_history.get("pages", []))
        seen_fingerprints: set[str] = set()

        if xvp_correction:
            target = 0 if platform == "tubepussy" else 2
            skip_seed = platform == "xvideosputaria"
            if platform == "xvideosputaria":
                seen_media.add(
                    "vazounudes.net/hls/db4bbebc-710b-445d-8b4b-2ae2cc081d82/480p/video.m3u8"
                )
        else:
            target = 2 if tube_correction and platform == "tubepussy" else 3
            skip_seed = tube_correction and platform == "tubepussy"

        for page_url in candidates:
            if sent >= target:
                break
            canonical_page = _canonical(page_url)
            if canonical_page in seen_pages:
                continue
            if skip_seed and canonical_page == _canonical(seed):
                continue
            attempted += 1
            print(
                "IRIS_PLATFORM_BATCH_ATTEMPT "
                + json.dumps(
                    {"platform": platform, "page": page_url, "attempt": attempted},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            path: Path | None = None
            try:
                if platform == "xvideosputaria" and discovery_state:
                    browser_resources = await probe_browser(
                        page_url,
                        max_requests=max(settings.max_browser_requests, 2200),
                        timeout_ms=22_000,
                        interaction_rounds=12,
                        disable_gpu=settings.browser_disable_gpu,
                        storage_state=discovery_state,
                    )
                    # Follow player frames inside the same authenticated browser
                    # state when the outer page did not expose media directly.
                    primary = [
                        item for item in browser_resources
                        if item.type.value in {"video", "playlist", "stream"}
                        and not item.metadata.get("hls_segment")
                        and not item.metadata.get("navigation_only")
                    ]
                    if not primary:
                        frames = [
                            item for item in browser_resources
                            if item.source == "browser:frame"
                            and item.metadata.get("navigation_only")
                        ]
                        for frame in frames[:4]:
                            nested = await probe_browser(
                                frame.url,
                                max_requests=min(settings.max_browser_requests, 1800),
                                timeout_ms=18_000,
                                interaction_rounds=8,
                                disable_gpu=settings.browser_disable_gpu,
                                storage_state=discovery_state,
                            )
                            for item in nested:
                                item.metadata.setdefault("embed_parent", page_url)
                            browser_resources.extend(nested)

                    selected, path, generated, info, rejected = await download_first_valid_video(
                        browser_resources
                    )
                    class _BrowserResult:
                        resources = browser_resources
                        title = selected.metadata.get("page_title") or selected.title
                    result = _BrowserResult()
                else:
                    result = await analyzer.analyze(page_url, deep=True)
                    selected, path, generated, info, rejected = await download_first_valid_video(
                        result.resources
                    )

                media_key = _media_key(selected.url)
                fingerprint = _file_fingerprint(path)
                if media_key in seen_media or fingerprint in seen_fingerprints:
                    failures.append(f"{page_url}: mídia duplicada {media_key}")
                    path.unlink(missing_ok=True)
                    path = None
                    continue
                seen_media.add(media_key)
                seen_fingerprints.add(fingerprint)

                meta = await _editorial_from_result(result, selected)
                if not _metadata_is_useful(meta):
                    try:
                        meta = await _editorial_from_browser(page_url)
                    except Exception as exc:
                        failures.append(
                            f"{page_url}: metadados via navegador falharam: "
                            f"{type(exc).__name__}: {str(exc)[:160]}"
                        )

                title = (
                    selected.title
                    or selected.metadata.get("page_title")
                    or result.title
                    or Path(path).stem
                )
                caption = format_video_caption(title, meta)

                receipt = await delivery.send_path_to_chat(
                    bot,
                    settings.admin_id,
                    path,
                    caption=caption,
                    as_video=True,
                    respect_channel_only=False,
                )
                sent += 1
                seen_pages.add(canonical_page)
                platform_history["media"] = sorted(seen_media)
                platform_history["pages"] = sorted(seen_pages)
                _save_history(history)

                row = {
                    "platform": platform,
                    "page": page_url,
                    "selected": selected.url,
                    "title": title,
                    "bytes": path.stat().st_size,
                    "duration": info.duration,
                    "width": info.width,
                    "height": info.height,
                    "codec": info.codec,
                    "audio": info.audio_codec,
                    "caption": caption,
                    "receipt": receipt,
                    "rejected": rejected,
                }
                rows.append(row)
                print(
                    "IRIS_PLATFORM_BATCH_SENT "
                    + json.dumps(row, ensure_ascii=False),
                    flush=True,
                )
                await asyncio.sleep(1.5)
            except Exception as exc:
                failure = f"{page_url}: {type(exc).__name__}: {str(exc)[:240]}"
                failures.append(failure)
                print(
                    "IRIS_PLATFORM_BATCH_FAIL "
                    + json.dumps(
                        {
                            "platform": platform,
                            "page": page_url,
                            "error": failure,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            finally:
                if path and path.exists():
                    path.unlink(missing_ok=True)

        report[platform] = {
            "sent": sent,
            "target": target,
            "attempted": attempted,
            "discovered": len(candidates),
            "rows": rows,
            "failures": failures[-12:],
        }

    print("IRIS_PLATFORM_BATCH_DONE " + json.dumps(report, ensure_ascii=False), flush=True)
    return report
