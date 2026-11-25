"""Price history (CSV, one row per change) and the weekly summary built from it."""

import csv
from datetime import datetime, timedelta
from pathlib import Path

from price.trackers import fmt_value
from shared.core import esc

HISTORY = Path(__file__).resolve().parent / "history.csv"


def append(
    rows: list[tuple[str, str, float, str]], path: Path = HISTORY, now: datetime | None = None
) -> None:
    """rows: (key, label, value, unit)"""
    ts = (now or datetime.now()).isoformat(timespec="seconds")
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "key", "label", "value", "unit"])
        for key, label, value, unit in rows:
            w.writerow([ts, key, label, value, unit])


def weekly_summary(path: Path = HISTORY, days: int = 7, now: datetime | None = None) -> str | None:
    """Min / max / latest per price over the last `days`, only for prices that moved."""
    if not path.exists():
        return None
    since = (now or datetime.now()) - timedelta(days=days)
    series: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if datetime.fromisoformat(r["ts"]) < since:
                continue
            s = series.setdefault(r["key"], {"label": r["label"], "unit": r["unit"], "v": []})
            s["v"].append(float(r["value"]))
    lines = []
    for s in series.values():
        if len(set(s["v"])) < 2:
            continue
        lo, hi, last = min(s["v"]), max(s["v"]), s["v"][-1]
        u = s["unit"]
        lines.append(
            f"{esc(s['label'])}: {fmt_value(u, lo)} – {fmt_value(u, hi)} (hiện {fmt_value(u, last)})"
        )
    return ("📊 <b>Giá tuần qua</b>\n" + "\n".join(lines[:14])) if lines else None
