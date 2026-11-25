"""Price sources. Each tracker returns:
    {"title": str, "unit": str, "stamp": str, "rows": [(key, label, value), ...]}
Units: luong (VND per luong), lit (VND per litre), usd_oz, usd_vnd.
"""

import re

import httpx

from shared.core import get_json
from shared.feeds import HEADERS

OUNCE_G, LUONG_G = 31.1035, 37.5
SJC_URL = "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx"
PNJ_URL = "https://edge-api.pnj.io/ecom-frontend/v1/get-gold-price"
PETROL_URL = "https://webgia.com/gia-xang-dau/petrolimex/"


def sjc(cfg: dict) -> dict:
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
    table = re.search(r"<table.*?</table>", html, re.S).group(0)
    rows = []
    for tr in re.findall(r"<tr.*?</tr>", table, re.S):
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
            for c in re.findall(r"<t[dh].*?</t[dh]>", tr, re.S)
        ]
        if len(cells) >= 3 and re.fullmatch(r"[\d.]+", cells[col]):
            rows.append((f"petrol:{cells[0]}", cells[0], float(cells[col].replace(".", ""))))
    stamp = re.search(r"Cập nhật lúc ([\d:]+ [\d/]+)", html)
    return {
        "title": f"⛽ Giá xăng dầu Petrolimex (vùng {cfg.get('region', 1)})",
        "unit": "lit",
        "stamp": stamp.group(1) if stamp else "",
        "rows": rows,
    }


def world_gold(cfg: dict) -> dict:
    xau = get_json("https://api.gold-api.com/price/XAU")["price"]
    return {
        "title": "🌍 Vàng thế giới",
        "unit": "usd_oz",
        "stamp": "",
        "rows": [("xau", "XAU/USD", xau)],
    }


def usd_vnd(cfg: dict) -> dict:
    rate = get_json("https://open.er-api.com/v6/latest/USD")["rates"]["VND"]
    return {
        "title": "💵 Tỷ giá",
        "unit": "usd_vnd",
        "stamp": "",
        "rows": [("usdvnd", "USD/VND", rate)],
    }


TRACKERS = {
    "sjc": sjc,
    "pnj": pnj,
    "petrol": petrol,
    "world_gold": world_gold,
    "usd_vnd": usd_vnd,
}


def fmt_value(unit: str, v: float) -> str:
    return {
        "luong": f"{v / 1e6:,.2f} tr",
        "lit": f"{v:,.0f}đ",
        "usd_oz": f"${v:,.0f}",
        "usd_vnd": f"{v:,.0f}",
    }[unit]


def fmt_delta(unit: str, d: float) -> str:
    return {
        "luong": f"{d / 1e3:+,.0f}k",
        "lit": f"{d:+,.0f}đ",
        "usd_oz": f"{d:+,.1f}$",
        "usd_vnd": f"{d:+,.0f}",
    }[unit]
