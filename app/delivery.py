from __future__ import annotations

import asyncio
import shutil
import zipfile
from pathlib import Path

from app.settings import Settings, settings

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
        self._userbot_lock = asyncio.Lock()

    @property
    def userbot_configured(self) -> bool:
        return bool(
            self.config.telegram_api_id
            and self.config.telegram_api_hash
            and self.config.telegram_session
        )

    async def send_path(self, message, path: Path, *, as_video: bool = False, caption: str | None = None) -> str:
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        if size <= self.config.bot_upload_limit_bytes:
            from telegram import InputFile
            with path.open("rb") as fh:
                payload = InputFile(fh, filename=path.name)
                if as_video and path.suffix.lower() in _VIDEO_EXT:
                    await message.reply_video(video=payload, caption=caption, supports_streaming=True)
                    return "bot:video"
                await message.reply_document(document=payload, caption=caption)
                return "bot:file"
        if self.userbot_configured:
            await self._send_userbot(path, caption=caption)
            await message.reply_text(
                f"Arquivo grande enviado pelo userbot: {path.name} · {human_bytes(size)}"
            )
            return "userbot"
        await message.reply_text(
            f"Download concluído: {path.name} · {human_bytes(size)}\n"
            "O arquivo excede o limite configurado do bot. Configure o userbot para entrega de arquivos grandes."
        )
        return "server"

    async def _send_userbot(self, path: Path, caption: str | None = None) -> None:
        from telethon import TelegramClient  # type: ignore
        from telethon.sessions import StringSession  # type: ignore

        async with self._userbot_lock:
            client = TelegramClient(
                StringSession(self.config.telegram_session),
                int(self.config.telegram_api_id),
                str(self.config.telegram_api_hash),
            )
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise RuntimeError("Sessão do userbot não está autorizada")
                me = await client.get_me()
                target = "me" if self.config.admin_id and getattr(me, "id", None) == self.config.admin_id else self.config.admin_id
                if not target:
                    raise RuntimeError("IRIS_ADMIN_ID não configurado")
                await client.send_file(target, str(path), caption=caption or path.name, force_document=True)
            finally:
                await client.disconnect()

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
