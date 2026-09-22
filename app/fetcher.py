from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from app.security import validate_public_url
from app.settings import Settings, settings


@dataclass(slots=True)
class FetchResult:
    url: str
    status_code: int
    content_type: str | None
    content_length: int | None
    body: bytes
    headers: dict[str, str]


class SafeFetcher:
    def __init__(self, config: Settings = settings, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.transport = transport

    async def fetch(
        self,
        url: str,
        max_bytes: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchResult:
        limit = max_bytes or self.config.max_html_bytes
        current = str(url)
        request_headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        }
        if headers:
            request_headers.update(headers)
        async with httpx.AsyncClient(
            timeout=self.config.request_timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
            headers=request_headers,
        ) as client:
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
                    declared = _int(response.headers.get("content-length"))
                    if declared is not None and declared > limit:
                        raise ValueError(f"Resposta excede o limite de {limit} bytes")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > limit:
                            raise ValueError(f"Resposta excede o limite de {limit} bytes")
                    return FetchResult(
                        url=str(response.url),
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type"),
                        content_length=declared,
                        body=bytes(data),
                        headers={k: v for k, v in response.headers.items()},
                    )
            raise ValueError("Muitos redirecionamentos")


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
