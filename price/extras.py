"""Price bot extras: your own price alerts (/bao), charts (/bieudo), Thursday petrol reminder,
spread / SJC-vs-PNJ lines, 30-day low/high, and the GitHub-token expiry warning."""

import logging
import re
from datetime import date, datetime

from price import history
from price.trackers import fmt_delta, fmt_value
from shared.core import Telegram, asset_url, esc, header

log = logging.getLogger(__name__)

# code typed by the user -> (row key or key prefix, unit, label)
CODES: dict[str, tuple[str, str, str]] = {
    "sjc": ("sjc:SJC 1L-10L:sell", "luong", "SJC 1L-10L · Bán ra"),
    "nhan": ("sjc:Nhẫn SJC 99,99%:sell", "luong", "Nhẫn SJC 99,99% · Bán ra"),
    "pnj": ("pnj:SJC:sell", "luong", "PNJ miếng SJC · Bán ra"),
    "pnj24k": ("pnj:N24K:sell", "luong", "Nhẫn PNJ 24K · Bán ra"),
    "xau": ("xau", "usd_oz", "Vàng thế giới XAU/USD"),
    "usd": ("usdvnd", "usd_vnd", "USD/VND"),
    "bac": ("xag", "usd2", "Bạc XAG/USD"),
    "btc": ("btc", "usd", "Bitcoin BTC/USD"),
    "brent": ("brent", "usd2", "Dầu Brent"),
    "ron95": ("petrol:Xăng E10 RON 95", "lit", "Xăng RON 95"),
}
CODE_LIST = ", ".join(CODES)
BANNER_BY_PREFIX = {
    "sjc": "price_gold",
    "pnj": "price_gold",
    "petrol": "price_petrol",
    "xau": "price_world",
    "usdvnd": "price_usd",
    "xag": "price_silver",
    "btc": "price_btc",
    "brent": "price_oil",
}
OPS = {
    "<": "<=", "<=": "<=", "duoi": "<=", "dưới": "<=", "giam": "<=", "thap": "<=",
    ">": ">=", ">=": ">=", "tren": ">=", "trên": ">=", "tang": ">=", "cao": ">=",
}  # fmt: skip
TARGET_RE = re.compile(r"^\s*(\w+)\s*(<=|>=|<|>|[^\W\d_]+)?\s*([\d.,]+)\s*(tr|k)?\s*$", re.I)
MAX_TARGETS = 20


def banner_name(key: str) -> str | None:
    for prefix, name in BANNER_BY_PREFIX.items():
        if key.startswith(prefix):
            return name
    return None


def resolve(code: str, values: dict) -> str | None:
    """Row key for a code in the current prices ({key: (value, label, unit)}); prefix match."""
    key = CODES[code][0]
    if key in values:
        return key
    return next((k for k in values if k.startswith(key)), None)


def to_value(unit: str, num: str, suffix: str | None) -> float:
    """'130' (triệu/lượng), '21.000', '60,000', '26k' ... -> the number in the tracker's unit."""
    suffix = (suffix or "").lower()
    if unit == "luong":
        v = float(num.replace(",", "."))
        return v * 1e3 if suffix == "k" else (v * 1e6 if v < 1000 or suffix == "tr" else v)
    if unit in ("lit", "usd_vnd"):
        v = float(re.sub(r"[.,]", "", num))
        return v * 1000 if (suffix == "k" or v < 1000) else v
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", num):  # US thousands: 60,000
        num = num.replace(",", "")
    return float(num.replace(",", "."))


def parse_target(text: str) -> tuple[str, str, float] | str:
    """'sjc 130' / 'sjc <130' / 'btc tren 70000' -> (code, op, value); else an error string."""
    m = TARGET_RE.match(text)
    if not m:
        return "Cú pháp: /bao <mã> [dưới|trên] <giá>, ví dụ /bao sjc 130 (triệu) hoặc /bao btc trên 70000"
    code, op_word, num, suffix = m.group(1).lower(), (m.group(2) or "<=").lower(), m.group(3), m.group(4)
    if code not in CODES:
        return f"Mã không có: {esc(code)}. Mã hợp lệ: {CODE_LIST}"
    if op_word not in OPS:
        return "Dùng 'dưới' (báo khi giá ≤) hoặc 'trên' (báo khi giá ≥)."
    try:
        value = to_value(CODES[code][1], num, suffix)
    except ValueError:
        return "Không đọc được con số."
    return code, OPS[op_word], value


def cmd_bao(tg: Telegram, state, args, text: str) -> str:
    parsed = parse_target(text)
    if isinstance(parsed, str):
        return parsed
    code, op, value = parsed
    targets = state.data.setdefault("targets", [])
    if len(targets) >= MAX_TARGETS:
        return f"Đã đủ {MAX_TARGETS} cảnh báo. Xoá bớt bằng /xoabao <số>."
    nid = max([t["id"] for t in targets], default=0) + 1
    targets.append({"id": nid, "code": code, "op": op, "value": value, "fired": False})
    _, unit, label = CODES[code]
    sign = "≤" if op == "<=" else "≥"
    return f"🎯 Đã đặt báo #{nid}: {esc(label)} {sign} <b>{fmt_value(unit, value)}</b>. /dsbao để xem."


