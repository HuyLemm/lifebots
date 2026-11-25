"""RSS/Atom helpers shared by the news and price bots."""

import re
import xml.etree.ElementTree as ET

import httpx

ATOM = "{http://www.w3.org/2005/Atom}"
TAG_RE = re.compile(r"<[^>]+>")
HEADERS = {"User-Agent": "Mozilla/5.0 LifeBots"}


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
            }
        )
    for e in root.iter(f"{ATOM}entry"):  # Atom (GitHub releases...)
        link = e.find(f"{ATOM}link")
        items.append(
            {
                "title": (e.findtext(f"{ATOM}title") or "").strip(),
                "link": link.get("href", "") if link is not None else "",
                "text": TAG_RE.sub(" ", e.findtext(f"{ATOM}content") or ""),
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
