# -*- coding: utf-8 -*-
"""r671：去哪儿 App 的 empty 只允许表示「列表页打开了、这天确实没航班」。

缺陷现场（我第一版）：`_one_date` 返回空列表时一律记
    path_note("航班列表", "empty", detail="该日未解析到带价航班")
——但空列表有五六种成因：没打开首页、设城市失败、没找到搜索按钮、
点搜索没反应、等 60s 也没等到列表页。把这些统统记成 `empty`，等于
把「没采到」说成「没票」，正是本项目最忌的账本说谎
（见 feedback-empty-needs-evidence）。

修法：`_one_date` / `_search_and_parse` 返回 (prices, reason)，
只有 reason=="empty"（确实看到过列表页但无航班）才记 empty，
其余记 timeout 并带上具体卡点。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.app_qunar import AppQunarCrawler


class _FakeLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


def _crawler():
    c = AppQunarCrawler.__new__(AppQunarCrawler)
    c.name = "app_qunar"
    c.package = "com.Qunar"
    c.paths = []
    c.login_suspect = False
    c.logger = _FakeLogger()
    return c


def _statuses(c):
    return [(p["path"], p["status"]) for p in c.paths]


def test_never_got_list_page_is_timeout_not_empty():
    """连列表页都没出现 → timeout，绝不能是 empty。"""
    c = _crawler()
    c._one_date = lambda *a, **k: ([], "no-list-page")
    out = _run_one_target(c)
    assert out == []
    assert ("航班列表", "timeout") in _statuses(c), _statuses(c)
    assert ("航班列表", "empty") not in _statuses(c), _statuses(c)


def test_confirmed_empty_list_is_empty():
    """确实打开了列表页但无航班 → 这才是 empty。"""
    c = _crawler()
    c._one_date = lambda *a, **k: ([], "empty")
    _run_one_target(c)
    assert ("航班列表", "empty") in _statuses(c), _statuses(c)


def test_home_not_opened_is_timeout_with_reason():
    """首页没打开：要能看出卡在哪一步，而不是笼统一句「未解析到」。"""
    c = _crawler()
    c._one_date = lambda *a, **k: ([], "no-home")
    _run_one_target(c)
    rec = [p for p in c.paths if p["path"] == "航班列表"][-1]
    assert rec["status"] == "timeout"
    assert "no-home" in rec["detail"], rec


def _run_one_target(c):
    """绕过设备，只跑 `_fetch_locked` 里「逐日期记账」那段。

    用假的 dev/首页探测：`_open_flight_home` 直接 True、`dump` 返回一个
    不含登录文案的空壳，让流程走到 `_one_date`（已被替换）。
    """
    class _Dev:
        def dump(self, retries=2):
            return '<hierarchy><node text="x" bounds="[0,0][1,1]" /></hierarchy>'

    c._open_flight_home = lambda dev: True
    c._dismiss_system_prompts = lambda dev: 0
    c.restore_login_snapshot = lambda dev: False
    return c._fetch_locked(_Dev(), "BJS", "SHA", ["2026-10-30"])
