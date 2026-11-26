"""Stuff bot: sales on Shopee, Lazada and TikTok Shop, read from public Telegram sale channels.

Run (from LifeBots/):
    python -m stuff.bot --dry --now      # preview, nothing sent
    python -m stuff.bot --loop
Channels, platforms and your interest keywords are set in stuff/config.toml. In Telegram you
can tune everything live: /keywords /add /del (or press 🚫 under a message), follow a product with
/theodoi, block fake-goods words with /chan, mark brands you trust with /tincay.
"""

from pathlib import Path

from shared.core import State, Telegram, run
from stuff.channels import (
    blocked_terms,
    check_channels,
    cmd_add,
    cmd_bochan,
    cmd_botheodoi,
    cmd_botincay,
    cmd_chan,
    cmd_del,
    cmd_dschan,
    cmd_dstheodoi,
    cmd_dstincay,
    cmd_keywords,
    cmd_theodoi,
    cmd_tincay,
    effective_keywords,
    trusted_brands,
)


def check(tg: Telegram, state: State, args, cfg: dict) -> None:
    state.data["_cfg_count"] = len(effective_keywords(cfg, state))
    state.data["_cfg_blocked"] = blocked_terms(cfg, state)  # so /dschan can list config + yours
    state.data["_cfg_trusted"] = trusted_brands(cfg, state)
    check_channels(tg, state, args, cfg)


COMMANDS = {
    "/keywords": (cmd_keywords, "Xem từ khoá đã thêm/bỏ"),
    "/add": (cmd_add, "Thêm từ khoá: /add bàn phím cơ"),
    "/del": (cmd_del, "Bỏ từ khoá: /del giày"),
    "/theodoi": (cmd_theodoi, "Theo dõi món: /theodoi bàn phím akko (luôn gửi, 🔥 gửi trước)"),
    "/botheodoi": (cmd_botheodoi, "Bỏ theo dõi: /botheodoi bàn phím akko"),
    "/dstheodoi": (cmd_dstheodoi, "Xem các từ đang theo dõi"),
    "/chan": (cmd_chan, "Chặn deal chứa từ: /chan hàng fake"),
    "/bochan": (cmd_bochan, "Bỏ chặn từ: /bochan nhái"),
    "/dschan": (cmd_dschan, "Xem các từ đang chặn"),
    "/tincay": (cmd_tincay, "Thêm hãng tin cậy (+điểm ⭐, ✅): /tincay logitech"),
    "/botincay": (cmd_botincay, "Bỏ hãng tin cậy: /botincay logitech"),
    "/dstincay": (cmd_dstincay, "Xem hãng tin cậy"),
}


def main() -> None:
    run(Path(__file__).parent, check, 300, COMMANDS, {"del:": cmd_del})


if __name__ == "__main__":
    main()
