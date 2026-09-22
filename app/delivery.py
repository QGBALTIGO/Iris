from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from app.settings import Settings, settings
from app.userbot import userbot

_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


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

    async def send_path(self, message, path: Path, *, as_video: bool = False, caption: str | None = None) -> str:
        if not path.is_file():
            raise FileNotFoundError(path)

        size = path.stat().st_size
        prefer_userbot = size >= self.config.userbot_threshold_bytes
        ready = await userbot.is_authorized() if userbot.configured else False

        if prefer_userbot and ready:
            target = self.config.admin_id or message.chat_id
            await userbot.send_file(
                target,
                path,
                caption=caption,
                as_video=as_video and path.suffix.lower() in _VIDEO_EXT,
            )
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
            target = self.config.admin_id or message.chat_id
            await userbot.send_file(
                target,
                path,
                caption=caption,
                as_video=as_video and path.suffix.lower() in _VIDEO_EXT,
            )
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
