# -*- coding: utf-8 -*-
r"""r635：节流行的「结论」与「速览」必须同一口径，否则同一行两个数打架。

实测形态（构造数据，1440 宽真渲染）：

    携程 路径劣化  国际 H5 45%（20 次） 偏低 · 国际 82% · H5 45%

左边按尝试级（20 次里 9 次成功 = 45%），右边按查询级（11 次查询 9 次成功
= 82%）。同一行差 37 个百分点，读者只会认为界面算错了。

真实库（data/prices.db，2026-10-30）本就存在这种背离：

    platform      scope  attempts  att%   queries  qry%   gap
    app_zhixing   dom          48   38%        18   100%  +62
    app_zhixing   intl         73   30%        22   100%  +70

根因：`path_degraded` 的 e1 读 `sc[...].rate`（尝试级），而右半
`scoped()` 读 `query_rate_live`（查询级）。尝试级把「同一查询的重试」
算成多次失败，查询级只看「这一问有没有问到」。

修法：e1 改查询口径 + 给出门槛线；域查询率健康、只有单次尝试低时，
说明是「重试已救回」，而不是继续喊「通路成功率偏低」催人换路。

负控：把 e1 的 `_rate(_r1)` 改回 `Math.round(rate*100)`（尝试级）
→ 两侧数字重新背离，本测试必须红。
"""
import json
import os
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 构造：国际域 20 次尝试（9 成功 → 45%），但只有 11 次查询（9 成功 → 82%）
# ——10 次失败尝试全挤在同一次查询里（重试）。
SCOPE_INTL = {
    "attempts": 20, "rate": 0.45, "ok": 9,
    "query_attempts": 11, "query_ok": 9, "query_rate": 0.8182,
    "query_attempts_live": 11, "query_rate_live": 0.8182,
    "query_detail_ok": 9, "query_calendar_only": 0,
    "paths": {"H5": {"attempts": 20, "rate": 0.45, "ok": 9}},
}
SCOPE_DOM = {"attempts": 0, "rate": 0.0, "query_attempts": 0,
             "query_rate": None, "query_attempts_live": 0,
             "query_rate_live": None, "paths": {}}


def _payloads(query_rate):
    intl = dict(SCOPE_INTL)
    intl.update({"query_rate": query_rate, "query_rate_live": query_rate})
    paths = {"ctrip": {
        "paths": {"H5": {"attempts": 20, "rate": 0.45, "ok": 9}},
        "scopes": {"intl": intl, "dom": SCOPE_DOM},
        "retired_paths": [],
        "query_attempts": 11, "query_rate": query_rate,
        "query_attempts_live": 11, "query_rate_live": query_rate,
    }}
    pacing = {
        "ok": True, "quiet": {}, "last_round": {"sent": 3, "paced": 1},
        "channels": {"ctrip": {
            "state": "ok", "needs_login": False, "scene": {},
            "advice": {"state": "path_degraded", "action": "route",
                       "severity": "auto", "eta_s": 0, "hint": "国际 H5 异常"},
        }},
        "cfg": {"warmup_platforms": [], "health_penalty_enabled": True},
        "coverage": {"sent": [], "ok": []}, "recurrences": [],
        "effect": {}, "iv_rate": {},
    }
    health = {"ok": True, "rounds": [], "stats": {},
              "push": {"channels": {}}, "paths": paths, "diag": [],
              "coverage": {}}
    return pacing, paths, health


