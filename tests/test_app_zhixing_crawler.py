# -*- coding: utf-8 -*-
"""智行 App 渠道：状态诚实、日历族产出、不伪造明细。"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.app_zhixing import AppZhixingCrawler

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "fixtures", "zhixing_list_dump.xml")


def _xml():
    with open(FIX, encoding="utf-8") as f:
        return f.read()


class FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeRuntime:
    def __init__(self, ok=True, why="ready", device=None):
        self.ok, self.why = ok, why
        self.device_obj = device
        self.locks = 0

    def ensure_ready(self):
        return self.ok, self.why

    def device(self):
        return self.device_obj

    def device_lock(self, timeout=600):
        self.locks += 1
        return FakeLock()


class FakeDevice:
    def __init__(self, dumps=None, started=True):
        self.dumps = list(dumps or [])
        self.started = started
        self.calls = []

    def window_size(self):
        return (900, 1600)

    def app_start(self, pkg):
        self.calls.append(("app_start", pkg))
        return self.started

    def dump(self, retries=2):
        self.calls.append(("dump",))
        return self.dumps.pop(0) if self.dumps else ""

    def current_activity(self):
        return "cn.suanya.zhixing/com.app.main.entrance.MainActivity"

    def screencap(self):
        return b""

    def tap(self, x, y):
        self.calls.append(("tap", x, y))
        return True


def _make(dumps=None, runtime_ok=True, why="ready", with_device=True):
    dev = FakeDevice(dumps if dumps is not None else [_xml()])
    rt = FakeRuntime(ok=runtime_ok, why=why, device=dev if with_device else None)
    # boot_wait 置 0：真实冷启动等待 10s，测试里不该睡
    c = AppZhixingCrawler({"android": {"enabled": True},
                           "app_boot_wait_s": 0},
                          logging.getLogger("t"))
    c._runtime = rt
    c._skip_route = True   # 导航是 UI 交互，另有真机验证；单测只测解析
    return c, rt, dev


def test_disabled_returns_empty_and_notes_reason():
    c, _, _ = _make(runtime_ok=False, why="disabled")
    out = c.safe_fetch("SHA", "BJS", ["2026-10-29"])
    assert out == []
    assert c.paths and c.paths[0]["status"] in ("unsupported", "error")


def test_calendar_prices_produced_from_real_dump():
    c, _, _ = _make()
    out = c.safe_fetch("SHA", "BJS", ["2026-10-29"])
    assert out, "应产出日历价"
    assert 450.0 in {f.price for f in out}
    for f in out:
        assert not f.flight_no, "未登录不得伪造航班号"


def test_calendar_rows_marked_as_calendar_path():
    c, _, _ = _make()
    c.safe_fetch("SHA", "BJS", ["2026-10-29"])
    assert "calendar" in {p["status"] for p in c.paths}


def test_masked_prices_never_become_zero():
    c, _, _ = _make()
    out = c.safe_fetch("SHA", "BJS", ["2026-10-29"])
    assert all(f.price > 0 for f in out), [f.price for f in out]


def test_login_state_is_not_recorded_as_empty():
    c, _, _ = _make(dumps=['<hierarchy><node text="立即登录" '
                           'bounds="[0,0][100,40]" /></hierarchy>'])
    out = c.safe_fetch("SHA", "BJS", ["2026-10-29"])
    assert out == []
    assert any(p["status"] == "login" for p in c.paths)
    assert not any(p["status"] == "empty" for p in c.paths)


def test_empty_requires_explicit_copy():
    c, _, _ = _make(dumps=['<hierarchy><node text="抱歉，没有找到符合条件的航班" '
                           'bounds="[0,0][100,40]" /></hierarchy>'])
    out = c.safe_fetch("SHA", "BJS", ["2026-10-29"])
    assert out == []
    assert any(p["status"] == "empty" for p in c.paths)


def test_unknown_page_marks_blocked_not_empty():
    c, _, _ = _make(dumps=["<hierarchy></hierarchy>"])
    c.safe_fetch("SHA", "BJS", ["2026-10-29"])
    assert not any(p["status"] == "empty" for p in c.paths)


def test_unknown_page_does_not_trip_captcha_breaker():
    """页面结构不认识 ≠ 被验证码拦。

    pacing.classify_failure 把 blocked 归为 captcha（300s 阶梯熔断），
    而"App 改版"重试也不会变好，更不该让整个渠道停摆 5 分钟——
    实机踩过：第一次识别失败就把 App 渠道熔断 300s。
    """
    from core.pacing import classify_failure
    c, _, _ = _make(dumps=["<hierarchy></hierarchy>"])
    c.safe_fetch("SHA", "BJS", ["2026-10-29"])
    statuses = [p["status"] for p in c.paths]
    kind = classify_failure(statuses, note="")
    assert kind != "captcha", statuses


def test_package_and_name():
    c, _, _ = _make()
    assert c.name == "app_zhixing"
    assert c.package == "cn.suanya.zhixing"
    assert c.login_url()


def test_uses_device_lock():
    c, rt, _ = _make()
    c.safe_fetch("SHA", "BJS", ["2026-10-29"])
    assert rt.locks >= 1


def test_dates_filtered_to_requested():
    """App 日历是 7 天条；只产出调用方请求的日期。"""
    c, _, _ = _make()
    out = c.safe_fetch("SHA", "BJS", ["2026-10-30"])
    assert {f.depart_date for f in out} == {"2026-10-30"}
