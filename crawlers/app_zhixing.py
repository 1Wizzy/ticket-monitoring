# -*- coding: utf-8 -*-
"""智行 App 渠道（cn.suanya.zhixing）。

数据面事实（2026-10-29 实测）：
  * 64 位包 `arm64-v8a`，装到 MuMu Android 15 实例；
  * **未登录**时列表里单班票价被遮成 `¥3??`，但顶部日历条的
    每日最低价是真实数字（¥450 / ¥430 / ¥377…）；
  * 因此未登录只产出**日历族**结果：每天一条最低价、无航班号。
    这属于 `core.models.CAL_PATH_FAMILY` 语义，不能冒充明细。

登录后是否放开单班价，P3 接内嵌登录画面时再验；本文件不预设。
"""
from __future__ import annotations

from typing import List

from core.models import FlightPrice

from .app_base import AppCrawler
from .app_zhixing_parse import (detect_state, parse_calendar,
                                parse_flight_rows)

import time

# 日历条上的日期是 MM-DD；换算年需要上下文（跨年时 12-31 → 01-01）
_APP_PKG = "cn.suanya.zhixing"
_APP_LABEL = "智行旅行"


def _md_to_ymd(md: str, anchor: str) -> str:
    """把 MM-DD 补成 YYYY-MM-DD，以查询日期为锚处理跨年。"""
    if not md or len(md) != 5:
        return ""
    try:
        mm, dd = int(md[:2]), int(md[3:])
    except ValueError:
        return ""
    try:
        ay, am = int(anchor[:4]), int(anchor[5:7])
    except (ValueError, IndexError):
        return ""
    year = ay
    # 日历条通常跨月，12 月查询出现 01-02 即翌年
    if am == 12 and mm == 1:
        year = ay + 1
    elif am == 1 and mm == 12:
        year = ay - 1
    return f"{year:04d}-{mm:02d}-{dd:02d}"


