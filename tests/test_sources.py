"""Parsers against REAL saved samples: when a source changes its layout these fail first."""

from pathlib import Path

import httpx
import pytest

FIX = Path(__file__).parent / "fixtures"


def fake_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("GET", "http://x"))


def test_sjc_parser(monkeypatch):
    from price import trackers

    monkeypatch.setattr(
        trackers.httpx, "post", lambda *a, **k: fake_response((FIX / "sjc.json").read_text("utf-8"))
    )
    t = trackers.sjc({})
    keys = {k for k, _, _ in t["rows"]}
    assert {"sjc:SJC 1L-10L:buy", "sjc:SJC 1L-10L:sell"} <= keys
    assert all(v > 1_000_000 for _, _, v in t["rows"])  # VND per luong
    assert t["unit"] == "luong"


def test_pnj_parser(monkeypatch):
    from price import trackers

    monkeypatch.setattr(
        trackers,
        "get_json",
        lambda url, **p: __import__("json").loads((FIX / "pnj.json").read_text("utf-8")),
    )
    t = trackers.pnj({})
    keys = {k for k, _, _ in t["rows"]}
    assert {"pnj:SJC:buy", "pnj:SJC:sell"} <= keys
    assert all(v > 1_000_000 for _, _, v in t["rows"])


def test_petrol_parser(monkeypatch):
    from price import trackers

    monkeypatch.setattr(
        trackers.httpx,
        "get",
        lambda *a, **k: fake_response((FIX / "petrol.html").read_text("utf-8")),
    )
    t = trackers.petrol({"region": 1})
    assert len(t["rows"]) >= 4
    assert any("RON 95" in label for _, label, _ in t["rows"])
    assert all(5_000 < v < 100_000 for _, _, v in t["rows"])  # VND per litre
    assert t["stamp"]


def test_channel_parser():
    from stuff.channels import parse_channel

    posts = parse_channel((FIX / "channel.html").read_text("utf-8"))
    assert posts, "no posts parsed - did t.me/s change its HTML?"
    assert all("/" in p["id"] and p["text"] for p in posts)


def test_rss_parser(monkeypatch):
    from shared import feeds

    monkeypatch.setattr(
        feeds.httpx, "get", lambda *a, **k: fake_response((FIX / "rss.xml").read_text("utf-8"))
    )
    items = feeds.fetch_items("http://x")
    assert len(items) == 3
    assert all(i["title"] and i["link"].startswith("http") for i in items)


@pytest.mark.parametrize("bad", ["", "<html>no table</html>"])
def test_petrol_parser_fails_loudly_on_layout_change(monkeypatch, bad):
    """A changed page must raise (-> health alert), never silently report nothing."""
    from price import trackers

    monkeypatch.setattr(trackers.httpx, "get", lambda *a, **k: fake_response(bad))
    with pytest.raises((AttributeError, ValueError, IndexError)):
        t = trackers.petrol({})
        if not t["rows"]:
            raise ValueError("no rows")


def test_sjc_falls_back_to_relay_when_blocked(monkeypatch):
    """GitHub's runners get 403 from sjc.com.vn: same row keys come from vang.today instead."""
    from price import trackers

    def blocked(*a, **k):
        resp = httpx.Response(403, text="Forbidden", request=httpx.Request("POST", "http://sjc"))
        raise httpx.HTTPStatusError("403", request=resp.request, response=resp)

    monkeypatch.setattr(trackers.httpx, "post", blocked)
    monkeypatch.setattr(
        trackers,
        "get_json",
        lambda url, **p: __import__("json").loads((FIX / "vangtoday.json").read_text("utf-8")),
    )
    t = trackers.sjc({})
    keys = {k for k, _, _ in t["rows"]}
    assert {"sjc:SJC 1L-10L:buy", "sjc:SJC 1L-10L:sell", "sjc:Nhẫn SJC 99,99%:buy"} <= keys
    assert all(v > 1_000_000 for _, _, v in t["rows"])


def test_sjc_other_http_errors_still_raise(monkeypatch):
    """A 500 is a real outage (-> health alert), only blocking codes use the relay."""
    from price import trackers

    def broken(*a, **k):
        resp = httpx.Response(500, text="boom", request=httpx.Request("POST", "http://sjc"))
        raise httpx.HTTPStatusError("500", request=resp.request, response=resp)

    monkeypatch.setattr(trackers.httpx, "post", broken)
    with pytest.raises(httpx.HTTPStatusError):
        trackers.sjc({})
