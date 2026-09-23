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

    async def _copy_existing_private_message(
        self,
        *,
        bot,
        source_chat_id: int,
        source_message_id: int,
        destination_chat_id: int,
    ) -> int:
        copied = await bot.copy_message(
            chat_id=destination_chat_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id,
        )
        return int(copied.message_id)

    async def _copy_existing_private_message_with_retry(
        self,
        *,
        bot,
        source_chat_id: int,
        source_message_id: int,
        destination_chat_id: int,
        max_attempts: int = 6,
    ) -> int:
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._copy_existing_private_message(
                    bot=bot,
                    source_chat_id=source_chat_id,
                    source_message_id=source_message_id,
                    destination_chat_id=destination_chat_id,
                )
            except Exception as exc:
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is None or attempt >= max_attempts:
                    raise
                try:
                    wait_seconds = float(retry_after.total_seconds())
                except Exception:
                    wait_seconds = float(retry_after)
                wait_seconds = max(1.0, wait_seconds) + 1.0
                print(
                    f"IRIS_CHANNEL_BACKFILL_FLOODWAIT message={source_message_id} "
                    f"attempt={attempt} wait={wait_seconds:.1f}s",
                    flush=True,
                )
                await asyncio.sleep(wait_seconds)
        raise RuntimeError("Falha inesperada no retry de copyMessage")

    async def _send_url(self, url: str, fallback_title: str | None = None, bot=None) -> tuple[str, int | None]:
        result = await self.analyzer.analyze(url, deep=True)
        resource, path, _, info, rejected = await download_first_valid_video(result.resources)
        title = (
            resource.title
            or resource.metadata.get("page_title")
            or result.title
            or fallback_title
            or Path(path).stem
        )
        caption = f"🎬 {str(title)[:220]}"
        try:
            receipt = await self.delivery.send_path_to_delivery_channel(
                path,
                bot=bot,
                caption=caption,
                as_video=True,
            )
            message_id = (
                int(receipt["message_id"])
                if receipt.get("message_id") is not None
                else None
            )
            print(
                "IRIS_CHANNEL_BACKFILL_SENT "
                + json.dumps(
                    {
                        "url": url,
                        "title": title,
                        "mode": receipt.get("mode"),
                        "message_id": message_id,
                        "duration": info.duration,
                        "width": info.width,
                        "height": info.height,
                        "rejected": rejected[-3:],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return str(title), message_id
        finally:
            path.unlink(missing_ok=True)

    async def run(self, bot=None) -> dict[str, int]:
        if not settings.delivery_channel_invite:
            raise RuntimeError("Canal de entrega não configurado.")
        if bot is None:
            raise RuntimeError("Bot API necessário para copiar mensagens antigas.")
        if not settings.admin_id:
            raise RuntimeError("IRIS_ADMIN_ID não configurado.")

        info = await self.delivery.delivery_channel_info(bot)
        if not info:
            raise RuntimeError("Canal de entrega não resolvido.")

        destination_chat_id = int(info["id"])
        bot_can_post = bool(info.get("bot_can_post"))
        userbot_can_post = bool(info.get("can_post"))

        queue_rows = site_queue.channel_backfill_items()
        queue_urls = {str(row["url"]) for row in queue_rows}
        sent = copied = uploaded = failed = 0

        for row in queue_rows:
            item_id = int(row["id"])
            url = str(row["url"])
            private_message_id = row.get("sent_message_id")

            try:
                # Fast path: the video already exists in the admin's private
                # chat with the bot. Telegram copies it server-side with no
                # download and no MTProto upload.
                if private_message_id is not None:
                    if not bot_can_post:
                        raise RuntimeError(
                            "O bot precisa ser administrador com permissão de postagem "
                            "no canal para copiar mensagens antigas sem reupload."
                        )
                    message_id = await self._copy_existing_private_message_with_retry(
                        bot=bot,
                        source_chat_id=int(settings.admin_id),
                        source_message_id=int(private_message_id),
                        destination_chat_id=destination_chat_id,
                    )
                    site_queue.mark_channel_sent(item_id, message_id)
                    sent += 1
                    copied += 1
                    print(
                        f"IRIS_CHANNEL_BACKFILL_COPY queue_item={item_id} "
                        f"from={settings.admin_id}:{private_message_id} "
                        f"to={destination_chat_id}:{message_id}",
                        flush=True,
                    )
                else:
                    # Only items with no historical Telegram message need the
                    # legacy network path.
                    if not userbot_can_post:
                        raise RuntimeError(
                            "Item sem message_id privado e a Conta 06 não pode publicar no canal."
                        )
                    title, message_id = await self._send_url(
                        url,
                        str(row.get("title") or "") or None,
                        bot=bot,
                    )
                    site_queue.mark_channel_sent(item_id, message_id)
                    sent += 1
                    uploaded += 1
                    print(
                        f"IRIS_CHANNEL_BACKFILL_UPLOAD queue_item={item_id} url={url}",
                        flush=True,
                    )
            except Exception as exc:
                failed += 1
                print(
                    f"IRIS_CHANNEL_BACKFILL_ERROR queue_item={item_id} "
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                    flush=True,
                )
            # Telegram applies a per-chat posting rate limit. Three seconds
            # keeps historical copies below the common channel threshold while
            # still being much faster than re-downloading/re-uploading media.
            await asyncio.sleep(3.2 if private_message_id is not None else 1.0)

        for url in self._manual_urls():
            if url in queue_urls or self._manual_done(url):
                continue
            try:
                if not userbot_can_post:
                    raise RuntimeError(
                        "URL manual sem mensagem privada registrada; reupload necessário, "
                        "mas a Conta 06 não pode publicar no canal."
                    )
                self._manual_mark(url, "processing")
                title, message_id = await self._send_url(url, bot=bot)
                self._manual_mark(url, "sent", title=title, message_id=message_id)
                sent += 1
                uploaded += 1
            except Exception as exc:
                failed += 1
                self._manual_mark(url, "failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
                print(
                    f"IRIS_CHANNEL_BACKFILL_ERROR manual={url} "
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                    flush=True,
                )
            await asyncio.sleep(1.0)

        return {
            "sent": sent,
            "copied": copied,
            "uploaded": uploaded,
            "failed": failed,
        }


channel_backfill = ChannelBackfill()
