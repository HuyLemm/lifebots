"""RSS/Atom helpers shared by the news and price bots."""

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
