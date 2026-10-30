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

# 节点解析分两步：先取整个 <node ...> 标签，再分别抽属性。
# **不要用一个正则同时抓 text/resource-id/bounds**——惰性量词下
# 中间那个可选分组会永远匹配空（实测踩过：rid 恒为 None，
# 日历价整片丢成 None 却不报错）。
_TAG_RE = re.compile(r"<node\b[^>]*>")
_ATTR_CACHE = {}


def _attr(tag: str, name: str) -> str:
    key = (name,)
    pat = _ATTR_CACHE.get(key)
    if pat is None:
        pat = re.compile(name + r'="([^"]*)"')
        _ATTR_CACHE[key] = pat
    m = pat.search(tag)
    return m.group(1) if m else ""


_BOUNDS_RE = re.compile(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')

# 日历格子的稳定 id（实测）：date-item-3 / date-item-3-date-text /
# date-item-3-price-text。**按 id 配对优先于按坐标配对**——坐标会随
# 分辨率/字体缩放漂移，id 是 App 自己的结构约定。
_ITEM_ID_RE = re.compile(r"^date-item-(\d+)(?:-(date|day|price)-text)?$")

_DATE_RE = re.compile(r"^(\d{2})-(\d{2})$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_FLIGHT_RE = re.compile(r"\b([A-Z]{2}\d{3,4})\b")
# 货币符号有全角/半角两种写法，dump 里都出现过，必须都认
_CURRENCY = "\u00a5\uffe5"
# 整串价：¥450 / ￥450 / ¥ 450
_PRICE_FULL_RE = re.compile(r"^[" + _CURRENCY + r"]\s*(\d{2,5})$")
# 遮罩价：4?? / 3??
_PRICE_MASK_RE = re.compile(r"^\d[?？]{1,3}$")
# 单独的货币符号（遮罩价的前半截 / 拆开价格的前半截）
_CURRENCY_ONLY = tuple(_CURRENCY)
# 拆开的价格后半截：纯数字
_DIGITS_RE = re.compile(r"^\d{2,5}$")

_EMPTY_MARKS = ("没有找到符合条件的航班", "查不到相关航班",
                "暂无符合条件的航班", "没有符合条件")
_LOGIN_MARKS = ("立即登录", "登录解锁更低价", "手机号登录", "验证码登录")


class Node:
    __slots__ = ("text", "rid", "x1", "y1", "x2", "y2")

    def __init__(self, text: str, rid: str, x1: int, y1: int,
                 x2: int, y2: int):
        self.text = text
        self.rid = rid
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2

    def __repr__(self):
        return f"Node({self.text!r}, {self.rid!r}, {self.x1},{self.y1})"


def _nodes(xml: str) -> List[Node]:
    out = []
    for m in _TAG_RE.finditer(xml or ""):
        tag = m.group(0)
        text = _attr(tag, "text").strip()
        if not text:
            continue
        b = _BOUNDS_RE.search(tag)
        if not b:
            continue
        out.append(Node(text, _attr(tag, "resource-id").strip(),
                        int(b.group(1)), int(b.group(2)),
                        int(b.group(3)), int(b.group(4))))
    return out


def _parse_calendar_by_id(nodes: List[Node]) -> List[dict]:
    """按 date-item-N-* 的 resource-id 配对日期与价格。

    这是首选路径：id 是 App 自己的结构约定，比坐标稳。同一个 N 下
    date-text 给日期、price-text 给价格；缺 price 的格子按"该日无价"
    处理（不是 0）。
    """
    groups: Dict[str, dict] = {}
    for n in nodes:
        m = _ITEM_ID_RE.match(n.rid or "")
        if not m:
            continue
        idx, kind = m.group(1), m.group(2) or "root"
        g = groups.setdefault(idx, {})
        g[kind] = n
    if not groups:
        return []

    out = []
    for idx in sorted(groups, key=lambda k: int(k)):
        g = groups[idx]
        date_node = g.get("date")
        if date_node is None:
            continue
        date_text = date_node.text
        if not _DATE_RE.match(date_text):
            continue
        price_node = g.get("price")
        price, masked = None, False
        if price_node is not None:
            pm = _PRICE_FULL_RE.match(price_node.text)
            if pm:
                price = int(pm.group(1))
            elif _PRICE_MASK_RE.match(price_node.text):
                masked = True
            elif price_node.text in _CURRENCY_ONLY:
                # 「¥」单独一格，数字在相邻节点 —— 交给坐标兜底路径
                price = None
        out.append({"date": date_text, "price": price, "masked": masked})
    return sorted(out, key=lambda r: r["date"])


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

    两条路径：
      1. **resource-id 配对**（首选）——App 自己的结构约定，最稳；
      2. **坐标配对**（兜底）——某些版本/页面没有 id 时才用。实测
         '10-29' cy=129 与 '¥450' cy=153 差 24px，是"上日期下价格"的
         竖排格子，不是同一行，所以兜底不能用行容差。
    """
    nodes = _nodes(xml)
    by_id = _parse_calendar_by_id(nodes)
    if by_id:
        return by_id

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

    列表页的价格是**两个独立节点**：`￥`（x1=793）紧跟 `270`（x1=806），
    并不像首页条那样整串 `¥256`。只看整串会把整页判成「有航班无价格」，
    进而被当成 `ok` 但有 0 行报价——明细整片作废（实测 11-05 踩过）。
    """
    nodes = _nodes(xml)
    price_nodes = []
    for i, n in enumerate(nodes):
        m = _PRICE_FULL_RE.match(n.text)
        if m:
            price_nodes.append((n, int(m.group(1)), False))
            continue
        if n.text in _CURRENCY_ONLY:
            # ① 拆开的正常价：￥ + 数字，同一行、紧邻右侧
            merged = False
            for nxt in nodes[i + 1:i + 3]:
                if not _DIGITS_RE.match(nxt.text) or not _same_row(n, nxt):
                    continue
                if not (0 <= nxt.x1 - n.x1 <= 60):
                    continue
                price_nodes.append((nxt, int(nxt.text), False))
                merged = True
                break
            if merged:
                continue
            # ② 后面紧跟 4?? 即遮罩价
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
        # 时刻：同一行左右各一个——左=起飞、右=到达。
        # 行高实测约 150px（时刻 cy=308、航司/航班号 cy=535 是相邻两块），
        # 容差给 60 会把「起降时刻」漏掉，只剩航班号。
        # 到达时刻必须一起取：下游 `_real_offer` 要求 depTime 与 arrTime
        # **同时存在**才算真实班次，只给起飞会让 App 的所有行被剔出最优池
        # ——库里明明有数据，页面却什么都看不到（实测踩过）。
        dep = ""
        arr = ""
        best_dy = None
        for cand in nodes:
            if not _TIME_RE.match(cand.text):
                continue
            dy = abs(cand.cy - n.cy)
            if dy > 120:
                continue
            if cand.x1 > n.x1:
                continue
            if best_dy is None or dy < best_dy:
                best_dy, dep = dy, cand.text
        # 右侧最近的时刻 = 到达（同排、在航班号右边）
        best_dx = None
        for cand in nodes:
            if not _TIME_RE.match(cand.text):
                continue
            if abs(cand.cy - n.cy) > 120:
                continue
            if cand.x1 <= n.x1:
                continue
            dx = cand.x1 - n.x1
            if best_dx is None or dx < best_dx:
                best_dx, arr = dx, cand.text
        # 价格：同航班行、水平在右侧
        price, masked = None, False
        best_dy = None
        for pnode, value, is_mask in price_nodes:
            dy = abs(pnode.cy - n.cy)
            if dy > 120:
                continue
            if pnode.x1 < n.x2:
                continue
            if best_dy is None or dy < best_dy:
                best_dy, price, masked = dy, value, is_mask
        rows.append({"flight_no": flight_no, "depart_time": dep,
                     "arrive_time": arr, "price": price, "masked": masked})
    return rows


def detect_state(xml: str) -> str:
    """页面状态：login / empty / ok / calendar / unknown。

    判据顺序（实测教训）：
      * **先判有数据**：未登录时页面顶部本来就有"登录解锁更低价"横幅，
        但列表照样渲染。若先判 login，会把"有数据但没登录"错误地当成
        "需要登录"，白白丢掉可用的日历价。
      * 再判空态：只有明确的"没有找到航班"文案才是 empty。
      * 最后才是 login：既无航班也没空态文案、但有登录墙，才归 login。

    「选择日期」日历弹层是**另一种有数据**的页面：它没有航班号，
    但有 flight_calendar_date_cell-* 的日期格子。不认它就会被判
    unknown，长窗口日历整片作废（实测踩过）。
    """
    if not xml or "<hierarchy" not in xml:
        return "unknown"
    if "flight_calendar_date_cell-" in xml:
        return "calendar"
    texts = [n.text for n in _nodes(xml)]
    joined = " ".join(texts)
    if any(_FLIGHT_RE.search(t) for t in texts):
        return "ok"
    if any(mark in joined for mark in _EMPTY_MARKS):
        return "empty"
    if any(mark in joined for mark in _LOGIN_MARKS):
        return "login"
    return "unknown"
