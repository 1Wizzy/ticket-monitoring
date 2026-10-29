# -*- coding: utf-8 -*-
"""r412：渠道登录五行必须严格对齐（几何守卫，非字符串匹配）。

真实缺陷（1440 视口实测）：飞猪行按钮右缘 1327，其余四行 1313，差 14px
（正好一个 gap）；行高 52 vs 65 参差。根因是**两种 DOM 结构混用**：

  · 单渠道 —— 按钮嵌在 .lgslot 里 → 三列网格的第三列塌成 0px
  · 飞猪双端 —— 按钮是 .lgcard 直接子级 → 真的占了第三列

修法是统一 DOM：操作一律提到 .lgcard 直接子级（.lgact 容器），
卡片固定三列 170px | 1fr | 96px，操作列 justify-self:end。

字符串守卫挡不住这类回归（CSS 写对了但 DOM 结构又变回混用照样错位），
所以这里用 Playwright 量真实几何。
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def server():
    import webui
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), webui._Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(1.2)
    yield port
    srv.shutdown()


def _measure(port, width):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": width, "height": 1200})
        pg.goto("http://127.0.0.1:%d/#cfg" % port,
                wait_until="domcontentloaded", timeout=30000)
        # 等登录卡真渲染出来再量：8 worker 并发时固定 2.6s 曾量到空表
        pg.wait_for_selector("#loginRows .lgcard", timeout=20000)
        pg.wait_for_timeout(600)
        rows = pg.evaluate("""() => [...document.querySelectorAll('#loginRows .lgcard')].map(c=>{
          const b=c.getBoundingClientRect();
          const a=c.querySelector('.lgact')||c.querySelector('.btn2');
          const ab=a?a.getBoundingClientRect():null;
          const n=c.querySelector('.lgname');
          const nb=n?n.getBoundingClientRect():null;
          return {h:Math.round(b.height), right:ab?Math.round(ab.right):null,
                  left:nb?Math.round(nb.left):null};
        })""")
        b.close()
    return rows


@pytest.mark.parametrize("width", [1440, 1100, 900, 620, 420])
def test_all_rows_share_right_edge(server, width):
    """五行操作按钮右缘必须完全一致（这是用户截图里的核心症状）。"""
    rows = _measure(server, width)
    assert len(rows) == 5, rows
    rights = {r["right"] for r in rows}
    assert len(rights) == 1, "右缘不齐 %r @%dpx" % (sorted(rights), width)


@pytest.mark.parametrize("width", [1440, 1100, 900, 620, 420])
def test_all_rows_share_height(server, width):
    """五行等高——飞猪多一段说明不应比别家高出一截。"""
    rows = _measure(server, width)
    heights = {r["h"] for r in rows}
    assert len(heights) == 1, "行高参差 %r @%dpx" % (sorted(heights), width)


def test_name_column_left_edges_align(server):
    """渠道名列左缘一致（网格第一列没有被内容撑歪）。"""
    rows = _measure(server, 1440)
    lefts = {r["left"] for r in rows}
    assert len(lefts) == 1, lefts
