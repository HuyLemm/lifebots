"""The behaviours that make the bots trustworthy: no lost messages, no duplicates, loud failures."""

import argparse
from pathlib import Path

import httpx

from shared.core import State, handle_update


class FakeTG:
    """Records messages; can be told to fail like a Telegram outage."""

    chat_id = "42"
    name = "testbot"
    dry = False

    def __init__(self):
        self.sent: list[str] = []
        self.buttons: list = []
        self.fail = False
        self.answered: list[str] = []

    def send(self, text, buttons=None):
        if self.fail:
            raise httpx.HTTPError("telegram down")
        self.sent.append(text)
        self.buttons.append(buttons)

    def safe_send(self, text):
        self.sent.append(text)

    def answer_callback(self, cid, text=""):
        self.answered.append(cid)


def args(now=False):
    return argparse.Namespace(now=now)


# ---------- news ----------


def _items(*titles):
    return [{"title": t, "link": f"http://x/{i}", "text": ""} for i, t in enumerate(titles)]


def test_news_message_survives_telegram_outage(tmp_path, monkeypatch):
    from news import bot

    cfg = {"feeds": [{"name": "F", "url": "u", "topic": "tech", "priority": 1}]}
    feed = {"items": _items("old story")}
    monkeypatch.setattr(bot, "fetch_items", lambda url: feed["items"])
    st, tg = State(tmp_path), FakeTG()

    bot.check(tg, st, args(), cfg)  # first run only remembers
    assert tg.sent == []

    feed["items"] = _items("old story", "brand new story")
    tg.fail = True
    try:
        bot.check(tg, st, args(), cfg)
    except httpx.HTTPError:
        pass  # run() logs this and carries on
    assert "http://x/1" not in st.data["seen"]  # NOT marked seen -> will be retried

    tg.fail = False
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1 and "brand new story" in tg.sent[0]
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1  # never twice


def test_news_same_story_from_two_outlets_sent_once(tmp_path, monkeypatch):
    from news import bot

    cfg = {"feeds": [{"name": "A", "url": "a"}, {"name": "B", "url": "b"}]}
    pages = {"a": [], "b": []}
    monkeypatch.setattr(bot, "fetch_items", lambda url: pages[url])
    st, tg = State(tmp_path), FakeTG()
    bot.check(tg, st, args(), cfg)
    pages["a"] = [{"title": "Big News!", "link": "http://a/1", "text": ""}]
    pages["b"] = [{"title": "big news", "link": "http://b/9", "text": ""}]
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1


def test_news_burst_becomes_one_digest(tmp_path, monkeypatch):
    from news import bot

    cfg = {"feeds": [{"name": "F", "url": "u", "topic": "tech"}], "digest_over": 3}
    feed = {"items": []}
    monkeypatch.setattr(bot, "fetch_items", lambda url: feed["items"])
    st, tg = State(tmp_path), FakeTG()
    bot.check(tg, st, args(), cfg)
    feed["items"] = _items("a story", "b story", "c story", "d story", "e story")
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1 and "5 tin mới" in tg.sent[0]


# ---------- stuff ----------


def _page(*posts):
    return "".join(
        f'<div data-post="{pid}"><div class="tgme_widget_message_text js-message_text">{text}</div></div>'
        for pid, text in posts
    )


def _stuff_cfg():
    return {
        "channels": [{"name": "A", "handle": "a"}, {"name": "B", "handle": "b"}],
        "channel_platforms": ["Lazada"],
        "channel_keywords": ["bàn phím", "giày"],
    }


def _patch_pages(monkeypatch, pages):
    from stuff import channels

    monkeypatch.setattr(
        channels.httpx,
        "get",
        lambda url, **k: httpx.Response(
            200, text=pages[url.rsplit("/", 1)[1]], request=httpx.Request("GET", url)
        ),
    )


def test_stuff_dedupes_across_channels_and_attaches_drop_button(tmp_path, monkeypatch):
    deal = "Bàn phím cơ Aula giá 595K https://s.lazada.vn/x"
    pages = {"a": _page(("a/1", "x")), "b": _page(("b/1", "x"))}
    _patch_pages(monkeypatch, pages)
    from stuff import channels

    st, tg = State(tmp_path), FakeTG()
    channels.check_channels(tg, st, args(), _stuff_cfg())  # seeding
    pages["a"] = _page(("a/1", "x"), ("a/2", deal))
    pages["b"] = _page(("b/1", "x"), ("b/2", deal))  # same product, other channel
    channels.check_channels(tg, st, args(), _stuff_cfg())
    assert len(tg.sent) == 1
    assert tg.buttons[0][0][0]["callback_data"] == "del:bàn phím"


