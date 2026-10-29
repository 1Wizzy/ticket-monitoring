# -*- coding: utf-8 -*-
"""智行 App 的 UI dump 解析（纯函数，可离线测）。

真实结构（2026-10-29 dump，见 tests/fixtures/zhixing_list_dump.xml）：

  日历条：日期 / 星期 / 价格 三个兄弟节点同一行
    '10-29'  [22,118][67,141]
    '今天'    [70,118][104,141]
    '¥450'   [39,142][86,164]

  航班行：时刻 / 机场 / 航班号 / 价格分列，价格未登录时被拆成
    '￥'     [793,426][806,449]     ← 货币符号单独一个节点
    '4??'    [806,413][865,451]     ← 数字被遮罩

所以价格解析必须同时处理「¥450 整串」与「￥ + 4?? 分离」两种形态，
且**绝不能**把 `4??` 当成 4 元或 0 元——那是把"看不到"伪装成"很便宜"。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# 节点：text + bounds
_NODE_RE = re.compile(
    r'text="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')

_DATE_RE = re.compile(r"^(\d{2})-(\d{2})$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_FLIGHT_RE = re.compile(r"\b([A-Z]{2}\d{3,4})\b")
# 货币符号有全角/半角两种写法，dump 里都出现过，必须都认
_CURRENCY = "\u00a5\uffe5"
# 整串价：¥450 / ￥450 / ¥ 450
_PRICE_FULL_RE = re.compile(r"^[" + _CURRENCY + r"]\s*(\d{2,5})$")
# 遮罩价：4?? / 3??
_PRICE_MASK_RE = re.compile(r"^\d[?？]{1,3}$")
# 单独的货币符号（遮罩价的前半截）
_CURRENCY_ONLY = tuple(_CURRENCY)

_EMPTY_MARKS = ("没有找到符合条件的航班", "查不到相关航班",
                "暂无符合条件的航班", "没有符合条件")
_LOGIN_MARKS = ("立即登录", "登录解锁更低价", "手机号登录", "验证码登录")


class Node:
    __slots__ = ("text", "x1", "y1", "x2", "y2")

    def __init__(self, text: str, x1: int, y1: int, x2: int, y2: int):
        self.text = text
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2

    def __repr__(self):
        return f"Node({self.text!r}, {self.x1},{self.y1})"


def _nodes(xml: str) -> List[Node]:
    out = []
    for m in _NODE_RE.finditer(xml or ""):
        text = (m.group(1) or "").strip()
        if not text:
            continue
        out.append(Node(text, int(m.group(2)), int(m.group(3)),
                        int(m.group(4)), int(m.group(5))))
    return out


def _same_row(a: Node, b: Node, tol: int = 14) -> bool:
    """同一视觉行：中心 y 差在容差内（dump 里同一行会差几像素）。"""
    return abs(a.cy - b.cy) <= tol


def _same_column_group(a: Node, b: Node, tol: int = 34) -> bool:
    """同一天列内的上下叠放：日期在上、价格在下。

    实测：'10-29' cy=129，'¥450' cy=153，相差 24px——比"同一行"的
    容差大，所以不能用 _same_row 判定；但它们在水平方向几乎左对齐
    （x1 22 vs 39）。日历格子正是这种「上日期下价格」的竖排。
    """
    return abs(a.cy - b.cy) <= tol and abs(a.x1 - b.x1) <= 30


def parse_calendar(xml: str) -> List[dict]:
    """解析顶部日历条：返回 [{date, price, masked}]，缺价日 price=None。

    date 保持 MM-DD（App 上就是 MM-DD，年份由调用方按查询日期补）。
    """
    nodes = _nodes(xml)
    prices = []
    for n in nodes:
        m = _PRICE_FULL_RE.match(n.text)
        if m:
            prices.append((n, int(m.group(1))))
            continue
        if _PRICE_MASK_RE.match(n.text):
            prices.append((n, None))

    out: Dict[str, dict] = {}
    for n in nodes:
        m = _DATE_RE.match(n.text)
        if not m:
            continue
        # 找同一天格子内、水平方向最接近的价格节点（日期在上、价格在下）
        best = None
        for pnode, value in prices:
            if not _same_column_group(n, pnode):
                continue
            dx = abs(pnode.x1 - n.x1)
            if best is None or dx < best[0]:
                best = (dx, pnode, value)
        if best is None:
            out.setdefault(n.text, {"date": n.text, "price": None,
                                    "masked": False})
            continue
        _, pnode, value = best
        prev = out.get(n.text)
        # 同一天出现多次时，取有价的那个
        if prev and prev["price"] is not None and value is None:
            continue
        out[n.text] = {"date": n.text, "price": value,
                       "masked": value is None and _PRICE_MASK_RE.match(
                           pnode.text) is not None}
    return sorted(out.values(), key=lambda r: r["date"])


def parse_flight_rows(xml: str) -> List[dict]:
    """解析航班行：以航班号为锚，向左取时刻、向右按行取价格。

    返回 [{flight_no, depart_time, price, masked}]；
    价格被遮罩时 price=None, masked=True（绝不给 0）。
    """
    nodes = _nodes(xml)
    price_nodes = []
    for i, n in enumerate(nodes):
        m = _PRICE_FULL_RE.match(n.text)
        if m:
            price_nodes.append((n, int(m.group(1)), False))
            continue
        if n.text in _CURRENCY_ONLY:
            # 后面紧跟一个 4?? 即遮罩价
            for nxt in nodes[i + 1:i + 3]:
                if _PRICE_MASK_RE.match(nxt.text) and _same_row(n, nxt):
                    price_nodes.append((nxt, None, True))
                    break

    rows = []
    for n in nodes:
        m = _FLIGHT_RE.search(n.text)
        if not m:
            continue
        flight_no = m.group(1)
        # 时刻：同航班行附近、水平在左侧的时刻节点
        dep = ""
        best_dx = None
        for cand in nodes:
            if not _TIME_RE.match(cand.text):
                continue
            if abs(cand.cy - n.cy) > 60:
                continue
            if cand.x1 > n.x1:
                continue
            dx = n.x1 - cand.x1
            if best_dx is None or dx < best_dx:
                best_dx, dep = dx, cand.text
        # 价格：同航班行、水平在右侧
        price, masked = None, False
        best_dx = None
        for pnode, value, is_mask in price_nodes:
            if abs(pnode.cy - n.cy) > 70:
                continue
            if pnode.x1 < n.x2:
                continue
            dx = pnode.x1 - n.x2
            if best_dx is None or dx < best_dx:
                best_dx, price, masked = dx, value, is_mask
        rows.append({"flight_no": flight_no, "depart_time": dep,
                     "price": price, "masked": masked})
    return rows


def detect_state(xml: str) -> str:
    """页面状态：login / empty / ok / unknown。

    判据顺序（实测教训）：
      * **先判有数据**：未登录时页面顶部本来就有"登录解锁更低价"横幅，
        但列表照样渲染。若先判 login，会把"有数据但没登录"错误地当成
        "需要登录"，白白丢掉可用的日历价。
      * 再判空态：只有明确的"没有找到航班"文案才是 empty。
      * 最后才是 login：既无航班也没空态文案、但有登录墙，才归 login。
    """
    if not xml or "<hierarchy" not in xml:
        return "unknown"
    texts = [n.text for n in _nodes(xml)]
    joined = " ".join(texts)
    if any(_FLIGHT_RE.search(t) for t in texts):
        return "ok"
    if any(mark in joined for mark in _EMPTY_MARKS):
        return "empty"
    if any(mark in joined for mark in _LOGIN_MARKS):
        return "login"
    return "unknown"
