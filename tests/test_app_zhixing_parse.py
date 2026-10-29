# -*- coding: utf-8 -*-
"""智行 dump 解析器：以真实抓取的 fixture 为准。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.app_zhixing_parse import (detect_state, parse_calendar,
                                        parse_flight_rows)

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "fixtures", "zhixing_list_dump.xml")


def _xml():
    with open(FIX, encoding="utf-8") as f:
        return f.read()


def test_calendar_extracts_real_prices():
    rows = parse_calendar(_xml())
    prices = {r["date"]: r["price"] for r in rows}
    # 真实界面今日与明日最低价（截图与 dump 一致）
    assert prices.get("10-29") == 450
    assert prices.get("10-30") == 430
    assert prices.get("10-31") == 377
    assert len(rows) >= 7


def test_calendar_sorted_by_date():
    rows = parse_calendar(_xml())
    keys = [r["date"] for r in rows]
    assert keys == sorted(keys)


def test_calendar_no_fake_zero_when_missing():
    """没有价格的日子必须是 None，不能补 0。"""
    for r in parse_calendar(_xml()):
        assert r["price"] is None or r["price"] > 0


def test_flight_rows_require_flight_no():
    rows = parse_flight_rows(_xml())
    assert rows, "fixture 里应有航班行"
    for r in rows:
        assert r["flight_no"], r
        assert len(r["flight_no"]) >= 5


def test_flight_rows_prices_are_masked_not_zero():
    """未登录时列表价是 ￥ + 4?? 的遮罩，必须标 masked，不得给出 0。"""
    rows = parse_flight_rows(_xml())
    masked = [r for r in rows if r.get("masked")]
    assert masked, "fixture 应存在被遮罩的列表价"
    for r in masked:
        assert r["price"] is None


def test_detect_state_ok_when_rows_present():
    assert detect_state(_xml()) == "ok"


def test_detect_state_empty_for_explicit_no_result():
    xml = ('<hierarchy><node text="抱歉，没有找到符合条件的航班" '
           'bounds="[0,0][100,40]" /></hierarchy>')
    assert detect_state(xml) == "empty"


def test_detect_state_login_only_when_no_rows():
    """有登录横幅但列表照常渲染时，应判 ok（未登录也能拿日历价）。"""
    xml = ('<hierarchy><node text="登录解锁更低价" bounds="[0,0][100,40]" />'
           '<node text="KN5978" bounds="[0,60][100,90]" /></hierarchy>')
    assert detect_state(xml) == "ok"


def test_detect_state_login_when_blank_page():
    xml = ('<hierarchy><node text="立即登录" bounds="[0,0][100,40]" />'
           '<node text="请输入手机号" bounds="[0,60][100,90]" /></hierarchy>')
    assert detect_state(xml) == "login"


def test_detect_state_unknown_on_garbage():
    assert detect_state("") == "unknown"
    assert detect_state("<hierarchy></hierarchy>") == "unknown"
