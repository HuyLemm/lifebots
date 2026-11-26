"""Weather features: several places, commute reminder, rain chart, worst-AQI hour, outlook,
suggestions, commands and the digest helper. Everything is faked: no network, no Telegram."""

import argparse
from datetime import datetime

import httpx

from shared.core import State
from weather import bot

START = "📍 <b>Thông báo lúc"


class FakeTG:
    chat_id = "42"
    name = "weather"
    dry = False

    def __init__(self):
        self.sent: list[str] = []
        self.photos: list = []
        self.fail = False

    def send(self, text, buttons=None, photo=None):
        if self.fail:
            raise httpx.HTTPError("telegram down")
        self.sent.append(text)
        self.photos.append(photo)

    def safe_send(self, text):
        self.sent.append(text)


def args(now=False):
    return argparse.Namespace(now=now)


def day(**over):
    d = {
        "code": 1, "tmax": 33, "tmin": 25, "feels_max": 34, "yesterday_max": 33,
        "rain_day_prob": 10, "rain_day_mm": 0, "uv": 4, "wind": 10, "hours_prob": [0] * 24,
        "hours_mm": [0] * 24, "hours_time": [f"2026-11-26T{h % 24:02d}:00" for h in range(24)],
        "aqi": 50, "pm25": 10, "now": 30, "feels": 32, "now_code": 1, "now_mm": 0,
        "humidity": 60, "now_wind": 8, "aqi_hours": [], "aqi_hours_time": [], "outlook": [],
    }  # fmt: skip
    d.update(over)
    return d


def setup(monkeypatch, per_place=None, d=None):
    """fetch() returns `per_place[(lat, lon)]` or the same day for every place."""
    monkeypatch.setattr(
        bot, "fetch", lambda lat, lon: (per_place or {}).get((lat, lon), d or day())
    )
    monkeypatch.setattr(bot, "watchdog", lambda *a, **k: None)
    monkeypatch.setattr(bot, "bar_chart", lambda *a, **k: None)


# ---------- 34: places ----------


def test_places_first_is_top_level_then_config_then_extra(tmp_path):
    cfg = {
        "place": "HCM", "latitude": 10.7, "longitude": 106.7,
        "places": [{"name": "Hà Nội", "latitude": 21.0, "longitude": 105.8}],
    }  # fmt: skip
    st = State(tmp_path)
    st.data["extra_places"] = [
        {"name": "Đà Nẵng", "latitude": 16.0, "longitude": 108.2},
        {"name": "hcm", "latitude": 1, "longitude": 1},  # same name as the first: ignored
    ]
    names = [p["name"] for p in bot.all_places(cfg, st)]
    assert names == ["HCM", "Hà Nội", "Đà Nẵng"]
    assert [p["key"] for p in bot.all_places(cfg, st)][0] == "main"


def test_first_place_keeps_legacy_state_keys_and_others_are_separate(tmp_path, monkeypatch):
    setup(monkeypatch, d=day(code=95))  # storm alert for everyone
    cfg = {
        "place": "HCM", "latitude": 1, "longitude": 1, "report_hours": [],
        "places": [{"name": "Hà Nội", "latitude": 2, "longitude": 2}],
    }  # fmt: skip
    st, tg = State(tmp_path), FakeTG()
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 2  # one message per place, each names its place
    assert "HCM" in tg.sent[0] and "Hà Nội" in tg.sent[1]
    assert all(m.startswith(START) for m in tg.sent)
    assert "storm" in st.data["last"]  # legacy top-level key for the FIRST place
    assert "storm" in st.data["places_state"]["hà-nội"]["last"]  # own state for the 2nd
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 2  # cooldowns are honoured per place


def test_legacy_state_means_no_resend_after_upgrade(tmp_path, monkeypatch):
    setup(monkeypatch, d=day())
    now = datetime.now()
    cfg = {"latitude": 1, "longitude": 1, "report_hours": [0]}
    st = State(tmp_path)
    st.data["last_report"] = f"{now:%Y-%m-%d}-0"  # old state: today's 0h report already sent
    tg = FakeTG()
    bot.check(tg, st, args(), cfg)
    assert tg.sent == []


