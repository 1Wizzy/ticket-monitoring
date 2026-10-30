# -*- coding: utf-8 -*-
"""智行首页操作：设置起降城市与日期，然后发起查询。

**只用文案锚点 + 结构相邻定位，不写死坐标**——坐标在不同分辨率/版本
下必变，而"机票查询按钮在首页下半部、城市在第二行"这类结构关系稳定。

流程（实测依据见 docs/APP_CHANNELS.md）：
  1. 回首页（app_stop + app_start，避免停在上次的列表页）
  2. 点出发城市 → 城市选择页 → 输入三字码/城市名 → 选第一项
  3. 点到达城市 → 同上
  4. 点日期 → 日历页 → 选目标日期
  5. 点「机票查询」

每一步都返回 (ok, detail)，调用方据此记 path_note，不猜。
"""
from __future__ import annotations

import re
import time

_NODE_RE = re.compile(
    r'text="([^"]*)"[^>]*(?:content-desc="([^"]*)")?[^>]*'
    r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')


class Node:
    __slots__ = ("text", "desc", "x1", "y1", "x2", "y2")

    def __init__(self, text, desc, x1, y1, x2, y2):
        self.text, self.desc = text, desc
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2

    @property
    def cx(self):
        return (self.x1 + self.x2) // 2

    @property
    def cy(self):
        return (self.y1 + self.y2) // 2

    def __repr__(self):
        return f"Node({self.text!r},{self.cx},{self.cy})"


def nodes(xml: str):
    out = []
    for m in _NODE_RE.finditer(xml or ""):
        text = (m.group(1) or "").strip()
        desc = (m.group(2) or "").strip()
        if not text and not desc:
            continue
        out.append(Node(text, desc, int(m.group(3)), int(m.group(4)),
                        int(m.group(5)), int(m.group(6))))
    return out


def find(nodes_list, *cands, exact=True):
    """按文案找第一个节点；exact=False 时为包含匹配。"""
    for want in cands:
        for n in nodes_list:
            if exact and n.text == want:
                return n
            if not exact and want in n.text:
                return n
    return None


def open_date_dialog(dev, log=None) -> tuple:
    """打开「选择日期」弹层（长窗口日历）。

    为什么要主动打开：首页日历条只覆盖约 8 天，而弹层是跨两个月的
    完整日历、每天明码标价（resource-id 里内嵌 ISO 日期）。对远期
    配置来说这是唯一能拿到价的入口。

    返回 (ok, detail)。失败不算致命——调用方可以退回首页日历条。

    刻意每次自己 dump：调用方手上的页面可能是"设航线之前"的旧帧，
    拿它定位日期入口会点到错的地方（省 5s 不如不错）。
    """
    xml = dev.dump()
    if not xml:
        return False, "未取到页面"
    if "Search_Calendar_Dialog_Title" in xml:
        return True, "已打开"

    trigger = None
    for n in nodes(xml):
        if re.fullmatch(r"\d{1,2}月\d{1,2}日", n.text or ""):
            trigger = n
            break
    if trigger is None:
        return False, "找不到日期入口"
    if not dev.tap(trigger.cx, trigger.cy):
        return False, "点击日期失败"

    # 轮询代替固定 sleep(3)+单次 dump：弹层通常在 1–2s 出现，
    # 固定等 3s 只是保底；轮询能在出现后立刻继续，失败时也不早退。
    deadline = time.time() + 12
    while time.time() < deadline:
        time.sleep(0.8)
        xml = dev.dump()
        if "Search_Calendar_Dialog_Title" in xml:
            if log:
                try:
                    log.info("[nav] 已打开长窗口日历")
                except Exception:
                    pass
            return True, "已打开"
    return False, "日期弹层未出现"


def close_date_dialog(dev) -> bool:
    """关闭日历弹层（点右上角关闭按钮）。"""
    for m in re.finditer(r'<node[^>]*resource-id="Search_Calendar_Dialog_Close"'
                         r'[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
                         dev.dump() or ""):
        x = (int(m.group(1)) + int(m.group(3))) // 2
        y = (int(m.group(2)) + int(m.group(4))) // 2
        return bool(dev.tap(x, y))
    return False


def set_flight_route(dev, from_name: str, to_name: str, date_ymd: str,
                     log=None, stop_before_search: bool = False) -> tuple:
    """设好起降与日期；stop_before_search=True 时停在首页不点查询。

    停在首页是为了先读「选择日期」长窗口日历——一旦点了「机票查询」
    进到列表页，日历入口就没了（实测列表页只有 8 天日期条）。
    """
    """在智行首页设置航线与日期并发起查询。返回 (ok, detail)。"""

    def say(msg):
        if log:
            try:
                log.info(msg)
            except Exception:
                pass

    dev.app_stop("cn.suanya.zhixing")
    time.sleep(1.5)
    if not dev.app_start("cn.suanya.zhixing"):
        return False, "启动 App 失败"

    # 轮询等首页，而不是固定 sleep。实测两个坑：
    #   ① 冷启动到首页可交互在 10–20s 波动（固定 8s 必失败）；
    #   ② 启动后**第一帧是上次残留的弹层**（124KB 日历层），
    #      第二帧才回到首页（63KB）——所以必须连续两帧都是首页
    #      才算稳定，否则会把残留弹层当成首页继续操作。
    xml = ""
    deadline = time.time() + 60
    stable = 0
    while time.time() < deadline:
        time.sleep(2.5)
        xml = dev.dump()
        if "机票查询" in xml and "Search_Calendar_Dialog_Title" not in xml:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
    if stable < 2:
        # App 会恢复到上次停留的弹层/子页（实测：卡在「选择日期」透明
        # Activity）。按返回键退出去比反复重启有效——实测一次返回即可。
        for _ in range(4):
            dev.keyevent("KEYCODE_BACK")
            time.sleep(2)
            xml = dev.dump()
            if "机票查询" in xml:
                break
        else:
            return False, "首页未就绪（等待 60s 并回退 4 级仍找不到「机票查询」）"

    ns = nodes(xml)

    ok, detail = _pick_city(dev, ns, from_name, side="from", log=say)
    if not ok:
        return False, f"设出发地失败：{detail}"
    xml = dev.dump()
    ns = nodes(xml)
    ok, detail = _pick_city(dev, ns, to_name, side="to", log=say)
    if not ok:
        return False, f"设目的地失败：{detail}"

    xml = dev.dump()
    ns = nodes(xml)
    ok, detail = _pick_date(dev, ns, date_ymd, log=say)
    if not ok:
        # 日期设不上不算致命：日历条覆盖范围有限，仍可用当前日期查询
        say(f"[warn] {detail}")

    if stop_before_search:
        # 关掉日期弹层（_pick_date 可能会打开它），留在首页
        close_date_dialog(dev)
        time.sleep(1)
        return True, "已设好航线（停在首页）"

    xml = dev.dump()
    ns = nodes(xml)
    btn = find(ns, "机票查询")
    if not btn:
        # 设城市/日期过程中可能进了子页没退回来（实测：日期选择会打开
        # 一个透明 Activity）。退一级再找，仍没有才判失败。
        dev.keyevent("KEYCODE_BACK")
        time.sleep(2)
        xml = dev.dump()
        ns = nodes(xml)
        btn = find(ns, "机票查询")
    if not btn:
        _dbg = [n.text for n in ns][:14]
        return False, f"找不到「机票查询」按钮（当前页文本：{_dbg}）"
    if not dev.tap(btn.cx, btn.cy):
        return False, "点击「机票查询」失败"
    time.sleep(12)
    return True, "已发起查询"


def _city_row(ns, side: str):
    """城市行：首页第二行左右两端各一个城市。

    实测首页布局：'上海'[74,472] 与 '北京'[826,472] 同一 y。
    选同一 y 上最左/最右的 2-3 字中文节点，避免误取其它行。
    """
    cands = [n for n in ns
             if 2 <= len(n.text) <= 4
             and all("\u4e00" <= c <= "\u9fff" for c in n.text)
             and 380 <= n.cy <= 560]
    if len(cands) < 2:
        return None
    cands.sort(key=lambda n: n.cx)
    return cands[0] if side == "from" else cands[-1]


def _pick_city(dev, ns, name: str, side: str, log=None) -> tuple:
    if not name:
        return False, "城市名为空"
    # 已一致就不点（避免多余跳转与失败面）
    cur = _city_row(ns, side)
    if cur and cur.text == name:
        return True, "已就位"

    # 只点"城市行"里的那个节点。**不要退化成点当前城市节点**：
    # 实测踩过——找不到明确入口时退而点 cur，结果点到了日期行，
    # 打开「选择日期」日历弹层，整条流程卡在透明 Activity 上。
    if cur is None:
        return False, "找不到城市行"
    target = cur
    if not dev.tap(target.cx, target.cy):
        return False, "点击城市失败"
    time.sleep(3)

    xml = dev.dump()
    ns2 = nodes(xml)
    # 进入城市选择页的判据：出现搜索/输入框，否则说明没跳转，直接失败
    box = find(ns2, "搜索", "请输入城市", exact=False)
    if box is None:
        return False, "未进入城市选择页"
    dev.tap(box.cx, box.cy)
    time.sleep(1)
    # 用中文输入；App 城市页支持中文搜索
    dev.input_text(name)
    time.sleep(3)
    xml = dev.dump()
    ns3 = nodes(xml)
    hit = find(ns3, name, exact=False)
    if hit is None:
        time.sleep(3)
        ns3 = nodes(dev.dump())
        hit = find(ns3, name, exact=False)
    if hit is None:
        return False, f"城市列表里找不到 {name}"
    dev.tap(hit.cx, hit.cy)
    time.sleep(3)
    return True, "已选"


def _pick_date(dev, ns, ymd: str, log=None, xml: str = "") -> tuple:
    """日期选择：首页有日期行（'10月30日'），点开日历后选目标日。

    首页日历条只覆盖约 8 天；更远的日期必须到「选择日期」长窗口里点。
    那里按 **resource-id 里的 ISO 日期**定位，不按文案匹配——文案是
    「11月5日」（不补零），跨月后还会重复，按文案找会点错或找不到
    （实测 11-05 就这样被判成「日历里找不到」而放弃）。

    弹层**已经开着**时（xml 里带 Search_Calendar_Dialog）必须直接点格子：
    此时弹层盖住了首页日期行，再去找「日期入口」只会得到「找不到」。
    """
    if not ymd or len(ymd) < 10:
        return False, "日期格式非法"
    mm = int(ymd[5:7])
    dd = int(ymd[8:10])
    want = f"{mm}月{dd}日"

    if "Search_Calendar_Dialog" in (xml or ""):
        return _tap_calendar_cell(dev, xml, ymd)

    # 首页直接显示目标日期则无需点开日历
    home_hit = find(ns, want)
    if home_hit:
        return True, "首页已显示目标日期"

    trigger = None
    for n in ns:
        if re.fullmatch(r"\d{1,2}月\d{1,2}日", n.text or ""):
            trigger = n
            break
    if trigger is None:
        return False, "找不到日期入口"
    if not dev.tap(trigger.cx, trigger.cy):
        return False, "点击日期失败"
    time.sleep(3)

    xml2 = dev.dump() or ""
    if "Search_Calendar_Dialog" in xml2:
        return _tap_calendar_cell(dev, xml2, ymd)

    # 兜底：目标日就在当前可见的普通日历页里（文案匹配）
    hit = find(nodes(xml2), want)
    if hit is None:
        return False, f"日历里找不到 {want}"
    dev.tap(hit.cx, hit.cy)
    time.sleep(2)
    return True, "已选日期"


def _tap_calendar_cell(dev, xml: str, ymd: str) -> tuple:
    """在已打开的长窗口日历里点目标日；成功后弹层会自己关掉。"""
    cell = _calendar_cell(xml, ymd)
    if cell is None:
        return False, f"日历里没有 {ymd} 这一格"
    if not dev.tap(*cell):
        return False, f"点击日历格失败 {ymd}"
    time.sleep(2)
    left = dev.dump() or ""
    if "Search_Calendar_Dialog" in left:
        return False, "点了日期但弹层没关"
    # 点完必须核对首页日期行真的换了：没换时点「机票查询」会重查上一日，
    # 结果被打上目标日期——价格错配而看不出任何异常。
    shown = home_date_text(left)
    if shown and shown != _want_md(ymd):
        return False, f"点完日期仍是 {shown}（期望 {_want_md(ymd)}）"
    return True, "已在长窗口选中日期"


def _want_md(ymd: str) -> str:
    """2026-11-05 -> '11月5日'（App 的日期行不补零）。"""
    return f"{int(ymd[5:7])}月{int(ymd[8:10])}日"


def home_date_text(xml: str) -> str:
    """首页日期行当前显示的目标日；找不到返回空串。"""
    for n in nodes(xml):
        t = (n.text or "").strip()
        if re.fullmatch(r"\d{1,2}月\d{1,2}日", t):
            return t
    return ""


def _calendar_cell(xml: str, ymd: str):
    """长窗口日历里某天的可点坐标；找不到返回 None。"""
    m = re.search(
        r'resource-id="flight_calendar_date_cell-%s"[^>]*'
        r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"' % re.escape(ymd), xml or "")
    if not m:
        return None
    x = (int(m.group(1)) + int(m.group(3))) // 2
    y = (int(m.group(2)) + int(m.group(4))) // 2
    return x, y
