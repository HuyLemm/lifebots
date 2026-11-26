"""Weather bot: scheduled summaries plus anomaly alerts (Open-Meteo, free, no key).

Run (from LifeBots/):
    python -m weather.bot --dry --now    # preview today's summary, nothing sent
    python -m weather.bot --loop
Sends the current weather + rain chance at fixed times every day (report_hours in config.toml,
default 0h, 8h, 12h, 16h, 20h), and alerts at ANY time on: rain soon, storms, heavy rain, heat, cold,
strong UV, strong wind, a big temperature swing vs yesterday, and bad air quality.

Extras: several places (config [[places]] + /them), a commute reminder (commute_times), an hourly
rain chart on the summary, the worst-AQI hour, a 2-day outlook and rule-based suggestions.
"""

import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from shared.charts import bar_chart
from shared.core import State, Telegram, asset_url, esc, get_json, header, load_config, run
from shared.health import watchdog

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
FETCH_ERRORS = (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError)
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
MAX_EXTRA_PLACES = 4  # each place costs 3 API calls per run

AQI_LEVELS = [
    (50, "Tốt 🟢"),
    (100, "Trung bình 🟡"),
    (150, "Kém cho người nhạy cảm 🟠"),
    (200, "Xấu 🔴"),
    (300, "Rất xấu 🟣"),
    (10**6, "Nguy hại 🟤"),
]

# WMO weather codes -> Vietnamese
WMO = {
    0: "Trời quang ☀️", 1: "Ít mây 🌤", 2: "Có mây ⛅", 3: "Nhiều mây ☁️",
    45: "Sương mù 🌫", 48: "Sương mù 🌫",
    51: "Mưa phùn 🌦", 53: "Mưa phùn 🌦", 55: "Mưa phùn dày 🌦", 56: "Mưa phùn lạnh 🌦", 57: "Mưa phùn lạnh 🌦",
    61: "Mưa nhỏ 🌧", 63: "Mưa vừa 🌧", 65: "Mưa to 🌧", 66: "Mưa lạnh 🌧", 67: "Mưa lạnh 🌧",
    71: "Tuyết 🌨", 73: "Tuyết 🌨", 75: "Tuyết dày 🌨", 77: "Tuyết hạt 🌨",
    80: "Mưa rào nhẹ 🌦", 81: "Mưa rào 🌧", 82: "Mưa rào rất to ⛈",
    85: "Tuyết rào 🌨", 86: "Tuyết rào 🌨",
    95: "Dông ⛈", 96: "Dông kèm mưa đá ⛈", 99: "Dông kèm mưa đá lớn ⛈",
}  # fmt: skip


def aqi_label(v: float) -> str:
    return next(label for limit, label in AQI_LEVELS if v <= limit)


def wmo_label(code: int) -> str:
    return WMO.get(code, f"Mã thời tiết {code}")


def _at(seq, i: int):
    try:
        return seq[i]
    except (IndexError, TypeError, KeyError):
        return None


def fetch(lat: float, lon: float) -> dict:
    daily = get_json(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat,
        longitude=lon,
        daily="weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
        "precipitation_sum,precipitation_probability_max,uv_index_max,wind_speed_10m_max",
        current="temperature_2m,apparent_temperature,weather_code,precipitation,"
        "relative_humidity_2m,wind_speed_10m",
        past_days=1,
        forecast_days=3,  # index 0 = yesterday, 1 = today, 2 = tomorrow, 3 = the day after
        timezone="auto",
    )
    hourly = get_json(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat,
        longitude=lon,
        hourly="precipitation_probability,precipitation",
        forecast_hours=24,
        timezone="auto",
    )
    air = get_json(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=lat,
        longitude=lon,
        current="us_aqi,pm2_5",
        hourly="us_aqi",
        forecast_hours=24,
        timezone="auto",
    )
    d = daily["daily"]
    h = hourly["hourly"]
    air_h = air.get("hourly") or {}
    outlook = []
    for i in (2, 3):
        if day := _at(d.get("time"), i):
            outlook.append(
                {
                    "date": day,
                    "tmin": _at(d["temperature_2m_min"], i),
                    "tmax": _at(d["temperature_2m_max"], i),
                    "rain": _at(d["precipitation_probability_max"], i),
                    "code": _at(d["weather_code"], i),
                }
            )
    return {
        "now": daily["current"]["temperature_2m"],
        "feels": daily["current"]["apparent_temperature"],
        "now_code": daily["current"]["weather_code"],
        "now_mm": daily["current"]["precipitation"],
        "humidity": daily["current"].get("relative_humidity_2m"),
        "now_wind": daily["current"].get("wind_speed_10m"),
        "code": d["weather_code"][1],
        "tmax": d["temperature_2m_max"][1],
        "tmin": d["temperature_2m_min"][1],
        "feels_max": d["apparent_temperature_max"][1],
        "yesterday_max": d["temperature_2m_max"][0],
        "rain_day_prob": d["precipitation_probability_max"][1],
        "rain_day_mm": d["precipitation_sum"][1],
        "uv": d["uv_index_max"][1],
        "wind": d["wind_speed_10m_max"][1],
        "hours_prob": h["precipitation_probability"],
        "hours_mm": h["precipitation"],
        "hours_time": h["time"],
        "aqi": air["current"]["us_aqi"],
        "pm25": air["current"]["pm2_5"],
        "aqi_hours": air_h.get("us_aqi") or [],
        "aqi_hours_time": air_h.get("time") or [],
        "outlook": outlook,
    }


