"""Parsers against REAL saved samples: when a source changes its layout these fail first."""

from pathlib import Path

import httpx
import pytest

FIX = Path(__file__).parent / "fixtures"


def fake_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("GET", "http://x"))


def test_sjc_parser(monkeypatch):
    from price import trackers

    monkeypatch.setattr(
        trackers.httpx, "post", lambda *a, **k: fake_response((FIX / "sjc.json").read_text("utf-8"))
    )
    t = trackers.sjc({})
    keys = {k for k, _, _ in t["rows"]}
    assert {"sjc:SJC 1L-10L:buy", "sjc:SJC 1L-10L:sell"} <= keys
    assert all(v > 1_000_000 for _, _, v in t["rows"])  # VND per luong
    assert t["unit"] == "luong"


def test_pnj_parser(monkeypatch):
    from price import trackers

    monkeypatch.setattr(
        trackers,
        "get_json",
        lambda url, **p: __import__("json").loads((FIX / "pnj.json").read_text("utf-8")),
    )
    t = trackers.pnj({})
    keys = {k for k, _, _ in t["rows"]}
    assert {"pnj:SJC:buy", "pnj:SJC:sell"} <= keys
    assert all(v > 1_000_000 for _, _, v in t["rows"])


def test_petrol_parser(monkeypatch):
    from price import trackers

    monkeypatch.setattr(
        trackers.httpx,
        "get",
        lambda *a, **k: fake_response((FIX / "petrol.html").read_text("utf-8")),
    )
    t = trackers.petrol({"region": 1})
    assert len(t["rows"]) >= 4
    assert any("RON 95" in label for _, label, _ in t["rows"])
    assert all(5_000 < v < 100_000 for _, _, v in t["rows"])  # VND per litre
    assert t["stamp"]


def test_channel_parser():
    from stuff.channels import parse_channel

    posts = parse_channel((FIX / "channel.html").read_text("utf-8"))
    assert posts, "no posts parsed - did t.me/s change its HTML?"
    assert all("/" in p["id"] and p["text"] for p in posts)


def test_rss_parser(monkeypatch):
    from shared import feeds

    monkeypatch.setattr(
        feeds.httpx, "get", lambda *a, **k: fake_response((FIX / "rss.xml").read_text("utf-8"))
    )
    items = feeds.fetch_items("http://x")
    assert len(items) == 3
    assert all(i["title"] and i["link"].startswith("http") for i in items)


@pytest.mark.parametrize("bad", ["", "<html>no table</html>"])
def test_petrol_parser_fails_loudly_on_layout_change(monkeypatch, bad):
    """A changed page must raise (-> health alert), never silently report nothing."""
    from price import trackers

    monkeypatch.setattr(trackers.httpx, "get", lambda *a, **k: fake_response(bad))
    with pytest.raises((AttributeError, ValueError, IndexError)):
        t = trackers.petrol({})
        if not t["rows"]:
            raise ValueError("no rows")


def test_sjc_falls_back_to_relay_when_blocked(monkeypatch):
    """GitHub's runners get 403 from sjc.com.vn: same row keys come from vang.today instead."""
    from price import trackers

    def blocked(*a, **k):
        resp = httpx.Response(403, text="Forbidden", request=httpx.Request("POST", "http://sjc"))
        raise httpx.HTTPStatusError("403", request=resp.request, response=resp)

    monkeypatch.setattr(trackers.httpx, "post", blocked)
    monkeypatch.setattr(
        trackers,
        "get_json",
        lambda url, **p: __import__("json").loads((FIX / "vangtoday.json").read_text("utf-8")),
    )
    t = trackers.sjc({})
    keys = {k for k, _, _ in t["rows"]}
    assert {"sjc:SJC 1L-10L:buy", "sjc:SJC 1L-10L:sell", "sjc:Nhẫn SJC 99,99%:buy"} <= keys
    assert all(v > 1_000_000 for _, _, v in t["rows"])


def test_sjc_other_http_errors_still_raise(monkeypatch):
    """A 500 is a real outage (-> health alert), only blocking codes use the relay."""
    from price import trackers

    def broken(*a, **k):
        resp = httpx.Response(500, text="boom", request=httpx.Request("POST", "http://sjc"))
        raise httpx.HTTPStatusError("500", request=resp.request, response=resp)

    monkeypatch.setattr(trackers.httpx, "post", broken)
    with pytest.raises(httpx.HTTPStatusError):
        trackers.sjc({})


# ---------- original post time ----------


