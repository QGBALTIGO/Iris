from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.fast_mtproto import upload_path
from app.settings import Settings, settings
from app.video_tools import make_thumbnail, normalize_video_mp4, probe_video


_INVITE_RE = re.compile(r"(?:t\.me/(?:\+|joinchat/)|telegram\.me/(?:\+|joinchat/))([A-Za-z0-9_-]+)")


class UserbotManager:
    def __init__(self, config: Settings = settings):
        self.config = config
        self._client = None
        self._lock = asyncio.Lock()
        self._pending_code_hash: str | None = None
        self._awaiting_password = False
        self._delivery_target = None
        self._delivery_target_key: str | None = None

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

    @staticmethod
    def _invite_hash(invite_url: str) -> str:
        match = _INVITE_RE.search(invite_url.strip())
        if not match:
            raise ValueError("Convite privado do Telegram inválido.")
        return match.group(1)

    async def resolve_delivery_target(self, invite_url: str):
        key = self._invite_hash(invite_url)
        if self._delivery_target is not None and self._delivery_target_key == key:
            return self._delivery_target

        from telethon.errors import UserAlreadyParticipantError  # type: ignore
        from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest  # type: ignore

        client = await self.client()
        if not await client.is_user_authorized():
            raise RuntimeError("Conta 06 ainda não está autenticada.")

        check = await client(CheckChatInviteRequest(key))
        entity = getattr(check, "chat", None)
        if entity is None:
            try:
                result = await client(ImportChatInviteRequest(key))
                entity = result.chats[0] if result.chats else None
            except UserAlreadyParticipantError:
                check = await client(CheckChatInviteRequest(key))
                entity = getattr(check, "chat", None)

        if entity is None:
            raise RuntimeError("Não consegui resolver o canal do convite.")

        self._delivery_target = entity
        self._delivery_target_key = key
        return entity

    async def delivery_target_info(self, invite_url: str) -> dict[str, object]:
        from telethon import utils  # type: ignore

        client = await self.client()
        entity = await self.resolve_delivery_target(invite_url)
        permissions = None
        try:
            permissions = await client.get_permissions(entity, "me")
        except Exception:
            pass

        broadcast = bool(getattr(entity, "broadcast", False))
        megagroup = bool(getattr(entity, "megagroup", False))
        is_creator = bool(getattr(permissions, "is_creator", False)) if permissions else False
        is_admin = bool(getattr(permissions, "is_admin", False)) if permissions else False
        if broadcast:
            can_post = is_creator or is_admin
        else:
            can_post = not bool(getattr(permissions, "send_messages", True) is False) if permissions else True

        return {
            "id": int(utils.get_peer_id(entity)),
            "title": getattr(entity, "title", None) or "Destino Telegram",
            "broadcast": broadcast,
            "megagroup": megagroup,
            "is_creator": is_creator,
            "is_admin": is_admin,
            "can_post": can_post,
        }

    async def _send_file_to_entity(
        self,
        target,
        file_or_url: str | Path,
        *,
        caption: str | None = None,
        as_video: bool = False,
        progress_callback=None,
        parse_mode=None,
    ):
        client = await self.client()
        if not await client.is_user_authorized():
            raise RuntimeError("Conta 06 ainda não está autenticada.")

        payload = file_or_url
        path = Path(file_or_url) if isinstance(file_or_url, (str, Path)) else None
        upload_path_value: Path | None = path if path is not None and path.is_file() else None
        generated_mp4 = False
        thumb: Path | None = None
        attributes = None
        mime_type = None

        if upload_path_value is not None:
            if as_video:
                from telethon.tl.types import DocumentAttributeVideo  # type: ignore

                upload_path_value, generated_mp4 = await normalize_video_mp4(upload_path_value)
                info = await probe_video(upload_path_value)
                thumb = upload_path_value.with_name(f".{upload_path_value.stem}.thumb.jpg")
                try:
                    await make_thumbnail(upload_path_value, thumb, second=1.0)
                except Exception:
                    thumb = None
                attributes = [
                    DocumentAttributeVideo(
                        duration=max(0.1, float(info.duration)),
                        w=max(1, int(info.width)),
                        h=max(1, int(info.height)),
                        supports_streaming=True,
                    )
                ]
                mime_type = "video/mp4"

            payload = await upload_path(client, upload_path_value, progress_callback=progress_callback)

        try:
            return await client.send_file(
                target,
                payload,
                caption=caption or "",
                force_document=not as_video,
                supports_streaming=as_video,
                attributes=attributes,
                thumb=str(thumb) if thumb and thumb.exists() else None,
                mime_type=mime_type,
                progress_callback=progress_callback if upload_path_value is None else None,
                parse_mode=parse_mode,
            )
        finally:
            if thumb:
                thumb.unlink(missing_ok=True)
            if generated_mp4 and upload_path_value:
                upload_path_value.unlink(missing_ok=True)

    async def send_to_bot(
        self,
        bot_username: str,
        file_or_url: str | Path,
        *,
        caption: str | None = None,
        as_video: bool = False,
        progress_callback=None,
    ):
        if not bot_username:
            raise RuntimeError("O bot não possui username público.")
        async with self._lock:
            target = bot_username if bot_username.startswith("@") else f"@{bot_username}"
            return await self._send_file_to_entity(
                target,
                file_or_url,
                caption=caption,
                as_video=as_video,
                progress_callback=progress_callback,
                parse_mode=parse_mode,
            )

    async def send_to_delivery_channel(
        self,
        invite_url: str,
        file_or_url: str | Path,
        *,
        caption: str | None = None,
        as_video: bool = False,
        progress_callback=None,
        parse_mode=None,
    ):
        async with self._lock:
            target = await self.resolve_delivery_target(invite_url)
            return await self._send_file_to_entity(
                target,
                file_or_url,
                caption=caption,
                as_video=as_video,
                progress_callback=progress_callback,
                parse_mode=parse_mode,
            )

    async def repair_delivery_channel_captions(
        self,
        invite_url: str,
        *,
        limit: int = 1000,
    ) -> dict[str, int]:
        async with self._lock:
            client = await self.client()
            target = await self.resolve_delivery_target(invite_url)
            scanned = 0
            edited = 0
            failed = 0

            async for message in client.iter_messages(target, limit=limit):
                scanned += 1
                text = getattr(message, "message", None) or ""
                if not text:
                    continue

                has_raw_html = (
                    "<b>" in text
                    or "</b>" in text
                    or "<blockquote" in text
                    or "</blockquote>" in text
                )
                if not has_raw_html:
                    continue

                try:
                    await client.edit_message(
                        target,
                        message.id,
                        text,
                        parse_mode="html",
                    )
                    edited += 1
                except Exception as exc:
                    failed += 1
                    print(
                        f"IRIS_CAPTION_REPAIR_ERROR message={message.id} "
                        f"{type(exc).__name__}: {str(exc)[:220]}",
                        flush=True,
                    )
                await asyncio.sleep(0.18)

            return {"scanned": scanned, "edited": edited, "failed": failed}

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
