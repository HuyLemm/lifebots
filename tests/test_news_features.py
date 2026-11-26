"""News bot features: topic switches, blocked/priority words, read-later, AI summary (optional),
similar-headline dedupe, extra feed pages / new-source seeding, top_headlines."""

import argparse
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from news import bot
from shared.core import State, handle_update
from shared.feeds import VN, parse_time


class FakeTG:
    chat_id = "42"
    name = "news"
    dry = False

    def __init__(self):
        self.sent: list[str] = []
        self.buttons: list = []
        self.photos: list = []
        self.answered: list[str] = []

    def send(self, text, buttons=None, photo=None):
        self.sent.append(text)
        self.buttons.append(buttons)
        self.photos.append(photo)

    def safe_send(self, text):
        self.sent.append(text)

    def answer_callback(self, cid, text=""):
        self.answered.append(cid)


def args(now=False):
    return argparse.Namespace(now=now)


def item(title, n=None, text="", published=None, image=None):
    n = n if n is not None else abs(hash(title)) % 10**8
    return {"title": title, "link": f"http://x/{n}", "text": text, "published": published, "image": image}


@pytest.fixture
def feeds(monkeypatch):
    pages: dict[str, list] = {}

    def fake_fetch(url):
        if url not in pages:
            raise httpx.HTTPError("down")
        return [dict(i) for i in pages[url]]

    monkeypatch.setattr(bot, "fetch_items", fake_fetch)
    return pages


def make_state(tmp_path):
    (tmp_path / "s").mkdir(exist_ok=True)
    return State(tmp_path / "s")


def cfg_for(*feed_specs, **extra):
    return {"feeds": list(feed_specs), **extra}


def started(tg, st, cfg):
    bot.check(tg, st, args(), cfg)  # first run only remembers what exists
    tg.sent.clear()
    tg.buttons.clear()
    tg.photos.clear()


# ---------- #25 similar headlines ----------


def test_fold_and_sig_words_ignore_accents_and_filler():
    assert bot.fold("Vụ án ĐẠI GIA") == "vu an dai gia"
    w = bot.sig_words("Apple ra mắt iPhone 17 Pro Max tại Việt Nam")
    assert {"apple", "iphone", "17", "pro", "max", "viet", "nam"} <= w
    assert "tai" not in w and "va" not in bot.sig_words("a và b")  # filler words dropped


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Apple ra mắt iPhone 17 Pro Max tại Việt Nam", "iPhone 17 Pro Max chính thức ra mắt tại Việt Nam, Apple công bố giá"),
        ("Giá vàng SJC tăng lên 134 triệu đồng một lượng", "Vàng SJC tăng lên 134 triệu đồng/lượng"),
        ("Nga và Ukraine trao đổi tù binh quy mô lớn nhất", "Ukraine và Nga trao đổi tù binh quy mô lớn nhất từ đầu chiến sự"),
    ],
)
def test_same_story_in_other_words_is_similar(a, b):
    assert bot.is_similar(bot.sig_words(a), bot.sig_words(b))


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Apple ra mắt iPhone 17 Pro Max tại Việt Nam", "Samsung ra mắt Galaxy S26 Ultra tại Việt Nam"),
        ("Giá vàng SJC tăng lên 134 triệu đồng một lượng", "Giá xăng dầu giảm mạnh trong kỳ điều chỉnh chiều nay"),
        ("Khởi công tuyến cáp quang biển đầu tiên", "Khởi công tuyến metro số 2 tại TP HCM"),
        ("Bão số 5", "Bão số 5 đổ bộ"),  # too short to judge: not merged
    ],
)
def test_different_stories_are_not_similar(a, b):
    assert not bot.is_similar(bot.sig_words(a), bot.sig_words(b))


