"""Cross-bot health: a bot that is dead stops touching its state.json, so a living bot can notice.

Used by the price bot (watches news/weather/stuff) and the weather bot (watches price).
"""

import time
import tomllib
from datetime import datetime
from pathlib import Path

from shared.core import ROOT, State, Telegram, esc


def _interval(name: str) -> int:
    try:
        with open(ROOT / name / "config.toml", "rb") as f:
            return int(tomllib.load(f).get("interval_seconds", 600))
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return 600


def bot_age(name: str) -> float | None:
    """Seconds since that bot last saved its state; None if it has no state (not running here)."""
    p: Path = ROOT / name / "state.json"
    return time.time() - p.stat().st_mtime if p.exists() else None


def watchdog(tg: Telegram, state: State, watch: list[str], my_interval: int) -> None:
    """Alert once when a watched bot goes silent, and once when it comes back."""
    gap = time.time() - state.data.get("last_check", time.time())
    if gap > 3 * my_interval:
        return  # this machine/bot was asleep: everybody looks stale, don't cry wolf
    flags = state.data.setdefault("watch", {})
    for name in watch:
        age = bot_age(name)
        if age is None:
            continue
        stale = age > max(3 * _interval(name), 600)
        if stale and not flags.get(name):
            flags[name] = True
            tg.safe_send(
                f"🚨 Bot <b>{esc(name)}</b> không hoạt động ({age / 60:.0f} phút không cập nhật)"
            )
        elif not stale and flags.get(name):
            flags[name] = False
            tg.safe_send(f"✅ Bot <b>{esc(name)}</b> đã hoạt động lại")


def heartbeat_text(names: list[str]) -> str:
    lines = [f"✅ <b>LifeBots</b> · {datetime.now():%d/%m %H:%M}"]
    for n in names:
        age = bot_age(n)
        if age is None:
            lines.append(f"• {n}: không có dữ liệu")
        else:
            ok = age <= max(3 * _interval(n), 600)
            lines.append(f"• {n}: {'ổn' if ok else '⚠️ im lặng'} ({age:.0f}s trước)")
    return "\n".join(lines)
