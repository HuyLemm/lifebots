"""Stuff bot extras: follow words, price memory, score, expiry reminders, albums, filters."""

import argparse
import time
from datetime import datetime, timedelta

import httpx
import pytest

from shared.core import State
from shared.feeds import VN
from stuff import channels
from stuff.channels import (
    format_deal,
    name_key,
    parse_channel,
    parse_expiry,
    price_note,
    price_to_vnd,
    product_deal,
    remember_price,
    score_deal,
    word_in,
)

HEADER_START = "📍 <b>Thông báo lúc "


class FakeTG:
    name = "stuff"
    dry = False
    chat_id = "1"

    def __init__(self):
        self.sent: list[str] = []
        self.photos: list = []
        self.albums: list[list[str]] = []
        self.fail = False

    def send(self, text, buttons=None, photo=None):
        if self.fail:
            raise httpx.HTTPError("telegram down")
        self.sent.append(text)
        self.photos.append(photo)

    def send_album(self, photos):
        self.albums.append(photos)

    def safe_send(self, text):
        self.sent.append(text)


def args(now=False):
    return argparse.Namespace(now=now)


def page(*posts):
    """posts: (id, text, [image urls])"""
    out = []
    for pid, text, imgs in posts:
        photos = "".join(
            f'<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'{u}\')"></a>'
            for u in imgs
        )
        out.append(
            f'<div data-post="{pid}">{photos}<div class="tgme_widget_message_text js-message_text">'
            f'{text}</div><time datetime="2026-11-26T05:00:00+00:00"></time></div>'
        )
    return "".join(out)


def patch_pages(monkeypatch, pages):
    monkeypatch.setattr(
        channels.httpx,
        "get",
        lambda url, **k: httpx.Response(
            200, text=pages[url.rsplit("/", 1)[1]], request=httpx.Request("GET", url)
        ),
    )


def cfg(**extra):
    return {
        "channels": [{"name": "A", "handle": "a"}],
        "channel_platforms": ["Lazada"],
        "channel_keywords": ["bàn phím"],
        **extra,
    }


def start(tmp_path, monkeypatch, **extra):
    """Seeded state + fake TG, channel 'a' returning the mutable pages['a']."""
    pages = {"a": page(("a/1", "x", []))}
    patch_pages(monkeypatch, pages)
    st, tg = State(tmp_path), FakeTG()
    channels.check_channels(tg, st, args(), cfg(**extra))  # seeding
    return pages, st, tg


# ---------- 28: price parsing + memory ----------


@pytest.mark.parametrize(
    ("text", "vnd"),
    [
        ("595K", 595_000),
        ("1tr2", 1_200_000),
        ("1.299k", 1_299_000),
        ("1,2tr", 1_200_000),
        ("2,4 tr", 2_400_000),
        ("199.000đ", 199_000),
        ("1.299.000đ", 1_299_000),
        ("150 nghìn", 150_000),
        ("1,5k", 1_500),
        ("1tr25", 1_250_000),
        ("~3xxK", None),
        ("5đ", None),
        ("", None),
        (None, None),
    ],
)
def test_price_to_vnd(text, vnd):
    assert price_to_vnd(text) == vnd


def test_name_key_ignores_price_and_percent():
    a = {"lines": ["Bàn phím cơ Aula F75 giá 595K giảm 40%"]}
    b = {"lines": ["Bàn phím cơ Aula F75 chỉ 549K giảm 45%"]}
    assert name_key(a) == name_key(b) != ""


def test_price_note_cheaper_dearer_and_equal():
    mem: dict = {}
    assert price_note(mem, "k", 500_000) == ("", False)  # nothing known yet
    remember_price(mem, "k", 600_000, 1000.0)
    remember_price(mem, "k", 650_000, 2000.0)
    note, low = price_note(mem, "k", 550_000)
    assert low and "📉 Rẻ hơn lần trước (650K → 550K)" in note
    note, low = price_note(mem, "k", 700_000)
    assert not low and note == "ℹ️ Lần trước: 650K"
    assert price_note(mem, "k", 650_000) == ("", False)
    for i in range(20):
        remember_price(mem, "k", 100_000 + i, 3000.0 + i)
    assert len(mem["k"]) <= channels.MEMORY_PER_PRODUCT


def test_memory_pruned_by_age_and_size():
    now = 10_000_000_000.0
    mem = {"old": [[now - channels.MEMORY_TTL - 5, 1000]], "fresh": [[now - 10, 2000]]}
    channels.prune_memory(mem, now)
    assert list(mem) == ["fresh"]
    big = {f"p{i}": [[now - i, 5000]] for i in range(channels.MEMORY_MAX_PRODUCTS + 50)}
    channels.prune_memory(big, now)
    assert len(big) == channels.MEMORY_MAX_PRODUCTS and "p0" in big  # newest kept