def test_similar_headline_from_second_outlet_is_sent_once(tmp_path, feeds):
    feeds["a"], feeds["b"] = [], []
    cfg = cfg_for(
        {"name": "A", "url": "a", "topic": "tech"}, {"name": "B", "url": "b", "topic": "tech"}
    )
    tg, st = FakeTG(), make_state(tmp_path)
    started(tg, st, cfg)
    feeds["a"] = [item("Apple ra mắt iPhone 17 Pro Max tại Việt Nam", 1)]
    feeds["b"] = [item("iPhone 17 Pro Max chính thức ra mắt tại Việt Nam, Apple công bố giá", 2)]
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1 and "Apple ra mắt iPhone" in tg.sent[0]
    # a LATER run: the similar headline is remembered for 48h
    feeds["b"].append(item("Apple ra mắt iPhone 17 Pro Max ở Việt Nam, giá từ 34 triệu", 3))
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1


# ---------- #19 topics, #20 blocked words, #21 priority words ----------


def run_cmd(st, tg, text):
    u = {"message": {"chat": {"id": 42}, "text": text}}
    handle_update(u, tg, st, args(), bot.COMMANDS, bot.CALLBACKS)


def test_topic_off_and_on_by_command(tmp_path, feeds):
    feeds["t"], feeds["c"] = [], []
    cfg = cfg_for(
        {"name": "T", "url": "t", "topic": "tech"}, {"name": "C", "url": "c", "topic": "crime"}
    )
    tg, st = FakeTG(), make_state(tmp_path)
    started(tg, st, cfg)
    run_cmd(st, tg, "/tat vụ án")
    assert st.data["off_topics"] == ["crime"]
    run_cmd(st, tg, "/tat vu an")  # no duplicates, accents optional
    assert st.data["off_topics"] == ["crime"]
    tg.sent.clear()
    feeds["t"] = [item("Tin công nghệ mới nhất hôm nay", 1)]
    feeds["c"] = [item("Khởi tố vụ án lừa đảo lớn", 2)]
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1 and "công nghệ" in tg.sent[0]
    bot.check(tg, st, args(), cfg)  # the off-topic item was marked seen, never sent later
    run_cmd(st, tg, "/bat vu an")
    tg.sent.clear()
    bot.check(tg, st, args(), cfg)
    assert tg.sent == []
    run_cmd(st, tg, "/chude")
    assert "✅ bật · ⚖️ Vụ án" in tg.sent[-1] and "✅ bật · 💻 Công nghệ" in tg.sent[-1]
    run_cmd(st, tg, "/tat linh tinh")
    assert "Dùng: /tat" in tg.sent[-1]


def test_blocked_words_always_win_and_are_accent_insensitive(tmp_path, feeds):
    feeds["t"] = []
    cfg = cfg_for({"name": "T", "url": "t", "topic": "tech"})
    tg, st = FakeTG(), make_state(tmp_path)
    started(tg, st, cfg)
    run_cmd(st, tg, "/chan bóng đá")
    run_cmd(st, tg, "/uutien messi")  # priority does NOT beat a block
    feeds["t"] = [
        item("Messi ghi bàn, bong da Argentina thắng lớn", 1),
        item("Một tin công nghệ bình thường", 2),
    ]
    bot.check(tg, st, args(), cfg)
    sent = [m for m in tg.sent if m.startswith("📍")]
    assert len(sent) == 1 and "công nghệ" in sent[0]
    run_cmd(st, tg, "/bochan bong da")
    assert st.data["block_words"] == []
    run_cmd(st, tg, "/tuchan")
    assert "chưa có" in tg.sent[-1]


def test_priority_words_go_first_with_fire_even_if_topic_off_or_feed_filtered(tmp_path, feeds):
    feeds["t"], feeds["w"] = [], []
    cfg = cfg_for(
        {"name": "T", "url": "t", "topic": "tech", "priority": 1},
        {"name": "W", "url": "w", "topic": "world", "priority": 2, "keywords": ["chiến sự"]},
    )
    tg, st = FakeTG(), make_state(tmp_path)
    started(tg, st, cfg)
    run_cmd(st, tg, "/uutien bitcoin")
    run_cmd(st, tg, "/tat the gioi")
    tg.sent.clear()
    feeds["t"] = [item("Tin công nghệ thường ngày", 1)]
    feeds["w"] = [
        item("Chiến sự leo thang ở Trung Đông", 2),  # topic off -> dropped
        item("Bitcoin vượt mốc 100.000 USD", 3),  # priority: bypasses topic off AND feed keywords
    ]
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 2
    assert "🔥 <b>Bitcoin vượt mốc" in tg.sent[0]  # first
    assert "Tin công nghệ thường ngày" in tg.sent[1] and "🔥" not in tg.sent[1]
    run_cmd(st, tg, "/dsuutien")
    assert "bitcoin" in tg.sent[-1]
    run_cmd(st, tg, "/bouutien bitcoin")
    assert st.data["prio_words"] == []


