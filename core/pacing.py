# -*- coding: utf-8 -*-
"""采集节流 / 熔断内核（1–5 区）。

纯逻辑、零 I/O、可注入时钟：所有决策都能单测，不在函数里 sleep，
也不碰浏览器与网络。三条硬约束：

1. **不在锁内睡眠**：`wait_hint()` 只返回"还要等几秒"，睡眠由调用方在
   渠道锁与全局闸门之外执行——否则慢渠道会把别的渠道一起拖住。
2. **熔断是"停手"不是"降速"**：冷却期内该渠道一个请求都不发，而不是
   拉长间隔继续试探。
3. **SessionBusyError 不算服务端拒绝**：自家锁没抢到只让位本轮，不累积
   strikes、不冷却——把它当限流退避，等于用自家 bug 惩罚对方站点。

配置全部来自 `schedule` 与 `crawler.rate / crawler.breaker`，缺省值保持
旧行为（关闭或宽松），保存即热生效。
"""
import math
import re
import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

PLATFORMS = ("ctrip", "fliggy", "qunar", "tongcheng", "tuniu")

_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")

# 失败归类关键词：按"谁的问题"分桶，顺序即优先级。
# 403/429 必须按「独立数字」匹配——路径详情里带价格（如 ￥1403）时，
# 裸子串会把正常出价当成限流信号，熔断一个健康的渠道。
_IP_CODE_RE = re.compile(r"\b(?:403|429)\b")
_IP_MARKS = ("风控", "限流", "179991", "ip封", "ip 封")
_CAPTCHA_MARKS = ("验证码", "滑块", "人机验证", "访问验证", "安全验证",
                  "challenge", "captcha")
_LOGIN_MARKS = ("请登录", "未登录", "需登录", "登录失效", "会话失效")
# 软拒：边缘节点/会话层没有正面拒绝，但也没给业务响应。它不是"真无票"，
# 也不该按 IP 限流熔断——正确动作是下一轮先养会话再查。
_SOFT_MARKS = ("软拒", "444", "202 空", "202空")

DEFAULTS = {
    "scan_quiet_start": "",
    "scan_quiet_end": "",
    "fast_ttl_min": 15,
    "slow_ttl_min": 120,
    "near_ratio": 0.15,
    "stagger_seconds": 0,
    "breaker_enabled": True,
    "breaker_base_s": 300,
    "breaker_cap_s": 3600,
    "login_cooldown_s": 900,
    "min_gap_s": 0,
    "per_minute": 0,
    "warmup_enabled": True,
    "warmup_platforms": ("qunar", "fliggy", "tuniu"),
    "cadence": {},
}


def _num(v, default, lo, hi, cast=int):
    try:
        n = cast(v)
    except (TypeError, ValueError):
        return default
    if n != n:                      # NaN
        return default
    return max(lo, min(hi, n))


def parse_pacing(cfg):
    """把 config.yaml 的 schedule / crawler.rate / crawler.breaker 归一成
    扁平字典；非法值回默认并钳位（宁缺勿错：坏配置不该让监控整轮停摆）。"""
    cfg = cfg or {}
    sc = cfg.get("schedule") or {}
    crw = cfg.get("crawler") or {}
    rate = crw.get("rate") or {}
    brk = crw.get("breaker") or {}

    out = dict(DEFAULTS)
    out["scan_quiet_start"] = (
        str(sc.get("scan_quiet_start") or "").strip()
        if parse_hhmm(sc.get("scan_quiet_start")) is not None else "")
    out["scan_quiet_end"] = (
        str(sc.get("scan_quiet_end") or "").strip()
        if parse_hhmm(sc.get("scan_quiet_end")) is not None else "")
    out["fast_ttl_min"] = _num(sc.get("fast_ttl_min"),
                               DEFAULTS["fast_ttl_min"], 5, 1440)
    out["slow_ttl_min"] = _num(sc.get("slow_ttl_min"),
                               DEFAULTS["slow_ttl_min"], 5, 1440)
    if out["slow_ttl_min"] < out["fast_ttl_min"]:
        out["slow_ttl_min"] = out["fast_ttl_min"]
    out["near_ratio"] = _num(sc.get("near_ratio"),
                             DEFAULTS["near_ratio"], 0.0, 1.0, float)
    out["stagger_seconds"] = _num(sc.get("stagger_seconds"),
                                  DEFAULTS["stagger_seconds"], 0, 120)
    out["breaker_enabled"] = bool(
        brk.get("enabled", DEFAULTS["breaker_enabled"]))
    out["breaker_base_s"] = _num(brk.get("base_s"),
                                 DEFAULTS["breaker_base_s"], 30, 3600)
    out["breaker_cap_s"] = _num(brk.get("cap_s"),
                                DEFAULTS["breaker_cap_s"], 120, 86400)
    if out["breaker_cap_s"] < out["breaker_base_s"]:
        out["breaker_cap_s"] = out["breaker_base_s"]
    out["login_cooldown_s"] = _num(brk.get("login_cooldown_s"),
                                   DEFAULTS["login_cooldown_s"], 0, 86400)
    out["min_gap_s"] = _num(rate.get("min_gap_s"),
                            DEFAULTS["min_gap_s"], 0, 300, float)
    if out["min_gap_s"] == int(out["min_gap_s"]):
        out["min_gap_s"] = int(out["min_gap_s"])
    out["per_minute"] = _num(rate.get("per_minute"),
                             DEFAULTS["per_minute"], 0, 600)
    # 预热编排（L1/L3）：只对声明了 warmup_url 的渠道生效；默认三家
    # （qunar/fliggy/tuniu）——ctrip 与同程在各自 fetch 内部已自带预热
    warm = crw.get("warmup") or {}
    out["warmup_enabled"] = bool(
        warm.get("enabled", DEFAULTS["warmup_enabled"]))
    _pl = warm.get("platforms")
    if _pl is None:
        _pl = list(DEFAULTS["warmup_platforms"])
    if isinstance(_pl, str):
        _pl = [x.strip() for x in _pl.split(",")]
    out["warmup_platforms"] = tuple(
        p for p in (_pl or []) if p in PLATFORMS)
    # 每渠道独立节奏（3 区扩展）：最小采集间隔，0=跟随全局 TTL
    cad = crw.get("cadence") or {}
    _cad = {}
    if isinstance(cad, dict):
        for _p in PLATFORMS:
            try:
                _v = int(cad.get(_p) or 0)
            except (TypeError, ValueError):
                _v = 0
            _cad[_p] = max(0, min(10080, _v))
    out["cadence"] = _cad
    return out


