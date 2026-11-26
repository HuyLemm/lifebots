"""Price sources. Each tracker returns:
    {"title": str, "unit": str, "stamp": str, "rows": [(key, label, value), ...]}
Units: luong (VND per luong), lit (VND per litre), usd_oz, usd_vnd.
"""

import re
from datetime import datetime

import httpx

from shared.core import get_json
from shared.feeds import HEADERS, VN, parse_time

OUNCE_G, LUONG_G = 31.1035, 37.5
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def stamp_text(dt) -> str:
    """Source-update time as '09:40 25/11/2026', or '' when the source gives none."""
    return f"{dt:%H:%M %d/%m/%Y}" if dt else ""


SJC_URL = "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx"
PNJ_URL = "https://edge-api.pnj.io/ecom-frontend/v1/get-gold-price"
PETROL_URL = "https://webgia.com/gia-xang-dau/petrolimex/"


VANGTODAY_URL = "https://www.vang.today/api/prices"
SJC_BACKUP = {
    "SJL1L10": "SJC 1L-10L",
    "SJ9999": "Nhẫn SJC 99,99%",
}  # vang.today code -> same row tag


def changes_today(code: str = "SJL1L10") -> dict:
    """How often vang.today saw this price change today: {'times': 2, 'day_change_sell': 400000}.
    (It only counts the changes, it does not list them.) {} on any problem."""
    try:
        day = get_json(VANGTODAY_URL, type=code, days=1)["history"][0]["prices"][code]
        return {"times": int(day["updates"]), "day_change_sell": float(day["day_change_sell"])}
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return {}


def sjc_backup() -> dict:
    """SJC prices relayed by vang.today - used when sjc.com.vn refuses the caller (e.g. 403
    for datacenter IPs such as GitHub's runners). Same row keys, so no false 'price changed'."""
    j = get_json(VANGTODAY_URL)
    prices = j["prices"]
    d = j.get("date", "")  # '2026-11-26' -> '26/11/2026'
    stamp = f"{j.get('time', '')} {d[8:10]}/{d[5:7]}/{d[:4]}" if d else ""
    rows = []
    for code, tag in SJC_BACKUP.items():
        p = prices.get(code)
        if p:
            rows.append((f"sjc:{tag}:buy", f"{tag} · Mua", float(p["buy"])))
            rows.append((f"sjc:{tag}:sell", f"{tag} · Bán", float(p["sell"])))
    return {
        "title": "🥇 Vàng SJC (HCM)",
        "unit": "luong",
        "stamp": stamp,
        "rows": rows,
        "changes_today": changes_today(),
    }


def sjc(cfg: dict) -> dict:
    try:
        return sjc_official(cfg)
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in (401, 403, 429):
            raise
        return sjc_backup()  # blocked, not broken: use the relay instead of alerting for days


def sjc_official(cfg: dict) -> dict:
    r = httpx.post(
        SJC_URL,
        data={"method": "GetCurrentGoldPricesByBranch", "goldPriceId": "1"},
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    wanted = {"Vàng SJC 1L": "SJC 1L-10L", "Vàng nhẫn SJC 99,99% 1 chỉ": "Nhẫn SJC 99,99%"}
    rows = []
    for x in j["data"]:
        if x["BranchName"] != cfg.get("branch", "Hồ Chí Minh"):
            continue
        for prefix, tag in wanted.items():
            if x["TypeName"].startswith(prefix):
                rows.append((f"sjc:{tag}:buy", f"{tag} · Mua", float(x["BuyValue"])))
                rows.append((f"sjc:{tag}:sell", f"{tag} · Bán", float(x["SellValue"])))
    return {"title": "🥇 Vàng SJC (HCM)", "unit": "luong", "stamp": j["latestDate"], "rows": rows}


def pnj(cfg: dict) -> dict:
    j = get_json(PNJ_URL, zone=cfg.get("zone", "00"))
    names = {"SJC": "Vàng miếng SJC", "N24K": "Nhẫn trơn PNJ 24K"}
    rows = []
    for x in j["data"]:
        if x["masp"] in names:
            # API gives thousand VND per chi; x10 chi per luong
            rows.append(
                (f"pnj:{x['masp']}:buy", f"{names[x['masp']]} · Mua", x["giamua"] * 10_000.0)
            )
            rows.append(
                (f"pnj:{x['masp']}:sell", f"{names[x['masp']]} · Bán", x["giaban"] * 10_000.0)
            )
    return {"title": "🥇 Vàng PNJ (HCM)", "unit": "luong", "stamp": "", "rows": rows}


def petrol(cfg: dict) -> dict:
    r = httpx.get(PETROL_URL, headers=HEADERS, timeout=20, follow_redirects=True)
    r.raise_for_status()
    html = r.text
    col = 1 if cfg.get("region", 1) == 1 else 2
    only = cfg.get("only", [])  # fuel names to keep (substring match); [] = all of them
    table = re.search(r"<table.*?</table>", html, re.S).group(0)
    rows = []
    for tr in re.findall(r"<tr.*?</tr>", table, re.S):
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
            for c in re.findall(r"<t[dh].*?</t[dh]>", tr, re.S)
        ]
        if len(cells) >= 3 and re.fullmatch(r"[\d.]+", cells[col]):
            if only and not any(w.lower() in cells[0].lower() for w in only):
                continue  # e.g. only = ["RON 95"]: a motorbike rider needs just this one
            rows.append((f"petrol:{cells[0]}", cells[0], float(cells[col].replace(".", ""))))
    stamp = re.search(r"Cập nhật lúc ([\d:]+ [\d/]+)", html)
    return {
        "title": f"⛽ {'Giá xăng' if only else 'Giá xăng dầu'} Petrolimex (vùng {cfg.get('region', 1)})",
        "unit": "lit",
        "stamp": stamp.group(1) if stamp else "",
        "rows": rows,
    }