def test_one_places_outage_does_not_block_the_other(tmp_path, monkeypatch):
    def fetch(lat, lon):
        if lat == 1:
            raise httpx.ConnectError("down")
        return day(code=95)

    monkeypatch.setattr(bot, "fetch", fetch)
    monkeypatch.setattr(bot, "watchdog", lambda *a, **k: None)
    cfg = {
        "place": "A", "latitude": 1, "longitude": 1, "report_hours": [],
        "places": [{"name": "B", "latitude": 2, "longitude": 2}],
    }  # fmt: skip
    st, tg = State(tmp_path), FakeTG()
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1 and "B" in tg.sent[0]
    assert st.data["health"]["thời tiết A"]["fails"] == 1


def test_failed_send_for_one_place_is_not_marked_and_still_raises(tmp_path, monkeypatch):
    setup(monkeypatch, d=day(code=95))
    cfg = {"latitude": 1, "longitude": 1, "report_hours": []}
    st, tg = State(tmp_path), FakeTG()
    tg.fail = True
    try:
        bot.check(tg, st, args(), cfg)
        raise AssertionError("should have raised")
    except httpx.HTTPError:
        pass
    assert "storm" not in st.data.get("last", {})


def test_commands_add_list_remove_place(tmp_path, monkeypatch):
    cfg = {"place": "HCM", "latitude": 1, "longitude": 1,
           "places": [{"name": "Huế", "latitude": 3, "longitude": 3}]}  # fmt: skip
    monkeypatch.setattr(bot, "_cfg", lambda: cfg)
    calls = []

    def geocode(url, **params):
        calls.append(params)
        if params["name"] == "Atlantis":
            return {}
        return {"results": [{"name": "Đà Nẵng", "latitude": 16.05, "longitude": 108.2,
                             "admin1": "Đà Nẵng", "country": "Việt Nam"}]}  # fmt: skip

    monkeypatch.setattr(bot, "get_json", geocode)
    st, tg = State(tmp_path), FakeTG()

    assert "Dùng" in bot.cmd_add_place(tg, st, args(), "")
    assert "Không tìm thấy" in bot.cmd_add_place(tg, st, args(), "Atlantis")
    assert st.data.get("extra_places", []) == []
    assert "Đã thêm" in bot.cmd_add_place(tg, st, args(), "da nang")
    assert calls[-1] == {"name": "da nang", "count": 1, "language": "vi"}
    assert st.data["extra_places"] == [{"name": "Đà Nẵng", "latitude": 16.05, "longitude": 108.2}]
    assert "đã có" in bot.cmd_add_place(tg, st, args(), "da nang")  # no duplicates
    listing = bot.cmd_places(tg, st, args(), "")
    assert "HCM" in listing and "Huế" in listing and "Đà Nẵng" in listing

    assert "config.toml" in bot.cmd_remove_place(tg, st, args(), "Huế")
    assert "Đã bỏ" in bot.cmd_remove_place(tg, st, args(), "đà nẵng")
    assert st.data["extra_places"] == []
    assert "Không thấy" in bot.cmd_remove_place(tg, st, args(), "Paris")


def test_add_place_limit_and_geocoder_outage(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "_cfg", lambda: {"latitude": 1, "longitude": 1})
    st, tg = State(tmp_path), FakeTG()
    st.data["extra_places"] = [{"name": f"P{i}", "latitude": i, "longitude": i} for i in range(4)]
    assert "Đã đủ" in bot.cmd_add_place(tg, st, args(), "x")

    st.data["extra_places"] = []

    def boom(url, **params):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(bot, "get_json", boom)
    assert "Chưa tra được" in bot.cmd_add_place(tg, st, args(), "x")
    assert st.data["extra_places"] == []


