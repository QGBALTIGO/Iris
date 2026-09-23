from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProtectionDetails:
    encrypted: bool = False
    drm: bool = False
    systems: tuple[str, ...] = ()


_DRM_UUIDS = {
    "edef8ba9-79d6-4ace-a3c8-27dcd51d21ed": "Widevine",
    "9a04f079-9840-4286-ab92-e65be0885f95": "PlayReady",
    "94ce86fb-07ff-4f43-adb8-93d2fa968ca2": "FairPlay",
    "e2719d58-a985-b3c9-781a-b030af78d30e": "ClearKey",
    "5e629af5-38da-4063-8977-97ffbd9902d4": "Marlin",
    "f239e769-efa3-4850-9c16-a903c6932efb": "Adobe Primetime",
}

_SERVICE_HOSTS = (
    ("crunchyroll.com", "Crunchyroll"),
    ("netflix.com", "Netflix"),
    ("primevideo.com", "Prime Video"),
    ("disneyplus.com", "Disney+"),
    ("max.com", "Max"),
    ("hbomax.com", "Max"),
    ("paramountplus.com", "Paramount+"),
    ("tv.apple.com", "Apple TV+"),
    ("globoplay.globo.com", "Globoplay"),
    ("hidive.com", "HIDIVE"),
    ("animationdigitalnetwork.fr", "Animation Digital Network"),
)

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def detect_streaming_service(url: str) -> str | None:
    try:
        host = urlsplit(url).netloc.lower().split(":", 1)[0]
    except ValueError:
        return None
    host = host.removeprefix("www.")
    for suffix, label in _SERVICE_HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return label

    # Amazon uses several regional domains for Prime Video watch pages.
    if host == "amazon.com" or host.endswith(".amazon.com") or re.fullmatch(r"(?:www\.)?amazon\.[a-z.]{2,}", host):
        path = urlsplit(url).path.lower()
        if any(token in path for token in ("/gp/video", "/amazonvideo", "/video/detail")):
            return "Prime Video"
    return None


def _systems_from_text(value: str) -> set[str]:
    lower = value.lower()
    systems: set[str] = set()
    for uuid, label in _DRM_UUIDS.items():
        if uuid in lower:
            systems.add(label)
    if "widevine" in lower:
        systems.add("Widevine")
    if "playready" in lower or "microsoft.com/playready" in lower:
        systems.add("PlayReady")
    if "com.apple.streamingkeydelivery" in lower or "skd://" in lower:
        systems.add("FairPlay")
    if "clearkey" in lower:
        systems.add("ClearKey")
    return systems


def _ordered(systems: set[str]) -> tuple[str, ...]:
    priority = {
        "Widevine": 0,
        "PlayReady": 1,
        "FairPlay": 2,
        "ClearKey": 3,
        "Marlin": 4,
        "Adobe Primetime": 5,
        "SAMPLE-AES": 6,
        "AES-128": 7,
        "CENC/DRM": 8,
    }
    return tuple(sorted(systems, key=lambda item: (priority.get(item, 99), item)))


def detect_hls_protection(text: str) -> ProtectionDetails:
    encrypted = False
    drm = False
    systems: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        upper = line.upper()
        if not (upper.startswith("#EXT-X-KEY") or upper.startswith("#EXT-X-SESSION-KEY")):
            continue

        attrs = {key: value.strip('"') for key, value in _ATTR_RE.findall(line)}
        method = attrs.get("METHOD", "NONE").upper()
        if method == "NONE":
            continue

        encrypted = True
        keyformat = attrs.get("KEYFORMAT", "")
        uri = attrs.get("URI", "")
        line_systems = _systems_from_text(" ".join((keyformat, uri, line)))
        systems.update(line_systems)

        if method in {"SAMPLE-AES", "SAMPLE-AES-CTR"}:
            drm = True
            if not line_systems:
                systems.add("SAMPLE-AES")
        elif line_systems:
            # AES encryption tied to an explicit DRM key system is still DRM.
            drm = True
        elif method == "AES-128":
            systems.add("AES-128")

    return ProtectionDetails(encrypted=encrypted, drm=drm, systems=_ordered(systems))


def detect_dash_protection(text: str) -> ProtectionDetails:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ProtectionDetails()

    found = False
    systems: set[str] = set()
    for elem in root.iter():
        if not elem.tag.endswith("ContentProtection"):
            continue
        found = True
        scheme = elem.attrib.get("schemeIdUri", "")
        value = elem.attrib.get("value", "")
        systems.update(_systems_from_text(" ".join((scheme, value))))

        # PSSH/default_KID descendants may carry the DRM system identifier.
        descendant_text = " ".join((child.text or "") for child in elem.iter())
        systems.update(_systems_from_text(descendant_text))

    if found and not systems:
        # Preserve Iris' conservative behavior: ContentProtection means the
        # stream should not be offered as a clear downloadable asset.
        systems.add("CENC/DRM")

    return ProtectionDetails(
        encrypted=found,
        drm=found,
        systems=_ordered(systems),
    )
