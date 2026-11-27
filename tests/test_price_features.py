"""Price bot extras: /bao targets, history series / 30-day extremes, reminders, extra lines."""

import csv
from datetime import date, datetime, timedelta

from price import extras, history
from shared.core import State


class TG:
    dry = False
    chat_id = "1"
    name = "price"

    def __init__(self):
        self.sent: list[tuple[str, object]] = []

    def send(self, text, buttons=None, photo=None):
        self.sent.append((text, photo))


def test_parse_target_units_and_operators():
    assert extras.parse_target("sjc 130") == ("sjc", "<=", 130_000_000.0)
    assert extras.parse_target("sjc trên 135,5") == ("sjc", ">=", 135_500_000.0)
    assert extras.parse_target("pnj <130") == ("pnj", "<=", 130_000_000.0)
    assert extras.parse_target("ron95 21.000") == ("ron95", "<=", 21000.0)
    assert extras.parse_target("ron95 21k") == ("ron95", "<=", 21000.0)
    assert extras.parse_target("usd 26000") == ("usd", "<=", 26000.0)
    assert extras.parse_target("btc tren 70,000") == ("btc", ">=", 70000.0)
    assert extras.parse_target("xau >3500") == ("xau", ">=", 3500.0)
    assert "Mã không có" in extras.parse_target("abc 1")
    assert "Cú pháp" in extras.parse_target("sjc")


def test_target_fires_once_then_rearms(tmp_path):
    st, tg = State(tmp_path), TG()
    extras.cmd_bao(tg, st, None, "sjc 130")
    key = "sjc:SJC 1L-10L:sell"

    def prices(v):
        return {key: (v, "SJC 1L-10L · Bán", "luong")}

    extras.check_targets(tg, st, prices(131e6))
    assert tg.sent == []  # above the threshold: silent
    extras.check_targets(tg, st, prices(129.9e6))
    assert len(tg.sent) == 1 and "Giá chạm ngưỡng" in tg.sent[0][0]
    assert tg.sent[0][1].endswith("price_gold.jpg")  # picture attached
    extras.check_targets(tg, st, prices(129.5e6))
    assert len(tg.sent) == 1  # no repeat while it stays below
    extras.check_targets(tg, st, prices(131e6))  # leaves the threshold -> re-armed
    extras.check_targets(tg, st, prices(129e6))
    assert len(tg.sent) == 2


def test_target_not_marked_when_send_fails(tmp_path):
    st = State(tmp_path)
    extras.cmd_bao(TG(), st, None, "btc trên 70000")

    class Broken(TG):
        def send(self, *a, **k):
            raise OSError("down")

    try:
        extras.check_targets(Broken(), st, {"btc": (71000.0, "BTC", "usd")})
    except OSError:
        pass
    assert st.data["targets"][0]["fired"] is False  # retried on the next run
    tg = TG()
    extras.check_targets(tg, st, {"btc": (71000.0, "BTC", "usd")})
    assert len(tg.sent) == 1


def test_target_list_and_delete(tmp_path):
    st, tg = State(tmp_path), TG()
    assert "Chưa có" in extras.cmd_dsbao(tg, st, None, "")
    extras.cmd_bao(tg, st, None, "sjc 130")
    extras.cmd_bao(tg, st, None, "usd 26000")
    assert "#1" in extras.cmd_dsbao(tg, st, None, "") and "#2" in extras.cmd_dsbao(tg, st, None, "")
    assert "Đã xoá" in extras.cmd_xoabao(tg, st, None, "1")
    assert [t["id"] for t in st.data["targets"]] == [2]
    extras.cmd_xoabao(tg, st, None, "all")
    assert st.data["targets"] == []


def _write_history(path, key, points):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "key", "label", "value", "unit"])
        for ts, v in points:
            w.writerow([ts.isoformat(timespec="seconds"), key, key, v, "luong"])


def test_series_carries_values_forward_and_extremes(tmp_path):
    p, now = tmp_path / "h.csv", datetime(2026, 11, 26, 12, 0)
    pts = [(now - timedelta(days=d, hours=1), 130e6 + d * 1e6) for d in (9, 8, 6, 3, 1)]
    _write_history(p, "k", pts)
    labels, values = history.series("k", 5, p, now)
    assert len(values) == 6 and labels[-1] == "26/11"
    assert values[-1] == 131e6  # last known value carried to today
    # 30-day extremes need a real history, and compare against everything before
    assert history.extreme("k", 120e6, 30, p, now) == "low"
    assert history.extreme("k", 140e6, 30, p, now) == "high"
    assert history.extreme("k", 133e6, 30, p, now) is None
    _write_history(p, "k", pts[:2])
    assert history.extreme("k", 100e6, 30, p, now) is None  # too young to claim a record


