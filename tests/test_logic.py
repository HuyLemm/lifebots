from news.bot import matches
from shared.feeds import contains_any
from weather.bot import anomalies, aqi_label, wmo_label


def test_aqi_label_bands():
    assert aqi_label(30).startswith("Tốt")
    assert aqi_label(128).startswith("Kém")
    assert aqi_label(180).startswith("Xấu")
    assert aqi_label(999).startswith("Nguy hại")


def test_keyword_match_is_case_insensitive():
    item = {"title": "Giá Vàng hôm nay", "text": ""}
    assert matches(item, ["giá vàng"])
    assert not matches(item, ["bất động sản"])


def test_empty_keywords_match_everything():
    assert matches({"title": "bất kỳ", "text": ""}, [])
    assert contains_any("x", [])


def _day(**over):
    d = {"code": 1, "tmax": 33, "tmin": 25, "feels_max": 35, "yesterday_max": 33,
         "rain_day_prob": 10, "rain_day_mm": 0, "uv": 6, "wind": 10, "hours_prob": [0] * 24,
         "hours_mm": [0] * 24, "hours_time": [""] * 24, "aqi": 50, "pm25": 10}  # fmt: skip
    return {**d, **over}


def test_calm_day_has_no_anomalies():
    assert anomalies(_day(), {}, 3) == []


def test_anomalies_detected():
    keys = {k for k, _, _ in anomalies(_day(code=95, uv=10, wind=60, tmax=40, aqi=180), {}, 3)}
    assert {"storm", "uv", "wind", "swing", "aqi"} <= keys


def test_rain_soon_uses_look_ahead_window():
    probs = [0, 0, 0, 90] + [0] * 20  # rain only at hour 4
    assert anomalies(_day(hours_prob=probs), {}, 3) == []
    assert any(k == "rain" for k, _, _ in anomalies(_day(hours_prob=probs), {}, 4))


def test_wmo_label_fallback():
    assert "Dông" in wmo_label(95)
    assert "123" in wmo_label(123)


def test_due_slot_fires_once_per_report_hour():
    from datetime import datetime

    from weather.bot import due_slot

    hours = [0, 8, 12, 16, 20]
    t = datetime(2026, 11, 25, 12, 5)
    key = due_slot(t, hours, None)
    assert key == "2026-11-25-12"
    assert due_slot(datetime(2026, 11, 25, 12, 40), hours, key) is None  # already sent
    assert due_slot(datetime(2026, 11, 25, 13, 0), hours, key) is None  # not a report hour
    assert due_slot(datetime(2026, 11, 26, 12, 0), hours, key) == "2026-11-26-12"  # next day
    assert due_slot(datetime(2026, 11, 25, 20, 0), hours, key) == "2026-11-25-20"


def test_due_slot_midnight():
    from datetime import datetime

    from weather.bot import due_slot

    assert due_slot(datetime(2026, 11, 26, 0, 3), [0, 8], "2026-11-25-20") == "2026-11-26-0"


def test_build_message_reports_changes_and_baseline():
    from price.bot import build_message

    t = {"title": "T", "unit": "lit", "stamp": "", "rows": [("a", "RON 95", 21000.0)]}
    prev: dict = {}
    msg, upd = build_message(t, prev, 0, False)
    assert "21,000đ" in msg and "→" not in msg  # first sight = baseline, no arrow
    assert prev == {}  # pure: nothing committed until the caller has sent the message
    prev.update(upd)

    assert build_message(t, prev, 0, False) == (None, {})  # unchanged -> silent
    t["rows"] = [("a", "RON 95", 21500.0)]
    msg, upd = build_message(t, prev, 0, False)
    assert "21,000đ → <b>21,500đ</b>" in msg and "🔺 tăng 500đ" in msg
    assert "đồng/lít" in msg  # the legend explains the unit
    assert upd == {"a": 21500.0}
    prev.update(upd)  # the bot commits this only after Telegram accepted the message
    t["rows"] = [("a", "RON 95", 21510.0)]
    assert build_message(t, prev, 1.0, False) == (None, {})  # below 1% threshold


