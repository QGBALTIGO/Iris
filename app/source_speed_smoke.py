from __future__ import annotations

import asyncio
import time

import httpx

from app.analyzer import Analyzer
from app.models import ResourceType

REAL_PAGE = "https://pornocomlegenda.blog/meia-irma-ensinando-o-irmao-a-durar-mais-no-sexo-family-therapy-legendado/"
PROBE_BYTES = 32 * 1024 * 1024


async def _httpx_probe(url: str, headers: dict[str, str]) -> tuple[int, float]:
    timeout = httpx.Timeout(connect=20, read=30, write=20, pool=20)
    started = time.monotonic()
    read = 0
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(512 * 1024):
                read += len(chunk)
                if read >= PROBE_BYTES:
                    break
    return read, time.monotonic() - started


async def _curl_probe(url: str, headers: dict[str, str]) -> tuple[int, float]:
    cmd = [
        "curl", "-L", "--silent", "--show-error",
        "--max-time", "45",
        "--range", f"0-{PROBE_BYTES - 1}",
        "--output", "/dev/null",
        "--write-out", "%{size_download} %{time_total}",
    ]
    for key, value in headers.items():
        cmd.extend(["--header", f"{key}: {value}"])
    cmd.append(url)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[-300:])
    size_raw, seconds_raw = stdout.decode().strip().split()
    return int(float(size_raw)), float(seconds_raw)


async def run_source_speed_smoke() -> list[str]:
    result = await asyncio.wait_for(Analyzer().analyze(REAL_PAGE, deep=True), timeout=45)
    media = [
        r for r in result.resources
        if r.type == ResourceType.VIDEO and not r.drm
    ]
    if not media:
        raise RuntimeError("Nenhum vídeo direto encontrado")
    resource = media[0]

    rows: list[str] = []
    for name, func in (("httpx", _httpx_probe), ("curl", _curl_probe)):
        try:
            read, seconds = await func(resource.url, resource.headers)
            rate = (read / 1024 / 1024) / max(seconds, 0.001)
            rows.append(f"{name} bytes={read} seconds={seconds:.3f} mbps={rate:.3f}")
        except Exception as exc:
            rows.append(f"{name} error={type(exc).__name__}:{str(exc)[:160]}")
    return rows
