# -*- coding: utf-8 -*-
"""AppCrawler 基类：账本契约与既有 BaseCrawler 对齐。"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.app_base import AppCrawler
from crawlers.base import BaseCrawler


class Dummy(AppCrawler):
    name = "app_dummy"
    package = "com.example.dummy"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.raise_in_fetch = False

    def fetch(self, from_city, to_city, dates, ret_date=""):
        if self.raise_in_fetch:
            raise RuntimeError("boom")
        self.path_note("app-calendar", "ok", rows=1, date=dates[0])
        return []

    def login_url(self):
        return "app://dummy"


def _crawler():
    return Dummy({"user_data_dir": "user_data"}, logging.getLogger("t"))


def test_path_status_covers_base_vocabulary():
    """App 渠道必须能表达 base 的全部状态，不能漏。"""
    missing = set(BaseCrawler.PATH_STATUS) - set(AppCrawler.PATH_STATUS)
    assert missing == set(), missing


def test_path_note_shape_matches_base():
    c = _crawler()
    c.path_reset()
    rec = c.path_note("app-calendar", "calendar", rows=3, date="2026-11-16",
                      detail="d", ms=12)
    base = BaseCrawler({"user_data_dir": "user_data"}, logging.getLogger("t"))
    brec = base.path_note("app-calendar", "calendar", rows=3,
                          date="2026-11-16", detail="d", ms=12)
    assert set(rec) == set(brec)


def test_illegal_status_becomes_error():
    c = _crawler()
    assert c.path_note("x", "nonsense")["status"] == "error"


def test_safe_fetch_swallows_exception():
    c = _crawler()
    c.raise_in_fetch = True
    out = c.safe_fetch("SHA", "BJS", ["2026-11-16"])
    assert out == []
    assert c.last_status == "error"


def test_safe_fetch_logs_platform_prefix(caplog):
    c = _crawler()
    with caplog.at_level(logging.INFO):
        c.safe_fetch("SHA", "BJS", ["2026-11-16"])
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "[app_dummy]" in joined


def test_path_reset_clears():
    c = _crawler()
    c.path_note("a", "ok")
    c.path_reset()
    assert c.paths == []


def test_login_url_is_abstract():
    c = _crawler()
    assert c.login_url() == "app://dummy"
