"""Public Telegram sale channels (săn sale Shopee/Lazada/TikTok Shop) via the t.me/s/ web preview.

Only PUBLIC channels have this preview. The bot reads new posts, keeps product deals (a named
product with a price and/or % off) matching your keywords, tags the platform from the links,
and forwards them. A post is only marked "seen" AFTER Telegram accepted the message, the same
product posted by two channels is sent once, and each message has a button to drop the keyword
that matched (so the filter improves as you use it).
"""

import html
import logging
import re
import time
from datetime import datetime

import httpx

from shared.core import State, Telegram, esc, header
from shared.feeds import HEADERS, contains_any, parse_time, posted_line

log = logging.getLogger(__name__)

PLATFORMS = {
    "Shopee": ("shopee", "shope.ee"),
    "Lazada": ("lazada", "lzd.co"),
    "TikTok Shop": ("tiktok",),
}
POST_SPLIT = re.compile(r'data-post="')
TEXT_RE = re.compile(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
HREF_RE = re.compile(r'href="([^"]+)"')
TIME_RE = re.compile(r'<time datetime="([^"]+)"')
PRODUCT_TTL = 24 * 3600


def parse_channel(page: str) -> list[dict]:
    """[{'id': 'chan/123', 'text': ..., 'links': [...]}] from a t.me/s/<channel> page."""
    posts = []
    for chunk in POST_SPLIT.split(page)[1:]:
        post_id = chunk.split('"', 1)[0]
        m = TEXT_RE.search(chunk)
        if not m:
            continue  # photo-only post
        raw = m.group(1)
        links = [html.unescape(h) for h in HREF_RE.findall(raw)]
        text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", raw))).strip()
        t = TIME_RE.search(chunk)  # the message's own <time datetime="..."> (original post time)
        posts.append(
            {
                "id": post_id,
                "text": text,
                "links": links,
                "time": parse_time(t.group(1)) if t else None,
            }
        )
    return posts


def platform_of(post: dict) -> str | None:
    blob = " ".join(post["links"] + [post["text"]]).lower()
    for name, needles in PLATFORMS.items():
        if any(n in blob for n in needles):
            return name
    return None


URL_RE = re.compile(r"https?://\S+")
VOUCHER_RE = re.compile(
    r"(?i)(mã\s*(giảm|\d+\s*%)|\d+\s*%[^\n]{0,40}tối đa|tối đa|back (mã|lượt)|svip|voucher|"
    r"trợ giá|hoàn xu|freeship|xtra|cào xu|list sp|tổng hợp mã|danh sách mã)"
)
PRICE_RE = re.compile(
    r"(?i)(?<![\w/])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s?(k|tr|triệu|nghìn|ngàn|đ|₫)(?!\w)"
)
APPROX_RE = re.compile(r"(?i)(?<!\w)(\d{1,2}xx)(?!\w)")  # "3xx" = roughly 3xxK
NOT_PRICE_BEFORE = re.compile(r"(?i)(tối đa|max|từ|đơn|giảm|trên|hơn|mỗi)\s*$")


def is_voucher(text: str) -> bool:
    return bool(VOUCHER_RE.search(text))


def extract_price(text: str) -> str | None:
    """First price that is the product's price (not a voucher amount like 'giảm 39k từ 150k')."""
    for m in re.finditer(r"(?i)(?<![\w/])(\d+)\s?tr(\d{1,2})(?!\w)", text):  # "2tr4" = 2.4 million
        return f"{m.group(1)},{m.group(2)} tr"
    for m in PRICE_RE.finditer(text):
        if NOT_PRICE_BEFORE.search(text[max(0, m.start() - 14) : m.start()]):
            continue
        num, unit = m.group(1), m.group(2)
        if unit.lower() == "k" and re.fullmatch(r"\d", num):
            continue  # "2K", "4K" are screen resolutions, not prices
        unit = unit.upper() if unit.lower() == "k" else unit
        return f"{num}{'' if unit in ('K', 'đ', '₫') else ' '}{unit}"
    m = APPROX_RE.search(text)
    return f"~{m.group(1)}K" if m else None


def extract_discount(text: str) -> int | None:
    m = re.search(r"-\s?(\d{1,2})\s?%", text) or re.search(r"(?i)giảm\s*(\d{1,2})\s?%", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(?i)giảm\s*(\d)\s*/\s*(\d)\s*giá", text)
    return round(int(m.group(1)) / int(m.group(2)) * 100) if m else None


def product_lines(text: str) -> list[str]:
    """Lines that carry a product name (links removed, leading emoji/bullets stripped)."""
    out = []
    for line in text.splitlines():
        clean = re.sub(r"^[^\w]+", "", URL_RE.sub("", line)).strip(" :-–|")
        if sum(c.isalpha() for c in clean) >= 4:
            out.append(clean[:100])
    return out


def product_deal(post: dict) -> dict | None:
    """A post that names a product with a price and/or a % off. Voucher posts return None."""
    text = post["text"]
    if is_voucher(text):
        return None
    price, discount, lines = extract_price(text), extract_discount(text), product_lines(text)
    if not lines or (price is None and discount is None):
        return None
    return {"lines": lines[:4], "price": price, "discount": discount}


def product_key(deal: dict) -> str:
    """Same product + price from two channels -> same key (so it is sent once)."""
    return re.sub(r"\W+", "", deal["lines"][0].lower())[:40] + "|" + (deal["price"] or "")


def format_deal(
    plat: str | None, channel: str, deal: dict, link: str, posted: datetime | None = None
) -> str:
    tag = f"[{plat}] " if plat else ""
    body = "\n".join(esc(line) for line in deal["lines"])
    facts = []
    if deal["price"]:
        facts.append(f"💰 <b>{esc(deal['price'])}</b>")
    if deal["discount"]:
        facts.append(f"🔻 giảm {deal['discount']}%")
    facts_line = ("\n" + " · ".join(facts)) if facts else ""
    head = header(f"🛍 Deal {esc(tag)}· {esc(channel)}")
    return f"{head}\n{posted_line(posted)}{body}{facts_line}\n{esc(link)}"


def effective_keywords(cfg: dict, state: State) -> list[str]:
    """config list + /add - /del (and the 🚫 button), so tuning needs no file editing."""
    base = cfg.get("channel_keywords", cfg.get("keywords", []))
    dropped = {k.lower() for k in state.data.get("kw_del", [])}
    return [k for k in [*base, *state.data.get("kw_add", [])] if k.lower() not in dropped]


def first_match(text: str, words: list[str]) -> str | None:
    low = text.lower()
    return next((w for w in words if w.lower() in low), None)


def drop_button(keyword: str | None) -> list[list[dict]] | None:
    data = f"del:{keyword}" if keyword else ""
    if not keyword or len(data.encode()) > 64:  # Telegram callback_data limit
        return None
    return [[{"text": f"🚫 Bỏ từ khoá “{keyword}”", "callback_data": data}]]


MAX_PAGES = 12  # ~8-20 posts per page: enough to cover a busy channel for about a day


def fetch_posts(handle: str, seen: dict, max_pages: int = MAX_PAGES) -> list[dict]:
    """Newest page first, then older pages (?before=<oldest id>) until we reach a post we already
    know. A scheduler that only wakes the bot every few hours therefore still sees everything
    posted in between, instead of just the latest ~20 posts."""
    posts: list[dict] = []
    before: int | None = None
    for _ in range(max_pages):
        url = f"https://t.me/s/{handle}" + (f"?before={before}" if before else "")
        r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        r.raise_for_status()
        page = parse_channel(r.text)
        if not page:
            break
        posts += page
        if any(p["id"] in seen for p in page):
            break  # reached known territory: nothing older is new
        before = min(int(p["id"].rsplit("/", 1)[1]) for p in page)
    return posts


def check_channels(tg: Telegram, state: State, args, cfg: dict) -> None:
    channels = cfg.get("channels", [])
    if not channels:
        return
    seen = state.data.setdefault("channel_posts", {})
    recent = state.data.setdefault("recent_products", {})  # product_key -> ts
    now = time.time()
    for k in [k for k, ts in recent.items() if now - ts > PRODUCT_TTL]:
        del recent[k]
    seeding = not state.data.get("channels_started") and not args.now
    wanted = cfg.get("channel_platforms", [])  # e.g. ["Shopee"]; empty = any
    kws = effective_keywords(cfg, state)
    cap = cfg.get("max_per_run", 8)
    todo: list[tuple[str, str, list[list[dict]] | None, str]] = []  # (post_id, msg, buttons, pkey)
    queued: set[str] = set()

    for ch in channels:
        handle = ch["handle"].lstrip("@")
        try:
            posts = fetch_posts(handle, seen, 1 if seeding else MAX_PAGES)
        except httpx.HTTPError as e:
            state.source_failed(f"kênh {handle}", f"{type(e).__name__}: {e}", tg)
            continue
        state.source_ok(f"kênh {handle}", tg)
        ch_kws = ch["keywords"] if "keywords" in ch else kws
        for post in posts:
            if post["id"] in seen:
                continue
            if seeding:
                seen[post["id"]] = True
                continue
            plat = platform_of(post)
            deal = product_deal(post)
            if (
                (wanted and plat not in wanted)
                or not contains_any(post["text"], ch_kws)
                or not deal
                or product_key(deal) in recent
                or product_key(deal) in queued
            ):
                seen[post["id"]] = True  # not a product deal for you / already told
                continue
            pkey = product_key(deal)
            queued.add(pkey)
            link = next(
                (u for u in post["links"] if any(n in u for n in sum(PLATFORMS.values(), ()))), ""
            )
            msg = format_deal(
                plat,
                ch.get("name", handle),
                deal,
                link or f"https://t.me/{post['id']}",
                post.get("time"),
            )
            todo.append((post["id"], msg, drop_button(first_match(post["text"], ch_kws)), pkey))

    state.data["channels_started"] = True
    while len(seen) > 5000:
        seen.pop(next(iter(seen)))
    if seeding:
        log.info("channels first run: remembered %d posts, nothing sent.", len(seen))
        return
    for post_id, msg, buttons, pkey in reversed(todo[:cap]):  # oldest first
        tg.send(msg, buttons)  # raises on failure -> stays unseen -> retried next round
        seen[post_id] = True
        recent[pkey] = now


# ---------- Telegram commands / buttons to tune keywords without editing files ----------


def cmd_keywords(tg: Telegram, state: State, args, text: str) -> str:
    added, dropped = state.data.get("kw_add", []), state.data.get("kw_del", [])
    cfg = state.data.get("_cfg_count", "?")
    lines = [f"🔎 Từ khoá đang dùng: <b>{cfg}</b>"]
    lines.append("➕ Đã thêm: " + (esc(", ".join(added)) if added else "chưa có"))
    lines.append("🚫 Đã bỏ: " + (esc(", ".join(dropped)) if dropped else "chưa có"))
    lines.append("Dùng /add &lt;từ&gt; hoặc /del &lt;từ&gt;")
    return "\n".join(lines)


def cmd_add(tg: Telegram, state: State, args, text: str) -> str:
    kw = text.strip().lower()
    if not kw:
        return "Dùng: /add bàn phím cơ"
    state.data["kw_del"] = [k for k in state.data.get("kw_del", []) if k.lower() != kw]
    adds = state.data.setdefault("kw_add", [])
    if kw not in adds:
        adds.append(kw)
    return f"➕ Đã thêm từ khoá “{esc(kw)}”"


def cmd_del(tg: Telegram, state: State, args, text: str) -> str:
    kw = text.strip().lower()
    if not kw:
        return "Dùng: /del giày"
    state.data["kw_add"] = [k for k in state.data.get("kw_add", []) if k.lower() != kw]
    dels = state.data.setdefault("kw_del", [])
    if kw not in dels:
        dels.append(kw)
    return f"🚫 Đã bỏ từ khoá “{esc(kw)}”. Gõ /add {esc(kw)} nếu muốn lấy lại."
