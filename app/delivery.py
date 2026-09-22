from __future__ import annotations

import base64
import json
import shutil
import zipfile
from pathlib import Path

from app.models import MediaResource, ResourceType
from app.settings import Settings, settings
from app.userbot import userbot

_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
_REMOTE_DOCUMENT_EXT = {".pdf", ".zip"}
_RELAY_PREFIX = "IRIS_RELAY:"


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


def relay_caption(
    chat_id: int,
    caption: str | None = None,
    *,
    queue_item_id: int | None = None,
    expected_kind: str | None = None,
) -> str:
    payload = {
        "chat_id": int(chat_id),
        "caption": (caption or "")[:650],
    }
    if queue_item_id is not None:
        payload["queue_item_id"] = int(queue_item_id)
    if expected_kind:
        payload["expected_kind"] = str(expected_kind)
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return _RELAY_PREFIX + encoded


def parse_relay_payload(value: str | None) -> dict[str, object] | None:
    if not value or not value.startswith(_RELAY_PREFIX):
        return None
    try:
        raw = base64.urlsafe_b64decode(value[len(_RELAY_PREFIX):].encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return {
            "chat_id": int(data["chat_id"]),
            "caption": str(data.get("caption") or ""),
            "queue_item_id": (
                int(data["queue_item_id"])
                if data.get("queue_item_id") is not None
                else None
            ),
            "expected_kind": (
                str(data["expected_kind"])
                if data.get("expected_kind")
                else None
            ),
        }
    except Exception:
        return None


def parse_relay_caption(value: str | None) -> tuple[int, str] | None:
    payload = parse_relay_payload(value)
    if not payload:
        return None
    return int(payload["chat_id"]), str(payload["caption"])


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

    async def _send_to_bot_target(
        self,
        bot,
        chat_id: int,
        file_or_url: str | Path,
        *,
        caption: str | None = None,
        as_video: bool = False,
        queue_item_id: int | None = None,
        progress_callback=None,
    ) -> None:
        me = await bot.get_me()
        if not me.username:
            raise RuntimeError("O bot não possui username público.")
        await userbot.send_to_bot(
            me.username,
            file_or_url,
            caption=relay_caption(
                chat_id,
                caption,
                queue_item_id=queue_item_id,
                expected_kind="video" if as_video else None,
            ),
            as_video=as_video,
            progress_callback=progress_callback,
        )

    async def _send_via_userbot(
        self,
        message,
        file_or_url: str | Path,
        *,
        caption: str | None = None,
        as_video: bool = False,
        progress_callback=None,
    ) -> None:
        await self._send_to_bot_target(
            message.get_bot(),
            message.chat_id,
            file_or_url,
            caption=caption,
            as_video=as_video,
            progress_callback=progress_callback,
        )

    async def send_resource_to_chat(
        self,
        bot,
        chat_id: int,
        resource: MediaResource,
        *,
        caption: str | None = None,
        queue_item_id: int | None = None,
        as_video: bool = True,
    ) -> None:
        if not await userbot.is_authorized():
            raise RuntimeError("Conta 06 não autenticada.")
        await self._send_to_bot_target(
            bot,
            chat_id,
            resource.url,
            caption=caption,
            as_video=as_video and resource.type == ResourceType.VIDEO,
            queue_item_id=queue_item_id,
        )

    async def send_path_to_chat(
        self,
        bot,
        chat_id: int,
        path: Path,
        *,
        caption: str | None = None,
        queue_item_id: int | None = None,
        as_video: bool = False,
        progress_callback=None,
    ) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)
        if not await userbot.is_authorized():
            raise RuntimeError("Conta 06 não autenticada.")
        await self._send_to_bot_target(
            bot,
            chat_id,
            path,
            caption=caption,
            as_video=as_video and path.suffix.lower() in _VIDEO_EXT,
            queue_item_id=queue_item_id,
            progress_callback=progress_callback,
        )

    async def send_remote_resource(
        self,
        message,
        resource: MediaResource,
        *,
        as_video: bool = False,
        caption: str | None = None,
    ) -> bool:
        if resource.drm or resource.type in {ResourceType.PLAYLIST, ResourceType.STREAM}:
            return False
        if resource.metadata.get("engine") == "yt-dlp":
            return False

        if await userbot.is_authorized():
            try:
                await self._send_via_userbot(
                    message,
                    resource.url,
                    caption=caption,
                    as_video=as_video and resource.type == ResourceType.VIDEO,
                )
                return True
            except Exception:
                pass

        if resource.headers:
            return False
        if resource.size is not None and resource.size > 20 * 1024 * 1024:
            return False
        try:
            suffix = Path(resource.url.split("?", 1)[0]).suffix.lower()
            if as_video and resource.type == ResourceType.VIDEO:
                await message.reply_video(video=resource.url, caption=caption, supports_streaming=True)
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

    async def send_path(
        self,
        message,
        path: Path,
        *,
        as_video: bool = False,
        caption: str | None = None,
        progress_callback=None,
    ) -> str:
        if not path.is_file():
            raise FileNotFoundError(path)

        size = path.stat().st_size
        prefer_userbot = size >= self.config.userbot_threshold_bytes
        ready = await userbot.is_authorized() if userbot.configured else False

        if prefer_userbot and ready:
            await self._send_via_userbot(
                message,
                path,
                caption=caption,
                as_video=as_video and path.suffix.lower() in _VIDEO_EXT,
                progress_callback=progress_callback,
            )
            return "userbot"

        if size <= self.config.bot_upload_limit_bytes:
            from telegram import InputFile

            with path.open("rb") as fh:
                payload = InputFile(fh, filename=path.name)
                if as_video and path.suffix.lower() in _VIDEO_EXT:
                    await message.reply_video(video=payload, caption=caption, supports_streaming=True)
                    return "bot:video"
                await message.reply_document(document=payload, caption=caption)
                return "bot:file"

        if ready:
            await self._send_via_userbot(
                message,
                path,
                caption=caption,
                as_video=as_video and path.suffix.lower() in _VIDEO_EXT,
            )
            return "userbot"

        raise RuntimeError("Arquivo grande e a Conta 06 ainda não está autenticada.")

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
