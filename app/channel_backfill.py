from __future__ import annotations

import asyncio
import html
import json
import sqlite3
import time
from pathlib import Path

from app.analyzer import Analyzer
from app.delivery import DeliveryManager
from app.settings import settings
from app.site_queue import site_queue
from app.video_candidates import download_first_valid_video


class ChannelBackfill:
    def __init__(self):
        base = Path("/data") if Path("/data").exists() else settings.downloads_dir
        self.db_path = base / "iris_channel_backfill.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.analyzer = Analyzer()
        self.delivery = DeliveryManager()
        self._ensure_schema()

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _ensure_schema(self):
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS manual_backfill (
                    url TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending',
                    title TEXT,
                    message_id INTEGER,
                    error TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )
            db.commit()

    def _manual_urls(self) -> list[str]:
        try:
            value = json.loads(settings.backfill_urls_json or "[]")
        except Exception:
            value = []
        return [str(x).strip() for x in value if str(x).strip().startswith(("http://", "https://"))]

    def _manual_done(self, url: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT status FROM manual_backfill WHERE url=?",
                (url,),
            ).fetchone()
        return bool(row and row["status"] == "sent")

    def _manual_mark(self, url: str, status: str, *, title: str | None = None, message_id: int | None = None, error: str | None = None):
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO manual_backfill(url,status,title,message_id,error,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET
                    status=excluded.status,
                    title=COALESCE(excluded.title, manual_backfill.title),
                    message_id=COALESCE(excluded.message_id, manual_backfill.message_id),
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (url, status, title, message_id, error, time.time()),
            )
            db.commit()

    async def _send_url(self, url: str, fallback_title: str | None = None) -> tuple[str, int | None]:
        result = await self.analyzer.analyze(url, deep=True)
        resource, path, _, info, rejected = await download_first_valid_video(result.resources)
        title = (
            resource.title
            or resource.metadata.get("page_title")
            or result.title
            or fallback_title
            or Path(path).stem
        )
        caption = f"🎬 <b>{html.escape(str(title)[:220])}</b>"
        try:
            sent = await self.delivery.send_path_to_delivery_channel(
                path,
                caption=caption,
                as_video=True,
            )
            print(
                "IRIS_CHANNEL_BACKFILL_SENT "
                + json.dumps(
                    {
                        "url": url,
                        "title": title,
                        "message_id": getattr(sent, "id", None),
                        "duration": info.duration,
                        "width": info.width,
                        "height": info.height,
                        "rejected": rejected[-3:],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return str(title), getattr(sent, "id", None)
        finally:
            path.unlink(missing_ok=True)

    async def run(self, bot=None) -> dict[str, int]:
        if not settings.delivery_channel_invite:
            raise RuntimeError("Canal de entrega não configurado.")

        info = await self.delivery.delivery_channel_info()
        if not info or not info.get("can_post"):
            title = (info or {}).get("title") or "canal"
            raise RuntimeError(f"A Conta 06 entrou em {title}, mas não tem permissão para publicar.")

        queue_rows = site_queue.channel_backfill_items()
        queue_urls = {str(row["url"]) for row in queue_rows}
        sent = failed = 0

        for row in queue_rows:
            item_id = int(row["id"])
            url = str(row["url"])
            try:
                title, message_id = await self._send_url(url, str(row.get("title") or "") or None)
                site_queue.mark_channel_sent(item_id, message_id)
                sent += 1
            except Exception as exc:
                failed += 1
                print(
                    f"IRIS_CHANNEL_BACKFILL_ERROR queue_item={item_id} "
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                    flush=True,
                )
            await asyncio.sleep(1.0)

        for url in self._manual_urls():
            if url in queue_urls or self._manual_done(url):
                continue
            try:
                self._manual_mark(url, "processing")
                title, message_id = await self._send_url(url)
                self._manual_mark(url, "sent", title=title, message_id=message_id)
                sent += 1
            except Exception as exc:
                failed += 1
                self._manual_mark(url, "failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
                print(
                    f"IRIS_CHANNEL_BACKFILL_ERROR manual={url} "
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                    flush=True,
                )
            await asyncio.sleep(1.0)

        return {"sent": sent, "failed": failed}


channel_backfill = ChannelBackfill()
