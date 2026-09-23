from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urljoin

from app.models import MediaVariant
from app.protection import detect_dash_protection


def inspect_mpd(text: str, base_url: str) -> tuple[list[MediaVariant], bool]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return [], False

    protection = detect_dash_protection(text)
    drm = protection.drm
    variants: list[MediaVariant] = []

    for representation in (e for e in root.iter() if e.tag.endswith("Representation")):
        width = _int(representation.attrib.get("width"))
        height = _int(representation.attrib.get("height"))
        bandwidth = _int(representation.attrib.get("bandwidth"))
        codecs = representation.attrib.get("codecs")
        mime = representation.attrib.get("mimeType")
        base = next((c.text.strip() for c in representation if c.tag.endswith("BaseURL") and c.text), None)
        if base:
            url = urljoin(base_url, base)
        else:
            url = base_url
        label = f"{height}p" if height else representation.attrib.get("id")
        variants.append(MediaVariant(url=url, label=label, width=width, height=height, bandwidth=bandwidth, codecs=codecs, mime_type=mime))
    return variants, drm


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