def test_every_message_still_starts_with_header_and_has_original_time(tmp_path, feeds):
    feeds["t"] = []
    cfg = cfg_for({"name": "T", "url": "t", "topic": "tech"})
    tg, st = FakeTG(), make_state(tmp_path)
    started(tg, st, cfg)
    when = datetime(2026, 11, 26, 7, 15, tzinfo=VN)
    feeds["t"] = [item("Tin có giờ đăng gốc", 1, published=when, image="http://img/1.jpg")]
    bot.check(tg, st, args(), cfg)
    assert tg.sent[0].startswith("📍 <b>Thông báo lúc")
    assert "🕒 Đăng lúc 07:15 (26/11)" in tg.sent[0]
    assert tg.photos[0] == "http://img/1.jpg"


# ---------- #22 read later ----------


def test_read_later_button_saves_and_lists(tmp_path, feeds):
    feeds["t"] = []
    cfg = cfg_for({"name": "T", "url": "t", "topic": "tech"})
    tg, st = FakeTG(), make_state(tmp_path)
    started(tg, st, cfg)
    feeds["t"] = [item("Bài đáng đọc sau", 7)]
    bot.check(tg, st, args(), cfg)
    button = tg.buttons[0][0][0]
    assert button["text"] == "📌 Đọc sau" and len(button["callback_data"].encode()) <= 64
    cq = {"callback_query": {"id": "9", "data": button["callback_data"], "message": {"chat": {"id": 42}}}}
    handle_update(cq, tg, st, args(), bot.COMMANDS, bot.CALLBACKS)
    handle_update(cq, tg, st, args(), bot.COMMANDS, bot.CALLBACKS)  # pressing twice: saved once
    assert [x["title"] for x in st.data["readlater"]] == ["Bài đáng đọc sau"]
    assert tg.answered == ["9", "9"]
    run_cmd(st, tg, "/doclai")
    assert tg.sent[-1].startswith("📍") and "Bài đáng đọc sau" in tg.sent[-1]
    run_cmd(st, tg, "/xoadoclai")
    assert st.data["readlater"] == []
    run_cmd(st, tg, "/doclai")
    assert "Chưa có tin nào" in tg.sent[-1]


def test_read_later_button_expires_gracefully_and_map_is_capped(tmp_path):
    st = make_state(tmp_path)
    tg = FakeTG()
    handle_update(
        {"callback_query": {"id": "1", "data": "rl:unknown", "message": {"chat": {"id": 42}}}},
        tg, st, args(), bot.COMMANDS, bot.CALLBACKS,
    )  # fmt: skip
    assert "Không tìm thấy" in tg.sent[-1]
    for i in range(350):
        bot.remember_for_button(st, f"t{i}", f"http://x/{i}", 1000.0 + i)
    assert len(st.data["rl_map"]) == 300
    bot.remember_for_button(st, "late", "http://x/late", 1000.0 + 4 * 24 * 3600)
    assert list(st.data["rl_map"]) == [bot.rl_id("http://x/late")]  # older than 3 days dropped


def test_readlater_list_is_capped_at_100_and_short(tmp_path):
    st = make_state(tmp_path)
    for i in range(130):
        st.data.setdefault("rl_map", {})[f"id{i}"] = {"t": f"Tin {i}", "l": f"http://x/{i}", "ts": 1e12}
        bot.cb_readlater(FakeTG(), st, args(), f"id{i}")
    assert len(st.data["readlater"]) == 100
    text = bot.readlater_text(st.data["readlater"])
    assert len(text) < 4096


