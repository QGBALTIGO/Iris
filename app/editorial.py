from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


@dataclass(slots=True)
class EditorialMetadata:
    title: str | None = None
    person: str | None = None
    categories: list[str] | None = None
    tags: list[str] | None = None
    description: str | None = None

    def __post_init__(self):
        self.categories = list(self.categories or [])
        self.tags = list(self.tags or [])

    def as_dict(self) -> dict:
        return asdict(self)


_SPACE = re.compile(r"\s+")
_BAD_HASH = re.compile(r"[^\wÀ-ÖØ-öø-ÿ]+", re.UNICODE)


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    value = _SPACE.sub(" ", html.unescape(value)).strip()
    return value or None


def _texts(nodes) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        value = _clean(node.get_text(" ", strip=True))
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = _clean(value) or ""
        if not value:
            continue
        key = value.casefold().lstrip("#")
        if key in seen:
            continue
        seen.add(key)
        out.append(value.lstrip("#"))
    return out


def _first(values: list[str]) -> str | None:
    return values[0] if values else None


def extract_editorial_metadata(page_html: str, page_url: str) -> EditorialMetadata:
    soup = BeautifulSoup(page_html, "html.parser")
    host = urlsplit(page_url).netloc.lower().removeprefix("www.")

    title = None
    og_title = soup.select_one('meta[property="og:title"]')
    if og_title and og_title.get("content"):
        title = _clean(og_title.get("content"))
    if not title and soup.title:
        title = _clean(soup.title.get_text(" ", strip=True))

    og_desc = soup.select_one('meta[property="og:description"]')
    desc_meta = soup.select_one('meta[name="description"]')
    description = _clean(
        (og_desc.get("content") if og_desc else None)
        or (desc_meta.get("content") if desc_meta else None)
    )

    people: list[str] = []
    categories: list[str] = []
    tags: list[str] = []

    if "tubepussy.org" in host:
        people.extend(_texts(soup.select(".tp-video-models-item a, .tp-video-models a")))
        categories.extend(_texts(soup.select(".tp-video-categories-item a, .tp-video-categories a")))
        tags.extend(_texts(soup.select(".tp-video-tags-item a, .tp-video-tags a")))

        if not people:
            people.extend(_texts([
                a for a in soup.select('a[href*="/models/"]')
                if "/models/" in (a.get("href") or "") and not (a.get("href") or "").rstrip("/").endswith("/models")
            ]))
        if not categories:
            categories.extend(_texts([
                a for a in soup.select('a[href*="/categories/"]')
                if "/categories/" in (a.get("href") or "") and not (a.get("href") or "").rstrip("/").endswith("/categories")
            ]))
        if not tags:
            tags.extend(_texts(soup.select('a[href*="/tags/"]')))

        # TubePussy Shorts uses a different taxonomy layout. On /shorts/<id>/
        # pages the visible hashtags link to /shorts/<taxonomy-slug>/ rather
        # than /tags/ or /categories/. Numeric /shorts/<id>/ links are videos,
        # so only non-numeric single-segment short links are metadata.
        path = urlsplit(page_url).path.lower()
        if re.fullmatch(r"/shorts/\d+/?", path):
            short_taxonomy_links = []
            for a in soup.select('a[href*="/shorts/"]'):
                href = (a.get("href") or "").split("?", 1)[0].split("#", 1)[0]
                href_path = urlsplit(href).path if "://" in href else href
                match = re.fullmatch(r"/shorts/([^/]+)/?", href_path)
                if not match:
                    continue
                slug = match.group(1).strip().lower()
                if not slug or slug.isdigit():
                    continue
                value = _clean(a.get_text(" ", strip=True))
                if not value or not value.startswith("#"):
                    continue
                short_taxonomy_links.append(a)

            # The first hashtag cluster belongs to the current short. Avoid
            # collecting taxonomy links from recommended videos lower down.
            if short_taxonomy_links:
                cluster: list = []
                first = short_taxonomy_links[0]
                parent = first.parent
                if parent is not None:
                    cluster = [
                        a for a in parent.select('a[href*="/shorts/"]')
                        if a in short_taxonomy_links
                    ]
                if not cluster:
                    cluster = short_taxonomy_links[:8]
                tags.extend(_texts(cluster))

    elif "xvideosputaria.com" in host:
        # WordPress-style taxonomies plus class/id based fallbacks.
        tag_links = list(soup.select('a[href*="/tag/"]'))
        category_links = list(soup.select('a[href*="/category/"]'))
        person_links = list(soup.select(
            'a[href*="/pornstar/"], a[href*="/pornstars/"], '
            'a[href*="/modelo/"], a[href*="/model/"], '
            'a[href*="/atriz/"], a[href*="/ator/"], a[href*="/performer/"]'
        ))

        # The site also renders three navigation groups (star/folder/tag).
        for container in soup.select('[class*="star"], [class*="model"], [class*="pornstar"], [class*="performer"], [id*="star"], [id*="model"]'):
            person_links.extend(container.select("a"))
        for container in soup.select('[class*="categor"], [id*="categor"]'):
            category_links.extend(container.select("a"))
        for container in soup.select('[class*="tag"], [id*="tag"]'):
            tag_links.extend(container.select("a"))

        people.extend(_texts(person_links))
        categories.extend(_texts(category_links))
        tags.extend(_texts(tag_links))

        # Avoid accidentally treating the WordPress title/tag navigation itself
        # as a person when the same link appears in more than one taxonomy.
        category_keys = {v.casefold() for v in categories}
        tag_keys = {v.casefold().lstrip("#") for v in tags}
        people = [
            p for p in people
            if p.casefold() not in category_keys
            and p.casefold().lstrip("#") not in tag_keys
        ]

    else:
        categories.extend(_texts(soup.select('a[rel~="category"], a[href*="/category/"]')))
        tags.extend(_texts(soup.select('a[rel~="tag"], a[href*="/tag/"]')))
        people.extend(_texts(soup.select(
            'a[href*="/model/"], a[href*="/models/"], a[href*="/performer/"]'
        )))

    return EditorialMetadata(
        title=title,
        person=_first(_dedupe(people)),
        categories=_dedupe(categories),
        tags=_dedupe(tags),
        description=description,
    )


