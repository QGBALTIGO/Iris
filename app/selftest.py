from __future__ import annotations

import asyncio
import base64
import tempfile
from io import BytesIO
from pathlib import Path

from app.delivery import build_pdf

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZVZsAAAAASUVORK5CYII="
)


async def run_telegram_selftest(bot, chat_id: int) -> list[str]:
    from telegram import InputFile

    results: list[str] = []

    await bot.send_message(chat_id, "IRIS • Diagnóstico\n\nTeste de mensagem: OK")
    results.append("texto: ok")

    with tempfile.TemporaryDirectory(prefix="iris-selftest-") as temp_dir:
        root = Path(temp_dir)
        png = root / "iris-test.png"
        png.write_bytes(_PNG_1X1)

        try:
            with png.open("rb") as fh:
                await bot.send_photo(chat_id, photo=InputFile(fh, filename=png.name), caption="IRIS · teste de imagem")
            results.append("imagem: ok")
        except Exception as exc:
            results.append(f"imagem: {type(exc).__name__}")

        try:
            pdf = build_pdf([png], root / "iris-test.pdf")
            with pdf.open("rb") as fh:
                await bot.send_document(chat_id, document=InputFile(fh, filename=pdf.name), caption="IRIS · teste de PDF")
            results.append("pdf: ok")
        except Exception as exc:
            results.append(f"pdf: {type(exc).__name__}")

        video = root / "iris-test.mp4"
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:d=1",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=stereo",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                "-y",
                str(video),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(stderr.decode(errors="replace")[-300:])

            with video.open("rb") as fh:
                await bot.send_video(
                    chat_id,
                    video=InputFile(fh, filename=video.name),
                    caption="IRIS · teste de vídeo",
                    supports_streaming=True,
                )
            results.append("vídeo: ok")

            with video.open("rb") as fh:
                await bot.send_document(
                    chat_id,
                    document=InputFile(fh, filename="iris-test-como-arquivo.mp4"),
                    caption="IRIS · vídeo enviado como arquivo",
                )
            results.append("vídeo como arquivo: ok")
        except Exception as exc:
            results.append(f"vídeo: {type(exc).__name__}")

    return results
