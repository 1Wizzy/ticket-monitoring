# -*- coding: utf-8 -*-
"""v1.5.302: compact console layout, unified chart hover, idempotent filters."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src():
    import webui
    with open(webui.__file__, encoding="utf-8") as f:
        return f.read()


def test_radar_hover_uses_nearest_point_anywhere_not_a_point_radius():
    src = _src()
    for s in (
        'id="spMonCross"',
        ".spmon-cross{position:absolute",
        "if(mx<g.padL||mx>g.W-g.padR||my<g.padT||my>g.H-g.padB)",
        "const dx=g.X(p.t)-mx,dy=g.Y(p.p)-my,d=dx*dx+dy*dy;",
        "cross.style.left=Math.round(c.offsetLeft+gx)+'px'",
        "const x=$('spMonCross');if(x)x.style.display='none'",
    ):
        assert s in src, s
    assert "d<=441" not in src


def test_search_kpi_facet_and_date_filters_are_idempotent():
    src = _src()
    for s in (
        "const on=!SRX.f.onlyQual;",
        "SRX.f.missing=!SRX.f.missing",
        "const same=Array.isArray(cur)?false:(String(cur)===''?false:String(cur)===String(v));",
        "SRX.date=(dv&&SRX.date===dv)?'':dv;",
        "SRX.f[k]=Array.isArray(SRX_DEFAULT_F[k])?[]:(typeof SRX_DEFAULT_F[k]==='boolean'?false:'')",
        "background:var(--r-blue-soft)!important;box-shadow:inset 0 -2px 0 var(--r-blue)!important",
    ):
        assert s in src, s


def test_search_header_is_compact_and_source_strip_is_a_table():
    src = _src()
    for s in (
        '<div class="srx-hero-copy" id="srxHeroCopy">',
        "let po='0';try{po=localStorage.getItem('jpsrx_probeopen2')||'0'}",
        "#searchview .srx-capbar{display:flex!important;background:transparent!important;border:0!important;",
        "#searchview .srx-probe:not(.open) .srx-probeacts>*:not(.srx-probe-toggle){display:none!important}",
        "#searchview .srx-kpi{display:inline-flex!important",
        "#searchview .srx-src+.srx-src{border-left:1px solid var(--r-hair)!important",
        "grid-auto-flow:column!important",
        "#searchview .srx-src-a{display:none!important}",
    ):
        assert s in src, s


def test_watch_uses_the_same_compact_summary_and_no_duplicate_routewatch():
    src = _src()
    for s in (
        "#montab-watch .ws-kpi{flex:1 1 0!important",
        "#montab-watch .ws-kpi .f{display:none!important}",
        "#montab-watch .ws-routewatch{display:none!important}",
        "#montab-watch .ws-chartwrap{height:200px!important;min-height:160px!important}",
        "#montab-watch .ws-route{min-height:44px!important",
    ):
        assert s in src, s


def test_search_overview_summary_bar_is_desktop_only_and_collapsible():
    src = _src()
    for s in (
        'id="srxOvSum"',
        'id="srxOvHero"',
        "function srxRenderOv()",
        "function srxToggleOverview()",
        "localStorage.setItem('jpsrx_ovopen'",
        "@media(max-width:1100px){\n #searchview .srx-ovbar{display:none!important}",
        "#searchview.show-overview .srx-brief{display:grid!important}",
        "#searchview.show-overview .srx-audit{display:flex!important}",
        "#searchview .srx-ovsum{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis",
    ):
        assert s in src, s


def test_mobile_search_toolbar_is_one_scrollable_row_not_a_two_column_grid():
    src = _src()
    for s in (
        "@media(max-width:560px){\n #searchview .srx-hero{padding:2px 2px 4px!important",
        "#searchview .srx-hero-act{display:flex!important;grid-template-columns:none!important;flex-wrap:nowrap!important",
        "#searchview .srx-hero-act:not(.open) .srx-btn[data-sec].srx-keep{display:none!important}",
        "#searchview .srx-hero-act.open .srx-btn.srx-keep[data-sec]{display:inline-flex!important}",
        "@media(max-width:360px){",
    ):
        assert s in src, s


def test_mobile_watch_gives_first_screen_to_the_chart_and_empty_filter_bar_stays_hidden():
    src = _src()
    for s in (
        "#montab-watch .ws-sub{display:none!important}",
        "#montab-watch .ws-routebar-h{display:none!important}",
        "#montab-watch .ws-chart-foot{display:none!important}",
        "if(!head&&!chips){el.innerHTML='';return}",
    ):
        assert s in src, s


def test_narrow_watch_kpi_values_never_wrap():
    src = _src()
    assert "#montab-watch .ws-kpi .v{font-size:15px!important;white-space:nowrap!important}" in src