def test_weekly_readlater_only_sunday_evening_once_and_if_not_empty(tmp_path):
    st, tg = make_state(tmp_path), FakeTG()
    sunday_eve = datetime(2026, 11, 29, 19, 30)  # 29/11/2026 is a Sunday
    assert sunday_eve.weekday() == 6
    bot.weekly_readlater(tg, st, sunday_eve)
    assert tg.sent == []  # empty list: nothing to remind
    st.data["readlater"] = [{"title": "T", "link": "http://x/1", "ts": 1}]
    bot.weekly_readlater(tg, st, sunday_eve.replace(hour=18))
    bot.weekly_readlater(tg, st, sunday_eve - timedelta(days=1))
    assert tg.sent == []  # before 19:00 / not Sunday
    bot.weekly_readlater(tg, st, sunday_eve)
    assert len(tg.sent) == 1 and tg.sent[0].startswith("📍")
    bot.weekly_readlater(tg, st, sunday_eve + timedelta(hours=1))
    assert len(tg.sent) == 1  # once a week
    bot.weekly_readlater(tg, st, sunday_eve + timedelta(days=7))
    assert len(tg.sent) == 2  # next Sunday again


# ---------- #23 AI summary ----------


def test_ai_off_by_default_never_calls_the_api(tmp_path, feeds, monkeypatch):
    feeds["t"] = []
    cfg = cfg_for({"name": "T", "url": "t", "topic": "tech"})
    tg, st = FakeTG(), make_state(tmp_path)
    started(tg, st, cfg)
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(1))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    feeds["t"] = [item("Tin bình thường", 1)]
    bot.check(tg, st, args(), cfg)
    assert calls == [] and "📝" not in tg.sent[0]


def test_ai_summary_added_truncated_and_failures_are_silent(tmp_path, feeds, monkeypatch):
    feeds["t"] = []
    cfg = cfg_for({"name": "T", "url": "t", "topic": "tech"}, ai={"enabled": True, "max_per_run": 2})
    tg, st = FakeTG(), make_state(tmp_path)
    started(tg, st, cfg)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    sent_requests = []

    class Resp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": self.text}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent_requests.append((url, headers, json, timeout))
        if len(sent_requests) == 2:
            raise httpx.ConnectError("boom")
        return Resp("Tóm tắt rất dài. " * 200)

    monkeypatch.setattr(httpx, "post", fake_post)
    feeds["t"] = [item("Tin một", 1), item("Tin hai khác hẳn", 2), item("Tin ba hoàn toàn khác", 3)]
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 3  # a failing summary never loses the story
    summaries = [m for m in tg.sent if "📝" in m]
    assert len(summaries) == 1 and all(len(m) <= 1024 for m in tg.sent)
    assert "…" in summaries[0]
    assert len(sent_requests) == 2  # max_per_run honoured (third story not even tried)
    url, headers, body, timeout = sent_requests[0]
    assert url == "https://api.anthropic.com/v1/messages" and timeout == 10
    assert headers["x-api-key"] == "sk-test-not-real" and headers["anthropic-version"] == "2023-06-01"
    assert body["model"] == "claude-haiku-5-5"
    assert "sk-test-not-real" not in "".join(tg.sent)


def test_ai_without_key_does_nothing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not call the API"))
    assert bot.ai_summary(item("x"), {}) is None


# ---------- #24/#26 sources, extra pages, new-source seeding ----------