def test_second_sighting_gets_memory_line_and_never_blocks(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)
    pages["a"] = page(
        ("a/1", "x", []), ("a/2", "Bàn phím cơ Aula F75 giá 600K https://s.lazada.vn/1", [])
    )
    channels.check_channels(tg, st, args(), cfg())
    assert len(tg.sent) == 1 and "Lần trước" not in tg.sent[0]
    pages["a"] += page(("a/3", "Bàn phím cơ Aula F75 chỉ 450K https://s.lazada.vn/2", []))
    channels.check_channels(tg, st, args(), cfg())
    assert len(tg.sent) == 2
    assert "📉 Rẻ hơn lần trước (600K → 450K)" in tg.sent[1]
    assert tg.sent[1].startswith(HEADER_START)


# ---------- 29/33: score, trusted brands, min_score ----------


def test_score_components():
    d = {"discount": 60, "price": "1K", "lines": ["x"]}
    assert score_deal(d) == 2
    assert score_deal({**d, "discount": 30}) == 1
    assert score_deal({**d, "discount": None}) == 0
    assert score_deal(d, new_low=True, trusted=True, watched=True) == 5
    assert score_deal(d, new_low=True, trusted=True, watched=True) <= 5


def test_word_in_is_whole_word():
    assert word_in("Cửa hàng HP chính hãng", "hp")
    assert not word_in("shop uy tín", "hp")
    assert word_in("Bàn phím Akko 3068", "akko")


def test_trusted_brand_tag_and_stars(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)
    pages["a"] = page(
        ("a/1", "x", []),
        ("a/2", "Bàn phím cơ Akko 3068 giá 800K giảm 50% https://s.lazada.vn/1", []),
    )
    channels.check_channels(tg, st, args(), cfg())
    assert "✅ akko" in tg.sent[0] and "⭐⭐⭐" in tg.sent[0]  # 50% (+2) + brand (+1)


def test_min_score_default_drops_nothing_and_can_be_set(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)
    pages["a"] = page(
        ("a/1", "x", []), ("a/2", "Bàn phím cơ lạ giá 300K https://s.lazada.vn/1", [])
    )
    channels.check_channels(tg, st, args(), cfg())
    assert len(tg.sent) == 1  # score 0 but min_score default 0 -> still sent

    (tmp_path / "second").mkdir()
    pages2, st2, tg2 = start(tmp_path / "second", monkeypatch)
    pages2["a"] = page(
        ("a/1", "x", []), ("a/2", "Bàn phím cơ lạ giá 300K https://s.lazada.vn/1", [])
    )
    channels.check_channels(tg2, st2, args(), cfg(min_score=2))
    assert tg2.sent == [] and "a/2" in st2.data["channel_posts"]  # dropped on purpose


# ---------- 27: follow words ----------


def test_follow_word_sends_unmatched_deal_hot_and_first(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)
    channels.cmd_theodoi(tg, st, args(), "Chuột Logitech")
    assert "chuột logitech" in st.data["watch"]
    pages["a"] = page(
        ("a/1", "x", []),
        ("a/2", "Bàn phím cơ Aula giá 500K giảm 10% https://s.lazada.vn/1", []),
        ("a/3", "Chuột Logitech G102 giá 300K https://s.lazada.vn/2", []),  # no keyword match
    )
    channels.check_channels(tg, st, args(), cfg())
    assert len(tg.sent) == 2
    assert "🔥 🛍 Deal" in tg.sent[0] and "Logitech" in tg.sent[0]  # hot goes first
    assert all(m.startswith(HEADER_START) for m in tg.sent)  # header still first
    assert "🔥" not in tg.sent[1]


def test_follow_commands_list_and_remove(tmp_path):
    st, tg = State(tmp_path), FakeTG()
    assert "Chưa theo dõi" in channels.cmd_dstheodoi(tg, st, args(), "")
    channels.cmd_theodoi(tg, st, args(), "  Akko  ")
    assert "akko" in channels.cmd_dstheodoi(tg, st, args(), "")
    channels.cmd_botheodoi(tg, st, args(), "akko")
    assert st.data["watch"] == []
    assert "Dùng:" in channels.cmd_theodoi(tg, st, args(), "")


# ---------- 33: blocked terms ----------


