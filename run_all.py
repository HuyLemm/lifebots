"""Supervisor: runs every bot that has a token and restarts any that dies.

Run:  python run_all.py   (Ctrl+C to stop)   - or let the 'LifeBots' scheduled task do it at logon.
Each bot writes logs/<bot>.log itself (rotating); crash output goes to logs/<bot>.stderr.log.
Only one supervisor can run at a time (a local port is used as a lock).
"""

import logging
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from shared.core import ROOT, setup_logging

BOTS = ("price", "news", "weather", "stuff")
LOCK_PORT = 47653
MIN_RESTART_GAP = 15  # seconds, so a bot that crashes at once doesn't spin
log = logging.getLogger("run_all")


def has_token(bot: str) -> bool:
    env = Path(ROOT / bot / ".env")
    return env.exists() and bool(
        re.search(r"^BOT_TOKEN=\S+", env.read_text(encoding="utf-8"), re.M)
    )


def start(bot: str) -> subprocess.Popen:
    err = open(ROOT / "logs" / f"{bot}.stderr.log", "ab")  # noqa: SIM115 - handed to the child
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        [sys.executable, "-X", "utf8", "-m", f"{bot}.bot", "--loop"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=err,
        creationflags=flags,
    )


def main() -> None:
    setup_logging("run_all")
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        log.error("another run_all is already running - exiting")
        sys.exit(1)

    active = [b for b in BOTS if has_token(b)]
    for b in set(BOTS) - set(active):
        log.warning("skip %s: no BOT_TOKEN in %s/.env", b, b)
    procs = {b: start(b) for b in active}
    last_start = dict.fromkeys(active, time.time())
    log.info("supervising: %s", ", ".join(active))
    try:
        while True:
            time.sleep(5)
            for b, p in procs.items():
                if p.poll() is not None and time.time() - last_start[b] >= MIN_RESTART_GAP:
                    log.warning("%s exited with code %s - restarting", b, p.returncode)
                    procs[b], last_start[b] = start(b), time.time()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs.values():
            p.terminate()


if __name__ == "__main__":
    main()
