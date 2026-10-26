# -*- coding: utf-8 -*-
"""r388：空结果不得被页头「登录/注册」字样误判成登录墙。

实测（2026-10-27 03:5x，飞猪国际 SHA→LHR，已登录 profile，页面 0 卡片）：

```
old_regex_hit(登录|扫码|login)=True  real_wall=False  logged_in=True
hit context: ... / 扫码下载飞猪 App / 微信小程序 / 微信公众号 / 增值电信...
```

旧判定 `re.search(r"登录|扫码|passport|login", url + body[:800])` 打在整页
文本上，页脚「扫码下载飞猪 App」与页头「登录/注册」恒命中 → **任何空
结果都会记 login**，触发 15 分钟登录冷却并提示用户重登。三渠道（飞猪
国际、携程 H5、同程 H5）统一改用 `classify_text` 真登录墙判据。
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.challenge import classify_text  # noqa: E402

_LOG = logging.getLogger("t_r388")


def _src(path):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "crawlers", path), encoding="utf-8") as f:
        return f.read()


def test_footer_scan_copy_is_not_login_wall():
    body = ("搜索 / 我的订单 / 飞猪国际 / 机票 / 出发 / 到达 / "
            "security-op-fliggy@alibaba-inc.com / 扫码下载飞猪 App / "
            "微信小程序 / 微信公众号 / 增值电信业务经营许可证")
    assert classify_text(body, "") is None, "页脚扫码入口不是登录墙"


def test_header_login_link_is_not_login_wall():
    body = "搜索 / 我的订单 / 登录/注册 / 飞猪国际 / 机票 / 酒店"
    assert classify_text(body, "") is None, "页头「登录/注册」链接不是登录墙"


def test_real_login_wall_still_detected():
    for body in ("请先登录后查看价格", "账号密码登录", "扫码登录",
                 "验证码登录"):
        sig = classify_text(body, "")
        assert sig is not None and sig.status == "login", body


def test_passport_url_is_login_wall():
    sig = classify_text("", "https://passport.fliggy.com/login.htm")
    assert sig is not None and sig.status == "login"


def test_three_crawlers_use_classify_text_for_login():
    """飞猪国际 / 携程 H5 / 同程 H5 的登录判定必须是 classify_text 单源。"""
    for f in ("fliggy.py", "ctrip.py", "tongcheng.py"):
        src = _src(f)
        assert 'r"登录|扫码|账号密码"' not in src, f
        assert 'r"登录|扫码|passport|login"' not in src, f
        assert 'classify_text(_body, str(page.url))' in src, f


def test_tongcheng_imports_classify_text():
    from crawlers import tongcheng as tc
    assert hasattr(tc, "classify_text"), "同程必须导入 classify_text"
