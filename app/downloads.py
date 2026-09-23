from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

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
    raw = unquote(raw)
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
        if resource.type == ResourceType.IMAGE:
            return "httpx"
        if shutil.which("aria2c"):
            return "aria2"
        return "httpx"

    async def download(self, resource: MediaResource, filename: str | None = None, progress=None) -> Path:
        await validate_public_url(resource.url)
        if resource.drm:
            raise DownloadRejected("Conteúdo protegido por DRM não é baixado pelo Iris")
        engine = self.choose_engine(resource)
        target = self.config.downloads_dir / (filename or safe_filename(resource))
        preferred_container = str(resource.metadata.get("container") or "mp4").lower()
        if resource.type == ResourceType.PLAYLIST:
            wanted_ext = ".mkv" if preferred_container == "mkv" else ".mp4"
            if target.suffix.lower() != wanted_ext:
                target = target.with_suffix(wanted_ext)
        target.parent.mkdir(parents=True, exist_ok=True)

        if engine == "aria2":
            try:
                return await self._aria2(resource, target, progress)
            except Exception:
                return await self._httpx(resource, target, progress)
        if engine in {"n_m3u8dl-re", "yt-dlp"}:
            errors: list[str] = []
            chain = [engine]
            for fallback in ("n_m3u8dl-re", "yt-dlp", "ffmpeg", "streamlink"):
                if fallback not in chain:
                    chain.append(fallback)
            for candidate in chain:
                if candidate == "n_m3u8dl-re" and not shutil.which("N_m3u8DL-RE"):
                    continue
                if candidate == "yt-dlp" and not shutil.which("yt-dlp"):
                    continue
                if candidate == "ffmpeg" and not shutil.which("ffmpeg"):
                    continue
                if candidate == "streamlink" and not shutil.which("streamlink"):
                    continue
                try:
                    return await self._stream_tool(resource, target, candidate, progress)
                except Exception as exc:
                    errors.append(f"{candidate}: {type(exc).__name__}: {str(exc)[:300]}")
                    target.unlink(missing_ok=True)
            raise DownloadRejected("Todos os motores de stream falharam: " + " | ".join(errors[-4:]))
        if engine == "unsupported-stream":
            if shutil.which("ffmpeg"):
                return await self._stream_tool(resource, target, "ffmpeg", progress)
            if shutil.which("streamlink"):
                return await self._stream_tool(resource, target, "streamlink", progress)
            raise DownloadRejected("Stream detectado, mas não há motor HLS/DASH instalado")
        if engine == "unsupported-ytdlp":
            raise DownloadRejected("Esse recurso precisa do yt-dlp")
        return await self._httpx(resource, target, progress)

    async def _httpx(self, resource: MediaResource, target: Path, progress=None) -> Path:
        headers = _replay_headers(resource.headers)
        timeout = httpx.Timeout(connect=20, read=None, write=30, pool=30)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, headers=headers, transport=self.transport) as client:
            current = resource.url
            for _ in range(self.config.max_redirects + 1):
                await validate_public_url(current)
                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            response.raise_for_status()
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    total = _int(response.headers.get("content-length"))
                    if total and total > self.config.max_download_bytes:
                        raise DownloadRejected("Arquivo excede o limite configurado")
                    downloaded = 0
                    temp = target.with_suffix(target.suffix + ".part")
                    try:
                        with temp.open("wb") as fh:
                            async for chunk in response.aiter_bytes(512 * 1024):
                                downloaded += len(chunk)
                                if downloaded > self.config.max_download_bytes:
                                    raise DownloadRejected("Arquivo excede o limite configurado")
                                fh.write(chunk)
                                if progress:
                                    value = downloaded / total if total else 0.0
                                    await progress(downloaded, total, value)
                        temp.replace(target)
                        if resource.type == ResourceType.IMAGE and not _has_valid_image_signature(target):
                            target.unlink(missing_ok=True)
                            raise DownloadRejected(
                                "O site entregou dados de imagem renderizados/obfuscados, não um arquivo de imagem bruto válido"
                            )
                    except Exception:
                        temp.unlink(missing_ok=True)
                        raise
                    return target
            raise DownloadRejected("Muitos redirecionamentos")

    async def _aria2(self, resource: MediaResource, target: Path, progress=None) -> Path:
        cmd = [
            "aria2c",
            "--continue=true",
            "--max-connection-per-server=12",
            "--split=12",
            "--min-split-size=1M",
            "--file-allocation=none",
            "--disk-cache=64M",
            "--max-tries=5",
            "--retry-wait=1",
            "--connect-timeout=15",
            "--timeout=30",
            "--summary-interval=1",
            "--dir",
            str(target.parent),
            "--out",
            target.name,
        ]
        for key, value in _replay_headers(resource.headers).items():
            cmd.extend(["--header", f"{key}: {value}"])
        cmd.append(resource.url)
        await _run(cmd, progress=progress)
        return target

    async def _stream_tool(self, resource: MediaResource, target: Path, engine: str, progress=None) -> Path:
        if engine == "n_m3u8dl-re":
            preferred_height = resource.metadata.get("preferred_height")
            preferred_audio = resource.metadata.get("preferred_audio")
            preferred_subtitle = resource.metadata.get("preferred_subtitle")
            preferred_container = str(resource.metadata.get("container") or "mp4").lower()

            cmd = [
                "N_m3u8DL-RE",
                resource.url,
                "--save-dir", str(target.parent),
                "--save-name", target.stem,
                "--thread-count", "16",
                "--download-retry-count", "5",
                "--http-request-timeout", "30",
                "--check-segments-count",
                "--del-after-done",
                "--no-log",
                "--disable-update-check",
                "-mt",
            ]

            if preferred_height or preferred_audio or preferred_subtitle:
                if preferred_height:
                    cmd.extend([
                        "-sv",
                        f'res=".*x{int(preferred_height)}":for=best',
                    ])
                else:
                    cmd.extend(["-sv", "best"])

                if preferred_audio:
                    cmd.extend([
                        "-sa",
                        f'lang="{preferred_audio}":for=best',
                    ])
                else:
                    cmd.extend(["-sa", "best"])

                if preferred_subtitle:
                    cmd.extend([
                        "-ss",
                        f'lang="{preferred_subtitle}":for=all',
                    ])
            else:
                cmd.append("--auto-select")

            if preferred_container == "mkv" and shutil.which("mkvmerge"):
                cmd.extend(["-M", "format=mkv:muxer=mkvmerge"])
            else:
                cmd.extend(["-M", "format=mp4"])

            for key, value in _replay_headers(resource.headers).items():
                cmd.extend(["--header", f"{key}: {value}"])
        elif engine == "yt-dlp":
            preferred_height = resource.metadata.get("preferred_height")
            preferred_container = str(resource.metadata.get("container") or "mp4").lower()
            cmd = [
                "yt-dlp",
                "--no-part",
                "--no-playlist",
                "--newline",
                "--concurrent-fragments", "12",
                "--impersonate", "chrome",
                "--extractor-args", "generic:impersonate=chrome",
            ]
            if preferred_height:
                cmd.extend([
                    "-f",
                    f"bv*[height<={int(preferred_height)}]+ba/b[height<={int(preferred_height)}]",
                ])
            if preferred_container == "mkv":
                cmd.extend(["--merge-output-format", "mkv", "--remux-video", "mkv"])
            else:
                cmd.extend(["--merge-output-format", "mp4", "--remux-video", "mp4"])
            cmd.extend(["-o", str(target)])
            for key, value in _replay_headers(resource.headers).items():
                cmd.extend(["--add-header", f"{key}:{value}"])
            cmd.append(resource.url)
        elif engine == "ffmpeg":
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            ]
            for key, value in _replay_headers(resource.headers).items():
                if key.lower() == "user-agent":
                    cmd.extend(["-user_agent", value])
                elif key.lower() == "referer":
                    cmd.extend(["-referer", value])
            cmd.extend([
                "-i", resource.url,
                "-map", "0:v:0?",
                "-map", "0:a:0?",
                "-c", "copy",
                "-movflags", "+faststart",
                str(target),
            ])
        elif engine == "streamlink":
            cmd = [
                "streamlink",
                "--retry-streams", "1",
                "--stream-segment-attempts", "5",
                "--stream-segment-threads", "8",
                "--force",
            ]
            for key, value in _replay_headers(resource.headers).items():
                cmd.extend(["--http-header", f"{key}={value}"])
            cmd.extend([
                "-o", str(target),
                resource.url,
                "best",
            ])
        else:
            raise DownloadRejected(f"Motor de stream desconhecido: {engine}")
        await _run(cmd, progress=progress)
        return _resolve_stream_output(target)


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
                if len(errors) > 30:
                    errors.pop(0)
            if progress:
                match = re.search(r"(?<![\d.])(100|\d{1,2}(?:\.\d+)?)%", text)
                if match:
                    value = max(0.0, min(1.0, float(match.group(1)) / 100))
                    await progress(0, None, value)

    await asyncio.gather(consume(proc.stdout), consume(proc.stderr, True))
    code = await proc.wait()
    if code != 0:
        raise RuntimeError("\n".join(errors)[-1400:])


def _resolve_stream_output(target: Path) -> Path:
    if target.exists():
        return target
    candidates = sorted(target.parent.glob(target.stem + ".*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix != ".part":
            return candidate
    return target


def _has_valid_image_signature(path: Path) -> bool:
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return False
    if head.startswith(b"\xff\xd8\xff"):
        return True
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if head.startswith((b"GIF87a", b"GIF89a")):
        return True
    if head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP":
        return True
    if head[4:12] in {b"ftypavif", b"ftypavis"}:
        return True
    return False


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