class AppZhixingCrawler(AppCrawler):
    name = "app_zhixing"
    package = _APP_PKG
    label = _APP_LABEL
    # 测试用：跳过 UI 导航（真实采集必须为 False，否则会把上次的航线当结果）
    _skip_route = False

    # ---------- 对外 ----------
    def login_url(self) -> str:
        """App 渠道没有可粘贴的 URL；返回人类可读的登录入口描述。"""
        return f"android://{_APP_PKG}（App 内：我的 → 登录）"

    def fetch(self, from_city: str, to_city: str, dates: List[str],
              ret_date: str = "") -> List[FlightPrice]:
        rt = self.runtime()
        ok, why = rt.ensure_ready()
        if not ok:
            # 设备/运行时不可用：明确入账，绝不伪装成"没航班"
            self.path_note("android-runtime", "unsupported",
                           detail=f"runtime not ready: {why}")
            self.logger.warning("[%s] Android 运行时未就绪：%s",
                                self.name, why)
            return []

        dev = rt.device()
        if dev is None:
            self.path_note("android-device", "unsupported",
                           detail="device unavailable")
            return []

        # 异常逃逸不在这里写：调用方统一走 `AppBaseCrawler.safe_fetch()`，
        # 它已 try/except 并记 `app-error`（含本段抛出的一切）。此处再包一层
        # 只是重复防御，且会抢在 safe_fetch 的去重判断之前多记一条。
        with rt.device_lock():
            return self._fetch_locked(dev, from_city, to_city, dates)

    # ---------- 内部 ----------
    def _fetch_locked(self, dev, from_city: str, to_city: str,
                      dates: List[str]) -> List[FlightPrice]:
        import time

        started = time.time()
        # 拦路物必须最先处理：广告弹层会盖住首页，后面所有点击都落在
        # 广告上（实测：不先关广告，设航线会点到弹层里的空白）。
        # 验证码/人脸在这里直接返回，不做任何后续操作。
        warm = dev.dump()
        if warm:
            warm, block = self._clear_blockers(dev, warm)
            if block.blocking:
                self.path_note("app-blocker", block.kind,
                               detail=block.detail)
                self.logger.warning("[%s] 页面被拦截：%s（%s）",
                                    self.name, block.kind, block.detail)
                return []

        # 必须先按配置设航线：App 会停在上次的查询页，不设就会把
        # 「上一次的航线」当成这次的结果（实机踩过：配置 SHA→BJS，
        # App 还停在上海→北京，数据看起来"正常"但根本不是这一条）。
        # 但航线没变时跳过：实测 set_flight_route 占整轮 86%（34.4s/40s），
        # 其中大半是重启 App + 重设同一对城市，纯属白跑。
        same = self._route_unchanged(dev, from_city, to_city)
        if same:
            self.logger.info("[%s] 航线未变（%s→%s），跳过重设",
                             self.name, from_city, to_city)
        else:
            ok, detail = self._set_route(dev, from_city, to_city, dates)
            if not ok:
                self.path_note("app-route", "blocked", detail=detail,
                               ms=int((time.time() - started) * 1000))
                self.logger.warning("[%s] 设置航线失败：%s", self.name, detail)
                return []
            self._remember_route(from_city, to_city)

        # 长窗口日历的入口在**首页的日期行**，一旦点了「机票查询」进了
        # 列表页就没有日历图标了（实测：列表页只有 8 天日期条、无日历
        # 入口）。所以必须在导航完成后、任何进一步跳转之前读弹层。
        long_xml = self._try_long_calendar(dev)
        if long_xml:
            out = self._emit_from_dialog_by_xml(
                long_xml, from_city, to_city, dates,
                int((time.time() - started) * 1000))
            if out:
                # 收尾把 App 留在首页，下一轮才能走"航线未变"快路径
                self._park_at_home(dev)
                return out

        xml = dev.dump()
        if not xml:
            self.path_note("app-dump", "timeout",
                           detail="empty ui hierarchy",
                           ms=int((time.time() - started) * 1000))
            return []

        # 拦路物优先于数据解析：广告可自动关一次再读；验证码/人脸只能人工，
        # 记独立状态并留下现场，绝不当成"没票"或"没数据"。
        xml, block = self._clear_blockers(dev, xml)
        if block.blocking:
            self.path_note("app-blocker", block.kind, detail=block.detail)
            self.logger.warning("[%s] 页面被拦截：%s（%s）",
                                self.name, block.kind, block.detail)
            return []

        state = detect_state(xml)
        if state == "login":
            self.login_suspect = True
            self.path_note("app-list", "login",
                           detail="需要登录（未登录只能拿日历价）")
            return []
        if state == "empty":
            self.path_note("app-list", "empty", detail="页面明确无航班")
            return []
        # calendar：页面已经是「选择日期」长窗口日历，直接按它解析，
        # 不必再打开弹层（它本来就在）。
        if state == "calendar":
            return self._emit_calendar(xml, from_city, to_city, dates,
                                       int((time.time() - started) * 1000))
        if state == "unknown":
            # 有页面但既不认登录也不认空态：这是"没看懂"，不是"没航班"。
            # 记 timeout 而不是 blocked：`blocked` 在 pacing 里归 captcha
            # （300s 阶梯熔断），而"页面结构不认识"重试也不会变好，
            # 更不该让整个 App 渠道停摆——这是解析器要修，不是要躲。
            self.path_note("app-list", "timeout",
                           detail="无法识别的页面状态（可能是 App 改版）")
            return []

        # 主动打开「选择日期」弹层再读一次：弹层是跨月长窗口日历，
        # 首页条只有 8 天。打开失败不影响结果，回落到首页条解析。
        return self._emit_calendar(xml, from_city, to_city, dates,
                                   int((time.time() - started) * 1000))

    def _park_at_home(self, dev):
        """把 App 留在首页，方便下一轮跳过重设航线。

        实测：读完长窗口后 App 停在「选择日期」透明 Activity，下一轮
        首页判据为假 → 又要重启 App + 重设城市，白花 35s（占整轮 86%）。
        收尾时退回到首页，下一轮就能走"航线未变"的快路径。
        """
        try:
            for _ in range(3):
                xml = dev.dump()
                if "机票查询" in xml and "Search_Calendar_Dialog" not in xml:
                    return True
                dev.keyevent("KEYCODE_BACK")
                time.sleep(1.5)
        except Exception:
            pass
        return False

    def _emit_from_dialog_by_xml(self, xml: str, from_city: str, to_city: str,
                                 dates: List[str], ms: int):
        """从长窗口日历页 XML 产出；不是日历页时返回 []。"""
        from .app_zhixing_calendar import (has_calendar_dialog,
                                           parse_date_dialog)
        if not has_calendar_dialog(xml):
            return []
        rows = parse_date_dialog(xml)
        if not rows:
            self.path_note("日历", "parse_error",
                           detail="日历弹层未解析出格子", ms=ms)
            return []
        return self._emit_from_dialog(rows, from_city, to_city, dates, ms)

    def _try_long_calendar(self, dev) -> str:
        """打开日期弹层并取页面；失败返回空串（不抛）。"""
        try:
            from .app_zhixing_nav import close_date_dialog, open_date_dialog
            ok, detail = open_date_dialog(dev, log=self.logger)
            if not ok:
                self.logger.info("[%s] 长窗口日历未打开：%s",
                                 self.name, detail)
                return ""
            xml = dev.dump()
            close_date_dialog(dev)   # 关掉，别把状态留给下一轮
            time.sleep(1)
            return xml
        except Exception as e:
            self.logger.info("[%s] 长窗口日历异常：%s", self.name,
                             type(e).__name__)
            return ""

    def _emit_from_dialog(self, dialog_rows, from_city: str, to_city: str,
                          dates: List[str], ms: int) -> List[FlightPrice]:
        """从「选择日期」弹层产出日历价（长窗口）。

        与首页日历条同属**日历族**：每天一条最低价、无航班号。
        差别只在覆盖长度（弹层跨月，首页约 8 天）。
        """
        wanted = set(dates or [])
        out: List[FlightPrice] = []
        for row in dialog_rows:
            ymd = row["date"]
            if wanted and ymd not in wanted:
                continue
            price = row.get("price")
            if price is None:
                continue
            out.append(FlightPrice(
                platform=self.name,
                from_city=from_city, to_city=to_city,
                depart_date=ymd, price=float(price),
                extra=('{"calendar": true, "masked": false, '
                       '"source": "app-calendar-dialog"}'),
                prov=f"app:{_APP_PKG}:calendar-dialog",
            ))

        priced = [r for r in dialog_rows if r.get("price")]
        covered = {r["date"] for r in dialog_rows}
        outside = len([d for d in wanted if d and d not in covered])
        detail = f"长窗口日历 {len(out)} 天 / 已渲染 {len(covered)} 格" \
                 f"（有价 {len(priced)}）"
        if outside:
            detail += f" · {outside} 个目标日期未渲染"
        self.path_note("日历", "calendar", rows=len(out),
                       date=(dates[0] if dates else ""), detail=detail, ms=ms,
                       url=f"android://{_APP_PKG}?view=calendar")
        if not out:
            self.logger.info("[%s] 长窗口日历命中 0 条（%s）",
                             self.name, detail)
        return out

    def _emit_calendar(self, xml: str, from_city: str, to_city: str,
                       dates: List[str], ms: int) -> List[FlightPrice]:
        # 页面可能是「选择日期」弹层（长窗口，跨两个月）或首页日历条
        # （短期，7–8 天）。前者优先——同样一次读页面，覆盖长得多。
        from .app_zhixing_calendar import (has_calendar_dialog, month_range,
                                           parse_date_dialog)
        dialog_rows = []
        if has_calendar_dialog(xml):
            dialog_rows = parse_date_dialog(xml)

        if dialog_rows:
            return self._emit_from_dialog(dialog_rows, from_city, to_city,
                                          dates, ms)

        rows = parse_calendar(xml)
        if not rows:
            self.path_note("日历", "parse_error",
                           detail="日历条未解析出价格", ms=ms)
            return []

        wanted = set(dates or [])
        anchor = (dates[0] if dates else "")
        out: List[FlightPrice] = []
        masked_days = 0
        for row in rows:
            ymd = _md_to_ymd(row["date"], anchor)
            if not ymd:
                continue
            if wanted and ymd not in wanted:
                continue
            price = row.get("price")
            if price is None:
                masked_days += 1
                continue
            out.append(FlightPrice(
                platform=self.name,
                from_city=from_city, to_city=to_city,
                depart_date=ymd, price=float(price),
                extra=('{"calendar": true, "masked": false, '
                       '"source": "app-calendar"}'),
                prov=f"app:{_APP_PKG}:calendar",
            ))

        detail = f"日历 {len(out)} 天"
        if masked_days:
            detail += f" · {masked_days} 天无价"
        # 「目标日期在日历窗口外」必须单独说明：智行日历条只覆盖首屏
        # 约 7–8 天，配置里的远期日期永远命中 0 条。若只记「日历 0 天」，
        # 读者分不清是"这几天没价"还是"这条航线压根不在窗口内"——
        # 前者是数据、后者是能力边界，处置完全不同（改配置 vs 改实现）。
        outside = 0
        if wanted and rows:
            inwin = {_md_to_ymd(r["date"], anchor) for r in rows}
            inwin.discard("")
            outside = len([d for d in wanted if d not in inwin])
        if outside:
            detail += f" · {outside} 个目标日期在日历窗口外"
            self.logger.info(
                "[%s] %d/%d 个目标日期不在智行日历窗口内（该窗口仅约 %d 天）",
                self.name, outside, len(wanted), len(rows))
        status = "calendar" if out else ("empty" if outside == 0 else "calendar")
        self.path_note("日历", status, rows=len(out),
                       date=anchor, detail=detail, ms=ms,
                       url=f"android://{_APP_PKG}")
        if not out:
            # 有日历但目标日期都没价：这是"没拿到价"，不是"无票"
            self.logger.info("[%s] 日历条命中 0 条（%s）", self.name, detail)
        return out

    def _clear_blockers(self, dev, xml: str):
        """处理页面拦路物。返回 (xml, Block)。

        策略分级（与 PC/H5 渠道的处置规范一致）：
          * **广告**：只是遮挡 → 自动点关闭，最多两次；关不掉就上报 `ad`，
            让用户看到"被广告挡住"，而不是记成没数据。
          * **验证码**（含滑块/图形点选）：风控已触发，**不自动破解**，
            记 `captcha`；这对应 CAPTCHA_SOLUTION 的人工接管边界。
          * **人脸识别**：生物特征核验，自动化无法也不应伪造 → 记 `face`，
            明确告知用户必须本人操作，且不会被重试掩盖。
          * **登录**：记 `login`，由内嵌登录画面处理。
        """
        from .app_blockers import Block, detect_block

        for _ in range(2):
            block = detect_block(xml)
            if block.kind != "ad" or not block.close_xy:
                return xml, block
            # 广告可关：点掉后重取一次页面
            self.logger.info("[%s] 关闭广告弹层 %s", self.name, block.close_xy)
            dev.tap(*block.close_xy)
            time.sleep(1.5)
            xml = dev.dump()
            if not xml:
                return "", Block("", "no-ui")
        # 关两次还在：当作关不掉的广告上报，不再纠缠
        return xml, detect_block(xml)

    def _boot_wait(self) -> float:
        """冷启动后等待首页的时间；测试里会被压到 0。"""
        return float(self.config.get("app_boot_wait_s", 10) or 0)

    def _remember_route(self, from_city: str, to_city: str):
        """记下本轮设过的航线（仅供日志/诊断；判据始终以界面为准）。"""
        self._last_route = (from_city, to_city)

    def _route_unchanged(self, dev, from_city: str, to_city: str) -> bool:
        """App 当前是否已经停在目标航线上（按界面文案判，不靠记忆）。

        为什么按界面判而不是记在上次调用里：App 可能被用户手动改过、
        被系统回收后恢复成别的航线；只有屏幕上的字才是事实。
        """
        try:
            from .app_zhixing_nav import _city_row, nodes
            xml = dev.dump()
            if not xml or "机票查询" not in xml:
                return False
            ns = nodes(xml)
            cur_from = _city_row(ns, "from")
            cur_to = _city_row(ns, "to")
            if cur_from is None or cur_to is None:
                return False
            return (cur_from.text == self._city_name(from_city)
                    and cur_to.text == self._city_name(to_city))
        except Exception:
            return False   # 判不了就当"需要重设"，宁慢勿错

    def _set_route(self, dev, from_city: str, to_city: str,
                   dates: List[str]):
        """按配置设置 App 内的航线与日期。"""
        if self._skip_route:
            return True, "skipped"
        try:
            from .app_zhixing_nav import set_flight_route
        except Exception as e:
            return False, f"导航模块不可用：{type(e).__name__}"

        target_date = dates[0] if dates else ""
        # 停在首页：下一步要读「选择日期」长窗口日历，进列表页就没入口了
        return set_flight_route(dev, self._city_name(from_city),
                                self._city_name(to_city),
                                target_date, log=self.logger,
                                stop_before_search=True)

    @staticmethod
    def _city_name(code: str) -> str:
        """三字码 → App 里显示的中文城市名。

        配置里常用机场码（PEK/NRT），而 App 的城市是城市级（北京/东京）；
        先过项目的 airport_city_code 归一，再取中文名。查不到就原样
        传下去——宁可让它显式失败，也不要瞎猜一个城市。
        """
        try:
            from core.citydata import CODE_NAME
        except Exception:
            return code
        c = (code or "").upper()
        try:
            from core.intl import airport_city_code
            c = (airport_city_code(c) or c).upper()
        except Exception:
            pass
        nm = CODE_NAME.get(c) or ""
        for suf in ("市", "自治州", "地区"):
            nm = nm.replace(suf, "")
        return nm or code
