from __future__ import annotations

import asyncio
from pathlib import Path

from app.fast_mtproto import upload_path
from app.settings import Settings, settings


class UserbotManager:
    def __init__(self, config: Settings = settings):
        self.config = config
        self._client = None
        self._lock = asyncio.Lock()
        self._pending_code_hash: str | None = None
        self._awaiting_password = False

    @property
    def configured(self) -> bool:
        return bool(
            self.config.telegram_api_id
            and self.config.telegram_api_hash
            and self.config.telegram_phone
        )

    @property
    def session_path(self) -> Path:
        path = Path(self.config.telegram_session_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def client(self):
        if not self.configured:
            raise RuntimeError("Conta 06 ainda não foi configurada.")
        if self._client is None:
            from telethon import TelegramClient  # type: ignore

            self._client = TelegramClient(
                str(self.session_path),
                int(self.config.telegram_api_id),
                str(self.config.telegram_api_hash),
                connection_retries=5,
                request_retries=5,
                retry_delay=1,
                flood_sleep_threshold=60,
                entity_cache_limit=10000,
            )
        if not self._client.is_connected():
            await self._client.connect()
        return self._client

    async def is_authorized(self) -> bool:
        if not self.configured:
            return False
        try:
            client = await self.client()
            return bool(await client.is_user_authorized())
        except Exception:
            return False

    async def begin_login(self) -> str:
        async with self._lock:
            client = await self.client()
            if await client.is_user_authorized():
                me = await client.get_me()
                first = getattr(me, "first_name", None) or "Conta 06"
                return f"already:{first}"
            sent = await client.send_code_request(str(self.config.telegram_phone))
            self._pending_code_hash = sent.phone_code_hash
            self._awaiting_password = False
            return "code"

    async def submit_code(self, code: str) -> str:
        from telethon.errors import SessionPasswordNeededError  # type: ignore

        async with self._lock:
            client = await self.client()
            if not self._pending_code_hash:
                raise RuntimeError("Nenhuma autenticação está aguardando código.")
            try:
                await client.sign_in(
                    phone=str(self.config.telegram_phone),
                    code=code.replace(" ", "").replace("-", ""),
                    phone_code_hash=self._pending_code_hash,
                )
            except SessionPasswordNeededError:
                self._awaiting_password = True
                return "password"
            self._pending_code_hash = None
            self._awaiting_password = False
            return "ok"

    async def submit_password(self, password: str) -> str:
        async with self._lock:
            if not self._awaiting_password:
                raise RuntimeError("Nenhuma autenticação está aguardando senha.")
            client = await self.client()
            await client.sign_in(password=password)
            self._pending_code_hash = None
            self._awaiting_password = False
            return "ok"

    async def account_label(self) -> str:
        if not await self.is_authorized():
            return "desconectada"
        client = await self.client()
        me = await client.get_me()
        name = " ".join(
            part for part in [getattr(me, "first_name", None), getattr(me, "last_name", None)] if part
        ).strip()
        username = getattr(me, "username", None)
        return f"{name or 'Conta 06'} (@{username})" if username else (name or "Conta 06")

    async def user_id(self) -> int:
        client = await self.client()
        me = await client.get_me()
        return int(me.id)

    async def send_to_bot(
        self,
        bot_username: str,
        file_or_url: str | Path,
        *,
        caption: str | None = None,
        as_video: bool = False,
        progress_callback=None,
    ):
        """Upload/send to the bot's private chat.

        This deliberately avoids sending to an arbitrary numeric user ID, which
        requires an MTProto access_hash. Bot usernames are globally resolvable.
        The Bot API can then copy the resulting message server-side to the
        destination chat without a second upload.
        """
        if not bot_username:
            raise RuntimeError("O bot não possui username configurado.")
        async with self._lock:
            client = await self.client()
            if not await client.is_user_authorized():
                raise RuntimeError("Conta 06 ainda não está autenticada.")
            target = bot_username if bot_username.startswith("@") else f"@{bot_username}"
            payload = file_or_url
            path = Path(file_or_url) if isinstance(file_or_url, (str, Path)) else None
            if path is not None and path.is_file():
                payload = await upload_path(client, path, progress_callback=progress_callback)
            return await client.send_file(
                target,
                payload,
                caption=caption or "",
                force_document=not as_video,
                supports_streaming=as_video,
                progress_callback=progress_callback if path is None or not path.is_file() else None,
            )

    async def delete_from_bot_chat(self, bot_username: str, message_id: int) -> None:
        try:
            client = await self.client()
            target = bot_username if bot_username.startswith("@") else f"@{bot_username}"
            await client.delete_messages(target, [message_id], revoke=True)
        except Exception:
            pass

    async def close(self) -> None:
        if self._client is not None and self._client.is_connected():
            await self._client.disconnect()


userbot = UserbotManager()
