from __future__ import annotations

import asyncio
from pathlib import Path

from app.delivery import relay_caption
from app.settings import settings
from app.userbot import userbot


async def run_native_video_smoke(bot, admin_id: int) -> None:
    if not await userbot.is_authorized():
        raise RuntimeError("Conta 06 não autenticada")

    me = await bot.get_me()
    if not me.username:
        raise RuntimeError("Bot sem username")

    root = settings.downloads_dir / "video-smoke"
    root.mkdir(parents=True, exist_ok=True)
    video = root / "iris-native-video-test.mp4"

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=800:sample_rate=44100",
        "-t", "4",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        "-y", str(video),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[-500:])

    try:
        await userbot.send_to_bot(
            me.username,
            video,
            caption=relay_caption(
                admin_id,
                "🎬 <b>Teste de vídeo nativo</b>\n\n"
                "✅ Streaming\n"
                "📐 640×360\n"
                "⏱️ 4s\n"
                "🖼️ Thumbnail gerada em 1s",
            ),
            as_video=True,
        )
    finally:
        video.unlink(missing_ok=True)
        try:
            root.rmdir()
        except OSError:
            pass