def world_gold(cfg: dict) -> dict:
    j = get_json("https://api.gold-api.com/price/XAU")
    return {
        "title": "🌍 Vàng thế giới",
        "unit": "usd_oz",
        "stamp": stamp_text(parse_time(j.get("updatedAt"))),
        "rows": [("xau", "XAU/USD", j["price"])],
    }


def usd_vnd(cfg: dict) -> dict:
    j = get_json("https://open.er-api.com/v6/latest/USD")
    return {
        "title": "💵 Tỷ giá",
        "unit": "usd_vnd",
        "stamp": stamp_text(parse_time(j.get("time_last_update_utc"))),
        "rows": [("usdvnd", "USD/VND", j["rates"]["VND"])],
    }


def silver(cfg: dict) -> dict:
    j = get_json("https://api.gold-api.com/price/XAG")
    return {
        "title": "🥈 Bạc thế giới",
        "unit": "usd2",
        "stamp": stamp_text(parse_time(j.get("updatedAt"))),
        "rows": [("xag", "XAG/USD", j["price"])],
    }


def bitcoin(cfg: dict) -> dict:
    j = get_json("https://api.gold-api.com/price/BTC")
    return {
        "title": "🪙 Bitcoin",
        "unit": "usd",
        "stamp": stamp_text(parse_time(j.get("updatedAt"))),
        "rows": [("btc", "BTC/USD", j["price"])],
    }


def brent(cfg: dict) -> dict:
    """Brent crude (USD/barrel) from Yahoo's public chart endpoint."""
    last: Exception | None = None
    for host in ("query2", "query1"):  # Yahoo rate-limits by host and rejects non-browser agents
        try:
            r = httpx.get(
                f"https://{host}.finance.yahoo.com/v8/finance/chart/BZ=F",
                params={"range": "1d", "interval": "1d"},
                headers=BROWSER_HEADERS,
                timeout=20,
            )
            r.raise_for_status()
            meta = r.json()["chart"]["result"][0]["meta"]
            break
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            last = e
    else:
        raise last or ValueError("brent unavailable")
    ts = meta.get("regularMarketTime")
    when = datetime.fromtimestamp(ts, tz=VN) if ts else None
    return {
        "title": "🛢 Dầu thô Brent",
        "unit": "usd2",
        "stamp": stamp_text(when),
        "rows": [("brent", "Brent", float(meta["regularMarketPrice"]))],
    }


TRACKERS = {
    "sjc": sjc,
    "pnj": pnj,
    "petrol": petrol,
    "world_gold": world_gold,
    "usd_vnd": usd_vnd,
    "silver": silver,
    "bitcoin": bitcoin,
    "brent": brent,
}


def fmt_value(unit: str, v: float) -> str:
    return {
        "luong": f"{v / 1e6:,.2f} tr",
        "lit": f"{v:,.0f}đ",
        "usd_oz": f"${v:,.0f}",
        "usd": f"${v:,.0f}",
        "usd2": f"${v:,.2f}",
        "usd_vnd": f"{v:,.0f}",
    }[unit]


def fmt_delta(unit: str, d: float) -> str:
    return {
        "luong": f"{d / 1e3:+,.0f}k" if abs(d) < 1e6 else f"{d / 1e6:+,.1f} tr",
        "lit": f"{d:+,.0f}đ",
        "usd_oz": f"{d:+,.1f}$",
        "usd": f"{d:+,.0f}$",
        "usd2": f"{d:+,.2f}$",
        "usd_vnd": f"{d:+,.0f}",
    }[unit]
