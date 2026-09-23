from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from app.analyzer import Analyzer
from app.delivery import DeliveryManager
from app.downloads import DownloadEngine
from app.editorial import format_video_caption
from app.models import MediaResource, ResourceType
from app.settings import settings
from app.video_candidates import download_first_valid_video

_SITE = "https://pornocomlegenda.blog"
_VIDEO_TYPES = {ResourceType.VIDEO, ResourceType.PLAYLIST, ResourceType.STREAM}
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class QueueItem:
    id: int
    url: str
    title: str | None
    status: str
    attempts: int
    published_at: str | None
    last_error: str | None


class SiteQueueManager:
    def __init__(self, db_path: Path | None = None):
        base = Path("/data") if Path("/data").exists() else settings.downloads_dir
        self.db_path = db_path or (base / "iris_site_queue.sqlite3")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.analyzer = Analyzer()
        self.delivery = DeliveryManager()
        self.engine = DownloadEngine()
        self.task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS queue_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site TEXT NOT NULL,
                    post_id TEXT,
                    url TEXT NOT NULL UNIQUE,
                    title TEXT,
                    published_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    media_url TEXT,
                    media_source TEXT,
                    sent_message_id INTEGER,
                    channel_message_id INTEGER,
                    channel_sent_at REAL,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    sent_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_queue_status
                    ON queue_items(site, status, published_at DESC, id ASC);
                CREATE TABLE IF NOT EXISTS queue_state (
                    site TEXT PRIMARY KEY,
                    running INTEGER NOT NULL DEFAULT 0,
                    paused INTEGER NOT NULL DEFAULT 1,
                    target_chat_id INTEGER,
                    discovered_total INTEGER NOT NULL DEFAULT 0,
                    last_discovery_at REAL,
                    last_started_at REAL,
                    last_recovery_nonce TEXT,
                    updated_at REAL NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(queue_state)").fetchall()
            }
            if "last_recovery_nonce" not in columns:
                db.execute("ALTER TABLE queue_state ADD COLUMN last_recovery_nonce TEXT")

            item_columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(queue_items)").fetchall()
            }
            if "channel_message_id" not in item_columns:
                db.execute("ALTER TABLE queue_items ADD COLUMN channel_message_id INTEGER")
            if "channel_sent_at" not in item_columns:
                db.execute("ALTER TABLE queue_items ADD COLUMN channel_sent_at REAL")
            db.execute(
                """
                INSERT OR IGNORE INTO queue_state(site, updated_at)
                VALUES(?, ?)
                """,
                (_SITE, time.time()),
            )
            db.commit()

    @staticmethod
    def _clean_title(value: str | None) -> str | None:
        if not value:
            return None
        value = html_lib.unescape(_TAG_RE.sub("", value)).strip()
        return value or None

    async def discover(self, site: str = _SITE) -> dict[str, int]:
        wordpress_rows: list[tuple[str, str, str | None, str | None]] = []
        sitemap_rows: list[tuple[str, str, str | None, str | None]] = []

        try:
            wordpress_rows = await self._discover_wordpress(site)
        except Exception:
            pass
        try:
            sitemap_rows = await self._discover_sitemaps(site)
        except Exception:
            pass

        merged: dict[str, tuple[str, str, str | None, str | None]] = {}
        for row in sitemap_rows:
            merged[row[1]] = row
        for row in wordpress_rows:
            existing = merged.get(row[1])
            if existing:
                merged[row[1]] = (
                    row[0] or existing[0],
                    row[1],
                    row[2] or existing[2],
                    row[3] or existing[3],
                )
            else:
                merged[row[1]] = row
        site_root = site.rstrip("/")
        rows = [
            row
            for row in merged.values()
            if row[1].rstrip("/") != site_root
            and urlsplit(row[1]).netloc.lower() == urlsplit(site).netloc.lower()
        ]

        if not rows:
            raise RuntimeError("Nenhuma postagem foi descoberta no site.")

        now = time.time()
        added = 0
        with self._connect() as db:
            db.execute(
                "DELETE FROM queue_items WHERE site=? AND rtrim(url, '/')=rtrim(?, '/')",
                (site, site),
            )
            for post_id, url, title, published_at in rows:
                cur = db.execute(
                    """
                    INSERT OR IGNORE INTO queue_items(
                        site, post_id, url, title, published_at,
                        status, attempts, created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                    """,
                    (site, post_id, url, title, published_at, now, now),
                )
                if cur.rowcount:
                    added += 1
                else:
                    db.execute(
                        """
                        UPDATE queue_items
                        SET title=COALESCE(?, title),
                            published_at=COALESCE(?, published_at),
                            updated_at=?
                        WHERE url=?
                        """,
                        (title, published_at, now, url),
                    )

            total = db.execute(
                "SELECT COUNT(*) FROM queue_items WHERE site=?",
                (site,),
            ).fetchone()[0]
            db.execute(
                """
                UPDATE queue_state
                SET discovered_total=?, last_discovery_at=?, updated_at=?
                WHERE site=?
                """,
                (total, now, now, site),
            )
            db.commit()

        return {
            "found": len(rows),
            "added": added,
            "total": total,
            "wordpress": len(wordpress_rows),
            "sitemap": len(sitemap_rows),
        }

    async def _discover_wordpress(self, site: str) -> list[tuple[str, str, str | None, str | None]]:
        endpoint = urljoin(site.rstrip("/") + "/", "wp-json/wp/v2/posts")
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/140 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "pt-BR,pt;q=0.9",
        }
        out: list[tuple[str, str, str | None, str | None]] = []
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20, read=30),
            follow_redirects=True,
            trust_env=False,
            headers=headers,
        ) as client:
            page = 1
            total_pages: int | None = None
            while total_pages is None or page <= total_pages:
                response = await client.get(
                    endpoint,
                    params={
                        "per_page": 100,
                        "page": page,
                        "orderby": "date",
                        "order": "desc",
                        "_fields": "id,link,date,title",
                    },
                )
                response.raise_for_status()
                if total_pages is None:
                    total_pages = int(response.headers.get("x-wp-totalpages") or 1)
                payload = response.json()
                if not isinstance(payload, list) or not payload:
                    break
                for post in payload:
                    link = str(post.get("link") or "").strip()
                    if not link:
                        continue
                    title_obj = post.get("title") or {}
                    title = self._clean_title(
                        title_obj.get("rendered") if isinstance(title_obj, dict) else str(title_obj)
                    )
                    out.append(
                        (
                            str(post.get("id") or link),
                            link,
                            title,
                            str(post.get("date") or "") or None,
                        )
                    )
                page += 1
        return out

    async def _discover_sitemaps(self, site: str) -> list[tuple[str, str, str | None, str | None]]:
        headers = {"User-Agent": "Mozilla/5.0 Chrome/140 Safari/537.36"}
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20, read=30),
            follow_redirects=True,
            trust_env=False,
            headers=headers,
        ) as client:
            indexes = [
                urljoin(site.rstrip("/") + "/", "wp-sitemap.xml"),
                urljoin(site.rstrip("/") + "/", "sitemap_index.xml"),
            ]
            sitemap_urls: list[str] = []
            for index_url in indexes:
                try:
                    response = await client.get(index_url)
                    if response.status_code != 200:
                        continue
                    root = ElementTree.fromstring(response.text)
                    locs = [
                        (node.text or "").strip()
                        for node in root.iter()
                        if node.tag.endswith("loc") and node.text
                    ]
                    sitemap_urls.extend(
                        loc for loc in locs
                        if any(key in loc.lower() for key in ("post", "posts"))
                    )
                    if sitemap_urls:
                        break
                except Exception:
                    continue

            out: list[tuple[str, str, str | None, str | None]] = []
            seen: set[str] = set()
            for sitemap_url in dict.fromkeys(sitemap_urls):
                try:
                    response = await client.get(sitemap_url)
                    response.raise_for_status()
                    root = ElementTree.fromstring(response.text)
                    entries = []
                    current: dict[str, str] = {}
                    for child in root:
                        current = {}
                        for node in child:
                            if node.tag.endswith("loc") and node.text:
                                current["loc"] = node.text.strip()
                            elif node.tag.endswith("lastmod") and node.text:
                                current["lastmod"] = node.text.strip()
                        if current.get("loc"):
                            entries.append(current)
                    for entry in entries:
                        url = entry["loc"]
                        if url in seen:
                            continue
                        seen.add(url)
                        out.append((url, url, None, entry.get("lastmod")))
                except Exception:
                    continue
            return out

    def _state(self, site: str = _SITE) -> sqlite3.Row:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM queue_state WHERE site=?",
                (site,),
            ).fetchone()

    def summary_for_logs(self) -> str:
        data = self.status()
        counts = data.get("counts") or {}
        return (
            f"running={data.get('running')} paused={data.get('paused')} "
            f"total={data.get('discovered_total')} sent={counts.get('sent', 0)} "
            f"pending={counts.get('pending', 0)} retry_local={counts.get('retry_local', 0)} "
            f"processing={counts.get('processing', 0)} awaiting_delivery={counts.get('awaiting_delivery', 0)} "
            f"failed={counts.get('failed', 0)} current={data.get('current')}"
        )

    def status(self, site: str = _SITE) -> dict[str, object]:
        with self._connect() as db:
            state = db.execute(
                "SELECT * FROM queue_state WHERE site=?",
                (site,),
            ).fetchone()
            counts = {
                row["status"]: row["count"]
                for row in db.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM queue_items
                    WHERE site=?
                    GROUP BY status
                    """,
                    (site,),
                ).fetchall()
            }
            current = db.execute(
                """
                SELECT id, title, url, status, attempts
                FROM queue_items
                WHERE site=? AND status IN ('processing','awaiting_delivery','retry_local')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (site,),
            ).fetchone()
        return {
            "running": bool(state["running"]) if state else False,
            "paused": bool(state["paused"]) if state else True,
            "target_chat_id": state["target_chat_id"] if state else None,
            "discovered_total": state["discovered_total"] if state else 0,
            "last_started_at": state["last_started_at"] if state else None,
            "counts": counts,
            "current": dict(current) if current else None,
        }

    def _set_run_state(self, *, running: bool, paused: bool, target_chat_id: int | None = None) -> None:
        now = time.time()
        with self._connect() as db:
            db.execute(
                """
                UPDATE queue_state
                SET running=?, paused=?,
                    target_chat_id=COALESCE(?, target_chat_id),
                    last_started_at=CASE WHEN ? THEN ? ELSE last_started_at END,
                    updated_at=?
                WHERE site=?
                """,
                (
                    int(running),
                    int(paused),
                    target_chat_id,
                    int(running and not paused),
                    now,
                    now,
                    _SITE,
                ),
            )
            db.commit()

    def pause(self) -> None:
        self._set_run_state(running=True, paused=True)

    def stop(self) -> None:
        self._set_run_state(running=False, paused=True)

    def reset_stale(self, older_than_seconds: int = 600) -> int:
        cutoff = time.time() - older_than_seconds
        with self._connect() as db:
            cur = db.execute(
                """
                UPDATE queue_items
                SET status=CASE
                        WHEN status='awaiting_delivery' THEN 'pending'
                        WHEN status='processing' THEN 'pending'
                        ELSE status
                    END,
                    last_error=CASE
                        WHEN status IN ('awaiting_delivery','processing')
                        THEN 'Retomado após interrupção'
                        ELSE last_error
                    END,
                    updated_at=?
                WHERE site=?
                  AND status IN ('processing','awaiting_delivery')
                  AND updated_at < ?
                """,
                (time.time(), _SITE, cutoff),
            )
            db.commit()
            return cur.rowcount

    def recover_failed_once(self, nonce: str, target_chat_id: int | None = None) -> int:
        if not nonce:
            return 0
        now = time.time()
        with self._connect() as db:
            state = db.execute(
                "SELECT last_recovery_nonce FROM queue_state WHERE site=?",
                (_SITE,),
            ).fetchone()
            if state and state["last_recovery_nonce"] == nonce:
                return 0

            cur = db.execute(
                """
                UPDATE queue_items
                SET status='pending',
                    updated_at=?
                WHERE site=? AND status='failed'
                """,
                (now, _SITE),
            )
            count = cur.rowcount

            db.execute(
                """
                UPDATE queue_state
                SET running=1,
                    paused=0,
                    target_chat_id=COALESCE(?, target_chat_id),
                    last_recovery_nonce=?,
                    updated_at=?
                WHERE site=?
                """,
                (target_chat_id, nonce, now, _SITE),
            )
            db.commit()
            return count

    def retry_failures(self) -> int:
        with self._connect() as db:
            cur = db.execute(
                """
                UPDATE queue_items
                SET status='pending', last_error=NULL, updated_at=?
                WHERE site=? AND status='failed'
                """,
                (time.time(), _SITE),
            )
            db.commit()
            return cur.rowcount

    def _next_item(self) -> QueueItem | None:
        with self._connect() as db:
            # A broken/expired source must never monopolize the 24/7 worker.
            # After four attempts it is preserved as failed and the queue moves on.
            db.execute(
                """
                UPDATE queue_items
                SET status='failed',
                    last_error=COALESCE(last_error, 'Limite de tentativas atingido'),
                    updated_at=?
                WHERE site=?
                  AND status IN ('retry_local','pending')
                  AND attempts >= 4
                """,
                (time.time(), _SITE),
            )
            db.commit()
            row = db.execute(
                """
                SELECT id, url, title, status, attempts, published_at, last_error
                FROM queue_items
                WHERE site=? AND status IN ('retry_local','pending')
                  AND attempts < 4
                ORDER BY
                    CASE status WHEN 'retry_local' THEN 0 ELSE 1 END,
                    published_at DESC,
                    id ASC
                LIMIT 1
                """,
                (_SITE,),
            ).fetchone()
            if not row:
                return None
            db.execute(
                """
                UPDATE queue_items
                SET status='processing', attempts=attempts+1, updated_at=?
                WHERE id=?
                """,
                (time.time(), row["id"]),
            )
            db.commit()
        return QueueItem(
            id=row["id"],
            url=row["url"],
            title=row["title"],
            status=row["status"],
            attempts=row["attempts"] + 1,
            published_at=row["published_at"],
            last_error=row["last_error"],
        )

    def _update_item(self, item_id: int, status: str, **fields) -> None:
        allowed = {
            "title",
            "media_url",
            "media_source",
            "last_error",
            "sent_message_id",
            "sent_at",
            "channel_message_id",
            "channel_sent_at",
        }
        values = {k: v for k, v in fields.items() if k in allowed}
        values["status"] = status
        values["updated_at"] = time.time()
        assignments = ", ".join(f"{key}=?" for key in values)
        params = list(values.values()) + [item_id]
        with self._connect() as db:
            db.execute(
                f"UPDATE queue_items SET {assignments} WHERE id=?",
                params,
            )
            db.commit()

    def mark_relay_result(
        self,
        item_id: int,
        *,
        media_kind: str,
        message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        if error:
            self._update_item(item_id, "retry_local", last_error=error)
            return
        if media_kind != "video":
            self._update_item(
                item_id,
                "retry_local",
                last_error=f"Entrega direta virou {media_kind}; refazendo via upload nativo",
            )
            return
        self._update_item(
            item_id,
            "sent",
            sent_message_id=message_id,
            last_error=None,
            sent_at=time.time(),
        )
        print(
            f"IRIS_QUEUE_SENT item={item_id} message={message_id}",
            flush=True,
        )

    def channel_backfill_items(self) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, url, title, media_url, media_source,
                       sent_message_id, sent_at
                FROM queue_items
                WHERE site=? AND status='sent' AND channel_sent_at IS NULL
                ORDER BY sent_at ASC, id ASC
                """,
                (_SITE,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_channel_sent(self, item_id: int, message_id: int | None) -> None:
        self._update_item(
            item_id,
            "sent",
            channel_message_id=message_id,
            channel_sent_at=time.time(),
        )

    async def wait_delivery(self, item_id: int, timeout: float = 180.0) -> str:
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            with self._connect() as db:
                row = db.execute(
                    "SELECT status FROM queue_items WHERE id=?",
                    (item_id,),
                ).fetchone()
            if not row:
                return "missing"
            status = str(row["status"])
            if status in {"sent", "retry_local", "failed"}:
                return status
            await asyncio.sleep(0.8)
        self._update_item(
            item_id,
            "pending",
            last_error="Timeout aguardando confirmação do Telegram",
        )
        return "pending"

    async def start(self, bot, target_chat_id: int, *, discover: bool = False) -> None:
        if discover:
            await self.discover()
        self.reset_stale(0)
        self._set_run_state(running=True, paused=False, target_chat_id=target_chat_id)
        if self.task and not self.task.done():
            return
        self.task = asyncio.create_task(self._worker(bot), name="iris-site-queue")

    async def resume(self, bot, target_chat_id: int) -> None:
        self.reset_stale(0)
        await self.start(bot, target_chat_id, discover=False)

    async def maybe_resume(self, bot) -> bool:
        state = self._state()
        if not state or not state["running"] or state["paused"]:
            return False
        target = state["target_chat_id"] or settings.admin_id
        if not target:
            return False
        self.reset_stale(0)
        await self.start(bot, int(target), discover=False)
        return True

    async def _worker(self, bot) -> None:
        while True:
            state = self._state()
            if not state or not state["running"] or state["paused"]:
                return
            target_chat_id = int(state["target_chat_id"] or settings.admin_id or 0)
            if not target_chat_id:
                self.stop()
                return

            item = self._next_item()
            if item is None:
                self.stop()
                try:
                    await bot.send_message(
                        target_chat_id,
                        "🏁 <b>Fila concluída</b>\n\n"
                        "Todos os itens pendentes foram processados.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return

            try:
                print(
                    f"IRIS_QUEUE_PROCESS item={item.id} attempt={item.attempts} url={item.url}",
                    flush=True,
                )
                await asyncio.wait_for(
                    self._process_item(bot, target_chat_id, item),
                    timeout=900.0,
                )
            except asyncio.CancelledError:
                self._update_item(
                    item.id,
                    "pending",
                    last_error="Worker interrompido",
                )
                raise
            except Exception as exc:
                self._update_item(
                    item.id,
                    "failed",
                    last_error=f"{type(exc).__name__}: {str(exc)[:450]}",
                )
                print(
                    f"IRIS_QUEUE_FAILED item={item.id} error={type(exc).__name__}:{str(exc)[:180]}",
                    flush=True,
                )

            await asyncio.sleep(1.5)

    async def _process_item(self, bot, target_chat_id: int, item: QueueItem) -> None:
        result = await self.analyzer.analyze(item.url, deep=False)
        media = self._pick_video(result.resources)
        if media is None:
            result = await self.analyzer.analyze(item.url, deep=True)
            media = self._pick_video(result.resources)
        if media is None:
            raise RuntimeError("Nenhum vídeo baixável encontrado")

        title = media.title or result.title or item.title or "Vídeo"
        media.title = title
        media.metadata.setdefault("page_title", title)
        media.metadata.setdefault("page_url", item.url)
        self._update_item(
            item.id,
            "processing",
            title=title,
            media_url=media.url,
            media_source=media.source,
            last_error=None,
        )

        caption = f"🎬 {title[:220]}"

        # First try the fast HTML result. If its CDN URL is expired/blocked,
        # refresh the page in Chromium and retry with fresh network-captured media.
        try:
            await self._send_local_native(
                bot,
                target_chat_id,
                item,
                result.resources,
                caption,
            )
            return
        except Exception as first_exc:
            print(
                f"IRIS_LEGENDADOS_REFRESH item={item.id} "
                f"first_error={type(first_exc).__name__}:{str(first_exc)[:220]}",
                flush=True,
            )

        deep = await self.analyzer.analyze(item.url, deep=True)
        fresh_media = self._pick_video(deep.resources)
        if fresh_media is None:
            raise RuntimeError(
                "Nenhum vídeo fresco encontrado após reanálise no navegador"
            )

        fresh_title = fresh_media.title or deep.title or title
        caption = f"🎬 {fresh_title[:220]}"
        self._update_item(
            item.id,
            "processing",
            title=fresh_title,
            media_url=fresh_media.url,
            media_source=fresh_media.source,
            last_error="Link rápido falhou; tentando mídia fresca do navegador",
        )

        await self._send_local_native(
            bot,
            target_chat_id,
            item,
            deep.resources,
            caption,
        )

    async def _send_local_native(
        self,
        bot,
        target_chat_id: int,
        item: QueueItem,
        resources: list[MediaResource],
        caption: str,
    ) -> None:
        path: Path | None = None
        try:
            media, path, generated, info, rejected = await download_first_valid_video(
                resources,
                engine=self.engine,
            )
            self._update_item(
                item.id,
                "awaiting_delivery",
                media_url=media.url,
                media_source=media.source,
                last_error=(
                    "Candidatos rejeitados: " + " | ".join(rejected[-3:])
                    if rejected else None
                ),
            )
            receipt = await self.delivery.send_path_to_chat(
                bot,
                target_chat_id,
                path,
                caption=caption,
                queue_item_id=item.id,
                as_video=True,
            )
            if receipt.get("mode") == "channel-direct":
                self.mark_channel_sent(
                    item.id,
                    int(receipt["message_id"]) if receipt.get("message_id") is not None else None,
                )
            else:
                await self.wait_delivery(item.id, timeout=300)
        finally:
            if path and path.exists():
                path.unlink(missing_ok=True)

    @staticmethod
    def _pick_video(resources: list[MediaResource]) -> MediaResource | None:
        candidates = [
            r for r in resources
            if r.type in _VIDEO_TYPES
            and not r.drm
            and r.metadata.get("raw_downloadable") is not False
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda r: (
                0 if r.type == ResourceType.VIDEO else 1,
                0 if r.source.startswith("browser:network") else 1,
                -(r.height or 0),
            )
        )
        return candidates[0]


site_queue = SiteQueueManager()
