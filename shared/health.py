"""Cross-bot health: a bot that is dead stops touching its state.json, so a living bot can notice.

Used by the price bot (watches news/weather/stuff) and the weather bot (watches price).
"""

import json
import os
import time
import tomllib
from pathlib import Path

from shared.core import ROOT, State, Telegram, esc, header


def _interval(name: str) -> int:
    try:
        with open(ROOT / name / "config.toml", "rb") as f:
            return int(tomllib.load(f).get("interval_seconds", 600))
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return 600


CI = bool(os.environ.get("LIFEBOTS_CI"))  # scheduled runs: GitHub cron is routinely 5-30 min late


def _limit(name: str) -> float:
    """Seconds of silence after which a bot counts as dead."""
    return max(3 * _interval(name), 4500 if CI else 600)


def bot_age(name: str) -> float | None:
    """Seconds since that bot last completed a check; None if it has no state here."""
    p: Path = ROOT / name / "state.json"
    if not p.exists():
        return None
    try:
        last = json.loads(p.read_text(encoding="utf-8")).get("last_check")
    except (OSError, ValueError):
        last = None
    return time.time() - (last or p.stat().st_mtime)


def watchdog(tg: Telegram, state: State, watch: list[str], my_interval: int) -> None:
    """Alert once when a watched bot goes silent, and once when it comes back."""
    gap = time.time() - state.data.get("last_check", time.time())
    if gap > max(3 * my_interval, 4500 if CI else 0):
        return  # this machine/bot was asleep: everybody looks stale, don't cry wolf
    flags = state.data.setdefault("watch", {})
    for name in watch:
        age = bot_age(name)
        if age is None:
            continue
        stale = age > _limit(name)
        if stale and not flags.get(name):
            flags[name] = True
            tg.safe_send(
                f"{header('🚨 Cảnh báo bot')}\n"
                f"Bot <b>{esc(name)}</b> không hoạt động ({age / 60:.0f} phút không cập nhật)"
            )
        elif not stale and flags.get(name):
            flags[name] = False
            tg.safe_send(f"{header('✅ Bot')}\nBot <b>{esc(name)}</b> đã hoạt động lại")


def stats_summary(names: list[str], days: int = 7) -> str | None:
    """'📈 Số tin 7 ngày qua: price 12 · news 210 ...' from each bot's state['stats'] (counted
    by the run loop), plus how many sources are currently failing. None before any stats exist."""
    parts, sick = [], []
    for n in names:
        try:
            data = json.loads((ROOT / n / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stats = sorted(data.get("stats", {}).items())[-days:]
        if stats:
            parts.append(f"{n} {sum(v for _, v in stats)}")
        sick += [f"{n}/{k}" for k, v in data.get("health", {}).items() if v.get("alerted")]
    if not parts:
        return None
    text = f"📈 <b>Số tin {days} ngày qua</b>: " + " · ".join(parts)
    return text + (f"\n⚠️ Nguồn đang lỗi: {esc(', '.join(sick))}" if sick else "")


def heartbeat_text(names: list[str]) -> str:
    lines = [header("✅ LifeBots đang hoạt động")]
    for n in names:
        age = bot_age(n)
        if age is None:
            lines.append(f"• {n}: không có dữ liệu")
        else:
            ok = age <= _limit(n)
            lines.append(f"• {n}: {'ổn' if ok else '⚠️ im lặng'} ({age:.0f}s trước)")
    return "\n".join(lines)
