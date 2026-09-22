from __future__ import annotations

import asyncio
import html
import json
from pathlib import Path

from app.analyzer import Analyzer
from app.delivery import DeliveryManager
from app.settings import settings
from app.video_candidates import download_first_valid_video


async def run_one_shot(bot) -> dict:
    if not settings.one_shot_url:
        raise RuntimeError("IRIS_ONE_SHOT_URL não configurado")
    if not settings.admin_id:
        raise RuntimeError("IRIS_ADMIN_ID não configurado")

    analyzer = Analyzer()
    delivery = DeliveryManager()

    result = await analyzer.analyze(settings.one_shot_url, deep=True)
    resource, path, generated, info, rejected = await download_first_valid_video(
        result.resources
    )

    title = (
        resource.title
        or resource.metadata.get("page_title")
        or result.title
        or Path(path).stem
    )
    caption = (
        f"🎬 <b>{html.escape(str(title)[:220])}</b>\n"
        f"📐 {info.width}×{info.height}\n"
        f"🎞 {html.escape((info.codec or '?').upper())}"
        + (f" + {html.escape((info.audio_codec or '').upper())}" if info.audio_codec else "")
        + "\n\n✨ <i>IRIS</i>"
    )

    try:
        await delivery.send_path_to_chat(
            bot,
            settings.admin_id,
            path,
            caption=caption,
            as_video=True,
        )
        payload = {
            "url": settings.one_shot_url,
            "selected": resource.url,
            "title": title,
            "path": str(path),
            "bytes": path.stat().st_size,
            "codec": info.codec,
            "audio": info.audio_codec,
            "width": info.width,
            "height": info.height,
            "duration": info.duration,
            "rejected": rejected,
        }
        print("IRIS_ONE_SHOT_SENT " + json.dumps(payload, ensure_ascii=False), flush=True)
        return payload
    finally:
        path.unlink(missing_ok=True)
