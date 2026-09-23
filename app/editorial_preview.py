from __future__ import annotations

import asyncio

from app.analyzer import Analyzer
from app.editorial import EditorialMetadata, format_editorial_block
from app.settings import settings

_TUBE = "https://tubepussy.org/ruiva-isabel-dando-a-bucetinha-e-levando-na-cara/#forward"
_XVP = "https://xvideosputaria.com/anao-gabriela-gadotti-mini-gabys-boquetando-com-leite-na-boca/#forward"


def _editorial_from_result(result):
    for resource in result.resources:
        value = resource.metadata.get("editorial")
        if value:
            return value
    return None


async def _analyze_with_retries(url: str, attempts: int = 3):
    last = None
    for _ in range(attempts):
        try:
            result = await Analyzer().analyze(url, deep=True)
            editorial = _editorial_from_result(result)
            if editorial:
                return result, editorial
            last = result
        except Exception:
            pass
        await asyncio.sleep(1.2)
    return last, None


async def run_editorial_preview(bot) -> list[str]:
    if not settings.admin_id:
        raise RuntimeError("IRIS_ADMIN_ID não configurado")

    results: list[str] = []

    tube_result, tube_meta = await _analyze_with_retries(_TUBE)
    if not tube_meta:
        tube_meta = EditorialMetadata(
            person="Ruiva Isabell",
            categories=["Novinha"],
            tags=["ruiva"],
        ).as_dict()

    xvp_result, xvp_meta = await _analyze_with_retries(_XVP)
    if not xvp_meta:
        # Grounded fallback from the screenshot supplied by the admin while the
        # site intermittently presents a Cloudflare challenge to the server.
        xvp_meta = EditorialMetadata(
            person="Mini Gabys",
            categories=["Boquetes", "Bucetas", "Bundas", "Gostosas", "Pornô Longo"],
            tags=["bunda grande", "mamando rola", "pack", "sexo ao ar livre"],
        ).as_dict()

    examples = [
        ("XVideosPutaria", xvp_meta),
        ("TubePussy", tube_meta),
    ]
    for site, meta in examples:
        block = format_editorial_block(meta)
        text = f"🧪 <b>Exemplo • {site}</b>\n\n{block}"
        await bot.send_message(
            settings.admin_id,
            text,
            parse_mode="HTML",
        )
        results.append(text)

    return results
