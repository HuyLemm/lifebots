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
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx

from shared.core import State, Telegram, esc, header
from shared.feeds import HEADERS, VN, contains_any, parse_time, posted_line

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
PHOTO_RE = re.compile(
    r"tgme_widget_message_photo_wrap[^>]*background-image:url\(['\"]?([^'\")]+)", re.S
)
PRODUCT_TTL = 24 * 3600
MEMORY_TTL = 60 * 24 * 3600  # how long a product's past prices are remembered
MEMORY_MAX_PRODUCTS = 2000
MEMORY_PER_PRODUCT = 6
REMINDER_MAX = 200
REMIND_BEFORE = 3600  # remind ~60 min before a deal expires
DEFAULT_BLOCKED = ["super fake", "like auth", "rep 1:1", "replica", "hàng fake", "nhái"]
DEFAULT_TRUSTED = [
    "apple", "anker", "baseus", "logitech", "xiaomi", "samsung", "sony", "jbl", "akko", "razer",
    "asus", "lenovo", "ugreen", "keychron", "aula", "dareu", "edifier", "hyperx", "steelseries",
    "corsair", "tp-link", "tplink", "kingston", "sandisk", "dell", "hp", "msi", "lg", "philips",
    "panasonic", "xiaomi", "realme", "oppo", "huawei", "garmin", "gopro", "dji", "casio",
]  # fmt: skip


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
        images = list(  # every picture of the post (an album is ONE message with several), no dups
            dict.fromkeys(html.unescape(u) for u in PHOTO_RE.findall(chunk))
        )
        posts.append(
            {
                "id": post_id,
                "text": text,
                "links": links,
                "time": parse_time(t.group(1)) if t else None,
                "image": images[0] if images else None,  # first picture = the product photo
                "images": images,
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
NOT_PRICE_BEFORE = re.compile(r"(?i)(tối đa|max|(?<!chỉ )từ|đơn|giảm|trên|hơn|mỗi)\s*$")
# "chỉ từ 191.000đ" IS a product price; a bare "từ 150k" is a voucher's minimum spend


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


# ---------- price memory (item 28) ----------

UNIT_K = ("k", "nghìn", "ngàn")
UNIT_TR = ("tr", "triệu")
GROUPED_RE = re.compile(r"\d{1,3}(?:[.,]\d{3})+")


def price_to_vnd(text: str | None) -> int | None:
    """'595K' -> 595000, '1tr2' -> 1200000, '1.299k' -> 1299000, '1,2tr' -> 1200000,
    '199.000đ' -> 199000. Approximations like '~3xxK' and anything unparsable -> None."""
    if not text:
        return None
    s = text.strip().lower().replace("₫", "đ")
    m = re.fullmatch(r"(\d+)\s?(?:tr|triệu)\s?(\d{1,2})", s)  # "1tr2" = 1.2 million
    if m:
        frac = m.group(2) if len(m.group(2)) > 1 else m.group(2) + "0"
        value = int(m.group(1)) * 1_000_000 + int(frac) * 10_000
    else:
        m = re.fullmatch(r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s?(k|tr|triệu|nghìn|ngàn|đ)?", s)
        if not m:
            return None
        num, unit = m.group(1), m.group(2) or "đ"
        grouped = bool(GROUPED_RE.fullmatch(num))
        if unit in UNIT_TR:
            value = float(num.replace(",", ".")) * 1_000_000
        elif unit in UNIT_K:
            value = (
                int(re.sub(r"[.,]", "", num)) if grouped else float(num.replace(",", "."))
            ) * 1000
        else:
            value = int(re.sub(r"[.,]", "", num)) if grouped else float(num.replace(",", "."))
    value = int(round(value))
    return value if value >= 1000 else None  # "5đ" is not a product price


def fmt_vnd(v: int) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}".rstrip("0").rstrip(".").replace(".", ",") + "tr"
    return f"{v / 1000:,.0f}K".replace(",", ".")


def name_key(deal: dict) -> str:
    """Product identity WITHOUT its price/discount, to recognise the same item at another price."""
    # the product name is the longest of the first lines (some channels open with a teaser such
    # as "Giá hời | chỉ từ 241.732đ" and name the product on the next line)
    line = max(deal["lines"][:3], key=len).lower()
    line = re.sub(r"(?i)(?<![\w/])\d+\s?(?:tr|triệu)\s?\d{1,2}(?!\w)", " ", line)
    line = PRICE_RE.sub(" ", line)
    line = re.sub(r"\d{1,2}\s?%", " ", line)
    line = re.sub(r"\b(giá|chỉ|còn|only|sale|giảm)\b", " ", line)
    return re.sub(r"\W+", "", line)[:40]


def prune_memory(memory: dict, now: float) -> None:
    for k in list(memory):
        memory[k] = [e for e in memory[k] if now - e[0] <= MEMORY_TTL]
        if not memory[k]:
            del memory[k]
    if len(memory) > MEMORY_MAX_PRODUCTS:  # evict the products not seen for longest
        for k in sorted(memory, key=lambda k: memory[k][-1][0])[
            : len(memory) - MEMORY_MAX_PRODUCTS
        ]:
            del memory[k]


def remember_price(memory: dict, key: str, vnd: int | None, now: float) -> None:
    if not key or vnd is None:
        return
    entries = memory.setdefault(key, [])
    entries.append([now, vnd])
    del entries[:-MEMORY_PER_PRODUCT]


def price_note(memory: dict, key: str, vnd: int | None) -> tuple[str, bool]:
    """('📉 Rẻ hơn lần trước (xx → yy)' | 'ℹ️ Lần trước: xx' | '', is_new_low). Informational only."""
    entries = memory.get(key) or []
    if vnd is None or not entries:
        return "", False
    last, lowest = entries[-1][1], min(e[1] for e in entries)
    if vnd < lowest:
        return f"📉 Rẻ hơn lần trước ({fmt_vnd(last)} → {fmt_vnd(vnd)})", True
    if vnd != last:
        return f"ℹ️ Lần trước: {fmt_vnd(last)}", False
    return "", False


# ---------- deal expiry (item 30) ----------

EXP_DATE = re.compile(
    r"(?:đến|tới|hết|hạn(?: dùng)?|kết thúc)\s*(?:hết\s*)?(?:ngày\s*)?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?"
)
EXP_TIME = re.compile(
    r"(?:đến|tới|hết)\s*(?:hết\s*)?(\d{1,2})(?:\s?(?:h|giờ)\s?(\d{2})?|:(\d{2}))(?!\d)"
)
ANY_DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?!\d)")
TODAY_RE = re.compile(r"(?:hết|trong|chỉ|duy nhất)\s*(?:ngày\s*)?hôm nay|hôm nay thôi")
LEFT_RE = re.compile(r"còn\s*(\d{1,2})\s?(?:h|giờ)\s?(\d{1,2})?|còn\s*(\d{1,3})\s?phút")


def _date(day: int, month: int, year: str | None, posted: datetime) -> datetime | None:
    y = int(year) if year else posted.year
    y += 2000 if y < 100 else 0
    try:
        d = posted.replace(
            year=y, month=month, day=day, hour=23, minute=59, second=0, microsecond=0
        )
    except ValueError:
        return None
    if not year and d < posted - timedelta(days=30):
        d = d.replace(year=y + 1)  # "đến 5/1" posted in December
    return d


def parse_expiry(text: str, posted: datetime | None = None) -> datetime | None:
    """When a deal ends, from phrases like 'đến 23h', 'đến 23:59', 'hết ngày 30/11', 'hết hôm nay',
    'còn 3 giờ'. Relative to the post's own time. None when unknown or already past at posting."""
    posted = posted or datetime.now(VN)
    low = text.lower()
    exp: datetime | None = None
    m = EXP_TIME.search(low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or m.group(3) or 0)
        if hour > 23 or minute > 59:
            return None
        d = ANY_DATE.search(low)
        day = _date(int(d.group(1)), int(d.group(2)), d.group(3), posted) if d else posted
        exp = day.replace(hour=hour, minute=minute, second=0, microsecond=0) if day else None
    elif m := EXP_DATE.search(low):
        exp = _date(int(m.group(1)), int(m.group(2)), m.group(3), posted)
    elif TODAY_RE.search(low):
        exp = posted.replace(hour=23, minute=59, second=0, microsecond=0)
    elif m := LEFT_RE.search(low):
        if m.group(3):
            exp = posted + timedelta(minutes=int(m.group(3)))
        else:
            exp = posted + timedelta(hours=int(m.group(1)), minutes=int(m.group(2) or 0))
    return exp if exp and exp > posted else None


# ---------- watch words, quality filters, score (items 27, 29, 33) ----------


def word_in(text: str, word: str) -> bool:
    """Whole-word, case-insensitive match ('hp' must not match 'shop')."""
    return bool(re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", text.lower()))


def effective_list(base: list[str], state: State, add_key: str, del_key: str) -> list[str]:
    dropped = {k.lower() for k in state.data.get(del_key, [])}
    return [k for k in [*base, *state.data.get(add_key, [])] if k.lower() not in dropped]


def blocked_terms(cfg: dict, state: State) -> list[str]:
    return effective_list(
        cfg.get("blocked_terms", DEFAULT_BLOCKED), state, "block_add", "block_del"
    )


def trusted_brands(cfg: dict, state: State) -> list[str]:
    return effective_list(
        cfg.get("trusted_brands", DEFAULT_TRUSTED), state, "trust_add", "trust_del"
    )


def first_word(text: str, words: list[str]) -> str | None:
    return next((w for w in words if word_in(text, w)), None)


def score_deal(
    deal: dict, *, new_low: bool = False, trusted: bool = False, watched: bool = False
) -> int:
    """0-5: discount (>=50% +2, >=25% +1), cheapest we have seen it (+1), trusted brand (+1),
    a word you follow (+1)."""
    score = 2 if (deal["discount"] or 0) >= 50 else 1 if (deal["discount"] or 0) >= 25 else 0
    return min(5, score + int(new_low) + int(trusted) + int(watched))


def format_deal(
    plat: str | None,
    channel: str,
    deal: dict,
    link: str,
    posted: datetime | None = None,
    *,
    hot: bool = False,
    stars: int = 0,
    brand: str | None = None,
    expiry: datetime | None = None,
    note: str = "",
) -> str:
    tag = f"[{plat}] " if plat else ""
    body = "\n".join(esc(line) for line in deal["lines"])
    facts = []
    if deal["price"]:
        facts.append(f"💰 <b>{esc(deal['price'])}</b>")
    if deal["discount"]:
        facts.append(f"🔻 giảm {deal['discount']}%")
    if stars:
        facts.append("⭐" * stars)
    if brand:
        facts.append(f"✅ {esc(brand)}")
    extra = ""
    if expiry:
        past = expiry <= datetime.now(VN)
        extra += f"\n{'⌛ Đã hết hạn' if past else '⏳ Hết hạn'}: {expiry:%H:%M} ({expiry:%d/%m})"
    if note:
        extra += f"\n{note}"
    facts_line = ("\n" + " · ".join(facts)) if facts else ""
    head = header(f"{'🔥 ' if hot else ''}🛍 Deal {esc(tag)}· {esc(channel)}")
    return f"{head}\n{posted_line(posted)}{body}{facts_line}{extra}\n{esc(link)}"


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
    min_score = cfg.get("min_score", 0)  # 0 = nothing is ever dropped for a low score
    watch = state.data.get("watch", [])
    blocked, brands = blocked_terms(cfg, state), trusted_brands(cfg, state)
    memory = state.data.setdefault("price_memory", {})
    prune_memory(memory, now)
    todo: list[dict] = []
    queued: set[str] = set()
    known: list[str] | None = state.data.get("known_channels")
    if known is None:  # older state: a channel counts as known if we already saw posts of it
        known = [
            h
            for h in (c["handle"].lstrip("@") for c in channels)
            if state.data.get("channels_started") and any(k.startswith(f"{h}/") for k in seen)
        ]
    state.data["known_channels"] = known

    for ch in channels:
        handle = ch["handle"].lstrip("@")
        # A channel we have never read is only remembered, never reported: adding a channel
        # must not dump its old posts on you.
        ch_seed = seeding or handle not in known
        try:
            posts = fetch_posts(handle, seen, 1 if ch_seed else MAX_PAGES)
        except httpx.HTTPError as e:
            state.source_failed(f"kênh {handle}", f"{type(e).__name__}: {e}", tg)
            continue
        state.source_ok(f"kênh {handle}", tg)
        if handle not in known:
            known.append(handle)
        ch_kws = ch["keywords"] if "keywords" in ch else kws
        for post in posts:
            if post["id"] in seen:
                continue
            if ch_seed:
                seen[post["id"]] = True
                continue
            text = post["text"]
            plat = platform_of(post)
            deal = product_deal(post)
            followed = first_word(text, watch)  # a word you asked to follow (/theodoi)
            if (
                first_word(text, blocked)  # fake / unwanted terms: never sent
                or (wanted and plat not in wanted)
                or not deal
                or not (followed or contains_any(text, ch_kws))
                or product_key(deal) in recent
                or product_key(deal) in queued
            ):
                seen[post["id"]] = True  # not a product deal for you / already told
                continue
            vnd, nkey = price_to_vnd(deal["price"]), name_key(deal)
            note, new_low = price_note(memory, nkey, vnd)  # informational: never blocks a deal
            brand = first_word(text, brands)
            stars = score_deal(deal, new_low=new_low, trusted=bool(brand), watched=bool(followed))
            if min_score and stars < min_score and not followed:
                seen[post["id"]] = True  # only when YOU set min_score > 0
                continue
            pkey = product_key(deal)
            queued.add(pkey)
            link = deal_link(post)
            expiry = parse_expiry(text, post.get("time"))
            channel_name = ch.get("name", handle)
            msg = format_deal(
                plat,
                channel_name,
                deal,
                link,
                post.get("time"),
                hot=bool(followed),
                stars=stars,
                brand=brand,
                expiry=expiry,
                note=note,
            )
            images = post.get("images") or ([post["image"]] if post.get("image") else [])
            todo.append(
                {
                    "id": post["id"],
                    "msg": msg,
                    "buttons": drop_button(first_match(text, ch_kws)),
                    "pkey": pkey,
                    "images": images,
                    "hot": bool(followed),
                    "stars": stars,
                    "nkey": nkey,
                    "vnd": vnd,
                    "expiry": expiry,
                    "line": deal["lines"][0],
                    "link": link,
                    "tag": f"[{plat}] " if plat else "",
                    "channel": channel_name,
                }
            )

    state.data["channels_started"] = True
    while len(seen) > 5000:
        seen.pop(next(iter(seen)))
    if seeding:
        log.info("channels first run: remembered %d posts, nothing sent.", len(seen))
        return
    # followed words first, then best score; ties oldest-first (todo lists newest first per channel)
    order = sorted(enumerate(todo), key=lambda p: (not p[1]["hot"], -p[1]["stars"], -p[0]))
    reminders = state.data.setdefault("reminders", [])
    for _, d in order[:cap]:
        # raises on failure -> stays unseen -> retried next round
        tg.send(d["msg"], d["buttons"], photo=d["images"][0] if d["images"] else None)
        seen[d["id"]] = True
        recent[d["pkey"]] = now
        remember_price(memory, d["nkey"], d["vnd"], now)
        if len(d["images"]) >= 3:  # 2+ more pictures than the one above: album, best effort
            send_album_safe(tg, d["images"][1:5])
        if d["expiry"] and d["expiry"].timestamp() - now > REMIND_BEFORE:
            reminders.append(
                {k: d[k] for k in ("line", "link", "tag", "channel")}
                | {"exp": d["expiry"].timestamp()}
            )
    del reminders[:-REMINDER_MAX]
    send_reminders(tg, state, now)


def deal_link(post: dict) -> str:
    """Product link: a marketplace link first, else the post's first outside link (affiliate
    shorteners such as shorten.asia), else the post itself. Telegram's own links/hashtags never."""
    http = [u for u in post["links"] if u.startswith("http") and "//t.me/" not in u]
    market = sum(PLATFORMS.values(), ())
    # an outside link needs a real path: Telegram auto-links shop names like "Store.vn" -> http://Store.vn/
    deep = [u for u in http if urlparse(u).path.strip("/")]
    return next((u for u in http if any(n in u for n in market)), None) or (
        deep[0] if deep else f"https://t.me/{post['id']}"
    )


def send_album_safe(tg: Telegram, photos: list[str]) -> None:
    album = getattr(tg, "send_album", None)
    if len(photos) < 2 or album is None:
        return
    try:
        album(photos)
    except Exception:  # noqa: BLE001 - the album is a bonus; the deal message already went out
        log.info("album not sent", exc_info=True)


def send_reminders(tg: Telegram, state: State, now: float) -> None:
    """ONE short reminder ~60 min before a stored deal expires, then forget it; expired ones are
    dropped. A failed send keeps the reminder for the next run."""
    keep = []
    for r in state.data.get("reminders", []):
        left = r["exp"] - now
        if left <= 0:
            continue
        if left <= REMIND_BEFORE:
            exp = datetime.fromtimestamp(r["exp"], VN)
            kind = f"⏰ Sắp hết hạn · {esc(r.get('tag', ''))}{esc(r.get('channel', ''))}".rstrip(
                " ·"
            )
            text = (
                f"{header(kind)}\n"
                f"Còn ~{max(1, int(left // 60))} phút (đến {exp:%H:%M})\n"
                f"{esc(r['line'])}\n{esc(r['link'])}"
            )
            try:
                tg.send(text)
                continue  # sent -> drop
            except httpx.HTTPError as e:
                log.warning("reminder not sent: %s", e)
        keep.append(r)
    state.data["reminders"] = keep[-REMINDER_MAX:]


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


# ---------- follow words (/theodoi), blocked terms (/chan), trusted brands (/tincay) ----------


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def cmd_theodoi(tg: Telegram, state: State, args, text: str) -> str:
    word = _norm(text)
    if not word:
        return "Dùng: /theodoi bàn phím akko"
    watch = state.data.setdefault("watch", [])
    if word not in watch:
        watch.append(word)
    return f"🔥 Đang theo dõi “{esc(word)}”: deal có từ này luôn được gửi (🔥, gửi trước)."


def cmd_botheodoi(tg: Telegram, state: State, args, text: str) -> str:
    word = _norm(text)
    if not word:
        return "Dùng: /botheodoi bàn phím akko"
    watch = state.data.get("watch", [])
    if word not in watch:
        return f"Chưa theo dõi “{esc(word)}”. Xem danh sách: /dstheodoi"
    watch.remove(word)
    return f"✅ Đã bỏ theo dõi “{esc(word)}”."


def cmd_dstheodoi(tg: Telegram, state: State, args, text: str) -> str:
    watch = state.data.get("watch", [])
    if not watch:
        return "🔥 Chưa theo dõi từ nào. Thêm: /theodoi bàn phím akko"
    return "🔥 Đang theo dõi: " + esc(", ".join(watch))


def _toggle(state: State, add_key: str, del_key: str, word: str, add: bool) -> None:
    """add=True: put in the add list and un-delete; add=False: delete (even a config default)."""
    adds, dels = state.data.setdefault(add_key, []), state.data.setdefault(del_key, [])
    keep, drop = (adds, dels) if add else (dels, adds)
    if word in drop:
        drop.remove(word)
    if word not in keep:
        keep.append(word)


def cmd_chan(tg: Telegram, state: State, args, text: str) -> str:
    word = _norm(text)
    if not word:
        return "Dùng: /chan hàng fake"
    _toggle(state, "block_add", "block_del", word, add=True)
    return f"🚫 Đã chặn: deal chứa “{esc(word)}” sẽ không được gửi."


def cmd_bochan(tg: Telegram, state: State, args, text: str) -> str:
    word = _norm(text)
    if not word:
        return "Dùng: /bochan nhái"
    _toggle(state, "block_del", "block_add", word, add=True)  # block_del wins over config list
    return f"✅ Đã bỏ chặn “{esc(word)}”."


def cmd_dschan(tg: Telegram, state: State, args, text: str) -> str:
    words = state.data.get("_cfg_blocked") or effective_list(
        DEFAULT_BLOCKED, state, "block_add", "block_del"
    )
    return "🚫 Đang chặn: " + (esc(", ".join(words)) if words else "chưa có từ nào")


def cmd_tincay(tg: Telegram, state: State, args, text: str) -> str:
    word = _norm(text)
    if not word:
        return "Dùng: /tincay logitech"
    _toggle(state, "trust_add", "trust_del", word, add=True)
    return f"✅ Thêm hãng tin cậy “{esc(word)}”: deal của hãng này được cộng điểm ⭐ và gắn ✅."


def cmd_botincay(tg: Telegram, state: State, args, text: str) -> str:
    word = _norm(text)
    if not word:
        return "Dùng: /botincay logitech"
    _toggle(state, "trust_del", "trust_add", word, add=True)
    return f"🚫 Đã bỏ hãng “{esc(word)}” khỏi danh sách tin cậy."


def cmd_dstincay(tg: Telegram, state: State, args, text: str) -> str:
    words = state.data.get("_cfg_trusted") or effective_list(
        DEFAULT_TRUSTED, state, "trust_add", "trust_del"
    )
    return "✅ Hãng tin cậy: " + (esc(", ".join(words)) if words else "chưa có")
