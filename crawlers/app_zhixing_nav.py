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
    time.sleep(3)
    xml = dev.dump()
    if "Search_Calendar_Dialog_Title" not in xml:
        return False, "日期弹层未出现"
    if log:
        try:
            log.info("[nav] 已打开长窗口日历")
        except Exception:
            pass
    return True, "已打开"


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
                     log=None) -> tuple:
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


def _pick_date(dev, ns, ymd: str, log=None) -> tuple:
    """日期选择：首页有日期行（'10月30日'），点开日历后选目标日。"""
    if not ymd or len(ymd) < 10:
        return False, "日期格式非法"
    mm = int(ymd[5:7])
    dd = int(ymd[8:10])
    want = f"{mm}月{dd}日"
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
    ns2 = nodes(dev.dump())
    hit = find(ns2, want)
    if hit is None:
        return False, f"日历里找不到 {want}"
    dev.tap(hit.cx, hit.cy)
    time.sleep(2)
    return True, "已选日期"
