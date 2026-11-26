"""Price bot: gold, petrol and other prices - alerts whenever a price updates.

Run (from LifeBots/):
    python -m price.bot --dry --now      # preview all current prices, nothing sent
    python -m price.bot --loop           # keep running, alert on every update
Which prices to track (and the minimum change worth an alert) is set in price/config.toml.
A price is only marked "reported" AFTER Telegram accepted the message, so nothing is lost.
"""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from price import history
from price.trackers import TRACKERS, fmt_delta, fmt_value
from shared.core import State, Telegram, asset_url, esc, header, run
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


LEGEND = {  # one short line that explains the numbers
    "luong": "Mua vào = tiệm mua của bạn · Bán ra = tiệm bán cho bạn · đơn vị: triệu đồng/lượng "
    "(1 lượng = 37,5 g)",
    "lit": "Giá bán lẻ, đơn vị: đồng/lít",
    "usd_oz": "Giá 1 ounce (31,1 g) vàng, tính bằng USD",
    "usd_vnd": "1 USD đổi được bao nhiêu đồng (tỷ giá tham khảo, ngân hàng có thể chênh)",
}
BANNERS = {
    "sjc": "price_gold",
    "pnj": "price_gold",
    "petrol": "price_petrol",
    "world_gold": "price_world",
    "usd_vnd": "price_usd",
}


def plain_label(label: str) -> str:
    """'Vàng miếng SJC · Mua' -> '... · Mua vào' (clear wording; stored labels stay unchanged)."""
    return label.replace(" · Mua", " · Mua vào").replace(" · Bán", " · Bán ra")


def build_message(
    tracker: dict,
    prev: dict,
    min_pct: float,
    show_all: bool,
    now: datetime | None = None,
    why: Callable[[int], str] | None = None,
) -> tuple[str | None, dict[str, float]]:
    """Pure: returns (message or None, {key: new_value} to commit once the message is sent).
    `why(direction)` (+1 rose / -1 fell) gives the one-line 'due to' note for a real change."""
    unit, lines, updates, direction = tracker["unit"], [], {}, 0
    for key, label, val in tracker["rows"]:
        old, name = prev.get(key), esc(plain_label(label))
        if old is None:
            updates[key] = val
            lines.append(f"{name}: <b>{fmt_value(unit, val)}</b>")
        elif val != old and abs(val - old) / old * 100 >= min_pct:
            updates[key] = val
            direction += 1 if val > old else -1
            arrow = "🔺 tăng" if val > old else "🔻 giảm"
            lines.append(
                f"{name}: {fmt_value(unit, old)} → <b>{fmt_value(unit, val)}</b> "
                f"{arrow} {fmt_delta(unit, abs(val - old)).lstrip('+-')}"
            )
        elif show_all:
            lines.append(f"{name}: <b>{fmt_value(unit, val)}</b>")
    if not updates and not show_all:
        return None, {}
    now = now or datetime.now()
    stamp = f"🕒 Nguồn cập nhật: {esc(tracker['stamp'])}\n" if tracker["stamp"] else ""
    notes = []
    if direction and why and (reason := why(1 if direction > 0 else -1)):
        notes.append(reason)
    if unit in LEGEND:
        notes.append(f"ℹ️ <i>{esc(LEGEND[unit])}</i>")
    body = "\n".join(lines) + ("\n\n" + "\n".join(notes) if notes else "")
    return f"{header(esc(tracker['title']), now)}\n{stamp}{body}", updates


def _verb(x: float) -> str:
    return "tăng" if x > 0 else "giảm"


def market_context(fetched: dict[str, dict], prev: dict) -> dict:
    """% moves of world gold and USD/VND vs the last REPORTED value (None if unknown), plus the
    current USD/VND - the facts the 'due to' notes are built from (no guessing beyond them)."""
    ctx: dict = {}
    for name, key in (("world_gold", "xau"), ("usd_vnd", "usdvnd")):
        row = next((r for r in fetched.get(name, {}).get("rows", []) if r[0] == key), None)
        old = prev.get(key)
        ctx[name] = (row[2] - old) / old * 100 if row and old else None
        ctx[f"{name}_value"] = row[2] if row else None
    return ctx


def explain(name: str, ctx: dict, tracker: dict) -> Callable[[int], str]:
    def why(d: int) -> str:
        world, usd = ctx.get("world_gold"), ctx.get("usd_vnd")
        if name in ("sjc", "pnj"):
            if world is None and usd is None:
                return ""
            parts = []
            if world is not None and abs(world) >= 0.3 and world * d > 0:
                parts.append(f"vàng thế giới {_verb(world)} {abs(world):.1f}%")
            if usd is not None and abs(usd) >= 0.1 and usd * d > 0:
                parts.append(f"USD/VND {_verb(usd)} {abs(usd):.1f}%")
            if parts:
                return f"💡 Có thể do {' và '.join(parts)} (so với lần báo trước)."
            if world is not None and abs(world) >= 0.3:
                return (
                    f"💡 Ngược chiều vàng thế giới ({_verb(world)} {abs(world):.1f}%): chủ yếu "
                    "do cung – cầu trong nước, tiệm tự điều chỉnh giá."
                )
            return "💡 Vàng thế giới và tỷ giá gần như không đổi: chủ yếu do cung – cầu trong nước."
        if name == "petrol":
            return f"💡 Giá {_verb(d)} theo kỳ điều chỉnh mới của Petrolimex, thường bám giá dầu thế giới."
        if name == "usd_vnd":
            return (
                "💡 USD mạnh lên so với đồng: vàng, xăng dầu, hàng nhập khẩu thường đắt hơn."
                if d > 0
                else "💡 USD yếu đi so với đồng: hàng nhập khẩu thường rẻ hơn."
            )
        if name == "world_gold" and (rate := ctx.get("usd_vnd_value")):
            xau = next((v for k, _, v in tracker["rows"] if k == "xau"), 0)
            vnd = xau * rate * 37.5 / 31.1035 / 1e6
            return f"💡 Quy đổi theo tỷ giá hiện tại ≈ {vnd:,.1f} triệu đồng/lượng (chưa gồm chênh lệch trong nước)."
        return ""

    return why


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
    fetched: dict[str, tuple[dict, dict]] = {}
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
        fetched[name] = (tcfg, tracker)
    ctx = market_context({n: t for n, (_, t) in fetched.items()}, prev)
    for name, (tcfg, tracker) in fetched.items():
        msg, updates = build_message(
            tracker,
            prev,
            tcfg.get("min_change_pct", 0),
            args.now,
            why=explain(name, ctx, tracker),
        )
        if msg:
            # raises on failure -> prev untouched -> retried next round
            tg.send(msg, photo=asset_url(BANNERS[name]) if name in BANNERS else None)
            prev.update(updates)
            if not tg.dry:
                labels = {k: lbl for k, lbl, _ in tracker["rows"]}
                history.append([(k, labels[k], v, tracker["unit"]) for k, v in updates.items()])
    maintenance(tg, state, cfg)


def main() -> None:
    run(Path(__file__).parent, check, 60)


if __name__ == "__main__":
    main()
