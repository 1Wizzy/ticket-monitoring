# -*- coding: utf-8 -*-
"""r613：配置页的渠道勾选必须覆盖**全部已注册渠道**，含 App 渠道。

`app_zhixing` 早已是正式渠道：`crawlers/__init__.py` 的 `REGISTRY`、
`core/health.py` 的健康日志正则、`core/channel_alert.py` 的告警名映射里
都有它；模块注释也写明「App 渠道必须显式加进用户 `platforms:` 才会被实例化」。

但配置页的渠道勾选来自 `PLATS`（只有 5 家浏览器渠道），用户**没有任何入口**
把 `app_zhixing` 加进自己的 `platforms`——只能手改 config.yaml。
这与「配置页面不允许存在任何无法配置的项」直接冲突。

修法：在渠道勾选行末尾补一个 App 渠道 chip，title 里点明它依赖
「全局参数 · Android 真机通道」总开关（那是另一组配置，避免让人误以为
勾了就会跑）。
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _w():
    return io.open(os.path.join(ROOT, "webui.py"), encoding="utf-8",
                   newline="").read()


def test_app_channel_is_selectable():
    w = _w()
    i = w.index("chip pch p-${p}")
    seg = w[i:i + 900]
    assert "p-app_zhixing" in seg, "渠道勾选里没有 App 渠道"
    assert "togPlat(${i},'app_zhixing',this)" in seg, (
        "App 渠道 chip 没有接到 togPlat（点了不会写 platforms）")


def test_app_chip_states_its_dependency():
    """必须点明依赖 Android 总开关，否则用户勾了却不跑，无从判断。"""
    w = _w()
    i = w.index("p-app_zhixing")
    seg = w[i:i + 400]
    assert "Android" in seg and "总开关" in seg, (
        "App 渠道 chip 没说明它依赖 Android 真机通道总开关：%s" % seg[:120])


def test_every_registry_channel_has_a_chip():
    """REGISTRY 里的每个渠道都要能在页面上勾选——这是通用不变式。"""
    import sys
    sys.path.insert(0, ROOT)
    from crawlers import REGISTRY
    w = _w()
    i = w.index("chip pch p-${p}")
    seg = w[i:i + 900]
    # 渠道 chip 有两个来源：`PLATS` 动态表（p-${p}）与显式追加的单个 chip。
    # 只查字面量会把 5 家动态生成的误报成缺口（实测 fliggy/tongcheng/tuniu）。
    dyn = set()
    k = w.index("const PLATS={")
    for part in w[k:w.index("}", k)].split(","):
        if ":" in part:
            dyn.add(part.split(":", 1)[0].strip().split()[-1].strip("'\""))
    missing = [c for c in REGISTRY
               if c not in dyn and ("p-" + c) not in seg and ("'%s'" % c) not in seg]
    assert not missing, "这些已注册渠道在页面上勾不到：%s" % missing


def test_renders_six_chips_and_toggles():
    """真渲染：6 个 chip，点一下 App 渠道要真的进 CFG.platforms。"""
    import socket
    import subprocess
    import sys
    import time
    import urllib.request

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "webui.py"), "--demo",
         "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = "http://127.0.0.1:%d" % port
    try:
        t0 = time.time()
        while time.time() - t0 < 60:
            try:
                op.open(base + "/api/state", timeout=2)
                break
            except Exception:
                time.sleep(0.3)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            import os as _os
            if not _os.path.exists(p.chromium.executable_path):
                import pytest
                pytest.skip("chromium 二进制缺失")
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1400, "height": 1000})
            pg.goto(base)
            pg.wait_for_function("typeof S!=='undefined'&&S&&S.users",
                                 timeout=25000)
            pg.evaluate("()=>{switchView('cfg')}")
            pg.wait_for_timeout(900)
            pg.evaluate("""()=>{const c=document.querySelector(
              '#cfgnav .cnav[data-p="users"]');if(c)c.click()}""")
            pg.wait_for_timeout(900)
            pg.evaluate("()=>{try{setOpenUser([0])}catch(e){};buildForm()}")
            pg.wait_for_timeout(900)
            got = pg.evaluate("""()=>{
              const chips=[...document.querySelectorAll('#cfgform .chip.pch')];
              const chip=chips.find(c=>String(c.className)
                .indexOf('p-app_zhixing')>=0);
              if(!chip)return {n:chips.length,missing:true};
              const before=(CFG[0].platforms||[]).slice();
              chip.click();
              const after=(CFG[0].platforms||[]).slice();
              chip.click();
              return {n:chips.length,before:before,after:after,
                      back:(CFG[0].platforms||[]).slice()}}""")
            b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()

    assert not got.get("missing"), "App 渠道 chip 没渲染：%s" % got
    assert got["n"] == 6, "渠道勾选数量应为 6：%s" % got
    assert "app_zhixing" in got["after"], "点了没写进 platforms：%s" % got
    assert "app_zhixing" not in got["back"], "再点没能取消：%s" % got
