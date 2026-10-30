# -*- coding: utf-8 -*-
r"""r644：登录分区必须覆盖 App 渠道（统一要求各厂商+渠道都登录）。

用户原话：「统一要求各个厂商+渠道都登录吧，如果 cookie 无法共享的要
独立登录的！抛弃之前的建议登录策略！」

实测缺口：登录分区只渲染 5 张卡（6 个浏览器槽位），`app_zhixing` 不在
其中。App 采集端会记 `login` 状态、有自己的登录 API
（`/api/app-login/*`）与内嵌登录画面，**唯独配置页上没有入口** ——
用户在「渠道登录」里找不到智行 App，无从登录。

判据是端到端：卡片存在 → 点「登录」→ 真的打开 App 登录画面。
负控：把 groups 里的 App 去掉 → 卡片消失，本测试红。
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _boot():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    srv = subprocess.Popen(
        [sys.executable, "webui.py", "--demo", "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = "http://127.0.0.1:%d" % port
    up = False
    for _ in range(160):
        try:
            op.open(base + "/api/state", timeout=1)
            up = True
            break
        except Exception:
            time.sleep(0.25)
    if not up:
        srv.terminate()
        pytest.skip("演示服务未在期限内起来")
    return srv, base


def test_app_channel_has_a_login_card_and_opens_the_screen():
    srv, base = _boot()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            if not os.path.exists(pw.chromium.executable_path):
                pytest.skip("chromium 二进制缺失")
            b = pw.chromium.launch()
            try:
                pg = b.new_page(viewport={"width": 1440, "height": 1200})
                errs = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.add_init_script("window.setInterval=()=>1;")
                pg.goto(base)
                pg.wait_for_function(
                    "typeof S!=='undefined'&&S&&S.users", timeout=30000)
                pg.evaluate("()=>{switchView('cfg')}")
                pg.wait_for_timeout(1500)
                got = pg.evaluate(r"""()=>{
                  const cards=[...document.querySelectorAll(
                    '#loginRows .lgcard')]
                    .map(e=>e.getAttribute('data-channel')||'');
                  const app=[...document.querySelectorAll(
                    '#loginRows .lgcard')]
                    .find(e=>e.getAttribute('data-channel')==='app_zhixing');
                  return {cards:cards, hasApp:!!app,
                          appText: app?(app.innerText||'').replace(/\s+/g,' ')
                                       :''};
                }""")
                # 浏览器 6 槽位仍要全在（App 不能顶掉谁）
                assert got["cards"][:5] == ["ctrip", "qunar", "fliggy",
                                            "tongcheng", "tuniu"], (
                    "浏览器渠道登录卡变了：%s" % got["cards"])
                assert got["hasApp"], (
                    "登录分区没有智行 App 的卡片（统一登录要求未落地）：%s"
                    % got["cards"])
                assert "必须登录" in got["appText"], (
                    "App 卡片没有标明登录要求：%r" % got["appText"])

                clicked = pg.evaluate("""()=>{
                  const card=[...document.querySelectorAll(
                    '#loginRows .lgcard')]
                    .find(e=>e.getAttribute('data-channel')==='app_zhixing');
                  const btn=card&&card.querySelector('.lgact button');
                  if(!btn)return null;
                  const lbl=btn.innerText.trim();
                  btn.click();
                  return lbl;}""")
                assert clicked, "App 卡片上没有可点的登录按钮"
                pg.wait_for_timeout(1500)
                modal = pg.evaluate(r"""()=>{
                  const m=document.getElementById('lgModal');
                  if(!m)return {visible:false,text:''};
                  const cs=getComputedStyle(m);
                  return {visible: cs.display!=='none'
                                   && cs.visibility!=='hidden',
                          text:(m.innerText||'').replace(/\s+/g,' ')};}""")
                assert modal["visible"], "点 App「登录」后没有打开登录画面"
                assert "智行 App 登录" in modal["text"], (
                    "打开的不是 App 登录画面：%r" % modal["text"][:120])
                assert not errs, "页面抛错：%s" % errs[:3]
            finally:
                b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()


def test_login_section_covers_every_required_channel():
    """登录分区覆盖的渠道 = 所有「必须登录」的采集渠道（浏览器 5 家 + App）。"""
    srv, base = _boot()
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(base + "/api/config", timeout=10) as r:
            pass
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            if not os.path.exists(pw.chromium.executable_path):
                pytest.skip("chromium 二进制缺失")
            b = pw.chromium.launch()
            try:
                pg = b.new_page(viewport={"width": 1440, "height": 1200})
                pg.add_init_script("window.setInterval=()=>1;")
                pg.goto(base)
                pg.wait_for_function(
                    "typeof S!=='undefined'&&S&&S.users", timeout=30000)
                pg.evaluate("()=>{switchView('cfg')}")
                pg.wait_for_timeout(1500)
                plats = pg.evaluate("""()=>{
                  const cards=[...document.querySelectorAll(
                    '#loginRows .lgcard')]
                    .map(e=>e.getAttribute('data-channel')||'');
                  return cards;}""")
            finally:
                b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()
    from crawlers import REGISTRY
    expected = set(REGISTRY.keys())
    missing = sorted(expected - set(plats))
    assert not missing, (
        "这些采集渠道在「渠道登录」分区里没有登录入口：%s（实际 %s）"
        % (missing, plats))