def test_commands_are_registered_for_help():
    assert set(bot.COMMANDS) == {"/diadiem", "/them", "/bo"}
    assert all("ví dụ" in desc for c, (_, desc) in bot.COMMANDS.items() if c != "/diadiem")


# ---------- 35: commute ----------


def test_due_commute_window_and_done():
    t = datetime(2026, 11, 26, 7, 30)
    assert bot.due_commute(t, ["07:00", "17:30"], []) == ["07:00"]  # late run: still due
    assert bot.due_commute(t, ["07:00"], ["2026-11-26-07:00"]) == []  # already handled today
    assert bot.due_commute(datetime(2026, 11, 26, 6, 59), ["07:00"], []) == []  # not yet
    assert bot.due_commute(datetime(2026, 11, 26, 9, 0), ["07:00"], []) == []  # window over
    assert bot.due_commute(t, ["bad", "7"], []) == []  # junk config never crashes


def test_commute_warns_only_when_needed_and_only_once(tmp_path, monkeypatch):
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(bot, "datetime", Clock)
    cfg = {"latitude": 1, "longitude": 1, "report_hours": [], "commute_times": ["12:00"]}

    # calm weather: silent, but handled for today
    setup(monkeypatch, d=day())
    st, tg = State(tmp_path / "a"), FakeTG()
    (tmp_path / "a").mkdir()
    bot.check(tg, st, args(), cfg)
    assert tg.sent == [] and st.data["commute_done"] == [f"{now:%Y-%m-%d}-12:00"]

    # rain in the next 2h: one reminder with the standard header, never a second one
    (tmp_path / "b").mkdir()
    st2, tg2 = State(tmp_path / "b"), FakeTG()
    rainy = day(hours_prob=[80, 90] + [0] * 22, hours_mm=[1.5, 2.0] + [0] * 22)
    setup(monkeypatch, d=rainy)
    bot.check(tg2, st2, args(), cfg)
    commute = [m for m in tg2.sent if "🚦" in m]
    assert len(commute) == 1 and commute[0].startswith(START)
    assert "Nhắc trước khi ra đường" in commute[0] and "áo mưa" in commute[0]
    bot.check(tg2, st2, args(), cfg)
    assert len([m for m in tg2.sent if "🚦" in m]) == 1


def test_commute_send_failure_is_retried(tmp_path, monkeypatch):
    now = datetime.now().replace(hour=7, minute=40, second=0, microsecond=0)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(bot, "datetime", Clock)
    setup(monkeypatch, d=day(aqi=160))
    cfg = {"latitude": 1, "longitude": 1, "report_hours": [], "commute_times": ["07:00"],
           "aqi_alert": 999}  # fmt: skip
    st, tg = State(tmp_path), FakeTG()
    tg.fail = True
    try:
        bot.check(tg, st, args(), cfg)
    except httpx.HTTPError:
        pass
    assert st.data.get("commute_done") == []  # not marked
    tg.fail = False
    bot.check(tg, st, args(), cfg)
    assert any("🚦" in m and "AQI 160" in m for m in tg.sent)


def test_commute_is_off_by_default(tmp_path, monkeypatch):
    setup(monkeypatch, d=day(hours_prob=[90] * 24, hours_mm=[3] * 24))
    cfg = {"latitude": 1, "longitude": 1, "report_hours": [], "rain_probability_pct": 999,
           "rain_mm": 999}  # fmt: skip
    st, tg = State(tmp_path), FakeTG()
    bot.check(tg, st, args(), cfg)
    assert tg.sent == []


# ---------- 36: rain chart ----------