def cmd_dsbao(tg: Telegram, state, args, text: str) -> str:
    targets = state.data.get("targets", [])
    if not targets:
        return "Chưa có cảnh báo nào. Ví dụ: /bao sjc 130"
    lines = ["🎯 <b>Cảnh báo giá của bạn</b>"]
    for t in targets:
        _, unit, label = CODES[t["code"]]
        sign = "≤" if t["op"] == "<=" else "≥"
        done = " (đã báo, chờ giá rời ngưỡng)" if t.get("fired") else ""
        lines.append(f"#{t['id']} {esc(label)} {sign} {fmt_value(unit, t['value'])}{done}")
    lines.append("Xoá: /xoabao <số> hoặc /xoabao all")
    return "\n".join(lines)


def cmd_xoabao(tg: Telegram, state, args, text: str) -> str:
    targets = state.data.setdefault("targets", [])
    if text.strip().lower() == "all":
        targets.clear()
        return "🗑 Đã xoá hết cảnh báo."
    if not text.strip().isdigit():
        return "Cú pháp: /xoabao <số> (xem số ở /dsbao) hoặc /xoabao all"
    before = len(targets)
    targets[:] = [t for t in targets if t["id"] != int(text)]
    return "🗑 Đã xoá." if len(targets) < before else "Không có cảnh báo số đó."