def test_stuff_message_survives_outage(tmp_path, monkeypatch):
    from stuff import channels

    pages = {"a": _page(("a/1", "x")), "b": _page()}
    _patch_pages(monkeypatch, pages)
    st, tg = State(tmp_path), FakeTG()
    channels.check_channels(tg, st, args(), _stuff_cfg())
    pages["a"] = _page(("a/1", "x"), ("a/2", "Giày sneaker nam chỉ 199K https://s.lazada.vn/y"))
    tg.fail = True
    try:
        channels.check_channels(tg, st, args(), _stuff_cfg())
    except httpx.HTTPError:
        pass
    assert "a/2" not in st.data["channel_posts"]
    tg.fail = False
    channels.check_channels(tg, st, args(), _stuff_cfg())
    assert len(tg.sent) == 1


def test_stuff_dropped_keyword_is_no_longer_matched(tmp_path, monkeypatch):
    from stuff import channels

    pages = {"a": _page(("a/1", "x")), "b": _page()}
    _patch_pages(monkeypatch, pages)
    st, tg = State(tmp_path), FakeTG()
    channels.check_channels(tg, st, args(), _stuff_cfg())
    channels.cmd_del(tg, st, args(), "giày")
    pages["a"] = _page(("a/1", "x"), ("a/2", "Giày sneaker nam chỉ 199K https://s.lazada.vn/y"))
    channels.check_channels(tg, st, args(), _stuff_cfg())
    assert tg.sent == []
    channels.cmd_add(tg, st, args(), "giày")  # bring it back
    assert "giày" in channels.effective_keywords(_stuff_cfg(), st)


# ---------- price ----------


def test_price_not_committed_when_telegram_fails(tmp_path, monkeypatch):
    from price import bot

    row = [("a", "RON 95", 21000.0)]
    monkeypatch.setattr(
        bot,
        "TRACKERS",
        {"petrol": lambda cfg: {"title": "T", "unit": "lit", "stamp": "", "rows": row}},
    )
    monkeypatch.setattr(bot, "maintenance", lambda *a, **k: None)
    cfg = {"trackers": {"petrol": {"enabled": True}}}
    st, tg = State(tmp_path), FakeTG()
    tg.fail = True
    try:
        bot.check(tg, st, args(), cfg)
    except httpx.HTTPError:
        pass
    assert st.data["values"] == {}  # baseline NOT stored -> reported on the next round
    tg.fail = False
    monkeypatch.setattr(bot.history, "append", lambda *a, **k: None)
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1 and st.data["values"] == {"a": 21000.0}


def test_price_broken_source_alerts_once_then_recovers(tmp_path, monkeypatch):
    from price import bot

    state = {"broken": True}

    def tracker(cfg):
        if state["broken"]:
            raise KeyError("data")
        return {"title": "T", "unit": "lit", "stamp": "", "rows": [("a", "x", 1000.0)]}

    monkeypatch.setattr(bot, "TRACKERS", {"petrol": tracker})
    monkeypatch.setattr(bot, "maintenance", lambda *a, **k: None)
    monkeypatch.setattr(bot.history, "append", lambda *a, **k: None)
    cfg = {"trackers": {"petrol": {"enabled": True}}}
    st, tg = State(tmp_path), FakeTG()
    for _ in range(5):
        bot.check(tg, st, args(), cfg)
    alerts = [m for m in tg.sent if "lỗi" in m]
    assert len(alerts) == 1  # once, not five times
    state["broken"] = False
    bot.check(tg, st, args(), cfg)
    assert any("hoạt động lại" in m for m in tg.sent)


# ---------- weather ----------


def test_weather_alert_not_marked_when_telegram_fails(tmp_path, monkeypatch):
    from weather import bot

    day = {"code": 95, "tmax": 33, "tmin": 25, "feels_max": 35, "yesterday_max": 33, "rain_day_prob": 10,
           "rain_day_mm": 0, "uv": 6, "wind": 10, "hours_prob": [0] * 24, "hours_mm": [0] * 24,
           "hours_time": [""] * 24, "aqi": 50, "pm25": 10, "now": 30, "feels": 33, "now_code": 1, "now_mm": 0}  # fmt: skip
    monkeypatch.setattr(bot, "fetch", lambda lat, lon: day)
    monkeypatch.setattr(bot, "watchdog", lambda *a, **k: None)
    cfg = {"latitude": 1, "longitude": 1, "report_hours": []}
    st, tg = State(tmp_path), FakeTG()
    tg.fail = True
    try:
        bot.check(tg, st, args(), cfg)
    except httpx.HTTPError:
        pass
    assert "storm" not in st.data.get("last", {})
    tg.fail = False
    bot.check(tg, st, args(), cfg)
    assert any("dông" in m for m in tg.sent)
    bot.check(tg, st, args(), cfg)
    assert sum("dông" in m for m in tg.sent) == 1  # cooldown honoured


