from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    pass


_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "metadata.google.internal"}
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".home", ".lan")
_ALLOWED_SCHEMES = {"http", "https"}


def is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def resolve_host(host: str) -> set[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return {info[4][0] for info in infos}


async def validate_public_url(
    url: str,
    resolver: Callable[[str], Awaitable[set[str]]] = resolve_host,
) -> str:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError("Somente URLs HTTP/HTTPS são permitidas")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URLs com credenciais embutidas não são permitidas")
    if not parsed.hostname:
        raise UnsafeUrlError("URL sem host")

    host = parsed.hostname.rstrip(".").lower()
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeUrlError("Host local/privado não é permitido")

    try:
        if not is_public_ip(host):
            raise UnsafeUrlError("IP privado/reservado não é permitido")
        return url
    except ValueError:
        pass

    try:
        addresses = await resolver(host)
    except socket.gaierror as exc:
        raise UnsafeUrlError("Não foi possível resolver o host") from exc

    if not addresses:
        raise UnsafeUrlError("Host sem endereço resolvido")
    if any(not is_public_ip(address) for address in addresses):
        raise UnsafeUrlError("Host resolve para rede privada/reservada")
    return url
