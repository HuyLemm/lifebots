"""RSS/Atom helpers shared by the news and price bots."""

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

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
            try:  # Tuổi Trẻ: '11/26/2026 1:03:00 PM' (US order, Vietnam local time, no zone)
                return datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=VN)
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


META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def og_image(page_html: str, base_url: str = "") -> str | None:
    """The article's own picture from its <meta property="og:image"> (or twitter:image)."""
    found: dict[str, str] = {}
    for tag in META_TAG_RE.findall(page_html):
        attrs = {m[0].lower(): (m[1] or m[2]) for m in ATTR_RE.findall(tag)}
        prop = (attrs.get("property") or attrs.get("name") or "").lower()
        if prop in ("og:image", "og:image:secure_url", "twitter:image", "twitter:image:src"):
            if content := attrs.get("content", "").strip():
                found.setdefault("og" if prop.startswith("og:") else "tw", content)
    url = found.get("og") or found.get("tw")
    if not url:
        return None
    url = urljoin(base_url, html.unescape(url))
    ok = url.startswith(("http://", "https://")) and not url.lower().split("?")[0].endswith(
        (".svg", ".ico", ".gif")
    )
    return url if ok else None


def article_image(url: str, timeout: float = 6.0, max_bytes: int = 300_000) -> str | None:
    """Fetch the article page and return its main picture, None on ANY problem (slow site,
    paywall, no meta tag...). Only the first `max_bytes` are read: the meta tags live in <head>."""
    try:
        with httpx.stream(
            "GET",
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": BROWSER_UA, "Accept-Language": "vi,en;q=0.8"},
        ) as r:
            if r.status_code >= 400:
                return None
            raw = b""
            for chunk in r.iter_bytes():
                raw += chunk
                if len(raw) >= max_bytes or b"</head>" in raw.lower():
                    break
            return og_image(raw.decode(r.encoding or "utf-8", errors="replace"), str(r.url))
    except (httpx.HTTPError, ValueError, LookupError):
        return None


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
                "title": html.unescape((it.findtext("title") or "").strip()),  # Thanh Niên: &igrave;
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
                "title": html.unescape((e.findtext(f"{ATOM}title") or "").strip()),
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
