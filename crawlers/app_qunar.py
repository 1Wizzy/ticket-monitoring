# -*- coding: utf-8 -*-
"""去哪儿 App 渠道（com.Qunar）。

数据面事实（2026-10-30 真机实测，MuMu / Android 15 / 10.4.24）：
  * **必须登录**：未登录时首页仍是登录页，「搜索」和日历「查价」都会
    跳到 `UCLoginByPhoneActivity`，返回键也退不出去；
  * 登录后日历页给出 6 天真实价（¥450/¥280/¥399…），点「下一步」进列表，
    列表含航班号/起降时刻/价格（实测 3 班：CZ8889 ￥280 等）；
  * 首页深链 `qunarphone://flight` 可直达机票页（绕开首启登录墙，
    但搜索仍要登录）。

因此本渠道把登录视为**硬前置**：未登录一律记 `login`，绝不记 `empty`。
"""
from __future__ import annotations

import json
import re
import time
from typing import List

from core.models import FlightPrice

from .app_base import AppCrawler
from .app_qunar_parse import detect_state, parse_flight_rows

_APP_PKG = "com.Qunar"
_APP_LABEL = "去哪儿旅行"
_FLIGHT_HOME_URI = "qunarphone://flight"

# 首页控件（RN 页面只有少量稳定 id，其余靠文案）
_ID_DEP_CITY = "com.Qunar:id/atom_flight_tv_dep_city"
_ID_ARR_CITY = "com.Qunar:id/atom_flight_tv_arr_city"
_ID_DEP_DATE = "com.Qunar:id/atom_flight_ll_dep_date"
_ID_SEARCH = "com.Qunar:id/atom_flight_rl_search_btn"

_NODE_RE = re.compile(r"<node\b([^>]*?)/?>")
_CITY_ALIAS = {"SHA": "上海", "CAN": "广州", "BJS": "北京", "PEK": "北京",
               "SZX": "深圳", "CTU": "成都", "HGH": "杭州", "WUH": "武汉",
               "XIY": "西安", "CKG": "重庆", "NKG": "南京", "TAO": "青岛"}


def _nodes(xml: str):
    out = []
    for m in _NODE_RE.finditer(xml or ""):
        s = m.group(1)
        t = re.search(r'text="([^"]*)"', s)
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', s)
        r = re.search(r'resource-id="([^"]*)"', s)
        if not b:
            continue
        out.append({"text": (t.group(1) if t else "").strip(),
                    "id": (r.group(1) if r else ""),
                    "x1": int(b.group(1)), "y1": int(b.group(2)),
                    "x2": int(b.group(3)), "y2": int(b.group(4))})
    return out


def _center(n):
    return (n["x1"] + n["x2"]) // 2, (n["y1"] + n["y2"]) // 2


def _find_id(xml, rid):
    for n in _nodes(xml):
        if n["id"] == rid:
            return n
    return None


def _find_text(xml, want, exact=True):
    for n in _nodes(xml):
        if (n["text"] == want) if exact else (want in n["text"]):
            return n
    return None


def _ymd_to_md(ymd: str) -> str:
    """2026-11-12 -> '11.12'（去哪儿日历的日期文案）。"""
    return "%s.%s" % (ymd[5:7], ymd[8:10])


def _today_iso() -> str:
    """设备本地今天（YYYY-MM-DD）：校准「今天」这个特殊文案用。"""
    return time.strftime("%Y-%m-%d")


