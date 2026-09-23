from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.analyzer import Analyzer
from app.delivery import DeliveryManager
from app.editorial import format_editorial_block, format_video_caption
from app.settings import settings
from app.video_candidates import download_first_valid_video

_SEEDS = {
    "tubepussy": "https://tubepussy.org/ruiva-isabel-dando-a-bucetinha-e-levando-na-cara/#forward",
    "xvideosputaria": "https://xvideosputaria.com/anao-gabriela-gadotti-mini-gabys-boquetando-com-leite-na-boca/#forward",
}

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


def _looks_like_post(url: str, host: str) -> bool:
    parsed = urlsplit(url)
    if parsed.netloc.lower().removeprefix("www.") != host:
        return False
    path = parsed.path or "/"
    if path == "/":
        return False
    lower = path.lower()
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
    if meta and format_editorial_block(meta):
        return dict(meta)
    for resource in result.resources:
        meta = resource.metadata.get("editorial")
        if meta and format_editorial_block(meta):
            return dict(meta)
    return None


async def run_platform_batch(bot) -> dict[str, object]:
    if not settings.admin_id:
        raise RuntimeError("IRIS_ADMIN_ID não configurado")

    analyzer = Analyzer()
    delivery = DeliveryManager()
    report: dict[str, object] = {}

    for platform, seed in _SEEDS.items():
        candidates = await _discover_related(seed, limit=35)
        sent = 0
        attempted = 0
        failures: list[str] = []
        rows: list[dict] = []

        for page_url in candidates:
            if sent >= 3:
                break
            attempted += 1
            path: Path | None = None
            try:
                result = await analyzer.analyze(page_url, deep=True)
                selected, path, generated, info, rejected = await download_first_valid_video(
                    result.resources
                )
                meta = await _editorial_from_result(result, selected)
                if not meta:
                    failures.append(f"{page_url}: sem pessoa/tags editoriais")
                    path.unlink(missing_ok=True)
                    path = None
                    continue

                title = (
                    selected.title
                    or selected.metadata.get("page_title")
                    or result.title
                    or Path(path).stem
                )
                caption = format_video_caption(str(title), meta)
                if not caption.startswith("<b>🚫 ") or "<blockquote expandable>" not in caption:
                    failures.append(f"{page_url}: legenda editorial incompleta")
                    path.unlink(missing_ok=True)
                    path = None
                    continue

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
            "attempted": attempted,
            "discovered": len(candidates),
            "rows": rows,
            "failures": failures[-12:],
        }

    print("IRIS_PLATFORM_BATCH_DONE " + json.dumps(report, ensure_ascii=False), flush=True)
    return report
