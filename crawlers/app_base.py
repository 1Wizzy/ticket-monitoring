# -*- coding: utf-8 -*-
"""App 渠道基类：不依赖 Playwright，但复用既有账本契约。

为什么另起一个基类而不是继承 BaseCrawler：BaseCrawler 的
`browser()` / `_browser_context()` 全是 Playwright 实现，App 渠道继承
它会把一整套用不上的浏览器路径带进来，且 `fetch` 里误调 `self.browser()`
会静默开一个 Chromium。这里只保留真正共享的部分：

  * `PATH_STATUS` / `path_reset()` / `path_note()` —— 账本字段与
    BaseCrawler **逐键一致**（test_app_crawler_base 钉住），这样探针
    面板、复发账、健康解析都能无差别消费；
  * `safe_fetch()` —— 同签名、同「吞异常返回 []」语义；
  * 日志行 `[<plat>] ...` —— 健康解析的唯一数据源格式。

差异点：`login_url()` 对 App 渠道返回的是**登录入口描述**而非真实 URL
（App 没有可粘贴的登录页），由 webui 走内嵌画面流程。
"""
from __future__ import annotations

import logging
import os
from typing import List

from core.models import FlightPrice

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AppCrawler:
    """Android App 渠道基类（单设备串行采集）。"""

    name: str = "app_base"
    package: str = ""          # Android 包名
    # 与 BaseCrawler 保持一致（另有 calendar 表达"只出每日最低价"），
    # 并补 App 特有的三类拦路物：广告弹层 / 验证码 / 人脸核验。
    # 它们必须能独立入账——把"被挡住"记成 empty 或 error，等于让账本说谎。
    PATH_STATUS = ("ok", "empty", "login", "blocked", "timeout",
                   "error", "parse_error", "unsupported", "calendar",
                   "soft_reject", "ad", "captcha", "face")
    # 会话是否可疑（webui 登录卡分层用），实例级每轮复位
    login_suspect: bool = False

    def __init__(self, config: dict, logger: logging.Logger):
        self.config = dict(config or {})
        self.logger = logger
        self.timeout_s = int(self.config.get("action_timeout_s", 60) or 60)
        self.paths: list = []
        self.last_path = ""
        self.last_status = ""
        self.last_error = ""
        self._runtime = None
        # 与浏览器渠道同一条约定：资料目录按仓库根解析，不随 cwd 漂移
        # （登录态快照要落在这里，chdir 后写错位置用户会找不到自己的凭证）。
        root = str(self.config.get("user_data_dir") or "user_data").strip() \
            or "user_data"
        self.user_data_root = os.path.abspath(os.path.join(_REPO_ROOT, root))

    # ---------- 账本（与 BaseCrawler 同形）----------
    def path_reset(self):
        self.paths = []
        self.last_path = ""
        self.last_status = ""

    def path_note(self, path: str, status: str, rows: int = 0,
                  date: str = "", detail: str = "", ms: int = 0,
                  http_status: int = 0, url: str = ""):
        if status not in self.PATH_STATUS:
            status = "error"
        rec = {"path": str(path), "status": status,
               "rows": int(rows or 0), "date": str(date or ""),
               "detail": str(detail or "")[:160], "ms": int(ms or 0),
               "http": int(http_status or 0),
               "mobile": True}
        if url:
            rec["url"] = str(url)[:500]
        if not hasattr(self, "paths"):
            self.path_reset()
        self.paths.append(rec)
        self.last_path = rec["path"]
        self.last_status = rec["status"]
        return rec

    # ---------- 运行时 ----------
    def runtime(self):
        if self._runtime is None:
            from core.android.runtime import get_runtime
            self._runtime = get_runtime(self.config, self.logger)
        return self._runtime

    # ---------- 调用入口 ----------
    def safe_fetch(self, from_city: str, to_city: str, dates: List[str],
                   ret_date: str = "") -> List[FlightPrice]:
        self.path_reset()
        self.last_error = ""
        try:
            self.logger.info("[%s] 开始抓取 %s->%s 日期=%s",
                             self.name, from_city, to_city, dates)
            results = self.fetch(from_city, to_city, dates,
                                 ret_date=ret_date) or []
            self.logger.info("[%s] 抓取完成，得到 %d 条记录",
                             self.name, len(results))
            return results
        except Exception as e:  # 设备掉线/解析失败都走这里
            self.last_error = f"{type(e).__name__}: {e}"
            self.logger.warning("[%s] 抓取失败: %s", self.name, self.last_error)
            if not any(p.get("status") == "error" for p in self.paths):
                self.path_note("app-error", "error", detail=self.last_error)
            return []

    # ---------- 子类必须实现 ----------
    def fetch(self, from_city: str, to_city: str, dates: List[str],
              ret_date: str = "") -> List[FlightPrice]:
        raise NotImplementedError

    def login_url(self) -> str:
        """App 渠道返回登录入口描述（无真实 URL）。"""
        raise NotImplementedError