class AppQunarCrawler(AppCrawler):
    name = "app_qunar"
    package = _APP_PKG

    def login_url(self) -> str:
        return ("android://%s?login=phone —— 去哪儿 App 必须用手机号登录"
                "（含验证码），登录后本渠道才可用" % _APP_PKG)

    def fetch(self, from_city: str, to_city: str, dates: List[str],
              ret_date: str = "") -> List[FlightPrice]:
        rt = self.runtime()
        ok, why = rt.ensure_ready()
        if not ok:
            self.path_note("android-runtime", "unsupported",
                           detail=f"runtime not ready: {why}")
            self.logger.warning("[%s] Android 运行时未就绪：%s", self.name, why)
            return []
        dev = rt.device()
        if dev is None:
            self.path_note("android-device", "unsupported",
                           detail="device unavailable")
            return []

        with rt.device_lock():
            self.restore_login_snapshot(dev)
            self._dismiss_system_prompts(dev)
            return self._fetch_locked(dev, from_city, to_city, dates)

    # ---------- 内部 ----------
    def _fetch_locked(self, dev, from_city: str, to_city: str,
                      dates: List[str]) -> List[FlightPrice]:
        started = time.time()

        # 冷启动后先看页面：登录墙必须最先识别，此时做任何点击都是白费
        if not self._open_flight_home(dev):
            self.path_note("app-dump", "timeout",
                           detail="没能打开机票首页")
            return []
        xml = dev.dump() or ""
        state = detect_state(xml)
        if state == "login" or self._is_login_page(xml):
            self.login_suspect = True
            self.path_note("app-login", "login",
                           detail="需要手机号登录（去哪儿不登录拿不到任何票价）")
            self.logger.warning("[%s] 页面要求登录，本渠道需先完成手机号登录",
                                self.name)
            return []

        from_name = _CITY_ALIAS.get(from_city, from_city)
        to_name = _CITY_ALIAS.get(to_city, to_city)

        out: List[FlightPrice] = []
        targets = [d for d in (dates or []) if d] or [""]
        for target in targets:
            try:
                got, why = self._one_date(dev, from_name, to_name, target,
                                          from_city, to_city)
            except Exception as e:
                self.logger.info("[%s] %s 采集异常：%s", self.name, target,
                                 type(e).__name__)
                got, why = [], "exception:" + type(e).__name__
            if got:
                out.extend(got)
                self.path_note("航班列表", "ok", rows=len(got), date=target,
                               detail="%d 条真实航班/时刻/价格" % len(got),
                               ms=int((time.time() - started) * 1000),
                               url=f"android://{_APP_PKG}?view=list")
            elif why == "empty":
                # 只有**确认列表页已打开且明确无航班**才记 empty。
                # 「没拿到页面 / 等不到结果」不是「没票」——那是 timeout，
                # 记成 empty 会让账本说谎（本项目最忌的一类）。
                self.path_note("航班列表", "empty", date=target,
                               detail="列表已打开，该日无可售航班")
            else:
                # 兜底：任何非 ok/empty 的原因都要如实写进 detail。
                # 曾出现 reason 为空串 → 记成一句「未取到可用列表页：」，
                # 读者看不出卡在哪一步（真机排查时白绕一圈）。
                self.path_note("航班列表", "timeout", date=target,
                               detail="未取到可用列表页：%s"
                                      % (why or "unknown"))
        return out

    def _is_login_page(self, xml: str) -> bool:
        return "请输入手机号" in (xml or "") or "获取验证码" in (xml or "")

    def _open_flight_home(self, dev) -> bool:
        """确保停在机票首页。用深链最稳（首页状态不可控时也能直达）。"""
        act = ""
        try:
            act = (dev.current_activity() or "").lower()
        except Exception:
            pass
        xml = dev.dump() or ""
        if "atom_flight_search_panel" in xml and not self._is_login_page(xml):
            return True
        if not dev.deep_link(_FLIGHT_HOME_URI):
            self.logger.info("[%s] 深链启动失败，改用 app_start", self.name)
            if not dev.app_start(_APP_PKG):
                return False
        deadline = time.time() + 40
        while time.time() < deadline:
            time.sleep(3)
            xml = dev.dump() or ""
            if self._is_login_page(xml):
                # 走到这里说明登录态没恢复：交给调用方按 login 入账
                return bool(xml)
            if "atom_flight_search_panel" in xml:
                return True
        return False

    def _one_date(self, dev, from_name: str, to_name: str,
                  target: str, from_city: str = "",
                  to_city: str = "") -> List[FlightPrice]:
        """设城市 → 选日期 → 点搜索 → 解析列表。返回 (prices, reason)。

        reason 只在没拿到价格时有意义：`empty` 表示**列表页确实打开了且
        该日无航班**；其他值（home/set-city/pick-date/timeout/login）都是
        「这次没采成」，调用方据此记 timeout 或 login，绝不能一律记 empty。
        """
        xml = self._ensure_home(dev)
        if not xml:
            return [], "no-home"
        if not self._set_city(dev, xml, from_name, side="dep"):
            self.logger.info("[%s] 设出发城市失败：%s", self.name, from_name)
            return [], "set-city-dep"
        # 选完城市会回到首页，这里直接读页面即可。**不要再调
        # `_ensure_home`**：它为了「不在首页」会先按 3 次 BACK、再等
        # 25s 深链，实测一次要 33s；三个步骤各调一次就把 60s 搜索窗口
        # 吃光，整轮 0 行（真机踩过）。
        if not self._set_city(dev, dev.dump() or "", to_name, side="arr"):
            self.logger.info("[%s] 设到达城市失败：%s", self.name, to_name)
            return [], "set-city-arr"
        if target:
            picked = self._pick_date(dev, dev.dump() or "", target)
            if not picked:
                # 选日失败常常是因为打开了日历弹层（或停在别的子页），
                # 此时首页的「搜索」按钮不可见 → `_search_and_parse`
                # 直接返回 no-search-button。先退回首页再搜：
                # 宁可查错日（有日期诚实性问题）也不能整轮 0 行。
                self.logger.info("[%s] 选日期失败（%s），退回首页继续搜索",
                                 self.name, target)
                if not self._ensure_home(dev):
                    return [], "pick-date-failed"
        return self._search_and_parse(dev, target, from_city, to_city)

    def _ensure_home(self, dev):
        """取首页 XML；不在首页就退回深链。

        实测坑：App 停在**日历页/列表页**时，`qunarphone://flight` 深链
        常被 RN 导航栈当成重复入口吞掉，25s 也等不到首页搜索面板，
        于是「设城市」找不到 `atom_flight_search_panel` 里的控件而失败，
        整轮 0 行。所以先按 BACK 退栈（最多 3 次），再发深链兜底。
        """
        xml = dev.dump() or ""
        if "atom_flight_search_panel" in xml:
            return xml
        if self._is_login_page(xml):
            return xml          # 交给调用方按 login 入账，别在这里兜圈
        for _ in range(3):
            if not dev.keyevent("KEYCODE_BACK"):
                break
            time.sleep(2)
            xml = dev.dump() or ""
            if "atom_flight_search_panel" in xml or self._is_login_page(xml):
                return xml
        if not dev.deep_link(_FLIGHT_HOME_URI):
            return ""
        deadline = time.time() + 25
        while time.time() < deadline:
            time.sleep(2.5)
            xml = dev.dump() or ""
            if "atom_flight_search_panel" in xml or self._is_login_page(xml):
                return xml
        return ""

    def _set_city(self, dev, xml: str, city: str, side: str) -> bool:
        """点城市位 → 在搜索页选城市。已有同名则直接返回 True。"""
        rid = _ID_DEP_CITY if side == "dep" else _ID_ARR_CITY
        node = _find_id(xml, rid)
        if node is None:
            return False
        if node["text"] == city:
            return True
        if not dev.tap(*_center(node)):
            return False
        # 城市选择页：轮询出现可点的同名城市
        deadline = time.time() + 20
        while time.time() < deadline:
            time.sleep(2)
            page = dev.dump() or ""
            hit = _find_text(page, city)
            if hit is not None:
                if dev.tap(*_center(hit)):
                    time.sleep(2.5)
                    return True
            # 需要先在输入框里键入城市名
            box = _find_text(page, "请输入城市", exact=False) \
                or _find_text(page, "搜索城市", exact=False) \
                or _find_text(page, "输入城市", exact=False)
            if box is not None:
                dev.tap(*_center(box))
                time.sleep(1)
                dev.input_text(city)
                time.sleep(2)
        return False

    def _pick_date(self, dev, xml: str, target: str) -> bool:
        """在首页日期行上选目标日。

        实测：首页日期行是**中文**「10月31日」，不是「10.31」——
        `_ymd_to_md` 产的 `10.30` 一个都匹配不上，选日必然失败（真机踩过）。
        所以按中文格式找；找不到再点日期行打开日历弹层找。
        """
        want_cn = "%d月%d日" % (int(target[5:7]), int(target[8:10]))

        def _hit(page):
            h = _find_text(page, want_cn)
            if h is None:
                h = _find_text(page, target)     # 弹层里可能用 ISO
            if h is None and target == _today_iso():
                # 首页/日历把「今天」写成「今天」，没有「10月30日」文案
                h = _find_text(page, "今天")
            return h

        hit = _hit(xml)
        if hit is not None:
            # 首页当前显示的日期行就是目标日 → 已是选中态，点它也是幂等的
            if not dev.tap(*_center(hit)):
                return False
            time.sleep(2)
            return True
        # 点日期行打开日历弹层
        trigger = None
        for n in _nodes(xml):
            if re.fullmatch(r"\d{1,2}月\d{1,2}日", n["text"] or ""):
                trigger = n
                break
        if trigger is None or not dev.tap(*_center(trigger)):
            return False
        time.sleep(3)
        for _ in range(10):
            page = dev.dump() or ""
            h = _hit(page)
            if h is not None:
                if not dev.tap(*_center(h)):
                    return False
                time.sleep(2)
                return True
            dev.swipe(800, 1100, 800, 700, 400)   # 往后翻月
            time.sleep(2)
        return False

    def _search_and_parse(self, dev, target: str, from_city: str = "",
                          to_city: str = ""):
        """点搜索并解析列表。返回 (prices, reason)，reason 见 `_one_date`。"""
        xml = dev.dump() or ""
        btn = _find_id(xml, _ID_SEARCH)
        if btn is None:
            btn = _find_text(xml, "搜索")
        if btn is None:
            return [], "no-search-button"
        if not dev.tap(*_center(btn)):
            return [], "tap-search-failed"
        deadline = time.time() + 60
        last = ""
        saw_list_page = False     # 曾确认「列表页打开了」（有起降时刻对）
        while time.time() < deadline:
            time.sleep(4)
            xml = dev.dump() or ""
            if self._is_login_page(xml):
                self.login_suspect = True
                self.path_note("app-login", "login",
                               detail="搜索后要求登录（登录态失效）")
                return [], "login"
            if detect_state(xml) != "ok":
                last = xml
                continue
            saw_list_page = True
            # 列表页要等价格渲染完：有航班号但没价格时再等一轮
            rows = parse_flight_rows(xml)
            if rows and any(r.get("price") for r in rows):
                prices = self._to_prices(rows, target, from_city, to_city)
                if prices:      # _to_prices 可能因价格带不合格返回空
                    return prices, "ok"
            last = xml
        if last:
            rows = parse_flight_rows(last)
            if rows:
                prices = self._to_prices(rows, target, from_city, to_city)
                if prices:
                    return prices, "ok"
                # 解析出了航班行但一行都没有可用价格：这是「解析不到价」，
                # 不是「这天没票」——记 no-priced-rows 更诚实。
                return [], "no-priced-rows"
        # 列表页打开过但确实没航班（或全部无价）→ 这才是 empty；
        # 连列表页都没出现 → 超时，不是「没票」。
        return [], ("empty" if saw_list_page else "no-list-page")

    def _to_prices(self, rows: List[dict], date: str,
                   from_city: str, to_city: str) -> List[FlightPrice]:
        """该日期全部航班 → **一条** FlightPrice（extra 是航班数组）。

        与其余渠道同一契约：一渠道一日期一行，extra 携带当天全部航班；
        消费端（webui 列表池 / Alerter 推送池）都是按数组读的。
        """
        flights, best = [], None
        for r in rows:
            price = r.get("price")
            if price is None:
                continue
            item = {"price": float(price),
                    "code": r.get("flight_no", ""),
                    "name": r.get("flight_no", ""),
                    "depDate": date,
                    "depTime": r.get("depart_time", ""),
                    "arrTime": r.get("arrive_time", "")}
            flights.append(item)
            if best is None or price < best["price"]:
                best = item
        if not flights:
            return []
        return [FlightPrice(
            platform=self.name, from_city=from_city, to_city=to_city,
            depart_date=date, price=best["price"],
            flight_no=best["code"], depart_time=best["depTime"],
            arrive_time=best["arrTime"],
            extra=json.dumps(flights, ensure_ascii=False),
            prov=f"app:{_APP_PKG}:list",
        )]
