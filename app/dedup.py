from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models import MediaResource

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_)", re.I)


def normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not _TRACKING.match(k)]
    query.sort()
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, urlencode(query), ""))


def deduplicate(resources: list[MediaResource]) -> list[MediaResource]:
    seen: dict[tuple[str, str], MediaResource] = {}
    for item in resources:
        key = (item.type.value, normalize_url(item.url))
        existing = seen.get(key)
        if existing is None:
            seen[key] = item
            continue
        if not existing.mime_type and item.mime_type:
            existing.mime_type = item.mime_type
        if not existing.size and item.size:
            existing.size = item.size
        if not existing.title and item.title:
            existing.title = item.title
        existing.headers.update({k: v for k, v in item.headers.items() if k not in existing.headers})
        known = {normalize_url(v.url) for v in existing.variants}
        existing.variants.extend(v for v in item.variants if normalize_url(v.url) not in known)
    return list(seen.values())
