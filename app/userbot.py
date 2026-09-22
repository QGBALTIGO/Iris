from __future__ import annotations

import asyncio
from pathlib import Path

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
        if username:
            return f"{name or 'Conta 06'} (@{username})"
        return name or "Conta 06"

    async def send_file(
        self,
        target: int | str,
        path: Path,
        *,
        caption: str | None = None,
        as_video: bool = False,
        progress_callback=None,
    ):
        async with self._lock:
            client = await self.client()
            if not await client.is_user_authorized():
                raise RuntimeError("Conta 06 ainda não está autenticada.")
            return await client.send_file(
                target,
                str(path),
                caption=caption or path.name,
                force_document=not as_video,
                supports_streaming=as_video,
                progress_callback=progress_callback,
            )

    async def close(self) -> None:
        if self._client is not None and self._client.is_connected():
            await self._client.disconnect()


userbot = UserbotManager()
