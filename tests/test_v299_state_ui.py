# -*- coding: utf-8 -*-
"""v1.5.299: stale fallback follows schedule, login status reads as row accent,
radar chart gets the same range/line-Kline/scroll controls as other trend charts."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src():
    import webui
    with open(webui.__file__, encoding="utf-8") as f:
        return f.read()


def test_stale_fallback_window_follows_schedule_interval():
    import webui
    old = webui._State.cfg
    try:
        webui._State.cfg = {"schedule": {"interval_minutes": 30}}
        assert webui._state_stale_hours() == 6
        webui._State.cfg = {"schedule": {"interval_minutes": 10080}}
        assert webui._state_stale_hours() == 168
    finally:
        webui._State.cfg = old
    src = _src()
    assert "hours=_state_stale_hours()" in src
    assert "补位窗口随调度周期" in src


def test_login_status_is_a_row_accent_not_a_misaligned_dot():
    src = _src()
    for s in (
        "#loginRows .lgdot{display:none!important}",
        "#loginRows .lgcard{border-left:3px solid var(--r-hair)!important}",
        "#loginRows .lgcard:has(.lgdot.on){border-left-color:var(--r-green)!important}",
        "#loginRows .lgcard:has(.lgdot.stale),#loginRows .lgcard:has(.lgdot.mid){border-left-color:var(--warn)!important}",
    ):
        assert s in src, s


def test_radar_chart_controls_match_trend_chart_capabilities():
    src = _src()
    for s in (
        'id="spMonRange"',
        'data-spmrange="2">48h',
        'data-spmrange="7"',
        'data-spmrange="30"',
        'data-spmrange="90"',
        'data-spmrange="180"',
        'id="spMonMode"',
        'data-spmmode="kline"',
        "function spMonSetRange(",
        "function spMonSetMode(",
        "localStorage.setItem('jpspmrange'",
        "localStorage.setItem('jpspmmode'",
        "K线=",
        "监控快照按日聚合",
        ".spmon-chartwrap{overflow-x:auto;overflow-y:hidden}",
        "#spMonChart{max-width:none}",
        "const units=SPMON.mode==='kline'?candles.length:times.length",
        "units>5?Math.max(baseW,Math.min(2600,units*54)):baseW",
        "p.p>0&&(!rangeMs||p.t>=cutoff)",
        "mn=Math.max(0,mn-pad)",
        "ctx.globalAlpha=low||sel?1:.38",
        "lastRight+8",
        "#searchview .srx-src+.srx-src{border-left:1px solid var(--r-hair)!important;",
        "#searchview .srx-capbar{background:transparent!important;border:0!important;",
    ):
        assert s in src, s