def test_petrol_reminder_thursday_only_once_and_mentions_no_oil(tmp_path):
    st, tg = State(tmp_path), TG()
    wed, thu = datetime(2026, 11, 25, 15), datetime(2026, 11, 26, 14)
    assert thu.weekday() == 3
    extras.petrol_reminder(tg, st, wed)
    assert tg.sent == []  # not a Thursday
    extras.petrol_reminder(tg, st, datetime(2026, 11, 26, 9))
    assert tg.sent == []  # Thursday morning: too early
    extras.petrol_reminder(tg, st, thu)
    assert len(tg.sent) == 1
    text = tg.sent[0][0]
    assert "giá xăng" in text and "dầu" not in text.lower() and "brent" not in text.lower()
    extras.petrol_reminder(tg, st, thu)
    assert len(tg.sent) == 1  # once per Thursday
    extras.petrol_reminder(tg, st, thu + timedelta(days=7))
    assert len(tg.sent) == 2  # and again the next Thursday


def test_petrol_message_says_xang_e10_ron95_not_dau():
    from price.bot import build_message, explain

    t = {
        "title": "⛽ Giá xăng E10 RON 95 · Petrolimex (vùng 1)",
        "unit": "lit",
        "stamp": "",
        "rows": [("petrol:Xăng E10 RON 95 mức 5", "Xăng E10 RON 95 mức 5", 21050.0)],
    }
    prev = {"petrol:Xăng E10 RON 95 mức 5": 21100.0}
    msg, _ = build_message(t, prev, 0, False, why=explain("petrol", {}, t))
    assert "Giá xăng E10 RON 95" in msg and "Giá xăng giảm" in msg
    assert "dầu" not in msg.lower()  # no oil talk in a petrol message


def test_token_warning_window(tmp_path):
    st, tg = State(tmp_path), TG()
    cfg = {"cron_token_expires": "2027-11-26", "token_warn_days": 14}
    extras.token_warning(tg, st, cfg, date(2027, 6, 1))
    assert tg.sent == []  # far away
    extras.token_warning(tg, st, cfg, date(2027, 11, 20))
    assert len(tg.sent) == 1 and "còn 6 ngày" in tg.sent[0][0]
    extras.token_warning(tg, st, cfg, date(2027, 11, 20))
    assert len(tg.sent) == 1  # once a day
    extras.token_warning(tg, st, cfg, date(2027, 11, 28))
    assert "đã hết hạn 2 ngày" in tg.sent[1][0]
    extras.token_warning(tg, st, {}, date(2027, 11, 28))  # not configured -> silent
    assert len(tg.sent) == 2


def test_spread_and_sjc_vs_pnj_lines():
    sjc = {
        "rows": [
            ("sjc:SJC 1L-10L:buy", "SJC 1L-10L · Mua", 129.5e6),
            ("sjc:SJC 1L-10L:sell", "SJC 1L-10L · Bán", 134.5e6),
        ]
    }
    pnj = {"rows": [("pnj:SJC:sell", "Vàng miếng SJC · Bán", 134.0e6)]}
    assert "5.00 tr (3.9%)" in extras.spread_line(sjc)
    line = extras.sjc_vs_pnj({"sjc": sjc, "pnj": pnj})
    assert "PNJ bán miếng SJC rẻ hơn SJC 0.50 tr" in line
    assert extras.sjc_vs_pnj({"sjc": sjc}) == ""  # PNJ missing -> no line
    assert extras.changes_line({"changes_today": {"times": 3, "day_change_sell": 400000.0}})
    assert extras.changes_line({"changes_today": {"times": 1, "day_change_sell": 1.0}}) == ""


def test_stats_summary_reads_other_bots(tmp_path, monkeypatch):
    import json

    from shared import health

    monkeypatch.setattr(health, "ROOT", tmp_path)
    for name, n in (("news", 40), ("price", 3)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "state.json").write_text(
            json.dumps({"stats": {"2026-11-25": n, "2026-11-26": n}, "health": {}}), encoding="utf-8"
        )
    (tmp_path / "stuff").mkdir()
    (tmp_path / "stuff" / "state.json").write_text(
        json.dumps({"health": {"kênh x": {"alerted": True}}}), encoding="utf-8"
    )
    text = health.stats_summary(["price", "news", "stuff"])
    assert "price 6" in text and "news 80" in text and "stuff/kênh x" in text
    assert health.stats_summary(["nothing"]) is None


