# -*- coding: utf-8 -*-
"""App 登录 HTTP 层：鉴权边界、未知渠道、帧 304、坐标校验、完成后清理。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.android import login_api
from core.android.login_session import (ST_DONE, AppLoginRegistry,
                                        AppLoginSession)

PACKAGES = {"app_zhixing": "cn.suanya.zhixing"}


class FakeDevice:
    def __init__(self):
        self.taps = []
        self.texts = []

    def screencap(self):
        return b"\x89PNG-1"

    def window_size(self):
        return (900, 1600)

    def tap(self, x, y):
        self.taps.append((x, y))
        return True

    def input_text(self, t):
        self.texts.append(t)
        return True

    def keyevent(self, k):
        return True

    def app_start(self, pkg):
        return True


class FakeRuntime:
    def __init__(self, ready=True):
        self.ready = ready
        self.d = FakeDevice()

    def ensure_ready(self):
        return (True, "ready") if self.ready else (False, "not_installed")

    def device(self):
        return self.d


def _ctx(rt=None, verify=None, snapshot=None):
    rt = rt or FakeRuntime()
    return {
        "packages": PACKAGES,
        "runtime_factory": lambda: rt,
        "verify": verify,
        "snapshot": snapshot,
    }, rt


def test_create_rejects_unknown_plat():
    reg = AppLoginRegistry()
    ctx, _ = _ctx()
    out, code = login_api.handle_create(reg, "nope", PACKAGES,
                                        ctx["runtime_factory"])
    assert code == 400 and out["ok"] is False


def test_create_reports_runtime_not_ready():
    reg = AppLoginRegistry()
    rt = FakeRuntime(ready=False)
    out, code = login_api.handle_create(
        reg, "app_zhixing", PACKAGES, lambda: rt)
    assert code == 409
    assert "未就绪" in out["err"]


def test_create_reports_disabled_when_no_runtime():
    reg = AppLoginRegistry()
    out, code = login_api.handle_create(
        reg, "app_zhixing", PACKAGES, lambda: None)
    assert code == 409
    assert "未启用" in out["err"]


def test_create_starts_app_and_registers():
    reg = AppLoginRegistry()
    ctx, rt = _ctx()
    out, code = login_api.handle_create(
        reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    assert code == 200 and out["ok"] is True
    assert log_ok(reg)


def log_ok(reg):
    s = reg.get("app_zhixing")
    return s is not None and s.package == "cn.suanya.zhixing"


def test_create_is_idempotent_for_live_session():
    reg = AppLoginRegistry()
    ctx, _ = _ctx()
    login_api.handle_create(reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    out, code = login_api.handle_create(
        reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    assert code == 200 and out.get("already") is True


def test_frame_returns_png_then_304():
    reg = AppLoginRegistry()
    ctx, _ = _ctx()
    login_api.handle_create(reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    png, code, hdr = login_api.handle_frame(reg, "app_zhixing")
    assert code == 200 and png.startswith(b"\x89PNG")
    assert hdr["Content-Type"] == "image/png"
    _, code2, _ = login_api.handle_frame(reg, "app_zhixing")
    assert code2 == 304, "同一画面应返回 304，避免重复传全图"


def test_frame_404_without_session():
    reg = AppLoginRegistry()
    _, code, _ = login_api.handle_frame(reg, "app_zhixing")
    assert code == 404


def test_tap_validates_integers():
    reg = AppLoginRegistry()
    ctx, _ = _ctx()
    login_api.handle_create(reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    out, code = login_api.handle_tap(reg, "app_zhixing", {"x": "abc", "y": 1})
    assert code == 400 and out["ok"] is False


def test_tap_forwards_to_device():
    reg = AppLoginRegistry()
    ctx, rt = _ctx()
    login_api.handle_create(reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    out, code = login_api.handle_tap(reg, "app_zhixing", {"x": 10, "y": 20})
    assert code == 200 and rt.d.taps == [(10, 20)]


def test_tap_rejected_when_out_of_screen():
    reg = AppLoginRegistry()
    ctx, rt = _ctx()
    login_api.handle_create(reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    out, code = login_api.handle_tap(reg, "app_zhixing",
                                     {"x": 9999, "y": 20})
    assert code == 409 and rt.d.taps == []


def test_input_rejects_too_long_text():
    reg = AppLoginRegistry()
    ctx, rt = _ctx()
    login_api.handle_create(reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    out, code = login_api.handle_input(
        reg, "app_zhixing", {"text": "x" * (login_api.MAX_TEXT + 1)})
    assert code == 400
    assert rt.d.texts == []


def test_input_forwards_normal_text():
    reg = AppLoginRegistry()
    ctx, rt = _ctx()
    login_api.handle_create(reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    out, code = login_api.handle_input(
        reg, "app_zhixing", {"text": "13800138000"})
    assert code == 200 and rt.d.texts == ["13800138000"]


def test_finish_cleans_up_and_reports_detail():
    reg = AppLoginRegistry()
    ctx, _ = _ctx(verify=lambda: (True, "已登录"),
                  snapshot=lambda: (True, "快照已存"))
    login_api.handle_create(reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    out, code = login_api.handle_finish(reg, "app_zhixing",
                                       ctx["verify"], ctx["snapshot"])
    assert code == 200 and out["ok"] is True
    assert "已登录" in out["detail"] and "快照已存" in out["detail"]
    # 完成后会话被移除，避免"已完成"状态一直挂着
    assert reg.get("app_zhixing") is None


def test_finish_fails_when_verify_says_not_logged_in():
    reg = AppLoginRegistry()
    ctx, _ = _ctx(verify=lambda: (False, "页面仍是登录墙"))
    login_api.handle_create(reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    out, code = login_api.handle_finish(reg, "app_zhixing", ctx["verify"])
    assert code == 409 and out["ok"] is False
    assert "登录墙" in out["err"]


def test_finish_tolerates_verify_exception():
    """校验函数自己崩了，不能把用户已完成的登录判成失败。"""
    def boom():
        raise RuntimeError("dump failed")

    reg = AppLoginRegistry()
    ctx, _ = _ctx(verify=boom)
    login_api.handle_create(reg, "app_zhixing", PACKAGES, ctx["runtime_factory"])
    out, code = login_api.handle_finish(reg, "app_zhixing", ctx["verify"])
    assert code == 200 and out["ok"] is True


def test_parse_body_limits_size():
    assert login_api.parse_body(b'{"plat":"x"}') == {"plat": "x"}
    try:
        login_api.parse_body(b"{" + b"0" * 40000)
        assert False, "超长 body 应拒绝"
    except ValueError:
        pass


def test_parse_body_bad_json_is_empty_dict():
    assert login_api.parse_body(b"not json") == {}


def test_dispatch_routes_state_and_unknown():
    reg = AppLoginRegistry()
    out, code = login_api.dispatch(reg, "GET", "/api/app-login/state",
                                   {"plat": ["app_zhixing"]}, None, {})
    assert code == 200 and out["session"] is None
    _, code2 = login_api.dispatch(reg, "GET", "/api/app-login/bogus", {}, None, {})
    assert code2 == 404