def test_blocked_default_and_user_terms(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)
    pages["a"] = page(
        ("a/1", "x", []),
        ("a/2", "Bàn phím cơ super fake 1:1 giá 200K https://s.lazada.vn/1", []),
        ("a/3", "Bàn phím cơ Newmen giá 400K https://s.lazada.vn/2", []),
    )
    channels.check_channels(tg, st, args(), cfg())
    assert len(tg.sent) == 1 and "Newmen" in tg.sent[0]
    assert "a/2" in st.data["channel_posts"]  # blocked = marked seen, not retried

    channels.cmd_chan(tg, st, args(), "newmen2")
    channels.cmd_bochan(tg, st, args(), "super fake")  # un-block a config default
    assert "newmen2" in channels.blocked_terms(cfg(), st)
    assert "super fake" not in channels.blocked_terms(cfg(), st)
    assert "newmen2" in channels.cmd_dschan(tg, st, args(), "")


def test_trusted_commands(tmp_path):
    st, tg = State(tmp_path), FakeTG()
    channels.cmd_tincay(tg, st, args(), "Newmen")
    channels.cmd_botincay(tg, st, args(), "apple")
    brands = channels.trusted_brands(cfg(), st)
    assert "newmen" in brands and "apple" not in brands and "logitech" in brands
    assert "newmen" in channels.cmd_dstincay(tg, st, args(), "")


# ---------- 30: expiry ----------

POSTED = datetime(2026, 11, 26, 14, 0, tzinfo=VN)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sale đến 23h", datetime(2026, 11, 26, 23, 0, tzinfo=VN)),
        ("Sale đến 23:59 hôm nay", datetime(2026, 11, 26, 23, 59, tzinfo=VN)),
        ("Giá sốc đến 23h30", datetime(2026, 11, 26, 23, 30, tzinfo=VN)),
        ("Từ 12h đến 18h", datetime(2026, 11, 26, 18, 0, tzinfo=VN)),
        ("Hết ngày 30/11", datetime(2026, 11, 30, 23, 59, tzinfo=VN)),
        ("Đến hết 30/11 nhé", datetime(2026, 11, 30, 23, 59, tzinfo=VN)),
        ("Sale đến 23h ngày 28/11", datetime(2026, 11, 28, 23, 0, tzinfo=VN)),
        ("Chỉ hôm nay thôi", datetime(2026, 11, 26, 23, 59, tzinfo=VN)),
        ("Còn 3 giờ", datetime(2026, 11, 26, 17, 0, tzinfo=VN)),
        ("Còn 1h30", datetime(2026, 11, 26, 15, 30, tzinfo=VN)),
        ("Còn 45 phút", datetime(2026, 11, 26, 14, 45, tzinfo=VN)),
        ("Hạn 5/1", datetime(2027, 1, 5, 23, 59, tzinfo=VN)),
        ("Giảm đến 50% bàn phím", None),
        ("Sale đến 10h", None),  # 10h already past when posted at 14h
        ("Không có hạn", None),
        ("đến 25h", None),
    ],
)
def test_parse_expiry(text, expected):
    assert parse_expiry(text, POSTED) == expected


def test_expiry_shown_and_reminder_sent_once_then_dropped(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)
    soon = datetime.now(VN) + timedelta(hours=3)
    hour = f"{soon:%H}h{soon:%M}"
    post_time = datetime.now(VN).strftime("%Y-%m-%dT%H:%M:%S%z")
    pages["a"] = (
        page(("a/1", "x", []))
        + f'<div data-post="a/2"><div class="tgme_widget_message_text">Bàn phím cơ Aula giá 500K '
        f'đến {hour} <a href="https://s.lazada.vn/1">link</a></div><time datetime="{post_time[:-2]}:{post_time[-2:]}"></time></div>'
    )
    channels.check_channels(tg, st, args(), cfg())
    assert "⏳ Hết hạn" in tg.sent[0]
    assert len(st.data["reminders"]) == 1  # >60 min away: stored, not sent yet
    n = len(tg.sent)

    channels.send_reminders(tg, st, time.time())
    assert len(tg.sent) == n  # still > 60 min

    exp = st.data["reminders"][0]["exp"]
    channels.send_reminders(tg, st, exp - 1800)  # 30 min before
    assert len(tg.sent) == n + 1
    assert tg.sent[-1].startswith(HEADER_START) and "⏰ Sắp hết hạn" in tg.sent[-1]
    assert "Bàn phím cơ Aula" in tg.sent[-1] and "https://s.lazada.vn/1" in tg.sent[-1]
    assert st.data["reminders"] == []  # only ONE reminder
    channels.send_reminders(tg, st, exp - 1500)
    assert len(tg.sent) == n + 1


