"""Test-suite wide guards: the tests must never touch the network by accident."""

import pytest


@pytest.fixture(autouse=True)
def no_article_page_fetches(monkeypatch):
    """news/bot.py looks up the article's own picture when a feed has none; in tests that would
    be a real HTTP request per story. Tests that want it monkeypatch it themselves."""
    import news.bot

    monkeypatch.setattr(news.bot, "article_image", lambda url, **k: None)
