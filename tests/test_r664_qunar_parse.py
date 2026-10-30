# -*- coding: utf-8 -*-
"""r664：去哪儿 App 解析器（用真机 dump 的坐标结构复现）。

真机实测（com.Qunar 10.4.24, 900x1600）列表页形态：
    y=363  20:30        22:50        ← 起降时刻（左/右，同一行）
    y=436  南航CZ8889               ← 航班号（**中文紧贴字母，没有词边界**）
    y=589  ¥ … 377                 ← 票价（右列）
    y=625  已减 ¥70                ← 优惠标签，**不是票价**

三条都是踩过的坑：
  1. `\\b[A-Z]{2}\\d{3,4}\\b` 在「南航CZ8889」上一个都匹配不到（中文与
     字母之间没有 \\b），航班号整列消失；
  2. 「已减 ¥70」的 70 被当成票价，把 ￥378 的票报成 ￥70；
  3. 卡片必须按 y 分段配对，按文档顺序贪心会把相邻卡片的时刻串起来。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.app_qunar_parse import detect_state, parse_flight_rows


def _n(text, x1, y1, x2, y2):
    return ('<node text="%s" resource-id="" class="android.widget.TextView"'
            ' bounds="[%d,%d][%d,%d]" />' % (text, x1, y1, x2, y2))


def _card(dep_y, dep, arr, code, price, discount=None, name_prefix="航司"):
    """按真机 dump 的坐标画一张卡片。

    真机上「已减」「¥」「70」三个节点在**同一行**（实测 y=625–647），
    连排在票价右侧；票价本身在下一行的右列。这里必须照样画——
    早期 fixture 把优惠标签画到了票价上方 66px（真机没有这种形态），
    结果测试针对的是一个不存在的布局。
    """
    parts = [
        _n(dep, 36, dep_y, 120, dep_y + 50),
        _n(arr, 270, dep_y, 354, dep_y + 50),
        _n("北京大兴", 36, dep_y + 34, 120, dep_y + 57),
        _n("虹桥T2", 267, dep_y + 34, 351, dep_y + 57),
        _n(name_prefix + code, 57, dep_y + 73, 160, dep_y + 94),
        _n("经济舱1.8折", 1413, dep_y + 72, 1563, dep_y + 95),
        _n("¥", 1492, dep_y + 53, 1504, dep_y + 94),
        _n(str(price), 1504, dep_y + 50, 1563, dep_y + 94),
    ]
    if discount:
        # 真机形态：与票价同一行（cy 差 0），在票价右侧连排
        parts += [_n("已减", 1560, dep_y + 50, 1594, dep_y + 72),
                  _n("¥", 1594, dep_y + 50, 1604, dep_y + 72),
                  _n(str(discount), 1604, dep_y + 50, 1623, dep_y + 72)]
    return "".join(parts)


def _xml(*cards):
    return ('<?xml version="1.0"?><hierarchy rotation="0">'
            + "".join(cards) + "</hierarchy>")


def test_chinese_prefixed_flight_no_is_parsed():
    """「南航CZ8889」必须取出 CZ8889。"""
    xml = _xml(_card(363, "20:30", "22:50", "CZ8889", 280))
    rows = parse_flight_rows(xml)
    assert rows and rows[0]["flight_no"] == "CZ8889", rows


def test_discount_label_is_not_a_fare():
    """「已减 ¥70」是优惠标签，票价仍是 ¥378。"""
    xml = _xml(_card(746, "21:35", "23:40", "MU123", 378, discount=70))
    rows = parse_flight_rows(xml)
    assert rows[0]["price"] == 378, rows


def test_cards_are_paired_by_row_not_document_order():
    """相邻卡片不得串行：每张卡片的到达时刻取自本行。"""
    xml = _xml(
        _card(363, "20:30", "22:50", "CZ8889", 280),
        _card(589, "20:30", "22:40", "MU5130", 377),
        _card(746, "21:35", "23:40", "MU123", 378, discount=70),
    )
    rows = parse_flight_rows(xml)
    got = [(r["flight_no"], r["depart_time"], r["arrive_time"], r["price"])
           for r in rows]
    assert got == [("CZ8889", "20:30", "22:50", 280),
                   ("MU5130", "20:30", "22:40", 377),
                   ("MU123", "21:35", "23:40", 378)], got


def test_missing_price_stays_none():
    """没有价格的卡片不得给 0 或猜价。"""
    xml = _xml(_n("20:30", 36, 363, 120, 413),
               _n("22:50", 270, 363, 354, 413),
               _n("南航CZ8889", 57, 436, 160, 457))
    rows = parse_flight_rows(xml)
    assert rows and rows[0]["price"] is None


def test_login_page_is_login_state():
    xml = ('<?xml version="1.0"?><hierarchy>'
           + _n("+86", 39, 673, 79, 706)
           + _n("请输入手机号", 159, 663, 1561, 717)
           + _n("获取验证码", 54, 785, 1546, 866)
           + "</hierarchy>")
    assert detect_state(xml) == "login"


def test_list_page_is_ok_state():
    xml = _xml(_card(363, "20:30", "22:50", "CZ8889", 280))
    assert detect_state(xml) == "ok"


def test_empty_only_on_explicit_copy():
    """只有明确的「没找到」文案才算 empty。"""
    xml = ('<?xml version="1.0"?><hierarchy>'
           + _n("没有找到符合条件的航班", 100, 700, 800, 740)
           + "</hierarchy>")
    assert detect_state(xml) == "empty"