def rain_peak(d: dict) -> str:
    """Hour with the highest rain chance in the next 24h, e.g. '16h (80%)'."""
    probs = [p or 0 for p in d["hours_prob"]]
    i = max(range(len(probs)), key=probs.__getitem__)
    return f"{d['hours_time'][i][11:13]}h ({probs[i]:.0f}%)" if probs[i] else "không có"


# ---------- cooldown bookkeeping (per place: `store` is a dict) ----------


def _cooled(store: dict, key: str, hours: float) -> bool:
    return time.time() - store.get("last", {}).get(key, 0) >= hours * 3600


def _mark(store: dict, key: str) -> None:
    store.setdefault("last", {})[key] = time.time()


def cooled_down(state: State, key: str, hours: float) -> bool:
    return _cooled(state.data, key, hours)


def mark(state: State, key: str) -> None:
    _mark(state.data, key)


def anomalies(d: dict, cfg: dict, hrs: int) -> list[tuple[str, float, str]]:
    """[(key, cooldown_hours, text)] for everything currently abnormal."""
    quick, daily = cfg.get("cooldown_hours", 4), 12  # daily-type alerts repeat at most twice/day
    out = []
    soon_p = max([p or 0 for p in d["hours_prob"][:hrs]] or [0])
    soon_mm = sum(d["hours_mm"][:hrs])
    if soon_p >= cfg.get("rain_probability_pct", 60) or soon_mm >= cfg.get("rain_mm", 1.0):
        out.append(
            (
                "rain",
                quick,
                f"🌧 <b>Sắp mưa</b> trong {hrs}h tới (xác suất {soon_p:.0f}%, ~{soon_mm:.1f} mm). Mang áo mưa!",
            )
        )
    if d["aqi"] >= cfg.get("aqi_alert", 150):
        out.append(
            (
                "aqi",
                quick,
                f"😷 <b>Không khí xấu</b>: AQI {d['aqi']:.0f} ({aqi_label(d['aqi'])}), PM2.5 {d['pm25']:.0f}. Nên đeo khẩu trang.",
            )
        )
    if d["code"] >= 95:
        out.append(
            (
                "storm",
                daily,
                f"⛈ <b>Có dông</b> hôm nay ({wmo_label(d['code'])}). Tránh chỗ trống trải, cây cao.",
            )
        )
    if d["rain_day_mm"] >= cfg.get("heavy_rain_mm", 30):
        out.append(
            (
                "heavy_rain",
                daily,
                f"🌊 <b>Mưa rất lớn</b> cả ngày ~{d['rain_day_mm']:.0f} mm, đề phòng ngập.",
            )
        )
    if d["feels_max"] >= cfg.get("heat_c", 38):
        out.append(
            (
                "heat",
                daily,
                f"🥵 <b>Nắng nóng</b>: cảm giác tới {d['feels_max']:.0f}°C. Uống đủ nước, hạn chế ra nắng.",
            )
        )
    if d["tmin"] <= cfg.get("cold_c", 18):
        out.append(("cold", daily, f"🥶 <b>Trời lạnh</b>: thấp nhất {d['tmin']:.0f}°C. Mặc ấm."))
    if d["uv"] >= cfg.get("uv_alert", 8):
        out.append(
            ("uv", daily, f"🕶 <b>UV cao</b>: chỉ số {d['uv']:.0f}. Che nắng, thoa kem chống nắng.")
        )
    if d["wind"] >= cfg.get("wind_kmh", 40):
        out.append(("wind", daily, f"💨 <b>Gió mạnh</b>: tới {d['wind']:.0f} km/h."))
    swing = d["tmax"] - d["yesterday_max"]
    if abs(swing) >= cfg.get("temp_swing_c", 5):
        out.append(
            (
                "swing",
                daily,
                f"🌡 <b>Nhiệt độ đổi mạnh</b>: cao nhất {d['tmax']:.0f}°C, {'nóng' if swing > 0 else 'lạnh'} hơn hôm qua {abs(swing):.0f}°C.",
            )
        )
    return out  # fmt: skip


