# -*- coding: utf-8 -*-
"""r632：长冷却不能显示成 `480:00`。

`breaker.cap_s` 上限 86400 秒（24 小时），而 `_paceLeft` 一律输出「分:秒」：

    28800 秒 → "480:00"
    86400 秒 → "1440:00"

没人能把 `480:00` 读成「8 小时」。用户判断不出「还要等 40 分钟还是 8 小时」，
这正是「可干预的及时性 / 可判断」的缺口——知道要等多久，才知道现在该做什么。

修法：≥1 小时用「N 小时 M 分」，1 小时内仍是「分:秒」（短冷却按秒读更精确）。

判据：真渲染注入 8 小时冷却，渠道标签必须是「冷却 8 小时」而不是「冷却 480:00」。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INJECT = (
    "(()=>{const fake={ok:true,quiet:{},cfg:{warmup_platforms:['qunar']},"
    "last_round:{},effect:{},channels:{},recurrences:[],paths:{platforms:{}},"
    "coverage:{ok:[],sent:[]}};"
    "fake.channels.ctrip={state:'cooldown',remain_s:28800,reason:'captcha',"
    "advice:{state:'path_degraded',hint:'',severity:'act'}};"
    "const o=window.fetch;window.fetch=function(u,x){"
    "if(String(u).indexOf('/api/pacing')>=0)"
    "return Promise.resolve(new Response(JSON.stringify(fake),"
    "{status:200,headers:{'Content-Type':'application/json'}}));"
    "return o.apply(this,arguments)};return true})()"
)

READ = ("(()=>{const b=document.getElementById('paceBox');if(!b)return 'NOBOX';"
        "const g=[...b.querySelectorAll('.glip')].map(x=>x.textContent.trim());"
        "return JSON.stringify(g)})()")


def test_long_cooldown_is_readable():
    import pytest
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]; s.close()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    srv = subprocess.Popen(
        [sys.executable, "webui.py", "--demo", "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = "http://127.0.0.1:%d" % port
    try:
        ok = False
        for _ in range(140):
            try:
                op.open(base + "/api/state", timeout=1); ok = True; break
            except Exception:
                time.sleep(0.25)
        if not ok:
            pytest.skip("演示服务未在期限内起来")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            if not os.path.exists(pw.chromium.executable_path):
                pytest.skip("chromium 二进制缺失")
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": 1440, "height": 1100})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(base)
            pg.wait_for_function("typeof S!=='undefined'&&S&&S.users",
                                 timeout=25000)
            pg.evaluate("()=>{switchView('mon');showMonTab('health')}")
            pg.wait_for_timeout(1000)
            pg.evaluate(INJECT)
            pg.evaluate("()=>loadPacing(true)")
            pg.wait_for_timeout(1200)
            labels = pg.evaluate(READ)
            b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()

    assert not errs, "页面抛错：%s" % errs[:3]
    got = json.loads(labels)
    assert got, "没有渲染出渠道状态标签"
    joined = " | ".join(got)
    assert "480:00" not in joined, (
        "8 小时冷却仍显示成 `480:00`（没人读得懂）：%s" % joined[:200])
    assert "8 小时" in joined, (
        "8 小时冷却没有按「N 小时」呈现：%s" % joined[:200])


def test_short_cooldown_keeps_minute_second():
    """1 小时以内仍用「分:秒」——短冷却按秒读更精确。"""
    src = open(os.path.join(ROOT, "webui.py"), encoding="utf-8",
               newline="").read()
    i = src.index("function _paceLeft(s){")
    seg = src[i:i + 400]
    assert "3600" in seg and "' 小时'" in seg, (
        "冷却格式化没有按 1 小时分档：%s" % seg[:200])
    assert "padStart(2,'0')" in seg, (
        "短冷却的「分:秒」格式被去掉了：%s" % seg[:200])
