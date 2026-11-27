"""One morning briefing (default 7h): key prices + the weather + top tech headlines in ONE message.

Sent by the price bot (it already has every fresh price in hand). A run that wakes late still
delivers it, but not after `morning_hour + 4` (a "morning" briefing at 15h is just noise).
"""

import logging
from datetime import datetime

from shared.core import ROOT, State, Telegram, asset_url, esc, header, load_config

log = logging.getLogger(__name__)
WINDOW_HOURS = 4


def _value(trackers: dict, key: str) -> float | None:
    """First row whose key equals / starts with `key`, across all trackers."""
    for tracker in trackers.values():
        for k, _, v in tracker["rows"]:
            if k == key or k.startswith(key):
                return v
    return None


def price_lines(trackers: dict) -> list[str]:
    lines = []
    buy, sell = _value(trackers, "sjc:SJC 1L-10L:buy"), _value(trackers, "sjc:SJC 1L-10L:sell")
    if buy and sell:
        lines.append(f"🥇 SJC: mua {buy / 1e6:.2f} · bán <b>{sell / 1e6:.2f}</b> tr/lượng")
    if ron := _value(trackers, "petrol:Xăng E10 RON 95"):
        lines.append(f"⛽ Xăng RON 95: <b>{ron:,.0f}đ</b>/lít")
    if usd := _value(trackers, "usdvnd"):
        lines.append(f"💵 USD/VND: <b>{usd:,.0f}</b>")
    if xau := _value(trackers, "xau"):
        lines.append(f"🌍 Vàng thế giới: <b>${xau:,.0f}</b>/oz")
    return lines


def morning_text(trackers: dict, now: datetime) -> str:
    parts = [header("🌅 Bản tin buổi sáng", now)]
    if prices := price_lines(trackers):
        parts.append("\n".join(prices))
    try:
        from weather.bot import digest_lines

        if weather := digest_lines(load_config(ROOT / "weather")):
            parts.append(weather)
    except Exception:  # noqa: BLE001 - a missing piece must not cancel the briefing
        log.exception("morning: weather part failed")
    try:
        from news.bot import top_headlines

        heads = top_headlines(load_config(ROOT / "news"), 3)
        if heads:
            parts.append(
                "📰 <b>Tin công nghệ nổi bật</b>\n"
                + "\n".join(f'• <a href="{esc(link)}">{esc(title)}</a>' for title, link in heads)
            )
    except Exception:  # noqa: BLE001
        log.exception("morning: news part failed")
    return "\n\n".join(parts)


def send_morning(
    tg: Telegram, state: State, cfg: dict, trackers: dict, now: datetime | None = None
) -> None:
    """Once per day at/after `morning_hour` (-1 = off). Marked sent only after Telegram accepted."""
    now = now or datetime.now()
    hour = cfg.get("morning_hour", -1)  # off unless price/config.toml turns it on
    today = f"{now:%Y-%m-%d}"
    if hour < 0 or not (hour <= now.hour < hour + WINDOW_HOURS):
        return
    if state.data.get("morning_day") == today:
        return
    tg.send(morning_text(trackers, now), photo=asset_url("morning"))
    state.data["morning_day"] = today
