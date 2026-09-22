from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from app.models import MediaResource, ResourceType
from app.settings import Settings, settings
from app.userbot import userbot

_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
_REMOTE_DOCUMENT_EXT = {".pdf", ".zip"}


def human_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def build_zip(paths: list[Path], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        for path in paths:
            if path.is_file():
                archive.write(path, arcname=path.name)
    return output


def build_pdf(paths: list[Path], output: Path) -> Path:
    if not paths:
        raise ValueError("Nenhuma imagem para gerar PDF")
    import img2pdf  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fh:
        fh.write(img2pdf.convert([str(p) for p in paths if p.is_file()]))
    return output


class DeliveryManager:
    def __init__(self, config: Settings = settings):
        self.config = config

    @property
    def userbot_configured(self) -> bool:
        return userbot.configured

    async def userbot_ready(self) -> bool:
        return await userbot.is_authorized()

    async def _copy_userbot_message(self, message, sent, bot_username: str, caption: str | None = None):
        bot = message.get_bot()
        source_chat = await userbot.user_id()
        copied = await bot.copy_message(
            chat_id=message.chat_id,
            from_chat_id=source_chat,
            message_id=sent.id,
            caption=caption,
        )
        await userbot.delete_from_bot_chat(bot_username, sent.id)
        return copied

    async def send_remote_resource(
        self,
        message,
        resource: MediaResource,
        *,
        as_video: bool = False,
        caption: str | None = None,
    ) -> bool:
        """Fast path: let Telegram fetch a direct public URL.

        First preference is Account 06 over MTProto. Telethon sends external
        URLs as external media, so Telegram performs the network fetch instead
        of Railway downloading and re-uploading the file. If that fails, the
        caller falls back to the normal download pipeline.
        """
        if resource.drm or resource.type in {ResourceType.PLAYLIST, ResourceType.STREAM}:
            return False
        if resource.metadata.get("engine") == "yt-dlp":
            return False

        bot = message.get_bot()
        me = await bot.get_me()
        bot_username = me.username
        if not bot_username:
            return False

        if await userbot.is_authorized():
            try:
                sent = await userbot.send_to_bot(
                    bot_username,
                    resource.url,
                    caption=caption,
                    as_video=as_video and resource.type == ResourceType.VIDEO,
                )
                await self._copy_userbot_message(message, sent, bot_username, caption)
                return True
            except Exception:
                pass

        # Cloud Bot API URL fetch is a useful fallback for small public media.
        # Telegram documents a 20 MB URL-fetch ceiling for non-photo content,
        # and sendDocument-by-URL is reliable for PDF/ZIP.
        if resource.headers:
            return False
        if resource.size is not None and resource.size > 20 * 1024 * 1024:
            return False
        try:
            suffix = Path(resource.url.split("?", 1)[0]).suffix.lower()
            if as_video and resource.type == ResourceType.VIDEO:
                await message.reply_video(
                    video=resource.url,
                    caption=caption,
                    supports_streaming=True,
                )
                return True
            if resource.type == ResourceType.IMAGE:
                if resource.size is not None and resource.size > 5 * 1024 * 1024:
                    return False
                await message.reply_photo(photo=resource.url, caption=caption)
                return True
            if resource.type == ResourceType.DOCUMENT and suffix in _REMOTE_DOCUMENT_EXT:
                await message.reply_document(document=resource.url, caption=caption)
                return True
        except Exception:
            return False
        return False

    async def send_path(self, message, path: Path, *, as_video: bool = False, caption: str | None = None) -> str:
        if not path.is_file():
            raise FileNotFoundError(path)

        size = path.stat().st_size
        prefer_userbot = size >= self.config.userbot_threshold_bytes
        ready = await userbot.is_authorized() if userbot.configured else False

        if prefer_userbot and ready:
            bot = message.get_bot()
            me = await bot.get_me()
            sent = await userbot.send_to_bot(
                me.username,
                path,
                caption=caption,
                as_video=as_video and path.suffix.lower() in _VIDEO_EXT,
            )
            await self._copy_userbot_message(message, sent, me.username, caption)
            return "userbot"

        if size <= self.config.bot_upload_limit_bytes:
            from telegram import InputFile

            with path.open("rb") as fh:
                payload = InputFile(fh, filename=path.name)
                if as_video and path.suffix.lower() in _VIDEO_EXT:
                    await message.reply_video(
                        video=payload,
                        caption=caption,
                        supports_streaming=True,
                    )
                    return "bot:video"
                await message.reply_document(document=payload, caption=caption)
                return "bot:file"

        if ready:
            bot = message.get_bot()
            me = await bot.get_me()
            sent = await userbot.send_to_bot(
                me.username,
                path,
                caption=caption,
                as_video=as_video and path.suffix.lower() in _VIDEO_EXT,
            )
            await self._copy_userbot_message(message, sent, me.username, caption)
            return "userbot"

        raise RuntimeError(
            "Arquivo acima do limite do Bot API e a Conta 06 ainda não está autenticada."
        )

    async def cleanup(self, paths: list[Path]) -> None:
        if not self.config.cleanup_after_delivery:
            return
        for path in paths:
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
