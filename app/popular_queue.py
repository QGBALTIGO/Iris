from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from app.analyzer import Analyzer
from app.browser_guard import browser_slot
from app.editorial import extract_editorial_metadata, format_video_caption
from app.extractors.browser import probe_browser
from app.platform_batch import (
    _canonical,
    _discover_related,
    _editorial_from_browser,
    _editorial_from_result,
    _file_fingerprint,
    _media_key,
    _metadata_is_useful,
    _looks_like_post,
)
from app.settings import settings
from app.userbot import userbot
from app.video_candidates import download_first_valid_video


SOURCES = (
    ("tubepussy", "https://tubepussy.org/latest-updates/"),
    ("xvideosputaria", "https://xvideosputaria.com/porno-novo-hdd/"),
)
_SOURCE_URL = dict(SOURCES)
_SOURCE_FALLBACKS = {
    "tubepussy": (
        "https://tubepussy.org/",
        "https://tubepussy.org/most-popular/",
    ),
    "xvideosputaria": (
        "https://xvideosputaria.com/",
        "https://xvideosputaria.com/mais-populares/#forward",
    ),
}
CATALOG_DISCOVERY_VERSION = "full-catalog-v4"


@dataclass(slots=True)
class PopularItem:
    id: int
    source: str
    page_url: str
    title: str | None
    rank: int
    attempts: int