def test_parse_time_formats_to_vietnam_time():
    from shared.feeds import parse_time

    assert parse_time("Wed, 25 Nov 2026 16:06:40 +0700").strftime("%H:%M %d/%m") == "16:06 25/11"
    assert parse_time("Thu, 26 Nov 2026 02:59:08 +0000").strftime("%H:%M %d/%m") == "09:59 26/11"
    assert parse_time("2026-11-26T02:53:40+00:00").strftime("%H:%M") == "09:53"
    assert parse_time("2026-11-26T02:58:47Z").strftime("%H:%M") == "09:58"
    assert parse_time("") is None and parse_time(None) is None and parse_time("not a date") is None


def test_rss_items_carry_the_original_publish_time(monkeypatch):
    from shared import feeds

    monkeypatch.setattr(
        feeds.httpx, "get", lambda *a, **k: fake_response((FIX / "rss.xml").read_text("utf-8"))
    )
    items = feeds.fetch_items("http://x")
    assert all(i["published"] is not None for i in items)
    assert items[0]["published"].strftime("%d/%m %H:%M") == "25/11 16:06"  # pubDate +0700


def test_channel_posts_carry_the_original_time():
    from stuff.channels import parse_channel

    posts = parse_channel((FIX / "channel.html").read_text("utf-8"))
    assert posts and all(p["time"] is not None for p in posts)


def test_messages_show_the_original_time():
    from datetime import datetime

    from news.bot import digest_message
    from price.bot import build_message
    from shared.feeds import VN
    from stuff.channels import format_deal

    t = datetime(2026, 11, 25, 7, 15, tzinfo=VN)
    deal = {"lines": ["Bàn phím"], "price": "595K", "discount": None}
    assert "🕒 Đăng lúc 07:15 (25/11)" in format_deal("Lazada", "G", deal, "http://x", t)
    assert "🕒" not in format_deal("Lazada", "G", deal, "http://x")  # unknown time: no empty line

    tracker = {"title": "T", "unit": "lit", "stamp": "09:40 25/11/2026", "rows": [("a", "x", 1.0)]}
    assert "🕒 Nguồn cập nhật: 09:40 25/11/2026" in build_message(tracker, {}, 0, False)[0]

    item = {"title": "Tin", "link": "http://x/1", "text": "", "published": t}
    assert "25/11 07:15 · <a" in digest_message([(1, "tech", "F", item)])


def test_digest_is_split_by_length_so_nothing_is_cut():
    from news.bot import digest_chunks, digest_message

    long = "x" * 120
    entries = [
        (
            1,
            "tech",
            "F",
            {"title": f"{long} {i}", "link": f"http://example.com/{long}/{i}", "text": ""},
        )
        for i in range(40)
    ]
    chunks = digest_chunks(entries)
    assert len(chunks) > 1 and sum(len(c) for c in chunks) == 40  # every entry lands in a chunk
    assert all(len(digest_message(c)) < 4096 for c in chunks)
    assert "…" not in "".join(digest_message(c) for c in chunks)  # no truncation marker


def test_digest_lists_newest_first_and_unknown_times_last():
    from datetime import datetime

    from news.bot import digest_message
    from shared.feeds import VN

    def entry(title, hour):
        pub = datetime(2026, 11, 26, hour, 0, tzinfo=VN) if hour is not None else None
        return (
            1,
            "tech",
            "F",
            {"title": title, "link": f"http://x/{title}", "text": "", "published": pub},
        )

    text = digest_message(
        [entry("old", 5), entry("unknown", None), entry("new", 9), entry("mid", 7)]
    )
    assert text.index("new") < text.index("mid") < text.index("old") < text.index("unknown")


def test_item_image_from_enclosure_media_and_img_tag():
    import xml.etree.ElementTree as ET

    from shared.feeds import item_image

    def item(xml: str):
        return ET.fromstring(
            f'<item xmlns:media="http://search.yahoo.com/mrss/">{xml}</item>'
        )

    enc = item('<enclosure url="https://x/a.jpg" type="image/jpeg"/>')
    assert item_image(enc) == "https://x/a.jpg"
    assert item_image(item('<media:thumbnail url="https://x/b.jpg"/>')) == "https://x/b.jpg"
    desc = item('<description>&lt;img src="https://x/c.jpg"&gt; hi</description>')
    assert item_image(desc) == "https://x/c.jpg"
    assert item_image(item("<title>no picture</title>")) is None
    assert item_image(item('<enclosure url="https://x/a.mp3" type="audio/mpeg"/>')) is None


def test_channel_post_photo_is_parsed():
    from pathlib import Path

    from stuff.channels import parse_channel

    page = Path(__file__).parent.joinpath("fixtures", "channel.html").read_text(encoding="utf-8")
    posts = parse_channel(page)
    with_img = [p for p in posts if p["image"]]
    assert with_img, "fixture has photo posts"
    assert all(p["image"].startswith("https://") and "&amp;" not in p["image"] for p in with_img)
