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


def _rows(key: str, path: Path) -> list[tuple[datetime, float]]:
    if not path.exists():
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["key"] == key:
                out.append((datetime.fromisoformat(r["ts"]), float(r["value"])))
    return sorted(out)


def keys_with_prefix(prefix: str, path: Path = HISTORY) -> list[str]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return sorted({r["key"] for r in csv.DictReader(f) if r["key"].startswith(prefix)})


def series(
    key: str, days: int = 7, path: Path = HISTORY, now: datetime | None = None
) -> tuple[list[str], list[float]]:
    """Daily values (last known value of each day, carried forward) for the last `days` days:
    (labels 'dd/mm', values). Empty lists when there is not enough history."""
    now = now or datetime.now()
    rows = _rows(key, path)
    if not rows:
        return [], []
    labels, values = [], []
    for back in range(days, -1, -1):
        day = (now - timedelta(days=back)).date()
        upto = [v for ts, v in rows if ts.date() <= day]
        if upto:
            labels.append(f"{day:%d/%m}")
            values.append(upto[-1])
    return labels, values


def extreme(
    key: str, value: float, days: int = 30, path: Path = HISTORY, now: datetime | None = None
) -> str | None:
    """'low' / 'high' when `value` is the lowest / highest of the last `days` days, else None.
    Needs a real history (>= 4 earlier points over >= 2 days) so a young history never claims it."""
    now = now or datetime.now()
    recent = [(ts, v) for ts, v in _rows(key, path) if ts >= now - timedelta(days=days)]
    if len(recent) < 4 or (recent[-1][0] - recent[0][0]) < timedelta(days=2):
        return None
    vals = [v for _, v in recent]
    if value < min(vals):
        return "low"
    if value > max(vals):
        return "high"
    return None


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