LEGEND = (
    "Cảm giác = nhiệt độ cơ thể thật sự cảm nhận (tính cả độ ẩm, gió) · "
    "AQI: ≤50 tốt, 51–100 vừa, >150 xấu"
)


def worst_aqi(d: dict) -> tuple[str, float] | None:
    """(hour like '17h', AQI) of the worst forecast hour in the next 24h, or None if unknown."""
    vals = [-1 if v is None else v for v in d.get("aqi_hours") or []]
    if not vals:
        return None
    i = max(range(len(vals)), key=vals.__getitem__)
    if vals[i] < 0:
        return None
    times = d.get("aqi_hours_time") or []
    hh = times[i][11:13] if i < len(times) else ""
    return (f"{int(hh)}h" if hh.isdigit() else ""), vals[i]


def reasons(d: dict, hrs: int, max_notes: int = 3) -> list[str]:
    """Short 'because' lines, each derived from the numbers we actually have."""
    out = []
    gap, hum, wind = d["feels"] - d["now"], d.get("humidity"), d.get("now_wind")
    soon = max([p or 0 for p in d["hours_prob"][:hrs]] or [0])
    if gap >= 3 and hum is not None and hum >= 70:
        out.append(
            f"🥵 Nóng hơn thực tế {gap:.0f}°C vì độ ẩm cao ({hum:.0f}%): mồ hôi khó bay hơi."
        )
    elif gap <= -3 and wind is not None and wind >= 15:
        out.append(f"🥶 Lạnh hơn thực tế {abs(gap):.0f}°C vì gió {wind:.0f} km/h.")
    if soon >= 50:
        out.append(
            f"☔ Nên mang áo mưa: xác suất mưa {soon:.0f}% trong {hrs}h tới "
            f"(cao nhất lúc {rain_peak(d)})."
        )
    if d["aqi"] >= 101:
        out.append(f"😷 Không khí kém (bụi mịn PM2.5 = {d['pm25']:.0f}): nên đeo khẩu trang.")
    worst = worst_aqi(d)
    # one line for the worst hour; skipped when it is about the same as right now (no repetition)
    if worst and worst[1] >= 101 and not (d["aqi"] >= 101 and worst[1] - d["aqi"] < 15):
        when = f" lúc {worst[0]}" if worst[0] else ""
        out.append(f"🌫 AQI cao nhất {worst[1]:.0f}{when}: tránh tập thể dục ngoài trời giờ đó.")
    if d["uv"] >= 6:
        out.append(f"🕶 UV {d['uv']:.0f} cao: che nắng, nhất là 10h–16h.")
    swing = d["tmax"] - d["yesterday_max"]
    if abs(swing) >= 3:
        out.append(f"🌡 Hôm nay {'nóng' if swing > 0 else 'lạnh'} hơn hôm qua {abs(swing):.0f}°C.")
    return out[:max_notes]


