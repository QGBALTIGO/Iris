from __future__ import annotations

import asyncio
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

    print("IRIS_LARGE_VIDEO_STAGE analyze", flush=True)
    analyzed = await asyncio.wait_for(Analyzer().analyze(REAL_PAGE, deep=True), timeout=45)
    media = [
        r
        for r in analyzed.resources
        if r.type in {ResourceType.VIDEO, ResourceType.PLAYLIST, ResourceType.STREAM}
        and not r.drm
    ]
    if not media:
        raise RuntimeError("Nenhum vídeo baixável encontrado no link real")

    resource = media[0]
    me = await bot.get_me()
    if not me.username:
        raise RuntimeError("Bot sem username")

    print(
        f"IRIS_LARGE_VIDEO_STAGE found type={resource.type.value} host={Path(resource.url).name[:80]}",
        flush=True,
    )

    # First test the same zero-copy route used in production. If Telegram can
    # fetch the CDN directly, this is dramatically faster than download+upload.
    if resource.type == ResourceType.VIDEO:
        started_direct = time.monotonic()
        try:
            await asyncio.wait_for(
                userbot.send_to_bot(
                    me.username,
                    resource.url,
                    caption=relay_caption(
                        admin_id,
                        "⚡ <b>Teste real — entrega direta</b>\n\n"
                        "O Telegram buscou o vídeo diretamente da origem.",
                    ),
                    as_video=True,
                ),
                timeout=25,
            )
            direct_seconds = time.monotonic() - started_direct
            print(
                f"IRIS_LARGE_VIDEO_DIRECT_OK seconds={direct_seconds:.3f}",
                flush=True,
            )
            return {
                "mode": "direct",
                "direct_seconds": direct_seconds,
            }
        except Exception as exc:
            print(
                f"IRIS_LARGE_VIDEO_DIRECT_FAIL {type(exc).__name__}: {str(exc)[:180]}",
                flush=True,
            )

    # Fallback: download with replayed headers, inspect the local MP4, then
    # send through the 16-connection MTProto uploader with native video attrs.
    root = settings.downloads_dir / "large-video-smoke"
    root.mkdir(parents=True, exist_ok=True)
    target_name = "teste-real-video-grande.mp4"

    print("IRIS_LARGE_VIDEO_STAGE download", flush=True)
    started_download = time.monotonic()
    path = await asyncio.wait_for(
        DownloadEngine().download(
            resource,
            filename=str(Path("large-video-smoke") / target_name),
        ),
        timeout=240,
    )
    download_seconds = time.monotonic() - started_download
    size = path.stat().st_size
    info = await probe_video(path)
    print(
        f"IRIS_LARGE_VIDEO_STAGE downloaded bytes={size} seconds={download_seconds:.3f} "
        f"duration={info.duration:.1f} width={info.width} height={info.height}",
        flush=True,
    )

    print("IRIS_LARGE_VIDEO_STAGE upload", flush=True)
    started_upload = time.monotonic()
    try:
        await asyncio.wait_for(
            userbot.send_to_bot(
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
            ),
            timeout=240,
        )
        upload_seconds = time.monotonic() - started_upload
        return {
            "mode": "local",
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
