"""Price bot: gold, petrol and other prices - alerts whenever a price updates.

Run (from LifeBots/):
    python -m price.bot --dry --now      # preview all current prices, nothing sent
    python -m price.bot --loop           # keep running, alert on every update
Which prices to track (and the minimum change worth an alert) is set in price/config.toml.
A price is only marked "reported" AFTER Telegram accepted the message, so nothing is lost.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import httpx

from price import history
from price.trackers import TRACKERS, fmt_delta, fmt_value
from shared.core import State, Telegram, esc, header, run
from shared.health import heartbeat_text, watchdog

log = logging.getLogger(__name__)
FETCH_ERRORS = (
    httpx.HTTPError,
    ET.ParseError,
    KeyError,
    IndexError,
    ValueError,
    AttributeError,
    TypeError,
)
WATCHED = ["news", "weather", "stuff"]


def build_message(
    tracker: dict, prev: dict, min_pct: float, show_all: bool, now: datetime | None = None
) -> tuple[str | None, dict[str, float]]:
    """Pure: returns (message or None, {key: new_value} to commit once the message is sent)."""
    unit, lines, updates = tracker["unit"], [], {}
    for key, label, val in tracker["rows"]:
        old = prev.get(key)
        if old is None:
            updates[key] = val
            lines.append(f"{esc(label)}: <b>{fmt_value(unit, val)}</b>")
        elif val != old and abs(val - old) / old * 100 >= min_pct:
            updates[key] = val
            arrow = "🔺" if val > old else "🔻"
            lines.append(
                f"{esc(label)}: {fmt_value(unit, old)} → <b>{fmt_value(unit, val)}</b> "
                f"{arrow} {fmt_delta(unit, val - old)}"
            )
        elif show_all:
            lines.append(f"{esc(label)}: <b>{fmt_value(unit, val)}</b>")
    if not updates and not show_all:
        return None, {}
    now = now or datetime.now()
    return f"{header(esc(tracker['title']), now)}\n" + "\n".join(lines), updates


def maintenance(tg: Telegram, state: State, cfg: dict) -> None:
    """Daily heartbeat + Monday weekly summary + watching the other bots."""
    now = datetime.now()
    hour, today = cfg.get("heartbeat_hour", 9), f"{now:%Y-%m-%d}"
    if hour >= 0 and now.hour >= hour and state.data.get("heartbeat_day") != today:
        text = heartbeat_text(["price", *WATCHED])
        if now.weekday() == 0 and (weekly := history.weekly_summary()):
            text += "\n\n" + weekly
        tg.send(text)
        state.data["heartbeat_day"] = today
    watchdog(tg, state, WATCHED, cfg.get("interval_seconds", 60))


def check(tg: Telegram, state: State, args, cfg: dict) -> None:
    prev = state.data.setdefault("values", {})
    for name, fetch in TRACKERS.items():
        tcfg = cfg.get("trackers", {}).get(name, {})
        if not tcfg.get("enabled", False):
            continue
        try:
            tracker = fetch(tcfg)
            if not tracker["rows"]:
                raise ValueError("no prices parsed (did the source change its layout?)")
        except FETCH_ERRORS as e:
            state.source_failed(name, f"{type(e).__name__}: {e}", tg)
            continue
        state.source_ok(name, tg)
        msg, updates = build_message(tracker, prev, tcfg.get("min_change_pct", 0), args.now)
        if msg:
            tg.send(msg)  # raises on failure -> prev untouched -> retried next round
            prev.update(updates)
            if not tg.dry:
                labels = {k: lbl for k, lbl, _ in tracker["rows"]}
                history.append([(k, labels[k], v, tracker["unit"]) for k, v in updates.items()])
    maintenance(tg, state, cfg)


def main() -> None:
    run(Path(__file__).parent, check, 60)


if __name__ == "__main__":
    main()
