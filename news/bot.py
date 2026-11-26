"""News bot: tech first, plus war / economy / crime, from RSS feeds with per-feed keywords.

Run (from LifeBots/):
    python -m news.bot --dry --now       # preview, nothing sent
    python -m news.bot --loop
Each feed has its own `keywords` (omit = every new item) and a `priority` (1 = sent first).
The first run only remembers existing items (no flood). An item is only marked "seen" AFTER
Telegram accepted it, the same story from two outlets is sent once, and a burst of news is
condensed into one digest message.
"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from shared.core import State, Telegram, esc, header, run
from shared.feeds import (  # noqa: F401  (matches re-exported for tests)
    fetch_items,
    matches,
    posted_line,
    short_time,
)

log = logging.getLogger(__name__)
TOPIC_ICONS = {"tech": "💻", "world": "⚔️", "economy": "📈", "crime": "⚖️"}
TOPIC_NAMES = {"tech": "Công nghệ", "world": "Thế giới", "economy": "Kinh tế", "crime": "Vụ án"}
TITLE_TTL = 48 * 3600


def norm_title(title: str) -> str:
    return re.sub(r"\W+", "", title.lower())[:60]


def digest_message(batch: list[tuple[int, str, str, dict]]) -> str:
    """One message grouped by topic: '• <link>title</link>'. Kept under Telegram's 4096 limit."""
    by_topic: dict[str, list[str]] = {}

    def newest_first(entry: tuple) -> tuple[bool, float]:
        published = entry[3].get("published")
        return (published is None, -published.timestamp() if published else 0.0)

    for _, topic, _name, item in sorted(batch, key=newest_first):
        when = short_time(item.get("published"))
        line = f'• {when + " · " if when else ""}<a href="{esc(item["link"])}">{esc(item["title"])}</a>'
        by_topic.setdefault(topic, []).append(line)
    out = [header(f"📰 Tin tổng hợp ({len(batch)} tin mới)")]
    for topic in [*TOPIC_ICONS, *[t for t in by_topic if t not in TOPIC_ICONS]]:
        if topic in by_topic:
            out.append(
                f"\n{TOPIC_ICONS.get(topic, '📰')} <b>{TOPIC_NAMES.get(topic, topic or 'Khác')}</b>"
            )
            out += by_topic[topic]
    return "\n".join(out)


def digest_chunks(fresh: list, limit: int = 3600) -> list[list]:
    """Split items into digest messages that each fit Telegram's 4096-char limit, so NO item is
    ever cut off (an item is only marked seen after the message that contains it was sent)."""
    chunks: list[list] = []
    cur: list = []
    for entry in fresh:
        if cur and len(digest_message([*cur, entry])) > limit:
            chunks.append(cur)
            cur = []
        cur.append(entry)
    if cur:
        chunks.append(cur)
    return chunks


def check(tg: Telegram, state: State, args, cfg: dict) -> None:
    seen = state.data.setdefault("seen", {})  # link -> True, insertion-ordered
    titles = state.data.setdefault("titles", {})  # normalised title -> ts (cross-outlet dedupe)
    now = time.time()
    for k in [k for k, ts in titles.items() if now - ts > TITLE_TTL]:
        del titles[k]
    seeding = not state.data.get("started") and not args.now
    fresh: list[tuple[int, str, str, dict]] = []  # (priority, topic, source, item)
    queued: set[str] = set()

    for feed in cfg.get("feeds", []):
        try:
            items = fetch_items(feed["url"])
        except (httpx.HTTPError, ET.ParseError) as e:
            state.source_failed(feed["name"], f"{type(e).__name__}: {e}", tg)
            continue
        state.source_ok(feed["name"], tg)
        kw = feed.get("keywords", [])
        for item in items:
            if item["link"] in seen:
                continue
            tkey = norm_title(item["title"])
            if seeding:
                seen[item["link"]] = True
                titles[tkey] = now
            elif not matches(item, kw) or tkey in titles or tkey in queued:
                seen[item["link"]] = True  # filtered out / already told via another outlet
            else:
                queued.add(tkey)
                fresh.append((feed.get("priority", 2), feed.get("topic", ""), feed["name"], item))

    while len(seen) > 5000:  # cap memory
        seen.pop(next(iter(seen)))
    state.data["started"] = True
    if seeding:
        log.info("first run: remembered %d items, nothing sent. New ones from now on.", len(seen))
        return

    fresh.sort(key=lambda f: f[0])  # tech (priority 1) first; sort is stable
    cap = cfg.get("max_per_run", 10)

    def mark(entry: tuple[int, str, str, dict]) -> None:
        seen[entry[3]["link"]] = True
        titles[norm_title(entry[3]["title"])] = now

    if len(fresh) > cfg.get("digest_over", 6):
        for batch in digest_chunks(fresh):  # every item is reported, as many messages as needed
            tg.send(digest_message(batch))
            for entry in batch:
                mark(entry)
        return
    for entry in fresh[:cap]:
        _, topic, name, item = entry
        icon = TOPIC_ICONS.get(topic, "📰")
        kind = f"{icon} {TOPIC_NAMES.get(topic, 'Tin tức')} · {esc(name)}"
        tg.send(
            f"{header(kind)}\n{posted_line(item.get('published'))}"
            f"{esc(item['title'])}\n{esc(item['link'])}"
        )
        mark(entry)  # only after Telegram accepted it; failures stay unseen -> retried


def main() -> None:
    run(Path(__file__).parent, check, 600)


if __name__ == "__main__":
    main()