def test_rain_chart_only_when_worth_it(monkeypatch):
    seen = {}

    def fake_chart(values, labels, title, subtitle="", **kw):
        seen.update(values=values, labels=labels, title=title, subtitle=subtitle, kw=kw)
        return b"JPEG"

    monkeypatch.setattr(bot, "bar_chart", fake_chart)
    assert bot.rain_chart(day(hours_prob=[10] * 24), "HCM") is None  # < 40%: banner instead
    assert bot.rain_chart(day(hours_prob=[0, 20, 70, 30] + [0] * 20), "HCM") == b"JPEG"
    assert len(seen["values"]) == 12 and seen["labels"][2] == "02h"
    assert seen["kw"]["highlight"] == {2} and seen["kw"]["vmax"] == 100 and seen["kw"]["unit"] == "%"
    assert "HCM" in seen["title"] and "70%" in seen["subtitle"]


def test_summary_gets_chart_alerts_keep_banner(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "fetch", lambda lat, lon: day(hours_prob=[0, 60, 80] + [0] * 21))
    monkeypatch.setattr(bot, "watchdog", lambda *a, **k: None)
    monkeypatch.setattr(bot, "bar_chart", lambda *a, **k: b"JPEG")
    st, tg = State(tmp_path), FakeTG()
    cfg = {"latitude": 1, "longitude": 1, "report_hours": [], "rain_probability_pct": 50}
    bot.check(tg, st, args(), cfg)  # alert only (no summary due)
    assert isinstance(tg.photos[0], str) and "raw.githubusercontent.com" in tg.photos[0]
    tg2 = FakeTG()
    (tmp_path / "x").mkdir()
    bot.check(tg2, State(tmp_path / "x"), args(now=True), cfg)  # summary
    assert tg2.photos[0] == b"JPEG"


def test_chart_failure_falls_back_to_banner(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "fetch", lambda lat, lon: day(hours_prob=[90] * 24))
    monkeypatch.setattr(bot, "watchdog", lambda *a, **k: None)
    monkeypatch.setattr(bot, "bar_chart", lambda *a, **k: None)  # Pillow missing etc.
    st, tg = State(tmp_path), FakeTG()
    bot.check(tg, st, args(now=True), {"latitude": 1, "longitude": 1})
    assert isinstance(tg.photos[0], str)  # banner URL, message still sent


# ---------- 37: worst AQI hour ----------


def test_worst_aqi_hour_line():
    times = [f"2026-11-26T{h:02d}:00" for h in range(24)]
    aqi_hours = [60] * 24
    aqi_hours[17] = 155
    d = day(aqi=60, aqi_hours=aqi_hours, aqi_hours_time=times)
    assert bot.worst_aqi(d) == ("17h", 155)
    text = "\n".join(bot.reasons(d, 3, max_notes=9))
    assert "AQI cao nhất 155 lúc 17h" in text and "tập thể dục" in text
    # fine all day -> no line; unknown data -> no line; same as right now -> no repetition
    assert not any("AQI cao nhất" in t for t in bot.reasons(day(aqi_hours=[50] * 24), 3, 9))
    assert not any("AQI cao nhất" in t for t in bot.reasons(day(), 3, 9))
    near = day(aqi=150, pm25=40, aqi_hours=[152] * 24, aqi_hours_time=times)
    assert not any("AQI cao nhất" in t for t in bot.reasons(near, 3, 9))
    assert bot.worst_aqi(day(aqi_hours=[None, None])) is None


# ---------- 38: outlook ----------


OUTLOOK = [
    {"date": "2026-11-27", "tmin": 25.2, "tmax": 33.4, "rain": 60, "code": 61},
    {"date": "2026-11-28", "tmin": 24.0, "tmax": 31.0, "rain": None, "code": None},
]


def test_outlook_lines_and_summary_flag():
    lines = bot.outlook_lines(day(outlook=OUTLOOK))
    assert lines[0].startswith("📅 Ngày mai (27/11):") and "25–33°C" in lines[0] and "mưa 60%" in lines[0]
    assert lines[1].startswith("📅 Ngày kia (28/11):") and "mưa" not in lines[1]  # unknown stays unknown
    now = datetime(2026, 11, 26, 8, 5)
    assert "Ngày mai" in bot.summary(day(outlook=OUTLOOK), "HCM", 3, now, outlook=True)
    assert "Ngày mai" not in bot.summary(day(outlook=OUTLOOK), "HCM", 3, now)
    assert bot.outlook_lines(day()) == []


