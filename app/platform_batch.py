from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.analyzer import Analyzer
from app.delivery import DeliveryManager
from app.editorial import extract_editorial_metadata, hashtag
from app.settings import settings
from app.video_candidates import download_first_valid_video

_SEEDS = {
    "tubepussy": "https://tubepussy.org/ruiva-isabel-dando-a-bucetinha-e-levando-na-cara/#forward",
    "xvideosputaria": "https://xvideosputaria.com/anao-gabriela-gadotti-mini-gabys-boquetando-com-leite-na-boca/#forward",
}

_FIXED_TAGS = [
    "#Pornô_Longo",
    "#Famosas",
    "#Lésbicas",
    "#Boquetes",
    "#Anal",
    "#Gostosas",
    "#Novinhas",
    "#Coroas",
    "#Bucetas",
    "#Peitudas",
    "#Mini_Gabys",
    "#Bundas",
    "#Anã",
    "#Chupando_Buceta",
    "#Gozada_Na_Cara",
    "#Mamando_Rola",
    "#Pack",
    "#Peitos_Naturais",
]

_FALLBACK_PERSON = {
    "tubepussy": "Ruiva Isabell",
    "xvideosputaria": "Mini Gabys",
}


def _fixed_test_caption(platform: str, meta: dict | None) -> str:
    person = str((meta or {}).get("person") or _FALLBACK_PERSON.get(platform) or "Vídeo")
    person_tag = hashtag(person) or "#Vídeo"
    body = "🔎 Tags: " + " / ".join(_FIXED_TAGS)
    return (
        f"<b>🚫 {html.escape(person_tag)}</b>\n\n"
        f"<blockquote expandable>{html.escape(body)}</blockquote>"
    )

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
    if host == "tubepussy.org" and re.match(r"^/[a-z]{2}/", lower):
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


async def _discover_related(seed: str, limit: int = 30) -> list[str]:
    from playwright.async_api import async_playwright

    host = urlsplit(seed).netloc.lower().removeprefix("www.")
    launch_args: list[str] = []
    if settings.browser_disable_gpu:
        launch_args.extend([
            "--disable-gpu",
            "--disable-gpu-compositing",
            "--disable-accelerated-2d-canvas",
            "--disable-accelerated-video-decode",
            "--disable-accelerated-video-encode",
        ])

    candidates: list[str] = [_canonical(seed)]
    seen = set(candidates)

    async with async_playwright() as p:
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

        async def collect(url: str) -> None:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=18_000)
                await page.wait_for_timeout(2_000)
                hrefs = await page.eval_on_selector_all(
                    "a[href]",
                    "(els) => els.map(a => a.href).filter(Boolean)",
                )
                for href in hrefs:
                    value = _canonical(str(href))
                    if value in seen or not _looks_like_post(value, host):
                        continue
                    seen.add(value)
                    candidates.append(value)
                    if len(candidates) >= limit:
                        break
            except Exception:
                return

        await collect(seed)
        if len(candidates) < 8:
            await collect(f"https://{host}/")

        await context.close()
        await browser.close()

    return candidates[:limit]


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

    async with async_playwright() as p:
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


async def run_platform_batch(bot) -> dict[str, object]:
    if not settings.admin_id:
        raise RuntimeError("IRIS_ADMIN_ID não configurado")

    analyzer = Analyzer()
    delivery = DeliveryManager()
    report: dict[str, object] = {}

    tube_correction = settings.platform_batch_nonce == "editorial-six-v3-correction"
    xvp_correction = settings.platform_batch_nonce == "editorial-xvp-v4-correction"

    for platform, seed in _SEEDS.items():
        candidates = await _discover_related(seed, limit=60)
        sent = 0
        attempted = 0
        failures: list[str] = []
        rows: list[dict] = []
        seen_media: set[str] = set()
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
            if skip_seed and _canonical(page_url) == _canonical(seed):
                continue
            attempted += 1
            path: Path | None = None
            try:
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
                caption = _fixed_test_caption(platform, meta)

                receipt = await delivery.send_path_to_chat(
                    bot,
                    settings.admin_id,
                    path,
                    caption=caption,
                    as_video=True,
                    respect_channel_only=False,
                )
                sent += 1
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
                failures.append(
                    f"{page_url}: {type(exc).__name__}: {str(exc)[:240]}"
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