def suggestions(d: dict, hrs: int, have: list[str]) -> list[str]:
    """Rule-based 'what to wear / bring' lines; never repeats a topic already in `have`."""

    def seen(emoji: str) -> bool:
        return any(t.startswith(emoji) for t in have)

    soon = max([p or 0 for p in d["hours_prob"][:hrs]] or [0])
    rain_day = d.get("rain_day_prob") or 0
    out = []
    if d["tmin"] <= 22 and d["now"] <= 25 and not seen("🥶"):
        out.append(f"🧥 Se lạnh (thấp nhất {d['tmin']:.0f}°C): mang áo khoác mỏng.")
    if d["feels_max"] >= 36 and not seen("🥵"):
        out.append(f"💧 Nóng (cảm giác tới {d['feels_max']:.0f}°C): mặc đồ thoáng, mang theo nước.")
    if rain_day >= 60 and soon < 50 and not seen("☔"):
        out.append(f"🌂 Hôm nay có thể mưa ({rain_day:.0f}%): mang sẵn áo mưa.")
    if d["wind"] >= 30:
        out.append(f"💨 Gió tới {d['wind']:.0f} km/h: đi xe máy cẩn thận, buộc gọn đồ.")
    return out


def notes(d: dict, hrs: int, max_notes: int = 4) -> list[str]:
    """The 💡-style notes of a summary: data-backed reasons first, then suggestions (max 4)."""
    base = reasons(d, hrs, max_notes=99)
    return (base + suggestions(d, hrs, base))[:max_notes]


OUTLOOK_LABELS = ("Ngày mai", "Ngày kia")


def outlook_lines(d: dict) -> list[str]:
    """Compact lines for tomorrow and the day after: weather, min–max °C, rain chance."""
    out = []
    for label, o in zip(OUTLOOK_LABELS, d.get("outlook") or [], strict=False):
        if o.get("tmin") is None or o.get("tmax") is None:
            continue
        date = o.get("date") or ""
        when = f" ({date[8:10]}/{date[5:7]})" if len(date) >= 10 else ""
        what = f" {wmo_label(o['code'])}" if o.get("code") is not None else ""
        rain = f" · mưa {o['rain']:.0f}%" if o.get("rain") is not None else ""
        out.append(f"📅 {label}{when}:{what} {o['tmin']:.0f}–{o['tmax']:.0f}°C{rain}")
    return out


def banner(d: dict, now: datetime, alert_key: str | None = None) -> str:
    """Picture name matching the situation (alerts first, then the current weather)."""
    by_alert = {
        "storm": "w_storm",
        "rain": "w_rain",
        "heavy_rain": "w_rain",
        "heat": "w_heat",
        "cold": "w_cold",
        "aqi": "w_aqi",
        "uv": "w_sun",
    }
    if alert_key in by_alert:
        return by_alert[alert_key]
    code = d["now_code"]
    if code >= 95:
        return "w_storm"
    if code >= 51:
        return "w_rain"
    if code in (45, 48):
        return "w_fog"
    if code == 3:
        return "w_cloud"
    night = now.hour >= 18 or now.hour < 5
    if code in (0, 1):
        return "w_night" if night else "w_sun"
    return "w_partly"


def rain_chart(d: dict, place: str) -> bytes | None:
    """Bar chart of the rain chance for each of the next 12 hours; only when it is worth a look
    (max >= 40%). None = no chart (caller keeps the banner picture)."""
    probs = [float(p or 0) for p in d["hours_prob"][:12]]
    if not probs or max(probs) < 40:
        return None
    times = d.get("hours_time") or []
    labels = [f"{_at(times, i)[11:13]}h" if len(_at(times, i) or "") >= 13 else "" for i in range(len(probs))]
    peak = max(range(len(probs)), key=probs.__getitem__)
    where = f" · {place}" if place else ""
    when = f" lúc {labels[peak]}" if labels[peak] else ""
    return bar_chart(
        probs,
        labels,
        f"Xác suất mưa 12 giờ tới{where}",
        f"Cao nhất {probs[peak]:.0f}%{when}",
        unit="%",
        highlight={peak},
        vmax=100,
    )