def test_expired_reminders_are_dropped_and_failed_send_is_retried(tmp_path):
    st, tg = State(tmp_path), FakeTG()
    base = {"line": "Bàn phím", "link": "https://x", "tag": "[Lazada] ", "channel": "A"}
    st.data["reminders"] = [{**base, "exp": 1000.0}, {**base, "exp": 5000.0}]
    tg.fail = True
    channels.send_reminders(tg, st, 4000.0)  # first expired -> dropped; second due but TG down
    assert [r["exp"] for r in st.data["reminders"]] == [5000.0]
    tg.fail = False
    channels.send_reminders(tg, st, 4100.0)
    assert len(tg.sent) == 1 and st.data["reminders"] == []


def test_reminder_cap(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)
    far = time.time() + 86400
    st.data["reminders"] = [
        {"line": "x", "link": "l", "tag": "", "channel": "A", "exp": far + i} for i in range(300)
    ]
    channels.send_reminders(tg, st, time.time())
    assert len(st.data["reminders"]) <= channels.REMINDER_MAX


# ---------- 32: albums ----------


def test_parse_channel_collects_all_images_in_order():
    html = page(
        ("a/1", "Bàn phím giá 1K", ["https://i/1.jpg", "https://i/2.jpg", "https://i/1.jpg"])
    )
    post = parse_channel(html)[0]
    assert post["images"] == ["https://i/1.jpg", "https://i/2.jpg"]
    assert post["image"] == "https://i/1.jpg"
    assert parse_channel(page(("a/2", "Bàn phím giá 1K", [])))[0]["image"] is None


def test_album_sent_after_first_photo_max_four_extras(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)
    imgs = [f"https://i/{n}.jpg" for n in range(8)]
    pages["a"] = page(
        ("a/1", "x", []), ("a/2", "Bàn phím cơ Aula giá 500K https://s.lazada.vn/1", imgs)
    )
    channels.check_channels(tg, st, args(), cfg())
    assert tg.photos == ["https://i/0.jpg"]
    assert tg.albums == [imgs[1:5]]  # at most 4 extras


def test_one_extra_photo_is_not_an_album(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)
    pages["a"] = page(
        ("a/1", "x", []),
        (
            "a/2",
            "Bàn phím cơ Aula giá 500K https://s.lazada.vn/1",
            ["https://i/0.jpg", "https://i/1.jpg"],
        ),
    )
    channels.check_channels(tg, st, args(), cfg())
    assert tg.albums == [] and len(tg.sent) == 1


def test_album_failure_never_loses_the_deal(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)

    def boom(photos):
        raise RuntimeError("album down")

    tg.send_album = boom
    pages["a"] = page(
        ("a/1", "x", []),
        (
            "a/2",
            "Bàn phím cơ Aula giá 500K https://s.lazada.vn/1",
            [f"https://i/{n}.jpg" for n in range(4)],
        ),
    )
    channels.check_channels(tg, st, args(), cfg())
    assert len(tg.sent) == 1 and "a/2" in st.data["channel_posts"]


# ---------- reliability properties are intact ----------


def test_best_scored_first_and_outage_keeps_posts_unseen(tmp_path, monkeypatch):
    pages, st, tg = start(tmp_path, monkeypatch)
    pages["a"] = page(
        ("a/1", "x", []),
        ("a/2", "Bàn phím cơ Newmen giá 300K https://s.lazada.vn/1", []),  # score 0
        ("a/3", "Bàn phím cơ Akko giá 800K giảm 60% https://s.lazada.vn/2", []),  # 2 + brand
    )
    tg.fail = True
    with pytest.raises(httpx.HTTPError):
        channels.check_channels(tg, st, args(), cfg())
    assert "a/2" not in st.data["channel_posts"] and "a/3" not in st.data["channel_posts"]
    tg.fail = False
    channels.check_channels(tg, st, args(), cfg())
    assert len(tg.sent) == 2 and "Akko" in tg.sent[0]  # best score first
    assert all(m.startswith(HEADER_START) for m in tg.sent)


def test_format_deal_stays_backward_compatible_and_short():
    deal = {"lines": ["Bàn phím"], "price": "595K", "discount": None}
    msg = format_deal("Lazada", "G", deal, "http://x")
    assert msg.startswith(HEADER_START) and "⭐" not in msg and "⏳" not in msg
    rich = format_deal(
        "Lazada", "G", deal, "http://x", hot=True, stars=3, brand="akko",
        expiry=datetime.now(VN) + timedelta(hours=2), note="📉 Rẻ hơn lần trước (1 → 0)",
    )  # fmt: skip
    assert rich.startswith(HEADER_START) and "🔥 🛍 Deal" in rich and "⭐⭐⭐ · ✅ akko" in rich
    assert len(rich.splitlines()) <= 8


