from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

from app.protection import detect_dash_protection, detect_hls_protection

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _attrs(line: str) -> dict[str, str]:
    return {key: value.strip('"') for key, value in _ATTR_RE.findall(line)}


def hls_track_details(text: str, base_url: str) -> dict[str, object]:
    audio: list[dict[str, object]] = []
    subtitles: list[dict[str, object]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.upper().startswith("#EXT-X-MEDIA"):
            continue
        attrs = _attrs(line)
        kind = attrs.get("TYPE", "").upper()
        if kind not in {"AUDIO", "SUBTITLES"}:
            continue
        item = {
            "kind": "audio" if kind == "AUDIO" else "subtitle",
            "language": attrs.get("LANGUAGE"),
            "name": attrs.get("NAME"),
            "group_id": attrs.get("GROUP-ID"),
            "default": attrs.get("DEFAULT", "NO").upper() == "YES",
            "autoselect": attrs.get("AUTOSELECT", "NO").upper() == "YES",
            "forced": attrs.get("FORCED", "NO").upper() == "YES",
            "channels": attrs.get("CHANNELS"),
            "url": urljoin(base_url, attrs["URI"]) if attrs.get("URI") else None,
        }
        (audio if kind == "AUDIO" else subtitles).append(item)

    protection = detect_hls_protection(text)
    return {
        "audio_tracks": audio,
        "subtitle_tracks": subtitles,
        "drm_systems": list(protection.systems),
        "encrypted": protection.encrypted,
        "drm": protection.drm,
    }


def _mime_for(adaptation: ET.Element) -> str:
    return (adaptation.attrib.get("mimeType") or adaptation.attrib.get("contentType") or "").lower()


def dash_track_details(text: str, base_url: str) -> dict[str, object]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {
            "audio_tracks": [],
            "subtitle_tracks": [],
            "drm_systems": [],
            "encrypted": False,
            "drm": False,
        }

    audio: list[dict[str, object]] = []
    subtitles: list[dict[str, object]] = []
    for adaptation in (e for e in root.iter() if e.tag.endswith("AdaptationSet")):
        mime = _mime_for(adaptation)
        lang = adaptation.attrib.get("lang")
        content_type = (adaptation.attrib.get("contentType") or "").lower()
        role_values = [
            elem.attrib.get("value")
            for elem in adaptation
            if elem.tag.endswith("Role") and elem.attrib.get("value")
        ]
        base = next(
            (child.text.strip() for child in adaptation if child.tag.endswith("BaseURL") and child.text),
            None,
        )
        representations = [e for e in adaptation if e.tag.endswith("Representation")]

        if "audio" in mime or content_type == "audio":
            codecs = adaptation.attrib.get("codecs")
            channels = None
            for child in adaptation.iter():
                if child.tag.endswith("AudioChannelConfiguration"):
                    channels = child.attrib.get("value")
                    break
            audio.append({
                "kind": "audio",
                "language": lang,
                "name": ",".join(filter(None, role_values)) or None,
                "channels": channels,
                "codecs": codecs,
                "representations": len(representations),
                "url": urljoin(base_url, base) if base else None,
            })
        elif (
            "text" in mime
            or "subtitle" in mime
            or content_type in {"text", "subtitle"}
            or any("sub" in (rep.attrib.get("mimeType") or "").lower() for rep in representations)
        ):
            subtitles.append({
                "kind": "subtitle",
                "language": lang,
                "name": ",".join(filter(None, role_values)) or None,
                "representations": len(representations),
                "url": urljoin(base_url, base) if base else None,
            })

    protection = detect_dash_protection(text)
    return {
        "audio_tracks": audio,
        "subtitle_tracks": subtitles,
        "drm_systems": list(protection.systems),
        "encrypted": protection.encrypted,
        "drm": protection.drm,
    }