def test_fmt_units():
    from price.trackers import fmt_delta, fmt_value

    assert fmt_value("luong", 134_100_000) == "134.10 tr"
    assert fmt_delta("luong", 400_000) == "+400k"
    assert fmt_delta("lit", -350) == "-350đ"


def test_parse_channel_and_platform():
    from stuff.channels import parse_channel, platform_of

    page = (
        '<div data-post="sale_vn/101"><div class="tgme_widget_message_text js-message_text" '
        'dir="auto">Bàn phím cơ giảm 40%<br/>Mua: <a href="https://s.shopee.vn/abc">link</a></div></div>'
        '<div data-post="sale_vn/102"><div class="tgme_widget_message_photo"></div></div>'
    )
    posts = parse_channel(page)
    assert len(posts) == 1  # photo-only post skipped
    assert posts[0]["id"] == "sale_vn/101"
    assert "Bàn phím cơ giảm 40%" in posts[0]["text"]
    assert platform_of(posts[0]) == "Shopee"
    assert platform_of({"text": "x", "links": []}) is None


def test_product_deal_extracts_price_and_skips_vouchers():
    from stuff.channels import extract_discount, extract_price, product_deal

    ok = {
        "text": "Lụm Ulanzi MT-44 giá đỡ ngon sale 185K rẻ cho ae tìm tripod\n\nhttps://s.lazada.vn/x"
    }
    d = product_deal(ok)
    assert d and d["price"] == "185K" and "Ulanzi MT-44" in d["lines"][0]

    assert extract_price("Tăm nước Xiaomi về giá 409K > link") == "409K"
    assert extract_price("Combo chỉ 94k: link") == "94K"
    assert extract_price("S75pro ng dùng 595K ae") == "595K"
    assert extract_price("mã giảm 39k từ 150k") is None  # voucher amounts are not prices
    assert extract_discount("Kệ từ tính Lock&Lock chính hãng -87%, ae cần thì vụt") == 87
    assert extract_discount("Giảm 3/4 giá\nLingbao | Combo Bàn Phím Cơ K98") == 75

    vouchers = [
        "Back mã 22% Sếp ơi! 15:28 - Mã giảm 22% tối đa 100K đơn 150K vừa thêm lượt.",
        "Back mã sVip 200K rồi kìa mn",
        "🔥 Mã AFF áp nhiều sp: AFFNCUST giảm 39k từ 150k: nhập tại link",
    ]
    assert all(product_deal({"text": v}) is None for v in vouchers)
    assert product_deal({"text": "Húppp : https://s.lazada.vn/x"}) is None  # no price, no name


def test_price_edge_cases():
    from stuff.channels import extract_price

    assert extract_price("Áp mã 2tr4 dưới sp lụm màn hình 2K 24inch sale rẻ") == "2,4 tr"
    assert extract_price("màn hình 2K 24inch") is None  # resolution, not a price
    assert extract_price("Điện thoại còn 16999K: link") == "16999K"


def test_due_slot_delivers_the_latest_missed_report():
    from datetime import datetime

    from weather.bot import due_slot

    hours = [0, 8, 12, 16, 20]
    assert due_slot(datetime(2026, 11, 26, 9, 30), hours, None) == "2026-11-26-8"  # 1h30 late
    assert (
        due_slot(datetime(2026, 11, 26, 11, 59), hours, None) == "2026-11-26-8"
    )  # 4h late, still sent
    assert due_slot(datetime(2026, 11, 26, 9, 30), hours, "2026-11-26-8") is None  # never twice
    assert (
        due_slot(datetime(2026, 11, 26, 18, 0), hours, "2026-11-26-12") == "2026-11-26-16"
    )  # 12 sent, 16 due
    assert due_slot(datetime(2026, 11, 26, 1, 10), hours, None) == "2026-11-26-0"  # midnight slot
    assert due_slot(datetime(2026, 11, 26, 9, 0), [8, 20], "2026-11-26-8") is None
    assert due_slot(datetime(2026, 11, 26, 5, 0), [8, 20], None) is None  # nothing due yet today


