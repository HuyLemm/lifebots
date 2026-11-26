"""RSS/Atom helpers shared by the news and price bots."""

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

ATOM = "{http://www.w3.org/2005/Atom}"
TAG_RE = re.compile(r"<[^>]+>")
HEADERS = {"User-Agent": "Mozilla/5.0 LifeBots"}
VN = timezone(timedelta(hours=7))


def parse_time(value: str | None) -> datetime | None:
    """RSS pubDate (RFC 822), Atom/Telegram ISO 8601 -> Vietnam time; None if absent/invalid."""
    if not value or not value.strip():
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(VN)


def posted_line(dt: datetime | None) -> str:
    """'🕒 Đăng lúc 07:15 (26/11)' - the ORIGINAL time of the information, or '' if unknown."""
    return f"🕒 Đăng lúc {dt:%H:%M} ({dt:%d/%m})\n" if dt else ""


def short_time(dt: datetime | None) -> str:
    """'07:15' for today, '25/11 07:15' for another day (compact, for digest lines)."""
    if not dt:
        return ""
    return f"{dt:%H:%M}" if dt.date() == datetime.now(VN).date() else f"{dt:%d/%m %H:%M}"


MEDIA = "{http://search.yahoo.com/mrss/}"
IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)


def _clean(url: str | None) -> str | None:
    return html.unescape(url) if url else None


def item_image(it: ET.Element) -> str | None:
    """Best picture URL of an RSS <item>: enclosure, media:content/thumbnail, or first <img>."""
    for enc in it.findall("enclosure"):
        if (enc.get("type") or "").startswith("image") and enc.get("url"):
            return _clean(enc.get("url"))
    for tag in ("content", "thumbnail"):
        for m in it.iter(f"{MEDIA}{tag}"):
            if m.get("url") and (m.get("medium") in (None, "image") or tag == "thumbnail"):
                if not (m.get("type") or "image").startswith("image"):
                    continue
                return _clean(m.get("url"))
    m = IMG_RE.search(it.findtext("description") or "")
    if not m:
        m = IMG_RE.search(it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or "")
    url = m.group(1) if m else None
    return _clean(url) if url and url.startswith("http") else None


def atom_image(e: ET.Element) -> str | None:
    for tag in ("thumbnail", "content"):
        for m in e.iter(f"{MEDIA}{tag}"):
            if m.get("url"):
                return _clean(m.get("url"))
    for field in ("content", "summary"):
        m = IMG_RE.search(e.findtext(f"{ATOM}{field}") or "")
        if m and m.group(1).startswith("http"):
            return html.unescape(m.group(1))
    return None


def fetch_items(url: str) -> list[dict]:
    r = httpx.get(url, timeout=20, follow_redirects=True, headers=HEADERS)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):  # RSS
        items.append(
            {
                "title": (it.findtext("title") or "").strip(),
                "link": (it.findtext("link") or "").strip(),
                "text": TAG_RE.sub(" ", it.findtext("description") or ""),
                "published": parse_time(it.findtext("pubDate")),
                "image": item_image(it),
            }
        )
    for e in root.iter(f"{ATOM}entry"):  # Atom (GitHub releases...)
        link = e.find(f"{ATOM}link")
        items.append(
            {
                "title": (e.findtext(f"{ATOM}title") or "").strip(),
                "link": link.get("href", "") if link is not None else "",
                "text": TAG_RE.sub(" ", e.findtext(f"{ATOM}content") or ""),
                "published": parse_time(
                    e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated")
                ),
                "image": atom_image(e),
            }
        )
    return [i for i in items if i["link"]]


def contains_any(text: str, words: list[str]) -> bool:
    """Case-insensitive; an empty word list matches everything."""
    if not words:
        return True
    low = text.lower()
    return any(w.lower() in low for w in words)


def matches(item: dict, keywords: list[str]) -> bool:
    return contains_any(f"{item['title']} {item['text']}", keywords)