# ---------- commands / health ----------


def _msg(text, chat=42):
    return {"update_id": 1, "message": {"chat": {"id": chat}, "text": text}}


def test_commands_only_obeyed_from_owner(tmp_path):
    from shared.core import COMMON_COMMANDS

    st, tg = State(tmp_path), FakeTG()
    handle_update(_msg("/pause", chat=999), tg, st, args(), COMMON_COMMANDS, {})
    assert not st.data.get("paused")
    handle_update(_msg("/pause"), tg, st, args(), COMMON_COMMANDS, {})
    assert st.data["paused"] is True
    handle_update(_msg("/resume"), tg, st, args(), COMMON_COMMANDS, {})
    assert st.data["paused"] is False


def test_now_command_requests_immediate_check(tmp_path):
    from shared.core import COMMON_COMMANDS

    st, tg, a = State(tmp_path), FakeTG(), args()
    handle_update(_msg("/now"), tg, st, a, COMMON_COMMANDS, {})
    assert a.now is True


def test_button_press_drops_keyword(tmp_path):
    from stuff.channels import cmd_del

    st, tg = State(tmp_path), FakeTG()
    cb = {
        "update_id": 2,
        "callback_query": {"id": "c1", "data": "del:giày", "message": {"chat": {"id": 42}}},
    }
    handle_update(cb, tg, st, args(), {}, {"del:": cmd_del})
    assert st.data["kw_del"] == ["giày"] and tg.answered == ["c1"]


def test_state_save_is_atomic(tmp_path):
    st = State(tmp_path)
    st.data["x"] = 1
    st.save()
    assert State(tmp_path).data == {"x": 1}
    assert not list(Path(tmp_path).glob("*.tmp"))


# ---------- scheduler (--once) mode ----------


class OnceTG(FakeTG):
    """FakeTG that can also hand back queued Telegram updates."""

    def __init__(self, updates=()):
        super().__init__()
        self.updates = list(updates)
        self.commands_set = False

    def get_updates(self, offset, timeout):
        out, self.updates = self.updates, []
        return out

    def set_commands(self, commands):
        self.commands_set = True


def _once(tmp_path, tg, state_data=None, now=False, interval=600):
    from shared.core import COMMON_COMMANDS, run_once

    st = State(tmp_path)
    st.data.update(state_data or {})
    calls = []
    run_once(lambda *a: calls.append(1), {}, interval, tg, st, args(now), COMMON_COMMANDS, {})
    return st, calls


def test_once_skips_check_when_interval_not_elapsed(tmp_path):
    import time

    _, calls = _once(tmp_path, OnceTG(), {"last_check": time.time() - 60})
    assert calls == []  # cron fires every 5 min but news wants every 10


def test_once_runs_check_when_due_and_stamps_time(tmp_path):
    import time

    st, calls = _once(tmp_path, OnceTG(), {"last_check": time.time() - 3600})
    assert calls == [1] and time.time() - st.data["last_check"] < 5


def test_once_now_flag_forces_check(tmp_path):
    import time

    _, calls = _once(tmp_path, OnceTG(), {"last_check": time.time()}, now=True)
    assert calls == [1]


def test_once_answers_pending_commands_and_respects_pause(tmp_path):
    tg = OnceTG([_msg("/pause")])
    st, calls = _once(tmp_path, tg, {"last_check": 0})
    assert st.data["paused"] is True and calls == []  # paused by a command from this very run
    assert st.data["offset"] == 2 and tg.commands_set


def test_once_ignores_commands_from_strangers(tmp_path):
    tg = OnceTG([_msg("/pause", chat=999)])
    st, calls = _once(tmp_path, tg, {"last_check": 0})
    assert not st.data.get("paused") and calls == [1]


def test_health_uses_last_check_not_file_mtime(tmp_path, monkeypatch):
    import json
    import time

    from shared import health

    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "state.json").write_text(json.dumps({"last_check": time.time() - 500}))
    monkeypatch.setattr(health, "ROOT", tmp_path)
    assert 480 < health.bot_age("x") < 560
