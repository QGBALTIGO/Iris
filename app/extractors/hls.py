from __future__ import annotations

import re
from urllib.parse import urljoin

from app.models import MediaVariant

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _attrs(line: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in _ATTR_RE.findall(line):
        result[key] = value.strip('"')
    return result


def inspect_hls(text: str, base_url: str) -> tuple[list[MediaVariant], bool, bool]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    variants: list[MediaVariant] = []
    encrypted = False
    drm = False

    for index, line in enumerate(lines):
        upper = line.upper()
        if upper.startswith("#EXT-X-KEY"):
            attrs = _attrs(line)
            method = attrs.get("METHOD", "NONE").upper()
            encrypted = method != "NONE"
            keyformat = attrs.get("KEYFORMAT", "").lower()
            if method in {"SAMPLE-AES", "SAMPLE-AES-CTR"} or any(x in keyformat for x in ("widevine", "playready", "fairplay", "urn:uuid")):
                drm = True
        if not upper.startswith("#EXT-X-STREAM-INF"):
            continue
        attrs = _attrs(line)
        next_url = None
        for following in lines[index + 1 :]:
            if not following.startswith("#"):
                next_url = urljoin(base_url, following)
                break
        if not next_url:
            continue
        width = height = None
        resolution = attrs.get("RESOLUTION")
        if resolution and "x" in resolution.lower():
            try:
                width, height = map(int, resolution.lower().split("x", 1))
            except ValueError:
                pass
        bandwidth = None
        try:
            if attrs.get("BANDWIDTH"):
                bandwidth = int(attrs["BANDWIDTH"])
        except ValueError:
            pass
        label = f"{height}p" if height else attrs.get("NAME")
        variants.append(
            MediaVariant(
                url=next_url,
                label=label,
                width=width,
                height=height,
                bandwidth=bandwidth,
                codecs=attrs.get("CODECS"),
                mime_type="application/vnd.apple.mpegurl",
            )
        )
    return variants, encrypted, drm