def test_fetch_feed_merges_extra_urls_dedupes_sorts_and_limits(feeds):
    t = datetime(2026, 11, 26, 10, 0, tzinfo=VN)
    feeds["m"] = [item("old", 1, published=t - timedelta(hours=5)), item("new", 2, published=t)]
    feeds["p2"] = [item("old", 1, published=t - timedelta(hours=5)), item("older", 3, published=t - timedelta(days=1))]
    feed = {"name": "F", "url": "m", "extra_urls": ["p2", "dead"], "limit": 3}
    ok: set[str] = set()
    got = bot.fetch_feed(feed, ok)
    assert [i["title"] for i in got] == ["new", "old", "older"]  # newest first, deduped by link
    assert ok == {"F|m", "F|p2"}  # the dead extra url is ignored, not an error
    assert len(bot.fetch_feed({**feed, "limit": 2})) == 2
    with pytest.raises(httpx.HTTPError):  # the MAIN url failing still propagates (health alert)
        bot.fetch_feed({"name": "F", "url": "dead"})


def test_new_source_is_seeded_silently_not_flooded(tmp_path, feeds):
    feeds["m"], feeds["extra"] = [item("Tin cũ chính", 1)], []
    cfg = cfg_for({"name": "F", "url": "m", "topic": "tech"})
    tg, st = FakeTG(), make_state(tmp_path)
    started(tg, st, cfg)
    # the config grows: an extra page full of OLD stories + a brand-new feed
    feeds["extra"] = [item(f"Tin cũ ở trang hai số {i} khác nhau hoàn toàn", 100 + i) for i in range(5)]
    feeds["n"] = [item("Tin cũ của nguồn mới toanh", 200)]
    cfg = cfg_for(
        {"name": "F", "url": "m", "extra_urls": ["extra"], "topic": "tech"},
        {"name": "N", "url": "n", "topic": "tech"},
    )
    bot.check(tg, st, args(), cfg)
    assert tg.sent == []  # remembered, not sent
    feeds["extra"].append(item("Tin MỚI thật sự ở trang hai", 300))
    feeds["n"].append(item("Tin MỚI thật sự của nguồn mới", 301))
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 2  # but genuinely new stories flow normally afterwards


def test_old_state_is_upgraded_without_losing_known_sources(tmp_path, feeds):
    feeds["https://vnexpress.net/rss/so-hoa.rss"] = []
    cfg = cfg_for(
        {"name": "VnExpress Số hoá", "url": "https://vnexpress.net/rss/so-hoa.rss", "topic": "tech"}
    )
    tg, st = FakeTG(), make_state(tmp_path)
    st.data = {"started": True, "seen": {}, "titles": {}}  # state written by the older bot version
    feeds["https://vnexpress.net/rss/so-hoa.rss"] = [item("Tin mới của nguồn cũ", 1)]
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1  # a source we already followed is NOT silenced by the upgrade


def test_digest_over_zero_means_single_messages_and_config_is_valid():
    cfg = tomllib.loads(Path("news/config.toml").read_text(encoding="utf-8"))
    assert cfg["digest_over"] == 0 and cfg["ai"]["enabled"] is False
    assert 8 < len(cfg["feeds"]) <= 16
    for f in cfg["feeds"]:
        assert f["topic"] in bot.TOPIC_NAMES
        if f["topic"] != "tech":
            assert f.get("keywords"), f"{f['name']}: non-tech sources must be keyword-filtered"
    assert len({f["name"] for f in cfg["feeds"]}) == len(cfg["feeds"])


# ---------- top_headlines (morning digest contract) ----------


def test_top_headlines_newest_first_no_side_effects_errors_swallowed(feeds):
    t = datetime(2026, 11, 26, 10, 0, tzinfo=VN)
    feeds["a"] = [item("Cũ nhất", 1, published=t - timedelta(hours=9)), item("Mới nhất", 2, published=t)]
    feeds["b"] = [item("Ở giữa", 3, published=t - timedelta(hours=2))]
    cfg = cfg_for(
        {"name": "A", "url": "a", "topic": "tech", "priority": 1},
        {"name": "B", "url": "b", "topic": "tech", "priority": 1},
        {"name": "W", "url": "w", "topic": "world", "priority": 2},  # not tech: ignored
        {"name": "D", "url": "dead", "topic": "tech", "priority": 1},  # down: skipped
    )
    got = bot.top_headlines(cfg, 2)
    assert got == [("Mới nhất", "http://x/2"), ("Ở giữa", "http://x/3")]
    assert bot.top_headlines({"feeds": None}, 3) == []  # garbage config: swallowed