def test_product_deal_unchanged_for_vouchers():
    assert product_deal({"text": "Mã giảm 50k đơn 200k", "links": []}) is None


def test_name_key_uses_product_line_not_the_teaser():
    a = {"lines": ["Giá hời | chỉ từ 241.732đ", "Sạc dự phòng 50000mah PD20W cho điện thoại"]}
    b = {"lines": ["Săn ngay | 257.260đ", "Bộ Keycap Valorant 14 phím PBT cho game thủ"]}
    assert name_key(a) != name_key(b)
    c = {"lines": ["Giá hời | chỉ từ 199.000đ", "Sạc dự phòng 50000mah PD20W cho điện thoại"]}
    assert name_key(a) == name_key(c)  # same product, other price/teaser


def test_chi_tu_price_is_a_product_price_but_bare_tu_is_a_voucher():
    from stuff.channels import extract_price

    assert extract_price("🔥 Lụm vội | 💰 chỉ từ 1.620.000đ\n📦 Bàn phím AULA F108") == "1.620.000đ"
    assert extract_price("Giảm 20k cho đơn từ 150k") is None


def test_deal_link_prefers_marketplace_then_outside_link_never_telegram():
    from stuff.channels import deal_link

    post = {
        "id": "c/5",
        "links": ["https://t.me/c?q=%23x", "https://shorten.asia/abc", "https://s.shopee.vn/z"],
    }
    assert deal_link(post) == "https://s.shopee.vn/z"
    assert (
        deal_link({**post, "links": ["https://t.me/c?q=%23x", "https://shorten.asia/abc"]})
        == "https://shorten.asia/abc"
    )
    assert deal_link({**post, "links": ["https://t.me/c?q=%23x"]}) == "https://t.me/c/5"


def test_deal_link_ignores_autolinked_shop_names():
    from stuff.channels import deal_link

    post = {"id": "c/9", "links": ["http://Store.vn/", "https://shorten.asia/kfCKmeVN"]}
    assert deal_link(post) == "https://shorten.asia/kfCKmeVN"
    assert deal_link({**post, "links": ["http://Store.vn/"]}) == "https://t.me/c/9"


def test_new_channel_is_seeded_silently_not_flooded(tmp_path, monkeypatch):
    """Adding a channel to config.toml must not dump its old posts; its NEXT posts do arrive."""
    import httpx

    from stuff import channels

    def page(*posts):
        return "".join(
            f'<div data-post="{pid}"><div class="tgme_widget_message_text js-message_text">{t}</div></div>'
            for pid, t in posts
        )

    old_deal = "Giày sneaker nam chỉ 199K https://s.lazada.vn/old"
    pages = {"a": page(("a/1", "x")), "new": page(("new/1", old_deal), ("new/2", old_deal + "2"))}
    monkeypatch.setattr(
        channels.httpx,
        "get",
        lambda url, **k: httpx.Response(
            200, text=pages[url.rsplit("/", 1)[1]], request=httpx.Request("GET", url)
        ),
    )
    cfg = {
        "channels": [{"name": "A", "handle": "a"}],
        "channel_platforms": ["Lazada"],
        "channel_keywords": ["giày"],
    }

    class Args:
        now = False

    class TG:
        dry, chat_id, name = False, "1", "stuff"

        def __init__(self):
            self.sent = []

        def send(self, text, buttons=None, photo=None):
            self.sent.append(text)

        def send_album(self, photos):
            pass

    st, tg = State(tmp_path), TG()
    channels.check_channels(tg, st, Args(), cfg)  # first run: seeding
    # legacy state without `known_channels` must be upgraded without re-seeding channel "a"
    st.data.pop("known_channels")
    pages["a"] = page(("a/1", "x"), ("a/2", "Giày sneaker nữ chỉ 299K https://s.lazada.vn/a2"))
    channels.check_channels(tg, st, Args(), cfg)
    assert len(tg.sent) == 1 and "299" in tg.sent[0]

    cfg["channels"].append({"name": "N", "handle": "new"})  # a channel is added later
    channels.check_channels(tg, st, Args(), cfg)
    assert len(tg.sent) == 1  # its old posts were only remembered
    pages["new"] = page(
        ("new/1", old_deal), ("new/2", old_deal + "2"), ("new/3", "Giày chạy bộ chỉ 399K https://s.lazada.vn/n3")
    )
    channels.check_channels(tg, st, Args(), cfg)
    assert len(tg.sent) == 2 and "399" in tg.sent[1]  # but the next post is delivered