def summary(d: dict, place: str, hrs: int, now: datetime, outlook: bool = False) -> str:
    soon = max([p or 0 for p in d["hours_prob"][:hrs]] or [0])
    lines = [
        f"{header(f'🌤 Thời tiết {place}', now)}",
        f"{wmo_label(d['now_code'])} · <b>{d['now']:.0f}°C</b> (cảm giác {d['feels']:.0f}°C)",
        f"🌧 Mưa {hrs}h tới: <b>{soon:.0f}%</b> · cả ngày: {d['rain_day_prob']:.0f}%",
        f"😷 AQI {d['aqi']:.0f} ({aqi_label(d['aqi'])})",
    ]
    if why := notes(d, hrs):
        lines += ["", *why]
    if outlook and (ahead := outlook_lines(d)):
        lines += ["", *ahead]
    lines += ["", f"ℹ️ <i>{LEGEND}</i>"]
    return "\n".join(lines)


def due_slot(now: datetime, report_hours: list[int], last: str | None) -> str | None:
    """Slot key like '2026-11-25-12' for the latest report hour that has passed and is unsent.

    A late scheduler (GitHub cron can be hours late) still delivers the report at its first run
    after the slot, labelled with the real time; if several slots were missed only the latest is
    sent (an old forecast is useless).
    """
    passed = [h for h in sorted(report_hours) if h <= now.hour]
    if not passed:
        return None
    key = f"{now:%Y-%m-%d}-{passed[-1]}"
    return None if key == last else key


# ---------- commute reminder ----------


def due_commute(now: datetime, times: list[str], done: list[str], window_h: float = 2.0) -> list[str]:
    """Reminder times ('HH:MM') that are due now: their time has come, it is at most `window_h`
    hours ago (a late scheduler still delivers) and they were not handled today."""
    out = []
    for t in times:
        try:
            h, m = (int(x) for x in t.split(":"))
            start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        except ValueError:
            continue
        if start <= now < start + timedelta(hours=window_h) and f"{now:%Y-%m-%d}-{t}" not in done:
            out.append(t)
    return out


def commute_warnings(d: dict, cfg: dict, look: int) -> list[tuple[str, str]]:
    """[(alert key, text)] for what is worth knowing in the next `look` hours (empty = stay quiet)."""
    out = []
    p = max([x or 0 for x in d["hours_prob"][:look]] or [0])
    mm = sum(x or 0 for x in d["hours_mm"][:look])
    if p >= cfg.get("rain_probability_pct", 60) or mm >= cfg.get("rain_mm", 1.0):
        out.append(("rain", f"☔ Mưa trong {look}h tới (xác suất {p:.0f}%, ~{mm:.1f} mm): mang áo mưa."))
    if d["code"] >= 95 or d["now_code"] >= 95:
        out.append(("storm", "⛈ Có dông: tránh chỗ trống trải, cây cao."))
    if d["feels"] >= cfg.get("commute_heat_c", 35):
        out.append(("heat", f"🥵 Đang nóng (cảm giác {d['feels']:.0f}°C): mang nước, che nắng."))
    if d["aqi"] >= cfg.get("commute_aqi", 101):
        out.append(("aqi", f"😷 Không khí kém (AQI {d['aqi']:.0f}): nên đeo khẩu trang."))
    return out


# ---------- places ----------


def slug(name: str) -> str:
    return re.sub(r"\W+", "-", name.lower()).strip("-")


def _cfg_places(cfg: dict) -> list[dict]:
    """Places defined in config.toml: top-level place/latitude/longitude first, then [[places]]."""
    out: list[dict] = []
    raw = []
    if "latitude" in cfg and "longitude" in cfg:
        raw.append((cfg.get("place", ""), cfg["latitude"], cfg["longitude"], "main"))
    raw += [(p.get("name", ""), p.get("latitude"), p.get("longitude"), "cfg") for p in cfg.get("places", [])]
    for name, lat, lon, source in raw:
        if lat is None or lon is None:
            continue
        key = "main" if not out else (slug(name) or f"p{len(out)}")
        if key in {p["key"] for p in out} or (name and slug(name) in {slug(p["name"]) for p in out}):
            continue
        out.append({"name": name, "latitude": lat, "longitude": lon, "key": key, "source": source})
    return out


def all_places(cfg: dict, state: State) -> list[dict]:
    """First = the default place (it keeps the legacy state keys), then config ones, then /them."""
    out = _cfg_places(cfg)
    for e in state.data.get("extra_places", []):
        key = slug(e["name"])
        if key and key not in {p["key"] for p in out} and slug(e["name"]) not in {slug(p["name"]) for p in out}:
            out.append({**e, "key": key, "source": "extra"})
    return out