def _render_rows(query_rate):
    """真起页面，把 payload 注进去，返回携程那一行的 innerText。"""
    from playwright.sync_api import sync_playwright

    import webui

    tmp = os.path.join(ROOT, "_scratch", "_r635t")
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write(webui.PAGE.replace("__PAGEVER__", "r635t"))

    pacing, paths, health = _payloads(query_rate)

    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(H, directory=tmp))
    port = srv.server_address[1]
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            if not os.path.exists(pw.chromium.executable_path):
                pytest.skip("chromium 二进制缺失")
            b = pw.chromium.launch()
            try:
                pg = b.new_page(viewport={"width": 1440, "height": 1000})
                pg.set_default_timeout(12000)
                pg.add_init_script("window.setInterval=()=>1;")
                pg.route("**/api/login-state", lambda r: r.fulfill(
                    status=200, body=json.dumps(
                        {"ok": True, "plats": {}, "slots": {}})))
                pg.route("**/api/paths?hours=24", lambda r: r.fulfill(
                    status=200, body=json.dumps(
                        {"ok": True, "demo": False, "platforms": paths})))
                pg.route("**/api/health", lambda r: r.fulfill(
                    status=200, body=json.dumps(health)))
                pg.route("**/api/pacing", lambda r: r.fulfill(
                    status=200, body=json.dumps(pacing)))
                pg.goto("http://127.0.0.1:%d/index.html" % port,
                        wait_until="domcontentloaded")
                pg.wait_for_function("typeof loadPacing==='function'")
                pg.evaluate("""async () => {
                  try{switchView('mon');}catch(e){}
                  document.querySelectorAll('#monview,#healthcard')
                    .forEach(e=>{e.hidden=false;e.style.display='';});
                  await loadPacing(true);
                  await new Promise(r=>setTimeout(r,400));
                }""")
                txt = pg.evaluate("""()=>{
                  const box=document.getElementById('paceBox');
                  if(!box)return '';
                  const rows=[...box.querySelectorAll('*')].filter(
                    e=>/携程|ctrip/.test(e.innerText||'')
                        &&e.children.length<6);
                  return (rows.find(e=>/路径劣化/.test(e.innerText))||{})
                    .innerText||box.innerText;
                }""")
            finally:
                b.close()
    finally:
        srv.shutdown()
        srv.server_close()
    return " ".join(str(txt).split())


def test_conclusion_percentage_matches_the_overview_panel():
    """域查询率偏低时，结论里的百分比必须就是右半那个数。

    负控：e1 改回尝试级 `rate` → 结论出 45%、右半出 82%，本测试红。
    """
    row = _render_rows(0.8182)
    assert "路径劣化" in row, "携程行没渲染出来：%r" % row[:200]
    # 右半的域速览：国际 82% —— 结论里必须出现同一个 82%
    assert "国际 82%" in row, "速览里没有「国际 82%」：%r" % row
    # 结论段必须用**同一个** 82%（查询口径）。r640 为压行高把「查询/次数/
    # 门槛」挪进了 title，结论只留「域 + 百分比」，所以这里匹配「国际 82%」
    # 出现在最前面（e1 段），而不是钉死「国际 查询 82%」这种逐字写法。
    head = row.split("国际 82% · H5")[0]
    assert "国际 82%" in head, (
        "结论没有用查询口径给出同一个 82%%（两侧口径不一致）：%r" % row)
    # 若还点名了最差通路，其百分比必须**显式标注**是单次（尝试级），
    # 否则读者会把 45% 当成域成功率，又和 82% 打架。
    if "最差通路" in row:
        assert "单次 45%" in row, (
            "点名了通路却没标注口径（会被当成域成功率）：%r" % row)


def test_healthy_domain_with_retries_says_retry_not_broken_path():
    """域查询率健康（91%）而单次尝试低时，不能说「通路成功率偏低」。

    否则读者看到「H5 通路成功率偏低 · 国际 91%」，会去徒劳地换路。
    正确说法是「偏低的是单次尝试，重试已救回」。
    """
    row = _render_rows(0.9091)
    assert "路径劣化" in row
    assert "国际 91%" in row, "速览里没有「国际 91%」：%r" % row
    assert "通路成功率偏低" not in row, (
        "域查询率 91%% 健康却仍判「通路成功率偏低」——自相矛盾：%r" % row)
    assert "重试" in row, "没有说清「是重试救回来的」：%r" % row