def test_outlook_once_a_day_in_first_report_after_forecast_hour(tmp_path, monkeypatch):
    now = datetime(2026, 11, 26, 8, 30)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(bot, "datetime", Clock)
    setup(monkeypatch, d=day(outlook=OUTLOOK))
    cfg = {"latitude": 1, "longitude": 1, "report_hours": [0, 8, 12]}
    st, tg = State(tmp_path), FakeTG()
    st.data["last_report"] = "2026-11-26-0"  # the 0h report went out earlier
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 1 and "Ngày mai" in tg.sent[0]  # 8h report carries the outlook
    assert st.data["last_outlook"] == "2026-11-26"

    now = datetime(2026, 11, 26, 12, 5)  # the 12h report of the same day: no outlook again
    bot.check(tg, st, args(), cfg)
    assert len(tg.sent) == 2 and "Ngày mai" not in tg.sent[1]


def test_no_outlook_in_the_midnight_report(tmp_path, monkeypatch):
    now = datetime(2026, 11, 26, 0, 10)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(bot, "datetime", Clock)
    setup(monkeypatch, d=day(outlook=OUTLOOK))
    st, tg = State(tmp_path), FakeTG()
    bot.check(tg, st, args(), {"latitude": 1, "longitude": 1, "report_hours": [0, 8]})
    assert len(tg.sent) == 1 and "Ngày mai" not in tg.sent[0]
    assert "last_outlook" not in st.data


# ---------- 39: suggestions ----------


def test_suggestions_are_notable_only_and_never_duplicate():
    calm = day()
    assert bot.suggestions(calm, 3, []) == []
    assert bot.notes(calm, 3) == []  # nothing notable -> no filler

    hot = day(feels_max=38)
    assert any(t.startswith("💧") for t in bot.suggestions(hot, 3, []))
    assert bot.suggestions(hot, 3, ["🥵 Nóng hơn thực tế"]) == []  # topic already covered

    cool = day(tmin=20, now=22)
    assert any(t.startswith("🧥") for t in bot.suggestions(cool, 3, []))

    maybe_rain = day(rain_day_prob=70)  # rain later today, none soon
    assert any(t.startswith("🌂") for t in bot.suggestions(maybe_rain, 3, []))
    assert bot.suggestions(maybe_rain, 3, ["☔ Nên mang áo mưa"]) == []

    assert any(t.startswith("💨") for t in bot.suggestions(day(wind=35), 3, []))


def test_notes_are_capped_at_four_but_reasons_default_stays_three():
    d = day(
        feels=36, humidity=85, feels_max=39, hours_prob=[80] * 24, uv=9, aqi=130, pm25=45,
        tmax=40, yesterday_max=30, wind=35,
    )  # fmt: skip
    assert len(bot.notes(d, 3)) == 4
    assert len(bot.reasons(d, 3)) == 3  # existing behaviour kept
    msg = bot.summary(d, "HCM", 3, datetime(2026, 11, 26, 9, 0))
    assert msg.count("\n") < 16 and len(msg) < 1000  # still short enough for a photo caption


# ---------- digest ----------


def test_digest_lines_shape_and_failure(monkeypatch):
    monkeypatch.setattr(bot, "fetch", lambda lat, lon: day(now_code=61, hours_prob=[70] * 24))
    out = bot.digest_lines({"latitude": 1, "longitude": 1, "place": "HCM"})
    lines = out.split("\n")
    assert len(lines) == 3 and "30°C" in lines[0] and "70%" in lines[1] and "AQI 50" in lines[2]

    def boom(lat, lon):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(bot, "fetch", boom)
    assert bot.digest_lines({"latitude": 1, "longitude": 1}) == ""
    assert bot.digest_lines({}) == ""  # no place configured -> empty, no crash
    assert bot.digest_lines({"places": [{"name": "A", "latitude": 1, "longitude": 1}]}) == ""