def _store(state: State, index: int, place: dict) -> dict:
    """Per-place state. The first place uses state.data itself (legacy `last`, `last_report`
    keys) so nothing is re-sent right after an upgrade."""
    if index == 0:
        return state.data
    return state.data.setdefault("places_state", {}).setdefault(place["key"], {})


def _cfg() -> dict:
    try:
        return load_config(HERE)
    except (OSError, ValueError):
        return {}


def cmd_places(tg: Telegram, state: State, args, text: str) -> str:
    places = all_places(_cfg(), state)
    if not places:
        return "Chưa có địa điểm nào. Dùng /them Đà Nẵng"
    lines = ["📍 <b>Địa điểm đang theo dõi</b>"]
    for i, p in enumerate(places, 1):
        tag = " (mặc định)" if i == 1 else (" (trong config)" if p["source"] == "cfg" else "")
        lines.append(f"{i}. {esc(p['name'] or 'chưa đặt tên')}{tag}")
    lines.append("Thêm: /them Đà Nẵng · Bỏ: /bo Đà Nẵng")
    return "\n".join(lines)


def cmd_add_place(tg: Telegram, state: State, args, text: str) -> str:
    name = text.strip()
    if not name:
        return "Dùng: /them Đà Nẵng"
    extras = state.data.setdefault("extra_places", [])
    if len(extras) >= MAX_EXTRA_PLACES:
        return f"Đã đủ {MAX_EXTRA_PLACES} địa điểm thêm. Gõ /bo <tên> để bỏ bớt."
    try:
        found = get_json(GEOCODE_URL, name=name, count=1, language="vi").get("results") or []
    except (httpx.HTTPError, ValueError) as e:
        return f"⚠️ Chưa tra được địa điểm lúc này ({type(e).__name__}). Thử lại sau."
    if not found:
        return f"❌ Không tìm thấy “{esc(name)}”. Thử viết có dấu hoặc tên khác."
    r = found[0]
    label = r["name"]
    if slug(label) in {slug(p["name"]) for p in all_places(_cfg(), state)}:
        return f"ℹ️ “{esc(label)}” đã có trong danh sách rồi."
    extras.append({"name": label, "latitude": r["latitude"], "longitude": r["longitude"]})
    area = r.get("admin1") or r.get("country") or ""
    return f"➕ Đã thêm <b>{esc(label)}</b>{f' ({esc(area)})' if area else ''}. Bản tin kế tiếp sẽ có nơi này."


def cmd_remove_place(tg: Telegram, state: State, args, text: str) -> str:
    want = slug(text)
    if not want:
        return "Dùng: /bo Đà Nẵng"
    extras = state.data.get("extra_places", [])
    hit = next((e for e in extras if slug(e["name"]) == want), None) or next(
        (e for e in extras if want in slug(e["name"])), None
    )
    if hit:
        extras.remove(hit)
        state.data.get("places_state", {}).pop(slug(hit["name"]), None)
        return f"🗑 Đã bỏ <b>{esc(hit['name'])}</b>."
    if any(want in slug(p["name"]) for p in _cfg_places(_cfg())):
        return "Địa điểm này nằm trong config.toml (sửa ở file, không bỏ bằng lệnh được)."
    return f"Không thấy “{esc(text.strip())}” trong các địa điểm đã thêm. Xem /diadiem"


COMMANDS = {
    "/diadiem": (cmd_places, "Xem các địa điểm đang theo dõi"),
    "/them": (cmd_add_place, "Thêm địa điểm · ví dụ: /them Đà Nẵng"),
    "/bo": (cmd_remove_place, "Bỏ địa điểm đã thêm · ví dụ: /bo Đà Nẵng"),
}


# ---------- the check ----------


