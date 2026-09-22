from __future__ import annotations

import time
from pathlib import Path

from app.fast_mtproto import upload_path
from app.settings import settings
from app.userbot import userbot

TEST_SIZE = 128 * 1024 * 1024


async def run_mtproto_speed_smoke(bot) -> list[str]:
    if not await userbot.is_authorized():
        raise RuntimeError("Conta 06 não autenticada")

    me = await bot.get_me()
    if not me.username:
        raise RuntimeError("Bot sem username")
    target = me.username if me.username.startswith("@") else f"@{me.username}"

    client = await userbot.client()
    root = settings.downloads_dir / "mtproto-speed"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "mtproto-128mb.bin"
    with path.open("wb") as fh:
        fh.truncate(TEST_SIZE)

    rows: list[str] = []
    try:
        for connections in (8, 12, 16):
            started = time.monotonic()
            try:
                uploaded = await upload_path(
                    client,
                    path,
                    connection_override=connections,
                )
                upload_seconds = time.monotonic() - started
                msg = await client.send_file(
                    target,
                    uploaded,
                    caption=f"IRIS internal MTProto benchmark {connections}",
                    force_document=True,
                )
                rate = (TEST_SIZE / 1024 / 1024) / max(upload_seconds, 0.001)
                rows.append(
                    f"connections={connections} seconds={upload_seconds:.3f} mbps={rate:.3f}"
                )
                try:
                    await client.delete_messages(target, [msg.id], revoke=True)
                except Exception:
                    pass
            except Exception as exc:
                rows.append(
                    f"connections={connections} error={type(exc).__name__}:{str(exc)[:140]}"
                )
    finally:
        path.unlink(missing_ok=True)
        try:
            root.rmdir()
        except OSError:
            pass
    return rows