def cmd_bieudo(tg: Telegram, state, args, text: str) -> str | None:
    """/bieudo sjc 30 -> daily line chart of that price over the last N days (default 7)."""
    from shared.charts import line_chart

    parts = text.lower().split()
    code = parts[0] if parts else "sjc"
    if code not in CODES:
        return f"Cú pháp: /bieudo <mã> [số ngày], ví dụ /bieudo sjc 30. Mã: {CODE_LIST}"
    days = min(max(int(parts[1]), 2), 90) if len(parts) > 1 and parts[1].isdigit() else 7
    key, unit, label = CODES[code]
    rows_key = key
    if not (labels_values := history.series(rows_key, days))[1]:  # prefix keys (petrol)
        for k in history.keys_with_prefix(key):
            labels_values = history.series(k, days)
            if labels_values[1]:
                break
    labels, values = labels_values
    if len(values) < 2:
        return "Chưa đủ lịch sử giá để vẽ biểu đồ (bot mới ghi từ khi bắt đầu chạy)."
    chart = line_chart(
        values,
        [labels[0], labels[len(labels) // 2], labels[-1]],
        label,
        f"{days} ngày qua · cao nhất {fmt_value(unit, max(values))} · thấp nhất {fmt_value(unit, min(values))}",
        fmt=lambda v: fmt_value(unit, v),
    )
    if not chart:
        return "Không vẽ được biểu đồ lúc này."
    tg.send(f"{header('📈 Biểu đồ giá')}\n{esc(label)} · {days} ngày qua", photo=chart)
    return None


def check_targets(tg: Telegram, state, values: dict) -> None:
    """values = {row key: (value, label, unit)} of ALL current prices. A target fires once, then
    re-arms when the price leaves the threshold; fired is set only after Telegram accepted it."""
    for t in state.data.get("targets", []):
        key = resolve(t["code"], values)
        if not key:
            continue
        val, _, unit = values[key]
        hit = val <= t["value"] if t["op"] == "<=" else val >= t["value"]
        if hit and not t.get("fired"):
            sign = "≤" if t["op"] == "<=" else "≥"
            tg.send(
                f"{header('🎯 Giá chạm ngưỡng bạn đặt')}\n"
                f"{esc(CODES[t['code']][2])}: <b>{fmt_value(unit, val)}</b> {sign} {fmt_value(unit, t['value'])}\n"
                f"ℹ️ <i>Báo 1 lần; giá rời ngưỡng rồi chạm lại sẽ báo tiếp. Xoá: /xoabao {t['id']}</i>",
                photo=asset_url(banner_name(key)) if banner_name(key) else None,
            )
            t["fired"] = True
        elif not hit and t.get("fired"):
            t["fired"] = False


# ---------- lines added to a price message ----------


def spread_line(tracker: dict) -> str:
    rows = {k: (label, v) for k, label, v in tracker["rows"]}
    for key, (label, buy) in rows.items():
        sell = rows.get(key[:-4] + ":sell") if key.endswith(":buy") else None
        if sell and buy:
            diff = sell[1] - buy
            return f"↔️ Chênh mua–bán {esc(label.split(' · ')[0])}: {diff / 1e6:.2f} tr ({diff / buy * 100:.1f}%)"
    return ""


def sjc_vs_pnj(fetched: dict) -> str:
    def val(name: str, key: str) -> float | None:
        return next((v for k, _, v in fetched.get(name, {}).get("rows", []) if k == key), None)

    s, p = val("sjc", "sjc:SJC 1L-10L:sell"), val("pnj", "pnj:SJC:sell")
    if s is None or p is None:
        return ""
    d = p - s
    if abs(d) < 10_000:
        return "⚖️ PNJ bán miếng SJC bằng giá SJC"
    return f"⚖️ PNJ bán miếng SJC {'đắt' if d > 0 else 'rẻ'} hơn SJC {abs(d) / 1e6:.2f} tr"


def extreme_line(tracker: dict) -> str:
    for key, label, v in tracker["rows"]:
        if key.endswith(":sell") or len(tracker["rows"]) == 1:
            where = history.extreme(key, v)
            if where:
                name = esc(label.replace(" · Bán", " · Bán ra"))
                return f"{'🏷 Thấp' if where == 'low' else '🔝 Cao'} nhất 30 ngày: {name}"
    return ""


def changes_line(tracker: dict) -> str:
    c = tracker.get("changes_today") or {}
    if c.get("times", 0) >= 2:
        return (
            f"🔁 Hôm nay giá SJC đã đổi {c['times']} lần "
            f"({fmt_delta('luong', c['day_change_sell'])} so với đầu ngày)"
        )
    return ""


def extras_for(name: str, tracker: dict, fetched: dict) -> list[str]:
    lines = []
    if name in ("sjc", "pnj"):
        lines += [spread_line(tracker), sjc_vs_pnj(fetched)]
    if name == "sjc":
        lines.append(changes_line(tracker))
    lines.append(extreme_line(tracker))
    return [line for line in lines if line]


# ---------- reminders ----------


def petrol_reminder(tg: Telegram, state, fetch_brent, now: datetime | None = None) -> None:
    """Thursday afternoon: petrol prices are usually re-set around 15h. Adds the Brent move vs
    last Thursday as a rough, clearly-labelled hint (Petrolimex follows Singapore/Platts + a
    stabilisation fund, so this is indicative only)."""
    now = now or datetime.now()
    today = f"{now:%Y-%m-%d}"
    if now.weekday() != 3 or now.hour < 13 or state.data.get("petrol_remind_day") == today:
        return
    try:
        brent = float(fetch_brent({})["rows"][0][2])
    except Exception:  # noqa: BLE001 - the reminder is useful even without the oil number
        brent = None
    ref = state.data.get("brent_ref")
    lines = [
        header("⛽ Kỳ điều hành giá xăng dầu", now),
        "Hôm nay thứ Năm: giá xăng dầu trong nước thường được điều chỉnh vào chiều nay (khoảng 15h).",
    ]
    if brent and ref and ref["day"] != today:
        pct = (brent - ref["value"]) / ref["value"] * 100
        trend = "tăng" if pct >= 1.5 else "giảm" if pct <= -1.5 else "đi ngang"
        lines.append(
            f"🛢 Dầu Brent ${brent:,.2f} ({pct:+.1f}% so với thứ Năm trước) → giá xăng có thể {trend}."
        )
        lines.append("ℹ️ <i>Chỉ là gợi ý tham khảo: giá thật còn phụ thuộc giá Singapore và quỹ bình ổn.</i>")
    elif brent:
        lines.append(f"🛢 Dầu Brent hiện ${brent:,.2f} (tuần sau sẽ có mốc để so sánh).")
    tg.send("\n".join(lines), photo=asset_url("price_petrol"))
    state.data["petrol_remind_day"] = today
    if brent:
        state.data["brent_ref"] = {"value": brent, "day": today}


def token_warning(tg: Telegram, state, cfg: dict, today: date | None = None) -> None:
    """The cron-job.org -> GitHub token expires; warn daily during the last `token_warn_days`."""
    raw = cfg.get("cron_token_expires")
    if not raw:
        return
    try:
        expires = date.fromisoformat(str(raw))
    except ValueError:
        return
    today = today or date.today()
    left = (expires - today).days
    if left > cfg.get("token_warn_days", 14) or state.data.get("token_warn_day") == str(today):
        return
    when = f"còn {left} ngày" if left >= 0 else f"đã hết hạn {-left} ngày trước"
    tg.send(
        f"{header('⏰ Token GitHub sắp hết hạn')}\n"
        f"Token của cron-job.org hết hạn {expires:%d/%m/%Y} ({when}).\n"
        "Tạo token mới (Actions: Read and write) → dán vào cron-job.org → sửa "
        "<code>cron_token_expires</code> trong price/config.toml. Hết hạn thì bot sẽ ngừng chạy."
    )
    state.data["token_warn_day"] = str(today)