# 只把「页面本身 / 业务接口」的拒绝当渠道信号。广告、统计、埋点请求
# 的 403/429/503 很常见，拿它们熔断渠道会让一次正常采集被第三方脚本
# 误伤——这不是"保守"，是把噪声当因果。
_IGNORE_NET_HOSTS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "googlesyndication.com", "facebook.", "hotjar.", "sentry.",
    "umeng.", "cnzz.", "hm.baidu.com", "newrelic.", "bugly.",
    "tingyun.", "oneapm.",
)


def should_watch_response(resource_type, url):
    """响应监听只接受文档与 XHR/fetch，并排除已知统计/广告域。"""
    rt = str(resource_type or "").lower()
    if rt not in ("document", "xhr", "fetch"):
        return False
    host = (urlparse(str(url or "")).hostname or "").lower()
    if not host:
        return False
    return not any(k in host for k in _IGNORE_NET_HOSTS)


def parse_local_ts(ts):
    """本地时间串 → epoch 秒；失败/空返回 0。用于重启时读库续上 TTL。"""
    s = str(ts or "").strip().replace("T", " ")
    if not s:
        return 0.0
    for n, fmt in ((19, "%Y-%m-%d %H:%M:%S"),
                   (16, "%Y-%m-%d %H:%M"),
                   (10, "%Y-%m-%d")):
        try:
            return time.mktime(time.strptime(s[:n], fmt))
        except (ValueError, OverflowError):
            continue
    return 0.0


def parse_retry_after(raw, now=None):
    """`Retry-After` → 需要等待的秒数（0=没要求）。

    两种合法形态：秒数（"120"）与 HTTP-date。服务端说等多久就等多久，
    因为这是唯一"对方明确告诉你"的信号——比我们自己拍的 2^n 阶梯可信。
    向上取整（宁晚不早），并封顶一天，防异常值把渠道挂死。
    """
    s = str(raw or "").strip()
    if not s:
        return 0
    try:
        sec = float(s)
    except (TypeError, ValueError):
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            return 0
        if dt is None:
            return 0
        try:
            sec = dt.timestamp() - (time.time() if now is None else float(now))
        except (OverflowError, OSError, ValueError):
            return 0
    if sec <= 0:
        return 0
    return int(min(86400, math.ceil(sec)))