def test_flush_stats_counts_and_resets(tmp_path):
    from shared.core import Telegram, flush_stats

    st, tg = State(tmp_path), Telegram(dry=False)
    tg.token = tg.chat_id = "x"
    tg.dry = False
    tg.sent_count = 3
    flush_stats(st, tg)
    assert sum(st.data["stats"].values()) == 3 and tg.sent_count == 0


def test_visible_len_ignores_html_tags():
    from shared.core import visible_len

    text = '<b>Xin</b> <a href="https://example.com/' + "x" * 500 + '">chào</a> &amp; bạn'
    assert visible_len(text) == len("Xin chào & bạn")


def test_morning_briefing_window_and_once_a_day(tmp_path, monkeypatch):
    from price import morning

    monkeypatch.setattr(morning, "morning_text", lambda trackers, now: "BRIEF")
    st, tg = State(tmp_path), TG()
    cfg = {"morning_hour": 7}
    morning.send_morning(tg, st, cfg, {}, datetime(2026, 11, 26, 6, 0))
    assert tg.sent == []  # too early
    morning.send_morning(tg, st, cfg, {}, datetime(2026, 11, 26, 9, 30))
    assert len(tg.sent) == 1 and tg.sent[0][0] == "BRIEF"  # a late run still delivers
    morning.send_morning(tg, st, cfg, {}, datetime(2026, 11, 26, 10, 0))
    assert len(tg.sent) == 1  # once a day
    morning.send_morning(tg, st, cfg, {}, datetime(2026, 11, 27, 15, 0))
    assert len(tg.sent) == 1  # too late in the day to be a "morning" briefing
    morning.send_morning(tg, st, {"morning_hour": -1}, {}, datetime(2026, 11, 28, 8, 0))
    assert len(tg.sent) == 1  # switched off


def test_morning_survives_missing_parts(monkeypatch):
    from price import morning

    def boom(*a, **k):
        raise RuntimeError("source down")

    monkeypatch.setattr(morning, "load_config", boom)
    tr = {"sjc": {"rows": [("sjc:SJC 1L-10L:buy", "x", 129.5e6), ("sjc:SJC 1L-10L:sell", "x", 134.5e6)]}}
    text = morning.morning_text(tr, datetime(2026, 11, 26, 7, 0))
    assert text.startswith("📍") and "SJC: mua 129.50" in text  # prices still go out


def test_silent_tracker_feeds_context_but_never_sends(tmp_path, monkeypatch):
    from price import bot

    rows = {
        "usd_vnd": [("usdvnd", "USD/VND", 25800.0)],
        "petrol": [("petrol:Xăng E10 RON 95 mức 5", "Xăng E10 RON 95 mức 5", 21000.0)],
    }
    units = {"usd_vnd": "usd_vnd", "petrol": "lit"}
    monkeypatch.setattr(
        bot,
        "TRACKERS",
        {n: (lambda cfg, n=n: {"title": n, "unit": units[n], "stamp": "", "rows": rows[n]}) for n in rows},
    )
    monkeypatch.setattr(bot, "maintenance", lambda *a, **k: None)
    monkeypatch.setattr(bot.history, "append", lambda *a, **k: None)
    cfg = {"trackers": {"usd_vnd": {"enabled": True, "silent": True}, "petrol": {"enabled": True}}}

    class A:
        now = False

    st, tg = State(tmp_path), TG()
    bot.check(tg, st, A(), cfg)
    assert len(tg.sent) == 1 and "Xăng E10 RON 95" in tg.sent[0][0]  # petrol only, no USD message
    assert st.data["values"]["usdvnd"] == 25800.0  # but USD is still tracked for explanations


def test_petrol_only_keeps_just_the_wanted_fuel(monkeypatch):
    import httpx

    from price import trackers

    html = (
        "<table>"
        "<tr><td>Xăng E10 RON 95 mức 5</td><td>21.050</td><td>21.470</td></tr>"
        "<tr><td>Xăng E10 RON 95 mức 3</td><td>19.050</td><td>19.430</td></tr>"
        "<tr><td>Dầu DO 0,05S mức 2</td><td>24.960</td><td>25.450</td></tr>"
        "</table>"
    )
    monkeypatch.setattr(
        trackers.httpx, "get", lambda *a, **k: httpx.Response(200, text=html, request=httpx.Request("GET", "u"))
    )
    all_rows = trackers.petrol({})["rows"]
    assert len(all_rows) == 3
    only = trackers.petrol({"only": ["E10 RON 95 mức 5"]})["rows"]
    assert [label for _, label, _ in only] == ["Xăng E10 RON 95 mức 5"]