def hashtag(value: str) -> str:
    value = _clean(value) or ""
    value = value.lstrip("#").replace("-", " ")
    value = _SPACE.sub("_", value)
    value = _BAD_HASH.sub("_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        return ""
    parts = []
    for part in value.split("_"):
        if part.islower() and part:
            part = part[0].upper() + part[1:]
        parts.append(part)
    return "#" + "_".join(parts)


def format_editorial_block(meta: dict | EditorialMetadata | None, *, max_chars: int = 760) -> str:
    if not meta:
        return ""
    if isinstance(meta, EditorialMetadata):
        data = meta.as_dict()
    else:
        data = dict(meta)

    person = _clean(data.get("person"))
    combined = _dedupe(list(data.get("categories") or []) + list(data.get("tags") or []))

    lines: list[str] = []
    if person:
        person_tag = hashtag(person)
        if person_tag:
            lines.append(f"<b>🚫 {html.escape(person_tag)}</b>")

    tags = [hashtag(value) for value in combined]
    tags = [value for value in tags if value]
    if tags:
        prefix = "🔎 Tags: "
        body = prefix
        kept: list[str] = []
        for tag in tags:
            trial = prefix + " / ".join(kept + [tag])
            if len(trial) > max_chars:
                break
            kept.append(tag)
        if len(kept) < len(tags):
            kept.append("…")
        body = prefix + " / ".join(kept)
        lines.append(f"<blockquote expandable>{html.escape(body)}</blockquote>")

    return "\n\n".join(lines)


def format_video_caption(title: str | None, meta: dict | EditorialMetadata | None) -> str:
    clean_title = _clean(title) or "Vídeo"

    data = meta.as_dict() if isinstance(meta, EditorialMetadata) else dict(meta or {})
    person = _clean(data.get("person"))
    combined = _dedupe(list(data.get("categories") or []) + list(data.get("tags") or []))

    # If a real model/person exists, the editorial block already has the
    # desired headline. For Shorts, where the site often exposes only title
    # + hashtag taxonomy, use the page title as the headline and keep the tags.
    if person:
        block = format_editorial_block(data)
        if block:
            return block[:1000]

    if combined:
        headline = f"<b>🚫 {html.escape(hashtag(clean_title) or '#Vídeo')}</b>"
        tag_only = format_editorial_block({
            "person": None,
            "categories": data.get("categories") or [],
            "tags": data.get("tags") or [],
        })
        if tag_only:
            return (headline + "\n\n" + tag_only)[:1000]

    return f"<b>🚫 {html.escape(hashtag(clean_title) or '#Vídeo')}</b>"[:1000]
