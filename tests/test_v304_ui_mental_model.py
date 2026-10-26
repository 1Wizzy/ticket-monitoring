# -*- coding: utf-8 -*-
"""v1.5.304: human reading order — situation first, evidence last, no duplicate stats."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src():
    import webui
    with open(webui.__file__, encoding="utf-8") as f:
        return f.read()


def test_search_specials_move_to_monitor_and_no_duplicate_fold_row():
    src = _src()
    assert 'id="srxSp"' not in src
    assert "function srxSpOpen(){try{switchView('mon');showMonTab('specials')" in src
    assert "特价雷达</button>" in src


def test_search_expanded_overview_drops_summary_and_restore_becomes_link():
    src = _src()
    for s in (
        "#searchview:not(.srx-ov-collapsed) .srx-ovbar{display:none!important}",
        'id="srxOvHero"',
        'id="srxRestoreLink"',
        "SRX.restoreChip=parts.join(' · ');",
        "const rl=$('srxRestoreLink')",
        "function srxPickDeparture(){SRX.ctx=true;srxRenderCtx()",
        'data-bandpick="1">选择出发日',
    ):
        assert s in src, s
    assert "已保持中性视图" not in src


def test_watch_situation_line_and_route_target_gap():
    src = _src()
    for s in (
        "#montab-watch .ws-kpis{display:flex!important",
        "#montab-watch .ws-routebar-h{display:none!important}",
        "#montab-watch .ws-chart-foot{display:none!important}",
        'class="tag ws-rt-gap"',
        "const _w0=g.rows.find(w=>w.active!==false)||g.rows[0]",
        "(g.count>1?(' · '+g.count+' 个关注'):'')",
    ):
        assert s in src, s
    assert "'个关注 · '+WS.list.filter" not in src


def test_radar_chart_full_width_and_compact_evidence_strip():
    src = _src()
    for s in (
        ".spmon-main{grid-template-columns:minmax(0,1fr)!important",
        ".spmon-low{display:grid!important",
        "每 '+he(String(j.auto_interval_h||2))+' 小时自动",
        "RADAR TREND",
    ):
        if s == "RADAR TREND":
            assert s not in src
        else:
            assert s in src, s
    assert "'<span class=\"sep\">/</span><span>'+(j.snapshots||0)+' 次监控</span>'" not in src


def test_channel_entry_is_a_matrix_and_radar_trend_has_no_white_end_labels():
    src = _src()
    for s in (
        '<div class="chboard-head"><span>渠道</span>',
        '<div class="chband-grid">',
        'class="chlink-surface"',
        "function chlinkChip(c,special){",
        "function spMonPanState()",
        "spmon-chartwrap.pan-x",
        "线尾圆点=最新采集价",
        "requestAnimationFrame(spMonPanState)",
    ):
        assert s in src, s
    assert "const marks=[];" not in src
    assert "ctx.fillRect(tx-tw-3,m.y-10,tw+6,13)" not in src


# ---------- r372：雷达口径修正 ----------

def test_radar_chart_breaks_line_on_basis_change():
    """裸票价与含税价不是同一把尺子，连线会把口径修正画成暴涨。

    实测：旧快照 ￥150（裸）与新快照 ￥685（含税）被连成一条竖线，
    读图等于「一天涨 4.5 倍」。同口径段内连续，跨口径另起一笔。
    """
    import webui
    src = webui.PAGE
    i0 = src.index("function spMonDraw")
    seg = src[i0:i0 + 9000]
    assert "mainBasis" in seg, "缺口径主尺（跨口径不连线）判定"
    assert "flush()" in seg, "跨口径未收笔"
    assert "seg.length===1" in seg.replace(" ", ""), "单点口径未成独立圆点"


def test_ctrip_calendar_uses_tax_inclusive_total():
    """携程日历 totalPrice 才是含税总价；price 是裸票。

    实取证：SHA→BKK 2026-11-16 price=150 / totalPrice=719，列表页
    ￥670 与 totalPrice 同口径。取 price 会把裸价混入跨渠道最低价。
    """
    from core.specials import ctrip_calendar_deals, parse_specials
    from datetime import date
    today = date(2026, 10, 16)
    payload = {"priceList": [
        {"departDate": "/Date(1793203200000+0800)/", "price": 150,
         "totalPrice": 719},
        {"departDate": "/Date(1793289600000+0800)/", "price": 185,
         "totalPrice": 685}]}
    w = parse_specials([{"enabled": True, "from": "SHA", "from_name": "上海",
                         "max_price": 900}], today)[0]
    deals = ctrip_calendar_deals(payload, "SHA", "BKK", "曼谷", w, today,
                                 days=30)
    assert sorted(d.src_price for d in deals) == [685.0, 719.0]
    assert all(d.tax_included is True for d in deals)
    assert all(d.price_basis_label == "含税总价" for d in deals)


# ---------- r372：当轮窗口与调度周期同拍 ----------

def test_round_query_window_scales_with_schedule():
    """当轮取行窗口曾硬编码 -40min：30min 周期下一轮跨 7 航线跑 20min
    再叠加调度抖动，整轮就落在窗外，主路径一行取不到→全量走补位，
    页面满屏「补位」（用户实报）。窗口须随周期放宽且有上限。"""
    import webui
    src = webui.PAGE if hasattr(webui, "PAGE") else ""
    py = open("webui.py", encoding="utf-8").read()
    i0 = py.index("def _user_state(")
    seg = py[i0:i0 + 4000]
    assert "interval_minutes" in seg, "当轮窗口未读调度周期"
    assert "min(180" in seg or "min( 180" in seg, "当轮窗口缺上限"
    assert "min(360" in py, "轮次标记 TTL 缺上限"
    assert src is not None