def check_place(
    tg: Telegram,
    store: dict,
    args,
    cfg: dict,
    p: dict,
    d: dict,
    now: datetime,
    hrs: int,
    commute: bool,
) -> None:
    place = esc(p["name"])
    today = f"{now:%Y-%m-%d}"
    due = [(k, t) for k, cooldown, t in anomalies(d, cfg, hrs) if _cooled(store, k, cooldown)]
    slot = due_slot(now, cfg.get("report_hours", [0, 8, 12, 16, 20]), store.get("last_report"))

    with_summary = bool(slot or args.now)
    want_outlook = (
        with_summary
        and bool(d.get("outlook"))
        and now.hour >= cfg.get("forecast_hour", 8)
        and (bool(args.now) or store.get("last_outlook") != today)
    )
    parts = ([summary(d, place, hrs, now, outlook=want_outlook)] if with_summary else []) + [
        t for _, t in due
    ]
    if parts:
        text = "\n\n".join(parts)
        if not with_summary:  # alerts only: still starts with the standard header
            text = f"{header(f'🌦 Cảnh báo thời tiết {place}', now)}\n{text}"
        first_alert = due[0][0] if due and not with_summary else None
        photo: str | bytes = asset_url(banner(d, now, first_alert))
        if with_summary and (chart := rain_chart(d, p["name"])):
            photo = chart  # a rain chart is worth more than the generic banner
        tg.send(text, photo=photo)  # raises on failure -> nothing marked -> retried next round
        for key, _ in due:
            _mark(store, key)
        if slot:
            store["last_report"] = slot
            if want_outlook:
                store["last_outlook"] = today

    if commute:
        send_commute(tg, store, cfg, p, d, now)


def send_commute(tg: Telegram, store: dict, cfg: dict, p: dict, d: dict, now: datetime) -> None:
    """Commute reminder (first place only): at each commute time, but only if the next few hours
    hold something worth a warning; otherwise silent. Late runs still deliver within ~2 hours."""
    today = f"{now:%Y-%m-%d}"
    done = [k for k in store.get("commute_done", []) if k.startswith(today)]
    store["commute_done"] = done
    times = due_commute(now, cfg.get("commute_times", []), done)
    if not times:
        return
    warns = commute_warnings(d, cfg, cfg.get("commute_look_hours", 2))
    if warns:
        kind = f"🚦 Nhắc trước khi ra đường {esc(p['name'])}".rstrip()
        text = f"{header(kind, now)}\n" + "\n".join(w for _, w in warns)
        tg.send(text, photo=asset_url(banner(d, now, warns[0][0])))
    done += [f"{today}-{t}" for t in times]  # also when quiet: handled for today


def check(tg: Telegram, state: State, args, cfg: dict) -> None:
    hrs = cfg.get("look_ahead_hours", 3)
    now = datetime.now()
    errors: list[Exception] = []
    for i, p in enumerate(all_places(cfg, state)):
        src = f"thời tiết {p['name']}".strip()
        try:
            d = fetch(p["latitude"], p["longitude"])
        except FETCH_ERRORS as e:
            state.source_failed(src, f"{type(e).__name__}: {e}", tg)
            continue
        state.source_ok(src, tg)
        try:
            check_place(tg, _store(state, i, p), args, cfg, p, d, now, hrs, commute=(i == 0))
        except Exception as e:  # noqa: BLE001 - one place must not block the others
            log.exception("weather check failed for %s", p["name"])
            errors.append(e)
    watchdog(tg, state, ["price"], cfg.get("interval_seconds", 300))
    if errors:
        raise errors[0]  # nothing was marked for the failed send -> retried next round


def digest_lines(cfg: dict) -> str:
    """2-3 short HTML lines (now, rain chance, AQI) for the FIRST place; '' on any error.
    No side effects: used by the morning digest."""
    try:
        p = _cfg_places(cfg)[0]
        d = fetch(p["latitude"], p["longitude"])
        hrs = cfg.get("look_ahead_hours", 3)
        soon = max([x or 0 for x in d["hours_prob"][:hrs]] or [0])
        return "\n".join(
            [
                f"{wmo_label(d['now_code'])} · <b>{d['now']:.0f}°C</b> (cảm giác {d['feels']:.0f}°C)",
                f"🌧 Mưa {hrs}h tới: <b>{soon:.0f}%</b> · cả ngày: {d['rain_day_prob']:.0f}%",
                f"😷 AQI {d['aqi']:.0f} ({aqi_label(d['aqi'])})",
            ]
        )
    except Exception as e:  # noqa: BLE001 - the digest must survive a weather outage
        log.warning("digest_lines failed: %s", e)
        return ""


def main() -> None:
    run(Path(__file__).parent, check, 300, COMMANDS)


if __name__ == "__main__":
    main()