class PopularQueueManager:
    """Persistent round-robin queue for the complete paginated catalogs."""

    def __init__(self, db_path: Path | None = None):
        base = Path("/data") if Path("/data").exists() else settings.downloads_dir
        self.db_path = db_path or (base / "iris_popular_queue.sqlite3")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.analyzer = Analyzer()
        self.task: asyncio.Task | None = None
        self.discovery_task: asyncio.Task | None = None
        self._browser_states: dict[str, dict] = {}
        self._discover_lock = asyncio.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        return db

    def _ensure_schema(self) -> None:
        now = time.time()
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS popular_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    page_url TEXT NOT NULL UNIQUE,
                    title TEXT,
                    rank INTEGER NOT NULL DEFAULT 999999,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    media_key TEXT,
                    fingerprint TEXT,
                    sent_message_id INTEGER,
                    last_error TEXT,
                    discovered_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    sent_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_popular_source_status_rank
                    ON popular_items(source, status, rank, id);
                CREATE INDEX IF NOT EXISTS idx_popular_media_key
                    ON popular_items(media_key);
                CREATE INDEX IF NOT EXISTS idx_popular_fingerprint
                    ON popular_items(fingerprint);

                CREATE TABLE IF NOT EXISTS popular_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    next_source TEXT NOT NULL DEFAULT 'tubepussy',
                    running INTEGER NOT NULL DEFAULT 0,
                    last_discovery_at REAL,
                    discovery_version TEXT,
                    updated_at REAL NOT NULL
                );
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(popular_state)").fetchall()
            }
            if "discovery_version" not in columns:
                db.execute("ALTER TABLE popular_state ADD COLUMN discovery_version TEXT")

            db.execute(
                """
                INSERT OR IGNORE INTO popular_state(
                    id, next_source, running, updated_at
                ) VALUES(1, 'tubepussy', 0, ?)
                """,
                (now,),
            )
            db.execute(
                """
                UPDATE popular_items
                SET status='pending',
                    last_error='Retomado após reinício',
                    updated_at=?
                WHERE status='processing'
                """,
                (now,),
            )
            db.commit()

    @staticmethod
    def _other_source(source: str) -> str:
        return "xvideosputaria" if source == "tubepussy" else "tubepussy"

    def _state(self) -> sqlite3.Row:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM popular_state WHERE id=1"
            ).fetchone()

    def _set_running(self, running: bool) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE popular_state SET running=?, updated_at=? WHERE id=1",
                (int(running), time.time()),
            )
            db.commit()

    def _set_next_source(self, source: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE popular_state SET next_source=?, updated_at=? WHERE id=1",
                (source, time.time()),
            )
            db.commit()

    def status(self) -> dict[str, object]:
        with self._connect() as db:
            state = db.execute(
                "SELECT * FROM popular_state WHERE id=1"
            ).fetchone()
            rows = db.execute(
                """
                SELECT source, status, COUNT(*) AS n
                FROM popular_items
                GROUP BY source, status
                """
            ).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(str(row["source"]), {})[str(row["status"])] = int(row["n"])
        return {
            "running": bool(state["running"]) if state else False,
            "next_source": str(state["next_source"]) if state else "tubepussy",
            "last_discovery_at": state["last_discovery_at"] if state else None,
            "discovery_version": state["discovery_version"] if state else None,
            "counts": counts,
        }

    def _persist_candidates(self, source: str, candidates: list[str]) -> dict[str, int]:
        listing = _canonical(_SOURCE_URL[source])
        known_listing_pages = {
            listing,
            "https://tubepussy.org/most-popular/",
            "https://tubepussy.org/latest-updates/",
            "https://tubepussy.org/top-rated/",
            "https://xvideosputaria.com/mais-populares/",
            "https://xvideosputaria.com/porno-novo-hdd/",
        }
        clean: list[str] = []
        seen: set[str] = set()
        for value in candidates:
            url = _canonical(value)
            if not url or url in known_listing_pages or url in seen:
                continue
            seen.add(url)
            clean.append(url)

        now = time.time()
        added = 0
        with self._connect() as db:
            for rank, page_url in enumerate(clean, start=1):
                cur = db.execute(
                    """
                    INSERT OR IGNORE INTO popular_items(
                        source, page_url, rank, status, attempts,
                        discovered_at, updated_at
                    ) VALUES(?, ?, ?, 'pending', 0, ?, ?)
                    """,
                    (source, page_url, rank, now, now),
                )
                if cur.rowcount:
                    added += 1
                else:
                    db.execute(
                        """
                        UPDATE popular_items
                        SET rank=?, updated_at=?
                        WHERE page_url=?
                        """,
                        (rank, now, page_url),
                    )
            db.execute(
                """
                UPDATE popular_state
                SET last_discovery_at=?, updated_at=?
                WHERE id=1
                """,
                (now, now),
            )
            db.commit()
        return {"found": len(clean), "added": added}

    async def discover_source(self, source: str) -> dict[str, int]:
        if source not in _SOURCE_URL:
            raise ValueError(f"Fonte desconhecida: {source}")

        seeds = (_SOURCE_URL[source],) + tuple(_SOURCE_FALLBACKS.get(source, ()))
        best_candidates: list[str] = []
        best_state: dict | None = None
        best_seed = seeds[0]
        last_error: Exception | None = None

        for seed in seeds:
            try:
                candidates, storage_state = await _discover_related(
                    seed,
                    limit=None,
                    max_listing_pages=max(50, int(settings.popular_queue_max_pages)),
                )
            except Exception as exc:
                last_error = exc
                print(
                    f"IRIS_CATALOG_SEED_ERROR source={source} seed={seed} "
                    f"error={type(exc).__name__}:{str(exc)[:220]}",
                    flush=True,
                )
                continue

            if len(candidates) > len(best_candidates):
                best_candidates = candidates
                best_state = storage_state
                best_seed = seed

            # A healthy first page has many cards. If the home produced enough
            # candidates, keep crawling that canonical route rather than doing
            # the fallback listing too.
            if len(candidates) >= 20:
                break

        if not best_candidates and last_error is not None:
            raise last_error

        if best_state:
            self._browser_states[source] = best_state
        result = self._persist_candidates(source, best_candidates)
        print(
            "IRIS_CATALOG_DISCOVER "
            + json.dumps(
                {
                    "source": source,
                    "seed": best_seed,
                    "max_pages": max(50, int(settings.popular_queue_max_pages)),
                    **result,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return result

    async def quick_discover_source(self, source: str) -> dict[str, int]:
        """Populate enough recent pages to start sending without a full-catalog stall."""
        if source not in _SOURCE_URL:
            raise ValueError(f"Fonte desconhecida: {source}")

        seeds = (_SOURCE_URL[source],) + tuple(_SOURCE_FALLBACKS.get(source, ()))
        best_candidates: list[str] = []
        best_state: dict | None = None
        best_seed = seeds[0]
        last_error: Exception | None = None

        for seed in seeds:
            try:
                candidates, storage_state = await _discover_related(
                    seed,
                    limit=600,
                    max_listing_pages=30,
                )
            except Exception as exc:
                last_error = exc
                print(
                    f"IRIS_POPULAR_QUICK_SEED_ERROR source={source} seed={seed} "
                    f"error={type(exc).__name__}:{str(exc)[:220]}",
                    flush=True,
                )
                continue

            if len(candidates) > len(best_candidates):
                best_candidates = candidates
                best_state = storage_state
                best_seed = seed
            if len(candidates) >= 100:
                break

        if not best_candidates and last_error is not None:
            raise last_error

        if best_state:
            self._browser_states[source] = best_state

        result = self._persist_candidates(source, best_candidates)
        print(
            "IRIS_POPULAR_QUICK_DISCOVER "
            + json.dumps(
                {
                    "source": source,
                    "seed": best_seed,
                    "max_pages": 30,
                    **result,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return result

    async def quick_discover(self) -> dict[str, dict[str, int]]:
        rows = await asyncio.gather(
            *(self.quick_discover_source(source) for source, _ in SOURCES),
            return_exceptions=True,
        )
        report: dict[str, dict[str, int]] = {}
        for (source, _), row in zip(SOURCES, rows):
            if isinstance(row, Exception):
                print(
                    f"IRIS_POPULAR_QUICK_ERROR source={source} "
                    f"error={type(row).__name__}:{str(row)[:240]}",
                    flush=True,
                )
                report[source] = {"found": 0, "added": 0}
            else:
                report[source] = row
        return report

    def _discovery_needed(self, *, force: bool = False) -> bool:
        state = self._state()
        last = float(state["last_discovery_at"] or 0) if state else 0.0
        age = time.time() - last
        refresh = max(300, int(settings.popular_queue_refresh_seconds))
        version = str(state["discovery_version"] or "") if state else ""
        migrated = version == CATALOG_DISCOVERY_VERSION
        return bool(
            force
            or not migrated
            or self._pending_count() == 0
            or age >= refresh
        )

    def _schedule_full_discovery(self, *, force: bool = False) -> None:
        if self.discovery_task and not self.discovery_task.done():
            return
        if not self._discovery_needed(force=force):
            return

        async def run_background():
            try:
                report = await self.discover()
                print(
                    "IRIS_POPULAR_BACKGROUND_DISCOVER "
                    + json.dumps(
                        {
                            "report": report,
                            "pending": self._pending_count(),
                            "status": self.status(),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"IRIS_POPULAR_BACKGROUND_ERROR "
                    f"{type(exc).__name__}:{str(exc)[:300]}",
                    flush=True,
                )
            finally:
                self.discovery_task = None

        self.discovery_task = asyncio.create_task(
            run_background(),
            name="iris-popular-full-discovery",
        )

    async def discover(self) -> dict[str, dict[str, int]]:
        async with self._discover_lock:
            report: dict[str, dict[str, int]] = {}

            async def run_source(source: str):
                try:
                    return source, await self.discover_source(source)
                except Exception as exc:
                    print(
                        f"IRIS_CATALOG_DISCOVER_ERROR source={source} "
                        f"error={type(exc).__name__}:{str(exc)[:240]}",
                        flush=True,
                    )
                    return source, {"found": 0, "added": 0}

            rows = await asyncio.gather(
                *(run_source(source) for source, _ in SOURCES)
            )
            report.update(rows)

            with self._connect() as db:
                db.execute(
                    """
                    UPDATE popular_state
                    SET discovery_version=?, updated_at=?
                    WHERE id=1
                    """,
                    (CATALOG_DISCOVERY_VERSION, time.time()),
                )
                db.commit()
            return report

    def _next_item(self, source: str) -> PopularItem | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT id, source, page_url, title, rank, attempts
                FROM popular_items
                WHERE source=? AND status='pending'
                ORDER BY rank ASC, id ASC
                LIMIT 1
                """,
                (source,),
            ).fetchone()
            if not row:
                return None
            db.execute(
                """
                UPDATE popular_items
                SET status='processing',
                    attempts=attempts+1,
                    updated_at=?
                WHERE id=?
                """,
                (time.time(), int(row["id"])),
            )
            db.commit()
        return PopularItem(
            id=int(row["id"]),
            source=str(row["source"]),
            page_url=str(row["page_url"]),
            title=row["title"],
            rank=int(row["rank"]),
            attempts=int(row["attempts"]) + 1,
        )

    def _mark_retry_or_failed(self, item: PopularItem, exc: Exception) -> str:
        max_attempts = max(1, int(settings.queue_max_attempts))
        status = "pending" if item.attempts < max_attempts else "failed"
        with self._connect() as db:
            db.execute(
                """
                UPDATE popular_items
                SET status=?,
                    last_error=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    f"{type(exc).__name__}: {str(exc)[:450]}",
                    time.time(),
                    item.id,
                ),
            )
            db.commit()
        return status

    def _mark_duplicate(
        self,
        item: PopularItem,
        *,
        media_key: str,
        fingerprint: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                UPDATE popular_items
                SET status='duplicate',
                    media_key=?,
                    fingerprint=?,
                    last_error='Mídia já enviada anteriormente',
                    updated_at=?
                WHERE id=?
                """,
                (media_key, fingerprint, time.time(), item.id),
            )
            db.commit()

    def _already_sent(self, media_key: str, fingerprint: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT 1
                FROM popular_items
                WHERE status='sent'
                  AND (
                    (media_key IS NOT NULL AND media_key=?)
                    OR
                    (fingerprint IS NOT NULL AND fingerprint=?)
                  )
                LIMIT 1
                """,
                (media_key, fingerprint),
            ).fetchone()
        return bool(row)

    def _mark_sent(
        self,
        item: PopularItem,
        *,
        title: str,
        media_key: str,
        fingerprint: str,
        message_id: int | None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                UPDATE popular_items
                SET status='sent',
                    title=?,
                    media_key=?,
                    fingerprint=?,
                    sent_message_id=?,
                    last_error=NULL,
                    sent_at=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    title,
                    media_key,
                    fingerprint,
                    message_id,
                    time.time(),
                    time.time(),
                    item.id,
                ),
            )
            db.commit()

    async def _editorial_with_state(
        self,
        page_url: str,
        storage_state: dict | None,
    ) -> dict | None:
        if not storage_state:
            return await _editorial_from_browser(page_url)

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

        async with browser_slot("popular_editorial"), async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=launch_args)
            context = await browser.new_context(
                storage_state=storage_state,
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
                await page.goto(
                    page_url,
                    wait_until="domcontentloaded",
                    timeout=22_000,
                )
                await page.wait_for_timeout(1_500)
                meta = extract_editorial_metadata(
                    await page.content(),
                    page.url,
                ).as_dict()
                return meta if _metadata_is_useful(meta) else None
            finally:
                await context.close()
                await browser.close()

    async def _download_item(self, item: PopularItem):
        source = item.source
        page_url = item.page_url
        storage_state = self._browser_states.get(source)

        if source == "xvideosputaria":
            if not storage_state:
                try:
                    _, storage_state = await _discover_related(
                        _SOURCE_URL[source],
                        limit=1,
                        max_listing_pages=1,
                    )
                    if storage_state:
                        self._browser_states[source] = storage_state
                    print(
                        "IRIS_POPULAR_SESSION_BOOTSTRAP "
                        + json.dumps(
                            {
                                "source": source,
                                "ready": bool(storage_state),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        f"IRIS_POPULAR_SESSION_BOOTSTRAP_ERROR source={source} "
                        f"error={type(exc).__name__}:{str(exc)[:220]}",
                        flush=True,
                    )
                    storage_state = None

            browser_resources = await probe_browser(
                page_url,
                max_requests=max(settings.max_browser_requests, 2200),
                timeout_ms=22_000,
                interaction_rounds=12,
                disable_gpu=settings.browser_disable_gpu,
                storage_state=storage_state,
            )

            primary = [
                r for r in browser_resources
                if r.type.value in {"video", "playlist", "stream"}
                and not r.metadata.get("hls_segment")
                and not r.metadata.get("navigation_only")
            ]
            if not primary:
                frames = [
                    r for r in browser_resources
                    if r.source == "browser:frame"
                    and r.metadata.get("navigation_only")
                ]
                for frame in frames[:4]:
                    nested = await probe_browser(
                        frame.url,
                        max_requests=min(settings.max_browser_requests, 1800),
                        timeout_ms=18_000,
                        interaction_rounds=8,
                        disable_gpu=settings.browser_disable_gpu,
                        storage_state=storage_state,
                    )
                    for resource in nested:
                        resource.metadata.setdefault("embed_parent", page_url)
                    browser_resources.extend(nested)

            selected, path, generated, info, rejected = await download_first_valid_video(
                browser_resources
            )

            class _BrowserResult:
                resources = browser_resources
                title = selected.metadata.get("page_title") or selected.title

            result = _BrowserResult()
        else:
            result = await self.analyzer.analyze(page_url, deep=True)
            selected, path, generated, info, rejected = await download_first_valid_video(
                result.resources
            )

        meta = await _editorial_from_result(result, selected)
        if not _metadata_is_useful(meta):
            try:
                meta = await self._editorial_with_state(page_url, storage_state)
            except Exception as exc:
                print(
                    f"IRIS_POPULAR_EDITORIAL_ERROR source={source} "
                    f"url={page_url} error={type(exc).__name__}:{str(exc)[:180]}",
                    flush=True,
                )

        title = (
            selected.title
            or selected.metadata.get("page_title")
            or getattr(result, "title", None)
            or item.title
            or Path(path).stem
        )
        return selected, path, info, rejected, meta, str(title)

    async def _process_item(self, item: PopularItem) -> bool:
        path: Path | None = None
        try:
            selected, path, info, rejected, meta, title = await self._download_item(item)
            media_key = _media_key(selected.url)
            fingerprint = _file_fingerprint(path)
            if self._already_sent(media_key, fingerprint):
                self._mark_duplicate(
                    item,
                    media_key=media_key,
                    fingerprint=fingerprint,
                )
                print(
                    f"IRIS_POPULAR_DUPLICATE source={item.source} url={item.page_url}",
                    flush=True,
                )
                return False

            caption = format_video_caption(title, meta)
            sent = await userbot.send_to_delivery_channel(
                settings.popular_channel_invite,
                path,
                caption=caption,
                as_video=True,
                parse_mode="html",
            )
            message_id = getattr(sent, "id", None)
            self._mark_sent(
                item,
                title=title,
                media_key=media_key,
                fingerprint=fingerprint,
                message_id=int(message_id) if message_id is not None else None,
            )
            print(
                "IRIS_POPULAR_SENT "
                + json.dumps(
                    {
                        "source": item.source,
                        "page": item.page_url,
                        "title": title,
                        "message_id": message_id,
                        "bytes": path.stat().st_size,
                        "duration": info.duration,
                        "width": info.width,
                        "height": info.height,
                        "caption": caption,
                        "rejected": rejected[-3:],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return True
        finally:
            if path and path.exists():
                path.unlink(missing_ok=True)

    def prune_navigation_items(self) -> int:
        changed = 0
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, source, page_url, status
                FROM popular_items
                WHERE status IN ('pending','processing','failed')
                """
            ).fetchall()
            for row in rows:
                source = str(row["source"])
                host = (
                    "tubepussy.org"
                    if source == "tubepussy"
                    else "xvideosputaria.com"
                )
                page_url = str(row["page_url"])
                if _looks_like_post(page_url, host):
                    continue
                db.execute(
                    """
                    UPDATE popular_items
                    SET status='ignored',
                        last_error='Página de catálogo/navegação',
                        updated_at=?
                    WHERE id=?
                    """,
                    (time.time(), int(row["id"])),
                )
                changed += 1
            db.commit()
        if changed:
            print(
                f"IRIS_POPULAR_PRUNE_NAVIGATION changed={changed}",
                flush=True,
            )
        return changed

    def _pending_count(self, source: str | None = None) -> int:
        with self._connect() as db:
            if source:
                row = db.execute(
                    "SELECT COUNT(*) AS n FROM popular_items WHERE source=? AND status='pending'",
                    (source,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT COUNT(*) AS n FROM popular_items WHERE status='pending'"
                ).fetchone()
        return int(row["n"]) if row else 0

    async def start(self, bot=None) -> None:
        if not settings.popular_queue_enabled:
            return
        if not settings.popular_channel_invite:
            raise RuntimeError("IRIS_POPULAR_CHANNEL_INVITE não configurado")
        if not await userbot.is_authorized():
            raise RuntimeError("Conta 06 não autenticada para a fila popular")

        info = await userbot.delivery_target_info(settings.popular_channel_invite)
        if not info.get("can_post"):
            raise RuntimeError(
                "Conta 06 entrou no canal popular, mas não tem permissão para publicar"
            )

        self.prune_navigation_items()
        pending_before = self._pending_count()
        print(
            "IRIS_POPULAR_PRESTART "
            + json.dumps(
                {
                    "channel_id": info.get("id"),
                    "channel_title": info.get("title"),
                    "pending": pending_before,
                    "status": self.status(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        if pending_before == 0:
            try:
                quick = await self.quick_discover()
                print(
                    "IRIS_POPULAR_QUICK_READY "
                    + json.dumps(
                        {
                            "report": quick,
                            "pending": self._pending_count(),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"IRIS_POPULAR_QUICK_FATAL "
                    f"{type(exc).__name__}:{str(exc)[:300]}",
                    flush=True,
                )

        self._set_running(True)
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self._worker(), name="iris-popular-queue")

        self._schedule_full_discovery(force=self._pending_count() == 0)

        print(
            "IRIS_POPULAR_START "
            + json.dumps(
                {
                    "channel_id": info.get("id"),
                    "channel_title": info.get("title"),
                    "next_source": self.status().get("next_source"),
                    "pending": self._pending_count(),
                    "status": self.status(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    async def _worker(self) -> None:
        failures_on_turn = 0
        while settings.popular_queue_enabled:
            try:
                self._schedule_full_discovery()
                state = self._state()
                preferred = str(state["next_source"] or "tubepussy")
                item = self._next_item(preferred)

                if item is None:
                    other = self._other_source(preferred)
                    item = self._next_item(other)
                    if item is None:
                        self._schedule_full_discovery(force=True)
                        print(
                            "IRIS_POPULAR_IDLE "
                            + json.dumps(
                                {
                                    "status": self.status(),
                                    "full_discovery_running": bool(
                                        self.discovery_task
                                        and not self.discovery_task.done()
                                    ),
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                        await asyncio.sleep(min(
                            30,
                            max(10, int(settings.popular_queue_idle_seconds)),
                        ))
                        continue

                try:
                    print(
                        "IRIS_POPULAR_PROCESS "
                        + json.dumps(
                            {
                                "id": item.id,
                                "source": item.source,
                                "rank": item.rank,
                                "attempt": item.attempts,
                                "url": item.page_url,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    sent = await asyncio.wait_for(
                        self._process_item(item),
                        timeout=max(60.0, settings.queue_item_timeout_seconds),
                    )
                except asyncio.CancelledError:
                    with self._connect() as db:
                        db.execute(
                            """
                            UPDATE popular_items
                            SET status='pending',
                                last_error='Worker interrompido',
                                updated_at=?
                            WHERE id=?
                            """,
                            (time.time(), item.id),
                        )
                        db.commit()
                    raise
                except Exception as exc:
                    status = self._mark_retry_or_failed(item, exc)
                    failures_on_turn += 1
                    self._set_next_source(self._other_source(item.source))
                    print(
                        (
                            "IRIS_POPULAR_RETRY"
                            if status == "pending"
                            else "IRIS_POPULAR_FAILED"
                        )
                        + f" source={item.source} attempt={item.attempts}/"
                        + f"{max(1, int(settings.queue_max_attempts))} "
                        + f"url={item.page_url} error={type(exc).__name__}:{str(exc)[:240]}",
                        flush=True,
                    )
                    await asyncio.sleep(2.0)
                    continue

                if sent:
                    failures_on_turn = 0
                    self._set_next_source(self._other_source(item.source))
                await asyncio.sleep(max(1.0, float(settings.popular_queue_gap_seconds)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"IRIS_POPULAR_WORKER_ERROR {type(exc).__name__}: {str(exc)[:300]}",
                    flush=True,
                )
                await asyncio.sleep(15)

        self._set_running(False)

    async def close(self) -> None:
        self._set_running(False)
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.discovery_task and not self.discovery_task.done():
            self.discovery_task.cancel()
            try:
                await self.discovery_task
            except asyncio.CancelledError:
                pass


popular_queue = PopularQueueManager()
