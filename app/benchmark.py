from __future__ import annotations

import time
from pathlib import Path

from app.delivery import human_bytes, relay_caption
from app.downloads import DownloadEngine
from app.models import MediaResource, ResourceType
from app.settings import settings
from app.userbot import userbot


def _rate(size: int, seconds: float) -> str:
    if seconds <= 0:
        return "—"
    return f"{human_bytes(int(size / seconds))}/s"


async def run_admin_benchmark(bot, admin_id: int) -> list[str]:
    root = settings.downloads_dir / "benchmark"
    root.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    # Real internet -> Railway throughput through the production engine.
    for label, url, size_name in [
        ("8 MB", "https://media.githubusercontent.com/media/inventer-dev/speed-test-files/main/8MB.bin", "bench-8mb.bin"),
        ("32 MB", "https://media.githubusercontent.com/media/inventer-dev/speed-test-files/main/32MB.bin", "bench-32mb.bin"),
    ]:
        target = root / size_name
        started = time.monotonic()
        try:
            path = await DownloadEngine().download(
                MediaResource(url=url, type=ResourceType.OTHER),
                filename=str(Path("benchmark") / size_name),
            )
            elapsed = time.monotonic() - started
            size = path.stat().st_size
            lines.append(f"⬇️ {label}: {elapsed:.2f}s • {_rate(size, elapsed)}")
        except Exception as exc:
            lines.append(f"⬇️ {label}: ❌ {type(exc).__name__}")
        finally:
            target.unlink(missing_ok=True)

    ready = await userbot.is_authorized()
    if not ready:
        lines.append("👤 Conta 06: ❌ não autenticada")
        return lines

    me = await bot.get_me()
    if not me.username:
        lines.append("👤 Conta 06: ❌ bot sem username")
        return lines

    # Local Railway -> Telegram MTProto upload. The bot's update handler
    # performs the server-side copy after receiving the Account 06 message.
    for mb in (1, 8, 24):
        path = root / f"upload-{mb}mb.bin"
        with path.open("wb") as fh:
            fh.truncate(mb * 1024 * 1024)
        started = time.monotonic()
        try:
            sent = await userbot.send_to_bot(
                me.username,
                path,
                caption=relay_caption(admin_id, f"🧪 Benchmark MTProto • {mb} MB"),
            )
            upload_elapsed = time.monotonic() - started
            lines.append(
                f"⬆️ {mb} MB: upload {upload_elapsed:.2f}s • "
                f"{_rate(mb * 1024 * 1024, upload_elapsed)}"
            )
        except Exception as exc:
            lines.append(f"⬆️ {mb} MB: ❌ {type(exc).__name__}: {str(exc)[:90]}")
        finally:
            path.unlink(missing_ok=True)

    # Zero-copy external media. Telegram fetches the URL rather than Railway
    # downloading and uploading it.
    external = "https://download.samplelib.com/mp4/sample-5s.mp4"
    started = time.monotonic()
    try:
        await userbot.send_to_bot(
            me.username,
            external,
            caption=relay_caption(admin_id, "⚡ Benchmark • entrega direta por URL"),
            as_video=True,
        )
        fetch_elapsed = time.monotonic() - started
        lines.append(f"🌐 URL → Telegram: {fetch_elapsed:.2f}s")
    except Exception as exc:
        lines.append(f"🌐 URL → Telegram: ❌ {type(exc).__name__}: {str(exc)[:90]}")

    try:
        root.rmdir()
    except OSError:
        pass
    return lines
