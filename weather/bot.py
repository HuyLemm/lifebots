"""Weather bot: today's summary plus anomaly alerts (Open-Meteo, free, no key).

Run (from LifeBots/):
    python -m weather.bot --dry --now    # preview today's summary, nothing sent
    python -m weather.bot --loop
Sends the current weather + rain chance at fixed times every day (report_hours in config.toml,
default 0h, 8h, 12h, 16h, 20h), and alerts at ANY time on: rain soon, storms, heavy rain, heat, cold,
strong UV, strong wind, a big temperature swing vs yesterday, and bad air quality.
"""

import time
from datetime import datetime
from pathlib import Path

from shared.core import State, Telegram, esc, get_json, run
from shared.health import watchdog

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


def fetch(lat: float, lon: float) -> dict:
    daily = get_json(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat,
        longitude=lon,
        daily="weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
        "precipitation_sum,precipitation_probability_max,uv_index_max,wind_speed_10m_max",
        current="temperature_2m,apparent_temperature,weather_code,precipitation",
        past_days=1,
        forecast_days=1,
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
        timezone="auto",
    )
    d = daily["daily"]  # index 0 = yesterday, 1 = today
    h = hourly["hourly"]
    return {
        "now": daily["current"]["temperature_2m"],
        "feels": daily["current"]["apparent_temperature"],
        "now_code": daily["current"]["weather_code"],
        "now_mm": daily["current"]["precipitation"],
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
    }


def rain_peak(d: dict) -> str:
    """Hour with the highest rain chance in the next 24h, e.g. '16h (80%)'."""
    probs = [p or 0 for p in d["hours_prob"]]
    i = max(range(len(probs)), key=probs.__getitem__)
    return f"{d['hours_time'][i][11:13]}h ({probs[i]:.0f}%)" if probs[i] else "không có"


def cooled_down(state: State, key: str, hours: float) -> bool:
    return time.time() - state.data.get("last", {}).get(key, 0) >= hours * 3600


def mark(state: State, key: str) -> None:
    state.data.setdefault("last", {})[key] = time.time()


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


def summary(d: dict, place: str, hrs: int, now: datetime) -> str:
    soon = max([p or 0 for p in d["hours_prob"][:hrs]] or [0])
    return (
        f"📍 <b>Thông báo lúc {now:%H:%M} ({now:%d/%m})</b> · {place}\n"
        f"{wmo_label(d['now_code'])} · <b>{d['now']:.0f}°C</b> (cảm giác {d['feels']:.0f}°C)\n"
        f"🌧 Mưa {hrs}h tới: <b>{soon:.0f}%</b> · cả ngày: {d['rain_day_prob']:.0f}%\n"
        f"😷 AQI {d['aqi']:.0f} ({aqi_label(d['aqi'])})"
    )


def due_slot(
    now: datetime, report_hours: list[int], last: str | None, grace_hours: int = 2
) -> str | None:
    """Slot key like '2026-11-25-12' for a report hour that is due and not yet sent.

    A slot stays due for `grace_hours` (default: its own hour + the next), so a late scheduler
    (GitHub cron can be hours late) still delivers the report, labelled with the real time.
    """
    for slot in sorted(report_hours, reverse=True):
        if 0 <= now.hour - slot < grace_hours:
            key = f"{now:%Y-%m-%d}-{slot}"
            return None if key == last else key
    return None


def check(tg: Telegram, state: State, args, cfg: dict) -> None:
    hrs = cfg.get("look_ahead_hours", 3)
    d = fetch(cfg["latitude"], cfg["longitude"])
    place = esc(cfg.get("place", ""))

    due = [
        (k, text) for k, cooldown, text in anomalies(d, cfg, hrs) if cooled_down(state, k, cooldown)
    ]
    now = datetime.now()
    slot = due_slot(now, cfg.get("report_hours", [0, 8, 12, 16, 20]), state.data.get("last_report"))

    parts = ([summary(d, place, hrs, now)] if (slot or args.now) else []) + [t for _, t in due]
    if parts:
        tg.send("\n\n".join(parts))  # raises on failure -> nothing marked -> retried next round
        for key, _ in due:
            mark(state, key)
        if slot:
            state.data["last_report"] = slot
    watchdog(tg, state, ["price"], cfg.get("interval_seconds", 300))


def main() -> None:
    run(Path(__file__).parent, check, 300)


if __name__ == "__main__":
    main()