def parse_hhmm(s):
    """'01:00' -> 60；空/非法/越界一律 None（宁缺勿错：错的时间窗会
    把全天静默或全天不静默，两种都比"不配置"更糟）。"""
    m = _HHMM.match(str(s or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def in_window(now_min, start_min, end_min):
    """左闭右开；start==end 视为关闭（不把"都填 01:00"误读成全天）。"""
    if start_min is None or end_min is None or start_min == end_min:
        return False
    if start_min < end_min:
        return start_min <= now_min < end_min
    return now_min >= start_min or now_min < end_min


def tier_of(age_min, near, fast_ttl, slow_ttl):
    """接近心理价位→快层；远离→慢层；未到 TTL→skip。"""
    ttl = fast_ttl if near else slow_ttl
    if age_min >= ttl:
        return "fast" if near else "slow"
    return "skip"


def stagger_offset(plat, base_seconds):
    """渠道起跑偏移：按固定顺序均分，保证 5 家不挤在同一秒。
    固定（非随机）是刻意的——同一渠道每轮偏移一致，便于复现与对账；
    周期本身的随机性由调度层的 jitter 负责。"""
    base = float(base_seconds or 0)
    if base <= 0:
        return 0.0
    order = list(PLATFORMS)
    step = base / len(order)
    if plat in order:
        return round(order.index(plat) * step, 1)
    import zlib
    return round((zlib.crc32(str(plat).encode("utf-8")) % 1000) / 1000 * base, 1)


def classify_failure(statuses, note=""):
    """把一次采集的路径状态归成 ip/captcha/login/""。

    空串=不是"对方拒绝"（超时、解析失败、真无票都归这里）——这类不该
    触发冷却，重试也不会变好，但没有理由停手整个渠道。
    """
    low = str(note or "").lower()
    if _IP_CODE_RE.search(low) or any(k in low for k in _IP_MARKS):
        return "ip"
    sts = {str(s or "") for s in (statuses or [])}
    if "soft_reject" in sts or any(k in str(note or "") for k in _SOFT_MARKS):
        return "soft"
    if "blocked" in sts or any(k.lower() in low for k in _CAPTCHA_MARKS):
        return "captcha"
    if "login" in sts or any(k in str(note or "") for k in _LOGIN_MARKS):
        return "login"
    return ""


class ChannelBreaker:
    """渠道级熔断：指数退避 + 封顶，成功即复位。

    kind:
      ip / captcha → 阶梯退避（base·2^(n-1)，封顶 cap），累积 strikes
      login        → 固定冷却（默认 15min），不累积 strikes，打 needs_login
      busy         → 0 秒（自家锁冲突，本轮让位即可）
    """

    def __init__(self, base_s=300, cap_s=3600, enabled=True,
                 login_cooldown_s=900):
        self._lock = threading.Lock()
        self._st = {}
        self.configure(base_s, cap_s, enabled, login_cooldown_s)

    def configure(self, base_s=None, cap_s=None, enabled=None,
                  login_cooldown_s=None):
        with self._lock:
            if base_s is not None:
                self.base_s = max(30, int(base_s))
            if cap_s is not None:
                self.cap_s = max(120, int(cap_s))
            if self.cap_s < self.base_s:
                self.cap_s = self.base_s
            if enabled is not None:
                self.enabled = bool(enabled)
            if login_cooldown_s is not None:
                self.login_cooldown_s = max(0, int(login_cooldown_s))

    def _rec(self, plat):
        return self._st.setdefault(str(plat), {
            "strikes": 0, "until": 0.0, "reason": "", "needs_login": False,
            "needs_warmup": False, "ok_streak": 0})

    def allow(self, plat, now=None):
        now = time.time() if now is None else now
        with self._lock:
            if not self.enabled:
                return True, 0
            st = self._rec(plat)
            if st["until"] > now:
                return False, int(round(st["until"] - now))
            return True, 0

    def trip(self, plat, kind, now=None, retry_after=0):
        now = time.time() if now is None else now
        kind = str(kind or "ip")
        if kind == "busy":
            return 0
        with self._lock:
            st = self._rec(plat)
            if kind == "soft":
                # 软拒只要求下一轮先预热，不冷却、不计 strikes：
                # 用整家熔断去惩罚"会话没养好"会错杀已能出数的渠道。
                st["needs_warmup"] = True
                st["reason"] = "soft"
                return 0
            if kind == "login":
                st["needs_login"] = True
                st["reason"] = "login"
                st["until"] = max(st["until"], now + self.login_cooldown_s)
                return self.login_cooldown_s
            if not self.enabled:
                return 0
            st["strikes"] = min(16, st["strikes"] + 1)
            cd = min(self.cap_s, self.base_s * (2 ** (st["strikes"] - 1)))
            try:
                ra = int(retry_after or 0)
            except (TypeError, ValueError):
                ra = 0
            if ra > 0:
                # 服务端指令优先：比阶梯长就照它（哪怕超过自设封顶），
                # 比阶梯短则取长的——提前回来只会再撞一次
                cd = min(86400, max(cd, ra))
            st["until"] = now + cd
            st["reason"] = kind
            return cd

    def note_ok(self, plat, need=2):
        """连续成功 need 次才清 strikes；软拒标记单独清零。

        - strikes/冷却：连续成功才复位，避免抖动渠道反复横跳；
        - needs_warmup：成功出数本身就是"会话已养熟"的最强证据，
          立即清（否则面板会一直提示"软拒待预热"）。
        """
        with self._lock:
            st = self._rec(plat)
            st["ok_streak"] = int(st.get("ok_streak") or 0) + 1
            st["needs_warmup"] = False
            if st.get("reason") == "soft":
                st["reason"] = ""
            if st["ok_streak"] >= max(1, int(need or 2)):
                st["strikes"] = 0
                st["until"] = 0.0
                st["reason"] = ""
                st["needs_login"] = False
            return st["ok_streak"]

    def note_warmup_ok(self, plat):
        """预热真的拿到风控 cookie 才清软拒标记。

        只访问首页不算数——没下发风控 cookie 的预热，深链仍会被 444。
        """
        with self._lock:
            st = self._rec(plat)
            st["needs_warmup"] = False
            if st["reason"] == "soft":
                st["reason"] = ""

    def reset(self, plat):
        with self._lock:
            self._st[str(plat)] = {
                "strikes": 0, "until": 0.0, "reason": "",
                "needs_login": False, "needs_warmup": False,
                "ok_streak": 0}

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        out = {}
        with self._lock:
            for p in PLATFORMS:
                st = self._st.get(p) or {}
                until = float(st.get("until") or 0)
                out[p] = {
                    "state": "cooldown" if until > now else "ok",
                    "remain_s": max(0, int(round(until - now))),
                    "strikes": int(st.get("strikes") or 0),
                    "reason": str(st.get("reason") or ""),
                    "needs_login": bool(st.get("needs_login")),
                    "needs_warmup": bool(st.get("needs_warmup")),
                    "ok_streak": int(st.get("ok_streak") or 0),
                }
            for p in self._st:
                if p in out:
                    continue
                st = self._st[p]
                until = float(st.get("until") or 0)
                out[p] = {
                    "state": "cooldown" if until > now else "ok",
                    "remain_s": max(0, int(round(until - now))),
                    "strikes": int(st.get("strikes") or 0),
                    "reason": str(st.get("reason") or ""),
                    "needs_login": bool(st.get("needs_login")),
                    "needs_warmup": bool(st.get("needs_warmup")),
                    "ok_streak": int(st.get("ok_streak") or 0),
                }
        return out


class TokenBucket:
    """渠道级请求预算：最小间隔 + 每分钟上限。

    `wait_hint` 只算不睡。分钟窗口是滑动的（deque），因为站点的限流
    窗口几乎都不是整分钟对齐的，固定窗口会放过 2 倍突发。
    """

    def __init__(self, min_gap_s=0, per_minute=0):
        self._lock = threading.Lock()
        self._last = {}
        self._win = {}
        self.configure(min_gap_s, per_minute)

    def configure(self, min_gap_s=None, per_minute=None):
        with self._lock:
            if min_gap_s is not None:
                self.min_gap_s = max(0.0, float(min_gap_s))
            if per_minute is not None:
                self.per_minute = max(0, int(per_minute))

    def wait_hint(self, plat, now=None):
        now = time.time() if now is None else now
        with self._lock:
            wait = 0.0
            last = self._last.get(str(plat))
            if self.min_gap_s > 0 and last is not None:
                wait = max(wait, last + self.min_gap_s - now)
            if self.per_minute > 0:
                dq = self._win.get(str(plat)) or deque()
                while dq and now - dq[0] >= 60:
                    dq.popleft()
                if len(dq) >= self.per_minute:
                    wait = max(wait, dq[0] + 60 - now)
            return max(0.0, round(wait, 1))

    def record(self, plat, now=None):
        now = time.time() if now is None else now
        with self._lock:
            p = str(plat)
            self._last[p] = now
            dq = self._win.setdefault(p, deque())
            dq.append(now)
            while dq and now - dq[0] >= 60:
                dq.popleft()

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            out = {}
            for p in set(list(self._last) + list(PLATFORMS)):
                dq = list(self._win.get(p) or ())
                recent = sum(1 for t in dq if now - t < 60)
                last = self._last.get(p)
                out[p] = {
                    "last_ago_s": (max(0, int(round(now - last)))
                                   if last is not None else -1),
                    "per_min_used": recent,
                }
            return out


def _age_min(ts, now):
    try:
        t = time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError, TypeError):
        return None
    return max(0.0, (now - t) / 60.0)


def shadow_path_preference(paths, min_samples=5, low=0.3):
    """影子模式：只算"若启用路径偏好，会把哪条路排前"，不改变采集。

    返回 {"prefer": [(path, rate, attempts)...], "avoid": [...]}。
    - 仅比较同平台内各入口（PC/H5/API/日历）的路径成功率；
    - 样本 < min_samples 不参与（防首轮波动误判）；
    - 日历路径不参与（它本来就不是明细路径）。
    """
    ranked = []
    for name, v in (paths or {}).items():
        v = v or {}
        n = int(v.get("attempts") or 0)
        if n < min_samples or str(name).startswith("日历"):
            continue
        ranked.append((str(name), float(v.get("rate") or 0.0), n))
    ranked.sort(key=lambda x: (-x[1], -x[2]))
    avoid = [x for x in ranked if x[1] < low]
    return {"prefer": ranked[:2], "avoid": avoid}


def effect_verdict(effect):
    """把干预前后对比翻译成一句可执行结论（供建议引擎/面板复用）。

    effect: storage.intervention_effect() 的返回。
    返回 (state, hint, severity)；无干预或样本不足时如实说"还不能判断"。
    """
    e = effect or {}
    if not e.get("has"):
        return "none", "", "ok"
    b, a = e.get("before"), e.get("after")
    if a is None:
        return "pending", ("干预后还没有新样本（%s）：等下一轮采集再判断"
                           % (e.get("action") or "干预")), "wait"
    if b is None:
        return "unknown_before", ("干预后成功率 %.0f%%（%d 次）；干预前窗口"
                                  "无样本，无法对比" % (a * 100,
                                                     int(e.get("after_n") or 0))), "ok"
    if a >= 0.7 and a > b:
        return "improved", ("干预有效：%.0f%% → %.0f%%"
                            % (b * 100, a * 100)), "ok"
    if a >= 0.7:
        return "recovered", ("干预后已回到 %.0f%%（干预前 %.0f%%）"
                             % (a * 100, b * 100)), "ok"
    if a > b:
        return "partial", ("有改善但仍偏低：%.0f%% → %.0f%%，建议再自检一次"
                           % (b * 100, a * 100)), "wait"
    return "no_effect", ("干预未见改善：%.0f%% → %.0f%%，换路径/重登后再试"
                         % (b * 100, a * 100)), "act"


def advise(plat, ch=None, paths=None, now=None, node=None):
    """把一个渠道的现状翻译成「用户该做什么」。

    返回 {"state","action","hint","eta_s","severity"}：
      severity=ok      不用管
      severity=wait    等就行（冷却到期会自动恢复）
      severity=auto    已自动处理/可自动处理（路径劣化）
      severity=act     必须人工（重登 / 换出口 / 解封）

    `node` 是 path_stats 的平台级节点（query_* / last_ok_at）。有它时
    用查询口径判健康；没有再退回路径口径（兼容旧调用）。
    """
    ch = ch or {}
    paths = paths or {}
    node = node or {}
    now = time.time() if now is None else now
    if ch.get("needs_warmup") and ch.get("state") != "cooldown":
        return {"state": "soft_reject", "action": "probe", "severity": "auto",
                "hint": "上一轮被软拒（202 空体/444 关连接，不是真无票）："
                        "本轮已安排先预热再查；若连续出现，点「自检」看路径",
                "eta_s": 0}
    if ch.get("needs_login"):
        return {"state": "login", "action": "relogin", "severity": "act",
                "hint": "登录态失效：点「去验证」用采集同一会话过验证，"
                        "关窗即落库，别用死 cookie 继续打",
                "eta_s": 0}
    if ch.get("state") == "cooldown":
        left = int(ch.get("remain_s") or 0)
        if (ch.get("reason") or "") in ("ip", "captcha"):
            hint = ("冷却中（%s）：点「去验证」在采集同一会话里过验证，"
                    "或等倒计时自动恢复" % ch.get("reason"))
        else:
            hint = "冷却中：到点自动恢复，不建议手动解除（会再撞一次）"
        return {"state": "cooldown", "action": "wait", "severity": "wait",
                "hint": hint, "eta_s": left}
    attempts = sum(int((v or {}).get("attempts") or 0)
                   for v in paths.values())
    q_attempts = int(node.get("query_attempts") or 0) or sum(
        int((v or {}).get("query_attempts") or 0) for v in paths.values())
    q_ok = int(node.get("query_ok") or 0) or sum(
        int((v or {}).get("query_ok") or 0) for v in paths.values())
    if not attempts and not q_attempts:
        return {"state": "unknown", "action": "scan", "severity": "ok",
                "hint": "近 24h 无样本：先跑一轮再判断", "eta_s": 0}
    bad = None
    for name, v in paths.items():
        v = v or {}
        n = int(v.get("attempts") or 0)
        if n < 5:
            continue
        # 优先用"排除售罄空结果"的比率：真无票不等于路径坏了。
        # None = 有效样本为 0，不参与判定（既非 0% 也非 100%）。
        _raw = v.get("rate_excl_empty", v.get("rate"))
        if _raw is None:
            continue
        # 有效样本护栏：分母里至少 3 次"非售罄"尝试才下路径结论，
        # 否则 1 次拦截 + 4 次售罄会把一条正常的路判成劣化。
        _valid = max(0, n - int(v.get("empty") or 0))
        if _valid < 3:
            continue
        _r = float(_raw or 0)
        if _r < 0.3 and (bad is None or n > bad[1]):
            bad = (name, n, _r, _valid)
    if bad:
        return {"state": "path_degraded", "action": "route",
                "severity": "auto",
                "hint": "%s 近 24h 有效率 %.0f%%（%d 次有效 / %d 次尝试）"
                        "——已在日志记录路径偏好（影子模式，暂不改变采集"
                        "顺序）；可点「自检」验证这条路"
                        % (bad[0], bad[2] * 100, bad[3], bad[1]),
                "eta_s": 0}
    ok_n = sum(int((v or {}).get("ok") or 0) for v in paths.values())
    rate = (q_ok / q_attempts if q_attempts
            else (ok_n / attempts if attempts else 0.0))
    det = int(node.get("detail_ok") or 0) or sum(
        int((v or {}).get("detail_ok") or 0) for v in paths.values())
    cal = int(node.get("calendar_ok") or 0) or sum(
        int((v or {}).get("calendar_ok") or 0) for v in paths.values())
    q_det = int(node.get("query_detail_ok") or 0)
    q_cal = int(node.get("query_calendar_only") or 0)
    if q_attempts and q_det == 0 and q_cal > 0:
        return {"state": "calendar_only", "action": "probe",
                "severity": "wait",
                "hint": "近 24h 只拿到日历价（%d 次查询），没有航班明细——"
                        "明细路径可能被拦或改版；点「自检」看具体路径"
                        % q_cal,
                "eta_s": 0}
    if attempts and det == 0 and cal > 0:
        return {"state": "calendar_only", "action": "probe",
                "severity": "wait",
                "hint": "近 24h 只拿到日历价（%d 次），没有航班明细——"
                        "明细路径可能被拦或改版；点「自检」看具体路径"
                        % cal,
                "eta_s": 0}
    if rate < 0.5:
        return {"state": "degraded", "action": "watch", "severity": "wait",
                "hint": "近 24h 成功率 %.0f%%，观察一轮；连续两轮再干预"
                        % (rate * 100), "eta_s": 0}
    # 新鲜度：成功率好看但数据很旧 = 看起来正常其实没在采。
    # 只看「最近一次成功」，不看最近一次尝试——连续失败会刷新尝试时间，
    # 用尝试时间会把"一直没成功"误报成"刚刚更新"。
    ages = []
    if node.get("last_ok_at"):
        a = _age_min(node.get("last_ok_at"), now)
        if a is not None:
            ages.append(a)
    for v in paths.values():
        v = v or {}
        t = v.get("last_ok_at") if "last_ok_at" in v else v.get("last_at")
        a = _age_min(t, now)
        if a is not None:
            ages.append(a)
    if ages and min(ages) > 120:
        return {"state": "stale", "action": "probe", "severity": "wait",
                "hint": "最近一次成功已 %d 分钟，点「立即扫描」或等下一轮"
                        % int(min(ages)), "eta_s": 0}
    return {"state": "ok", "action": "none", "severity": "ok",
            "hint": "近 24h 成功率 %.0f%%（明细 %d / 日历 %d），无需操作"
                    % (rate * 100, det, cal),
            "eta_s": 0}


class Pacing:
    """一轮扫描共享的节流上下文：静默窗 + 分层 TTL + 熔断 + 令牌桶。

    实例挂在 `rt["pacing"]`，与进程同寿命——熔断状态跨轮保留，热重载
    只换阈值不丢已验证过的"这家现在不能碰"。
    """

    def __init__(self, cfg=None, clock=time.time):
        self._clock = clock
        self._lock = threading.RLock()
        self.cfg = parse_pacing(cfg or {})
        self._breaker = ChannelBreaker(
            self.cfg["breaker_base_s"], self.cfg["breaker_cap_s"],
            self.cfg["breaker_enabled"], self.cfg["login_cooldown_s"])
        self._bucket = TokenBucket(self.cfg["min_gap_s"],
                                   self.cfg["per_minute"])
        self._last = {}
        self._health = {}
        self._plan = []
        self._round = {"at": "", "fast": 0, "slow": 0, "skip": 0,
                       "trips": [], "busy": [],
                       "sent": 0, "paced": 0, "sent_by": {}, "paced_by": {}}

    # ---------- 配置热更新 ----------
    def refresh(self, cfg):
        new = parse_pacing(cfg or {})
        with self._lock:
            self.cfg = new
        # 锁外下发：Pacing→Breaker 的嵌套加锁在 refresh 与 observe 之间
        # 构成反向顺序，虽当前无环，但保持「同一时刻只持一把锁」最省心
        self._breaker.configure(
            new["breaker_base_s"], new["breaker_cap_s"],
            new["breaker_enabled"], new["login_cooldown_s"])
        self._bucket.configure(new["min_gap_s"], new["per_minute"])
        return new

    # ---------- 1 区：静默窗 ----------
    def quiet_now(self, now=None):
        now = self._clock() if now is None else now
        lt = time.localtime(now)
        return in_window(lt.tm_hour * 60 + lt.tm_min,
                         parse_hhmm(self.cfg["scan_quiet_start"]),
                         parse_hhmm(self.cfg["scan_quiet_end"]))

    # ---------- 2 区：熔断 ----------
    def allow(self, plat, now=None):
        return self._breaker.allow(plat, now)

    def trip(self, plat, kind, now=None, retry_after=0):
        return self._breaker.trip(plat, kind, now, retry_after)

    def reset(self, plat):
        self._breaker.reset(plat)

    def note_ok(self, plat, need=2):
        return self._breaker.note_ok(plat, need)

    def note_warmup_ok(self, plat):
        self._breaker.note_warmup_ok(plat)

    # ---------- 5 区：令牌桶 ----------
    def wait_hint(self, plat, now=None):
        return self._bucket.wait_hint(plat, now)

    def record(self, plat, now=None):
        self._bucket.record(plat, now)

    # ---------- 3 区：分层 TTL ----------
    def set_health(self, plat, rate, attempts):
        """按近 24h 成功率自动加罚（成功率保护，不是熔断）。

        熔断只管"对方明确拒绝"；持续解析失败/空结果不会触发它，却同样
        烧请求。这里按账本自动拉长该渠道的 TTL：0.4 以下 ×3、0.7 以下
        ×1.5，样本 <5 次不判（避免首轮波动就误降频）。
        """
        try:
            r = float(rate or 0)
            n = int(attempts or 0)
        except (TypeError, ValueError):
            return
        if n < 5:
            penalty = 1.0
        elif r >= 0.7:
            penalty = 1.0
        elif r >= 0.4:
            penalty = 1.5
        else:
            penalty = 3.0
        with self._lock:
            self._health[str(plat)] = {"rate": round(r, 3), "attempts": n,
                                       "penalty": penalty}

    def note_fetch(self, plat, key, price, ok=True):
        """只有"真拿到价"才刷新 TTL；失败/空结果不刷新，下一轮照常重试
        （否则一次软失败会让该查询静默两小时）。"""
        if not ok:
            return
        try:
            p = float(price)
        except (TypeError, ValueError):
            return
        if p <= 0:
            return
        with self._lock:
            self._last[(str(plat),) + tuple(key)] = (self._clock(), p)

    def seed_fetch(self, plat, key, price, at_ts):
        """用库里的最近一次真实明细续上 TTL（重启不重新全量打站）。

        只升不降：同一键已有更新的内存记录时保留内存值。时间来自库的
        fetched_at，不是"现在"——否则重启会把两天前的数据当刚采过。
        """
        try:
            p = float(price)
            ts = float(at_ts or 0)
        except (TypeError, ValueError):
            return False
        if p <= 0 or ts <= 0:
            return False
        k = (str(plat),) + tuple(key)
        with self._lock:
            old = self._last.get(k)
            if old and float(old[0]) >= ts:
                return False
            self._last[k] = (ts, p)
        return True

    def plan_query(self, plat, key, threshold=0, days_ahead=None):
        """返回 (tier, 原因)。tier ∈ fast/slow/skip。

        `days_ahead` 是出发日距今天数：临期行程价格变化快，给一个更短的
        TTL；远期只做低频基线。它只缩短 TTL，不会让本该跳过的查询变快。
        """
        now = self._clock()
        with self._lock:
            last = self._last.get((str(plat),) + tuple(key))
            near_ratio = float(self.cfg["near_ratio"])
            fast_ttl = int(self.cfg["fast_ttl_min"])
            slow_ttl = int(self.cfg["slow_ttl_min"])
            pen = float((self._health.get(str(plat)) or {}).get("penalty")
                        or 1.0)
        # 成功率保护：账本差就自动拉长 TTL，而不是等它撞出 429 再熔断
        fast_ttl = max(5, int(round(fast_ttl * pen)))
        slow_ttl = max(5, int(round(slow_ttl * pen)))
        # 临期加密：3 天内快层减半（不低于 5 分钟），30 天外慢层翻倍。
        # 只改本键 TTL，不改变健康惩罚的方向（penalty 仍然只加不减）。
        try:
            _d = int(days_ahead) if days_ahead is not None else None
        except (TypeError, ValueError):
            _d = None
        if _d is not None and _d >= 0:
            if _d <= 3:
                fast_ttl = max(5, int(round(fast_ttl / 2.0)))
            elif _d >= 30:
                slow_ttl = min(1440, int(round(slow_ttl * 2.0)))
        if not last:
            return "fast", "无历史基线"
        ts, price = last
        age = max(0.0, (now - ts) / 60.0)
        floor = int((self.cfg.get("cadence") or {}).get(str(plat), 0) or 0)
        if floor and age < floor:
            # 渠道节奏是硬地板：这家慢，就别用全局 30 分钟的节奏催它
            return "skip", "渠道节奏 %.0f/%d 分钟" % (age, floor)
        th = float(threshold or 0)
        near = True
        if th > 0 and price > 0:
            near = abs(price - th) / th <= near_ratio
        tier = tier_of(age, near, fast_ttl, slow_ttl)
        if tier == "skip":
            ttl = fast_ttl if near else slow_ttl
            return tier, "TTL 内（%.0f/%.0f 分钟）" % (age, ttl)
        return tier, ("贴近阈值" if near else "远离阈值") + "，%.0f 分钟未采" % age

    # ---------- 观测 ----------
    def begin_round(self, at=""):
        with self._lock:
            self._round = {"at": at, "fast": 0, "slow": 0, "skip": 0,
                           "trips": [], "busy": [],
                           # 请求预算：计划数 vs 实际发出，以及按渠道
                           # 计的实际发送/被预算挡下的次数
                           "sent": 0, "paced": 0, "sent_by": {},
                           "paced_by": {}, "ok_by": {}, "soft_by": {}}
            self._plan = []

    def note_plan(self, plat, key, tier, why):
        with self._lock:
            self._round[tier] = int(self._round.get(tier) or 0) + 1
            if len(self._plan) < 200:
                self._plan.append({
                    "plat": plat, "key": "-".join(str(x) for x in key),
                    "tier": tier, "why": why})

    def note_sent(self, plat):
        """真正发出一次渠道请求（不含预热；预热另计）。"""
        with self._lock:
            self._round["sent"] = int(self._round.get("sent") or 0) + 1
            _b = self._round.setdefault("sent_by", {})
            _b[plat] = int(_b.get(plat) or 0) + 1

    def note_result(self, plat, ok=False, soft=False):
        """记录本轮该渠道是否真的出数（一轮一渠道只记最终结果）。

        这是"本轮成功率"的直接口径：有数渠道数 / 实发渠道数。
        与路径账不同，它不受一次查询多路径影响。
        """
        with self._lock:
            if ok:
                _b = self._round.setdefault("ok_by", {})
                _b[plat] = int(_b.get(plat) or 0) + 1
            elif soft:
                _b = self._round.setdefault("soft_by", {})
                _b[plat] = int(_b.get(plat) or 0) + 1

    def note_paced(self, plat, why=""):
        """因 TTL/熔断/预算等待超限而主动没发。"""
        with self._lock:
            self._round["paced"] = int(self._round.get("paced") or 0) + 1
            _b = self._round.setdefault("paced_by", {})
            _b[plat] = int(_b.get(plat) or 0) + 1

    def note_busy(self, plat):
        with self._lock:
            if plat not in self._round["busy"]:
                self._round["busy"].append(plat)

    def note_trip(self, plat, kind, cooldown_s, retry_after=0):
        with self._lock:
            self._round["trips"].append(
                {"plat": plat, "kind": kind, "cooldown_s": int(cooldown_s),
                 "retry_after": int(retry_after or 0)})

    def observe(self, plat, statuses, note="", retry_after=0):
        """一次采集落地后调用：真拒绝→熔断；拿到数据→复位。

        `retry_after` 来自响应头（秒）。有它就以它为准，没有才用自算阶梯。
        返回 (kind, cooldown_s)；kind 空串表示"不算拒绝"。
        """
        kind = classify_failure(statuses, note)
        if not kind:
            if any(str(s) == "ok" or str(s) == "calendar"
                   for s in (statuses or [])):
                self.reset(plat)
            return "", 0
        # 软拒不清 needs_warmup：预热成功（拿到风控 cookie）后由
        # note_warmup_ok() 清；否则下一轮继续先预热。
        cd = self.trip(plat, kind, retry_after=retry_after)
        self.note_trip(plat, kind, cd, retry_after)
        return kind, cd

    def snapshot(self, now=None):
        now = self._clock() if now is None else now
        lt = time.localtime(now)
        start = parse_hhmm(self.cfg["scan_quiet_start"])
        end = parse_hhmm(self.cfg["scan_quiet_end"])
        with self._lock:
            rnd = dict(self._round)
            plan = list(self._plan)
            cfg = dict(self.cfg)
        with self._lock:
            _sent = sorted((rnd.get("sent_by") or {}).keys())
            _ok = sorted((rnd.get("ok_by") or {}).keys())
            _soft = sorted((rnd.get("soft_by") or {}).keys())
        channels = self._breaker.snapshot(now)
        with self._lock:
            health = {k: dict(v) for k, v in self._health.items()}
        for p, b in self._bucket.snapshot(now).items():
            if p in channels:
                channels[p]["bucket"] = b
        for p, h in health.items():
            if p in channels:
                channels[p]["health"] = h
        return {
            "cfg": cfg,
            "quiet": {
                "start": cfg["scan_quiet_start"],
                "end": cfg["scan_quiet_end"],
                "active": in_window(lt.tm_hour * 60 + lt.tm_min, start, end),
            },
            "channels": channels,
            "bucket": self._bucket.snapshot(now),
            "last_round": rnd,
            "coverage": {
                "sent": _sent, "ok": _ok, "soft": _soft,
                "ratio": (round(len(_ok) / len(_sent), 3) if _sent else 0.0),
            },
            "plan": plan,
        }
