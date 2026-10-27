# -*- coding: utf-8 -*-
"""r400：qunar 的「当前搜索无航线」是通道级门拒，不是"真没航班"。

实测（2026-10-27 08:0x，同一会话连打四条航线，浏览器 H5 与 httpx 各一遍）：

```
SHA→BKK   ret=false code=3  msg="当前搜索无航线，请更换城市重新搜索"
SHA→HKG   ret=false code=3  同上（换「香港」「中国香港」两种城市名都一样）
SHA→NRT   ret=false code=3  同上
SHA→TYO   ret=false code=3  同上
```

SHA→BKK 在 2 小时前刚从**同一渠道**采到 ¥662（9C7293），却有同样的回应。
真"查了没票"不会四条全无、更不会与刚采到的价矛盾 —— 这是通道级门拒。

若不识别，它会落进 `login_suspect ? login : empty` 的 else 分支被记成
empty，即向读者宣告「今天确实没有航班」并停止关注。按 soft_reject
（门拒、预热可解、可自愈）记录才是事实。

另：H5 完全没拿到东西（无响应、空正文、无风控/无票证据）此前也记 empty；
改为 error——「没证据」不能用来支撑「无票」这个结论。
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.qunar import QunarCrawler  # noqa: E402

GATE = ('{"ret":false,"msg":"当前搜索无航线，请更换城市重新搜索",'
        '"code":3,"data":"x"}')


def _c():
    c = QunarCrawler({}, logging.getLogger("t_r400"))
    c.path_reset()
    c._last_challenge = None
    return c


def test_blanket_gate_recognized():
    assert QunarCrawler._blanket_gate_text(GATE) == "当前搜索无航线，请更换城市重新搜索"


def test_ordinary_no_result_is_not_blanket_gate():
    """真正的"没有符合条件的航班"（1999 措辞）不能被当成门拒。"""
    body = ('{"bstatus":{"code":1999,"msg":"没有查询到符合条件的航班"},'
            '"code":-1,"data":null,"msg":"没有查询到符合条件的航班","ret":false}')
    assert QunarCrawler._blanket_gate_text(body) == ""


def test_gate_is_not_treated_as_empty():
    """门拒不得被 _no_result_text 认成"真无票"。"""
    c = _c()
    assert c._no_result_text(GATE) is False


def test_h5_gate_records_soft_reject():
    c = _c()

    def _browser(*a, **k):
        # 证据由浏览器路径在调用内产生（_fetch_one 起点会清空旧证据）
        c.last_soft_reject = {"kind": "blanket_gate",
                              "note": "当前搜索无航线，请更换城市重新搜索"}
        return None

    c._fetch_via_browser = _browser
    c._fetch_via_httpx = lambda *a, **k: None
    c._fetch_one("SHA", "HKG", "2026-11-16")
    st = {p["path"]: p["status"] for p in c.paths}
    assert st.get("H5") == "soft_reject", c.paths


def test_soft_evidence_reset_between_paths():
    """H5 的门拒证据不得漏到 API（r398 同类泄漏的守卫）。"""
    c = _c()
    seen = []

    def _httpx(fc, tc, d):
        seen.append(bool(getattr(c, "last_soft_reject", None)))
        c.last_soft_reject = {"kind": "blanket_gate", "note": "gate"}
        return None

    c._fetch_via_browser = lambda *a, **k: None
    c.last_soft_reject = {"kind": "blanket_gate", "note": "H5 gate"}
    c._fetch_via_httpx = _httpx
    c._fetch_one("SHA", "HKG", "2026-11-16")
    assert seen == [False], "API 起点必须已清 H5 的门拒证据，实际=%r" % seen


def test_no_evidence_is_error_not_empty():
    """H5 什么都没拿回来时按 error 记，不宣称无票。"""
    c = _c()

    def _browser(*a, **k):
        c._last_no_evidence = True
        return None

    c._fetch_via_browser = _browser
    c._fetch_via_httpx = lambda *a, **k: None
    QunarCrawler.login_suspect = False
    try:
        c._fetch_one("SHA", "HKG", "2026-11-16")
        st = {p["path"]: p["status"] for p in c.paths}
        assert st.get("H5") == "error", c.paths
    finally:
        QunarCrawler.login_suspect = False