# ---------- shared/feeds additions ----------


def test_parse_time_understands_tuoitre_us_format_and_titles_are_unescaped(monkeypatch):
    dt = parse_time("11/26/2026 1:03:00 PM")
    assert dt is not None and (dt.hour, dt.minute, dt.day, dt.month) == (13, 3, 26, 11)
    assert dt.utcoffset() == timedelta(hours=7)
    from shared import feeds as feeds_mod

    xml = (
        b"<rss><channel><item><title>H&amp;igrave;nh &#7843;nh &amp;amp; tin</title>"
        b"<link>http://x/1</link></item></channel></rss>"
    )

    class R:
        content = xml

        def raise_for_status(self):
            pass

    monkeypatch.setattr(feeds_mod.httpx, "get", lambda *a, **k: R())
    assert feeds_mod.fetch_items("http://x")[0]["title"] == "Hình ảnh & tin"


def test_commands_have_usage_examples_for_help():
    for cmd, (_, desc) in bot.COMMANDS.items():
        assert cmd.startswith("/") and desc
    assert "ví dụ" in bot.COMMANDS["/tat"][1] and "rl:" in bot.CALLBACKS


def test_og_image_prefers_og_over_twitter_and_resolves_relative():
    from shared.feeds import og_image

    page = (
        '<html><head><meta name="twitter:image" content="https://x/tw.jpg">'
        "<meta content='/img/a&amp;b.jpg?w=1200' property='og:image'></head>"
    )
    assert og_image(page, "https://news.example/a/b") == "https://news.example/img/a&b.jpg?w=1200"
    assert og_image('<meta name="twitter:image" content="https://x/tw.jpg">') == "https://x/tw.jpg"
    assert og_image('<meta property="og:image" content="https://x/logo.svg">') is None
    assert og_image("<html><head><title>no picture</title></head>") is None


def test_article_image_reads_head_only_and_never_raises(monkeypatch):
    import httpx

    from shared import feeds

    html_page = b'<html><head><meta property="og:image" content="https://x/p.jpg"></head><body>' + b"x" * 10**6

    def fake_stream(method, url, **kw):
        class R:
            status_code, encoding, url = 200, "utf-8", httpx.URL("https://x/story")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def iter_bytes(self):
                yield from (html_page[i : i + 4096] for i in range(0, len(html_page), 4096))

        return R()

    monkeypatch.setattr(feeds.httpx, "stream", fake_stream)
    assert feeds.article_image("https://x/story") == "https://x/p.jpg"

    def boom(*a, **k):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(feeds.httpx, "stream", boom)
    assert feeds.article_image("https://x/story") is None  # a slow site must not break a news run


def test_news_without_feed_image_uses_the_article_picture(tmp_path, monkeypatch):
    from news import bot as news
    from shared.core import State

    class TG:
        dry, chat_id, name = False, "1", "news"

        def __init__(self):
            self.sent = []

        def send(self, text, buttons=None, photo=None):
            self.sent.append((text, photo))

    items = {"v": []}
    monkeypatch.setattr(news, "fetch_feed", lambda feed, ok: (ok.add(feed["name"]), items["v"])[1])
    monkeypatch.setattr(news, "article_image", lambda url: "https://x/og.jpg")
    cfg = {"feeds": [{"name": "F", "url": "u", "topic": "tech"}]}

    class A:
        now = False

    st, tg = State(tmp_path), TG()
    news.check_feeds(tg, st, A(), cfg)  # first run: seeding
    items["v"] = [
        {"title": "Có ảnh trong RSS", "link": "https://x/1", "text": "", "published": None, "image": "https://x/rss.jpg", "src": "F"},
        {"title": "Không có ảnh trong feed", "link": "https://x/2", "text": "", "published": None, "image": None, "src": "F"},
    ]
    news.check_feeds(tg, st, A(), cfg)
    assert [p for _, p in tg.sent] == ["https://x/rss.jpg", "https://x/og.jpg"]