def test_price_message_explains_the_change_from_real_data():
    from price.bot import build_message, explain, market_context

    gold = {
        "title": "Vàng",
        "unit": "luong",
        "stamp": "",
        "rows": [("sjc:x:buy", "SJC · Mua", 86_000_000.0), ("sjc:x:sell", "SJC · Bán", 88_000_000.0)],
    }
    prev = {"sjc:x:buy": 85_000_000.0, "sjc:x:sell": 87_000_000.0, "xau": 4000.0, "usdvnd": 26000.0}
    world = {"rows": [("xau", "XAU", 4040.0)]}  # +1.0%
    usd = {"rows": [("usdvnd", "USD", 26000.0)]}  # unchanged
    ctx = market_context({"world_gold": world, "usd_vnd": usd}, prev)
    msg, _ = build_message(gold, prev, 0, False, why=explain("sjc", ctx, gold))
    assert "Mua vào" in msg and "Bán ra" in msg
    assert "Có thể do vàng thế giới tăng 1.0%" in msg
    assert "USD/VND" not in msg  # unchanged -> not blamed
    assert "triệu đồng/lượng" in msg  # legend

    # world gold moved the other way: say it is NOT the world price
    ctx = market_context({"world_gold": {"rows": [("xau", "XAU", 3960.0)]}, "usd_vnd": usd}, prev)
    msg, _ = build_message(gold, prev, 0, False, why=explain("sjc", ctx, gold))
    assert "Ngược chiều vàng thế giới" in msg

    # nothing known about world/usd -> no invented reason
    ctx = market_context({}, {})
    msg, _ = build_message(gold, prev, 0, False, why=explain("sjc", ctx, gold))
    assert "💡" not in msg


def test_weather_reasons_and_banner():
    from datetime import datetime

    from weather.bot import banner, reasons, summary

    d = {"code": 1, "tmax": 34, "tmin": 25, "feels_max": 38, "yesterday_max": 30, "rain_day_prob": 70,
         "rain_day_mm": 5, "uv": 9, "wind": 10, "hours_prob": [60, 80, 40], "hours_mm": [1, 2, 0],
         "hours_time": ["2026-11-26T14:00", "2026-11-26T15:00", "2026-11-26T16:00"], "aqi": 120,
         "pm25": 45, "now": 30, "feels": 36, "now_code": 61, "now_mm": 0.5, "humidity": 85,
         "now_wind": 8}  # fmt: skip
    notes = reasons(d, 3, max_notes=9)
    text = "\n".join(notes)
    assert "độ ẩm cao (85%)" in text and "áo mưa" in text and "PM2.5" in text
    assert len(reasons(d, 3)) == 3  # short by default
    msg = summary(d, "HCM", 3, datetime(2026, 11, 26, 14, 0))
    assert "Cảm giác = " in msg and "AQI: ≤50 tốt" in msg  # legend

    calm = {**d, "feels": 30, "hours_prob": [0, 0, 0], "aqi": 40, "uv": 3, "tmax": 30}
    assert reasons(calm, 3) == []  # nothing to explain -> no filler
    assert "💡" not in summary(calm, "HCM", 3, datetime(2026, 11, 26, 14, 0))
    assert banner(d, datetime(2026, 11, 26, 14)) == "w_rain"
    assert banner({**d, "now_code": 0}, datetime(2026, 11, 26, 21)) == "w_night"
    assert banner({**d, "now_code": 0}, datetime(2026, 11, 26, 9)) == "w_sun"
    assert banner(d, datetime(2026, 11, 26, 9), "storm") == "w_storm"
