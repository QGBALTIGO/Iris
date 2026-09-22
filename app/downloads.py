from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.models import MediaResource, ResourceType
from app.security import validate_public_url
from app.settings import Settings, settings


class DownloadRejected(ValueError):
    pass


_ALLOWED_REPLAY_HEADERS = {
    "referer",
    "user-agent",
    "origin",
    "authorization",
    "cookie",
    "accept",
    "accept-language",
    "plus-vw-token",
}


def _replay_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in _ALLOWED_REPLAY_HEADERS or key.lower().startswith("x-")
    }


def safe_filename(resource: MediaResource, index: int = 1) -> str:
    raw = resource.title or Path(urlsplit(resource.url).path).name or f"download-{index}"
    raw = re.sub(r"[^\w.()\[\] -]+", "_", raw, flags=re.UNICODE).strip(" ._")
    if not raw:
        raw = f"download-{index}"
    ext = Path(urlsplit(resource.url).path).suffix
    if ext and not Path(raw).suffix:
        raw += ext
    return raw[:180]


class DownloadEngine:
    def __init__(self, config: Settings = settings, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.transport = transport
        self.config.downloads_dir.mkdir(parents=True, exist_ok=True)

    def choose_engine(self, resource: MediaResource) -> str:
        if resource.drm:
            return "blocked-drm"
        if resource.metadata.get("engine") == "yt-dlp":
            return "yt-dlp" if shutil.which("yt-dlp") else "unsupported-ytdlp"
        if resource.type == ResourceType.PLAYLIST:
            if shutil.which("N_m3u8DL-RE"):
                return "n_m3u8dl-re"
            if shutil.which("yt-dlp"):
                return "yt-dlp"
            return "unsupported-stream"
        if shutil.which("aria2c"):
            return "aria2"
        return "httpx"

    async def download(self, resource: MediaResource, filename: str | None = None, progress=None) -> Path:
        await validate_public_url(resource.url)
        if resource.drm:
            raise DownloadRejected("Conteúdo DRM não é baixado pelo Iris")
        engine = self.choose_engine(resource)
        target = self.config.downloads_dir / (filename or safe_filename(resource))
        if engine == "aria2":
            return await self._aria2(resource, target, progress)
        if engine in {"n_m3u8dl-re", "yt-dlp"}:
            return await self._stream_tool(resource, target, engine, progress)
        if engine == "unsupported-stream":
            raise DownloadRejected("Stream detectado, mas yt-dlp/N_m3u8DL-RE não está instalado")
        if engine == "unsupported-ytdlp":
            raise DownloadRejected("Esse recurso precisa do yt-dlp, que não está instalado")
        return await self._httpx(resource, target, progress)

    async def _httpx(self, resource: MediaResource, target: Path, progress=None) -> Path:
        headers = _replay_headers(resource.headers)
        async with httpx.AsyncClient(timeout=None, follow_redirects=False, trust_env=False, headers=headers, transport=self.transport) as client:
            current = resource.url
            for _ in range(self.config.max_redirects + 1):
                await validate_public_url(current)
                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            response.raise_for_status()
                        from urllib.parse import urljoin
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    total = _int(response.headers.get("content-length"))
                    if total and total > self.config.max_download_bytes:
                        raise DownloadRejected("Arquivo excede o limite configurado")
                    downloaded = 0
                    temp = target.with_suffix(target.suffix + ".part")
                    with temp.open("wb") as fh:
                        async for chunk in response.aiter_bytes(256 * 1024):
                            downloaded += len(chunk)
                            if downloaded > self.config.max_download_bytes:
                                raise DownloadRejected("Arquivo excede o limite configurado")
                            fh.write(chunk)
                            if progress:
                                value = downloaded / total if total else 0.0
                                await progress(downloaded, total, value)
                    temp.replace(target)
                    return target
            raise DownloadRejected("Muitos redirecionamentos")

    async def _aria2(self, resource: MediaResource, target: Path, progress=None) -> Path:
        cmd = ["aria2c", "--continue=true", "--max-connection-per-server=8", "--split=8", "--min-split-size=1M", "--dir", str(target.parent), "--out", target.name]
        for key, value in _replay_headers(resource.headers).items():
            cmd.extend(["--header", f"{key}: {value}"])
        cmd.append(resource.url)
        await _run(cmd, progress=progress)
        return target

    async def _stream_tool(self, resource: MediaResource, target: Path, engine: str, progress=None) -> Path:
        if engine == "n_m3u8dl-re":
            cmd = ["N_m3u8DL-RE", resource.url, "--save-dir", str(target.parent), "--save-name", target.stem, "--auto-select"]
            for key, value in _replay_headers(resource.headers).items():
                cmd.extend(["--header", f"{key}: {value}"])
        else:
            cmd = ["yt-dlp", "--no-part", "--no-playlist", "-o", str(target)]
            for key, value in _replay_headers(resource.headers).items():
                cmd.extend(["--add-header", f"{key}:{value}"])
            cmd.append(resource.url)
        await _run(cmd, progress=progress)
        return target


async def _run(cmd: list[str], progress=None) -> None:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    errors: list[str] = []

    async def consume(stream, capture_errors: bool = False):
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if capture_errors and text:
                errors.append(text)
                if len(errors) > 20:
                    errors.pop(0)
            if progress:
                match = re.search(r"(?<![\d.])(100|\d{1,2}(?:\.\d+)?)%", text)
                if match:
                    value = max(0.0, min(1.0, float(match.group(1)) / 100))
                    await progress(0, None, value)

    await asyncio.gather(consume(proc.stdout), consume(proc.stderr, True))
    code = await proc.wait()
    if code != 0:
        raise RuntimeError("\n".join(errors)[-1000:])


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
