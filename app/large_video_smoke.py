from __future__ import annotations

import time
from pathlib import Path

from app.analyzer import Analyzer
from app.delivery import relay_caption
from app.downloads import DownloadEngine
from app.models import ResourceType
from app.settings import settings
from app.userbot import userbot
from app.video_tools import probe_video

REAL_PAGE = "https://pornocomlegenda.blog/meia-irma-ensinando-o-irmao-a-durar-mais-no-sexo-family-therapy-legendado/"


async def run_large_video_smoke(bot, admin_id: int) -> dict[str, float | int | str]:
    if not await userbot.is_authorized():
        raise RuntimeError("Conta 06 não autenticada")

    analyzed = await Analyzer().analyze(REAL_PAGE, deep=True)
    media = [
        r
        for r in analyzed.resources
        if r.type in {ResourceType.VIDEO, ResourceType.PLAYLIST, ResourceType.STREAM}
        and not r.drm
    ]
    if not media:
        raise RuntimeError("Nenhum vídeo baixável encontrado no link real")

    resource = media[0]
    root = settings.downloads_dir / "large-video-smoke"
    root.mkdir(parents=True, exist_ok=True)
    target_name = "teste-real-video-grande.mp4"

    started_download = time.monotonic()
    path = await DownloadEngine().download(
        resource,
        filename=str(Path("large-video-smoke") / target_name),
    )
    download_seconds = time.monotonic() - started_download
    size = path.stat().st_size
    info = await probe_video(path)

    me = await bot.get_me()
    if not me.username:
        raise RuntimeError("Bot sem username")

    started_upload = time.monotonic()
    try:
        await userbot.send_to_bot(
            me.username,
            path,
            caption=relay_caption(
                admin_id,
                "🎬 <b>Teste real — vídeo grande</b>\n\n"
                f"⏱️ {int(round(info.duration))}s\n"
                f"📐 {info.width}×{info.height}\n"
                f"💾 {size / 1024 / 1024:.1f} MB\n"
                "✅ Streaming nativo + thumbnail",
            ),
            as_video=True,
        )
        upload_seconds = time.monotonic() - started_upload
        return {
            "bytes": size,
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "download_seconds": download_seconds,
            "upload_seconds": upload_seconds,
            "download_mbps": (size / 1024 / 1024) / max(download_seconds, 0.001),
            "upload_mbps": (size / 1024 / 1024) / max(upload_seconds, 0.001),
        }
    finally:
        path.unlink(missing_ok=True)
        try:
            root.rmdir()
        except OSError:
            pass
