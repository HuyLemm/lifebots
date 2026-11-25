"""Stuff bot: sales on Shopee, Lazada and TikTok Shop, read from public Telegram sale channels.

Run (from LifeBots/):
    python -m stuff.bot --dry --now      # preview, nothing sent
    python -m stuff.bot --loop
Channels, platforms and your interest keywords are set in stuff/config.toml. In Telegram you
can tune keywords live: /keywords, /add <word>, /del <word>, or press 🚫 under a message.
"""

from pathlib import Path

from shared.core import State, Telegram, run
from stuff.channels import (
    check_channels,
    cmd_add,
    cmd_del,
    cmd_keywords,
    effective_keywords,
)


def check(tg: Telegram, state: State, args, cfg: dict) -> None:
    state.data["_cfg_count"] = len(effective_keywords(cfg, state))
    check_channels(tg, state, args, cfg)


COMMANDS = {
    "/keywords": (cmd_keywords, "Xem từ khoá đã thêm/bỏ"),
    "/add": (cmd_add, "Thêm từ khoá: /add bàn phím cơ"),
    "/del": (cmd_del, "Bỏ từ khoá: /del giày"),
}


def main() -> None:
    run(Path(__file__).parent, check, 300, COMMANDS, {"del:": cmd_del})


if __name__ == "__main__":
    main()
