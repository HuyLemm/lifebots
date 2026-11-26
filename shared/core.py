"""Shared plumbing for every bot: .env/config/state, Telegram (retry, buttons, commands),
logging with rotation, source-health alerts, and the run loop.

Each bot lives in its own folder (price/, news/, weather/, stuff/) holding bot.py,
config.toml, .env and state.json; this module only provides the machinery.
"""

import argparse
import html
import json
import logging
import os
import sys
import time
import tomllib
from collections.abc import Callable
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx

try:  # corporate proxy / antivirus certs on Windows
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):  # None under pythonw
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252 by default

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("lifebots")

# command handler: (tg, state, args, text_after_command) -> reply text or None
Handler = Callable[["Telegram", "State", argparse.Namespace, str], "str | None"]


def setup_logging(name: str) -> None:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", "%d/%m %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    fh = RotatingFileHandler(
        logs / f"{name}.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    # supervised (hidden): the rotating file is enough. Docker sets LIFEBOTS_LOG_STDOUT=1.
    if sys.stderr is not None and (sys.stderr.isatty() or os.environ.get("LIFEBOTS_LOG_STDOUT")):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # would log every request URL (token!)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def load_env(bot_dir: Path) -> None:
    env = bot_dir / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def load_config(bot_dir: Path) -> dict:
    with open(bot_dir / "config.toml", "rb") as f:
        return tomllib.load(f)


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def header(kind: str, now: datetime | None = None) -> str:
    """First line of EVERY message: when it was sent and what kind of information it is.
    Needed because delivery can be late (a scheduler wakes the bots every few minutes/hours)."""
    now = now or datetime.now()
    return f"📍 <b>Thông báo lúc {now:%H:%M} ({now:%d/%m})</b> · {kind}"


def get_json(url: str, **params) -> dict:
    r = httpx.get(url, params=params or None, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return r.json()


class Telegram:
    """dry=True (or missing token/chat id) prints instead of sending. Sends retry on 429/5xx."""

    def __init__(self, dry: bool = False, name: str = ""):
        self.token = os.environ.get("BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.name = name
        self.dry = dry or not (self.token and self.chat_id)
        if not dry and self.dry:
            log.warning("BOT_TOKEN / TELEGRAM_CHAT_ID not set in this bot's .env -> printing only")

    def _call(self, method: str, payload: dict, timeout: float = 20) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = httpx.post(url, json=payload, timeout=timeout)
            except httpx.TransportError as e:
                last = e
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 429:
                wait = r.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(min(wait, 30))
                last = httpx.HTTPError("telegram 429 rate limited")
                continue
            if r.status_code >= 500:
                last = httpx.HTTPError(f"telegram {r.status_code}")
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise httpx.HTTPError(f"telegram {method} failed after retries: {last}")

    def send(self, text: str, buttons: list[list[dict]] | None = None) -> None:
        if self.dry:
            print(f"--- (dry) ---\n{text}\n")
            return
        payload: dict = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        self._call("sendMessage", payload)

    def safe_send(self, text: str) -> None:
        """For alerts about problems: never let a failed alert break the bot."""
        try:
            self.send(text)
        except httpx.HTTPError as e:
            log.warning("could not send alert: %s", e)

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        if self.dry:
            return []
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, timeout=timeout + 10)["result"]

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        if not self.dry:
            try:
                self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
            except httpx.HTTPError as e:
                log.warning("answerCallbackQuery failed: %s", e)

    def set_commands(self, commands: dict[str, str]) -> None:
        if self.dry:
            return
        body = [{"command": c.lstrip("/"), "description": d[:200]} for c, d in commands.items()]
        try:
            self._call("setMyCommands", {"commands": body})
        except httpx.HTTPError as e:
            log.warning("setMyCommands failed: %s", e)


class State:
    """Tiny JSON file so restarts don't re-send or forget baselines, plus source health."""

    def __init__(self, bot_dir: Path):
        self.path = bot_dir / "state.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)  # atomic: a crash mid-write can't corrupt the state

    def source_ok(self, name: str, tg: Telegram) -> None:
        h = self.data.get("health", {}).pop(name, None)
        if h and h.get("alerted"):
            tg.safe_send(
                f"{header('✅ Nguồn dữ liệu')}\n<b>{esc(tg.name)}</b>: nguồn “{esc(name)}” đã hoạt động lại"
            )

    def source_failed(self, name: str, err: object, tg: Telegram, threshold: int = 3) -> None:
        """Count consecutive failures; alert ONCE when a source keeps failing."""
        h = self.data.setdefault("health", {}).setdefault(name, {"fails": 0, "alerted": False})
        h["fails"] += 1
        log.warning("source %s failed (%d in a row): %s", name, h["fails"], err)
        if h["fails"] >= threshold and not h["alerted"]:
            h["alerted"] = True
            tg.safe_send(
                f"{header('⚠️ Cảnh báo nguồn dữ liệu')}\n"
                f"<b>{esc(tg.name)}</b>: nguồn “{esc(name)}” lỗi {h['fails']} lần liên tiếp\n"
                f"{esc(str(err)[:200])}"
            )


# ---------- Telegram commands (long polling: no public URL needed) ----------


def _status(tg: Telegram, state: State, args: argparse.Namespace, text: str) -> str:
    last = state.data.get("last_check")
    ago = f"{int(time.time() - last)} giây trước" if last else "chưa có"
    bad = [k for k, v in state.data.get("health", {}).items() if v.get("alerted")]
    lines = [
        f"🤖 <b>{esc(tg.name)}</b>: {'⏸ đang tạm dừng' if state.data.get('paused') else '▶️ đang chạy'}",
        f"Kiểm tra gần nhất: {ago}",
    ]
    if bad:
        lines.append("⚠️ Nguồn đang lỗi: " + esc(", ".join(bad)))
    return "\n".join(lines)


def _pause(tg: Telegram, state: State, args: argparse.Namespace, text: str) -> str:
    state.data["paused"] = True
    return "⏸ Đã tạm dừng. Gõ /resume để chạy lại."


def _resume(tg: Telegram, state: State, args: argparse.Namespace, text: str) -> str:
    state.data["paused"] = False
    args.now = False
    return "▶️ Đã chạy lại."


def _now(tg: Telegram, state: State, args: argparse.Namespace, text: str) -> str:
    args.now = True
    return "⏳ Đang lấy dữ liệu hiện tại…"


COMMON_COMMANDS: dict[str, tuple[Handler, str]] = {
    "/status": (_status, "Tình trạng bot"),
    "/now": (_now, "Gửi ngay bản hiện tại"),
    "/pause": (_pause, "Tạm dừng thông báo"),
    "/resume": (_resume, "Chạy lại"),
}


def handle_update(
    u: dict,
    tg: Telegram,
    state: State,
    args: argparse.Namespace,
    commands: dict[str, tuple[Handler, str]],
    callbacks: dict[str, Handler],
) -> None:
    """Only the owner's chat is obeyed; anything else is ignored."""
    cq = u.get("callback_query")
    if cq:
        if str(cq.get("message", {}).get("chat", {}).get("id")) != tg.chat_id:
            return
        data = cq.get("data", "")
        for prefix, fn in callbacks.items():
            if data.startswith(prefix):
                reply = fn(tg, state, args, data[len(prefix) :])
                tg.answer_callback(cq["id"], "Đã xong ✓")
                if reply:
                    tg.send(reply)
                return
        tg.answer_callback(cq["id"])
        return
    msg = u.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != tg.chat_id:
        return
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return
    cmd, _, rest = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd in ("/start", "/help"):
        tg.send("Lệnh:\n" + "\n".join(f"{c} — {d}" for c, (_, d) in commands.items()))
        return
    entry = commands.get(cmd)
    if entry:
        reply = entry[0](tg, state, args, rest.strip())
        if reply:
            tg.send(reply)


def wait_and_listen(
    tg: Telegram,
    state: State,
    args: argparse.Namespace,
    commands: dict[str, tuple[Handler, str]],
    callbacks: dict[str, Handler],
    seconds: float,
) -> None:
    """Sleep `seconds` while answering Telegram commands; wake early on /now."""
    if tg.dry:
        time.sleep(seconds)
        return
    end = time.monotonic() + seconds
    while (remaining := end - time.monotonic()) > 0:
        try:
            updates = tg.get_updates(state.data.get("offset"), max(1, min(25, int(remaining))))
        except httpx.HTTPError as e:
            log.debug("getUpdates: %s", e)
            time.sleep(min(5, remaining))
            continue
        for u in updates:
            state.data["offset"] = u["update_id"] + 1
            try:
                handle_update(u, tg, state, args, commands, callbacks)
            except Exception:
                log.exception("command failed")
        if updates:
            state.save()
        if args.now:
            return


def run_once(
    check: Callable,
    cfg: dict,
    interval: int,
    tg: Telegram,
    state: State,
    args: argparse.Namespace,
    commands: dict[str, tuple[Handler, str]],
    callbacks: dict[str, Handler],
) -> None:
    """One short run for a scheduler (GitHub Actions cron).

    Answers Telegram commands that arrived since the last run, runs the check only when its
    interval has elapsed (the cron fires every ~5 min, news wants every 10), saves, exits.
    """
    if not tg.dry:
        if not state.data.get("commands_set"):
            tg.set_commands({c: d for c, (_, d) in commands.items()})
            state.data["commands_set"] = True
        try:
            updates = tg.get_updates(state.data.get("offset"), 0)
        except httpx.HTTPError as e:
            log.warning("getUpdates failed: %s", e)
            updates = []
        for u in updates:
            state.data["offset"] = u["update_id"] + 1
            try:
                handle_update(u, tg, state, args, commands, callbacks)
            except Exception:
                log.exception("command failed")

    due = args.now or time.time() - state.data.get("last_check", 0) >= interval - 45
    if due and not state.data.get("paused"):
        try:
            check(tg, state, args, cfg)
            state.source_ok("check", tg)
        except Exception as e:  # noqa: BLE001 - a bad source must not fail the whole run
            log.exception("check failed")
            state.source_failed("check", f"{type(e).__name__}: {e}", tg)
        state.data["last_check"] = time.time()
    if not tg.dry:
        state.save()


def run(
    bot_dir: Path,
    check: Callable,
    interval_default: int,
    commands: dict[str, tuple[Handler, str]] | None = None,
    callbacks: dict[str, Handler] | None = None,
) -> None:
    """check(tg, state, args, cfg) runs once, or every N seconds with --loop.

    Never dies on a bad source: any error is logged, counted and retried next round.
    """
    p = argparse.ArgumentParser(description=bot_dir.name)
    p.add_argument("--loop", action="store_true", help="keep running")
    p.add_argument(
        "--once",
        action="store_true",
        help="scheduler mode (GitHub Actions): answer pending commands, check if due, exit",
    )
    p.add_argument("--interval", type=int, help="seconds between checks (default: config.toml)")
    p.add_argument("--dry", action="store_true", help="print instead of sending to Telegram")
    p.add_argument("--now", action="store_true", help="send a snapshot even if nothing changed")
    args = p.parse_args()

    setup_logging(bot_dir.name)
    load_env(bot_dir)
    cfg = load_config(bot_dir)
    interval = args.interval or cfg.get("interval_seconds", interval_default)
    tg, state = Telegram(args.dry, name=bot_dir.name), State(bot_dir)
    all_commands = {**COMMON_COMMANDS, **(commands or {})}
    if args.once:
        run_once(check, cfg, interval, tg, state, args, all_commands, callbacks or {})
        return
    if args.loop:
        tg.set_commands({c: d for c, (_, d) in all_commands.items()})
        log.info("%s started (interval %ss)", bot_dir.name, interval)

    while True:
        if not state.data.get("paused"):
            try:
                check(tg, state, args, cfg)
                state.source_ok("check", tg)
            except Exception as e:  # noqa: BLE001 - the loop must survive anything
                log.exception("check failed")
                state.source_failed("check", f"{type(e).__name__}: {e}", tg)
        state.data["last_check"] = time.time()
        if not tg.dry:  # a --dry preview must never consume the real baseline/first-run state
            state.save()
        if not args.loop:
            break
        args.now = False
        wait_and_listen(tg, state, args, all_commands, callbacks or {}, interval)
