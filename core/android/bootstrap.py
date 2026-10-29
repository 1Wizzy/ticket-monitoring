# -*- coding: utf-8 -*-
"""首次引导：检测 Android 运行时状态，给出可执行的下一步。

设计原则（对齐 `ensure_chromium` 的按需模式，但更保守）：
  * **chromium 是 ~120MB 能静默下，MuMu 是 ~7GB + 需要一次管理员确认**，
    所以这里只做「检测 + 明确指引」，绝不偷偷替用户装一个大软件。
  * 引导必须是**可执行的一条路**：告诉用户点哪里、装完回来看什么。
  * 未启用 App 渠道时**完全不执行**（不探测磁盘、不跑子进程）。
"""
from __future__ import annotations

import os
import sys

from .config import normalize, resolve_install_dir
from .mumu import MuMuManager

# MuMu 官方下载入口（与本项目实测一致的渠道）
MUMU_DOWNLOAD_HINT = "https://mumu.163.com/"

# 首次引导的阶段（与 UI 进度态一一对应）
STEPS = (
    ("check", "检测 Android 运行时"),
    ("install", "安装 MuMu 模拟器"),
    ("boot", "启动并等待实例就绪"),
    ("apk", "安装智行 App"),
    ("verify", "验证可采集"),
)


def status(cfg: dict) -> dict:
    """返回引导状态。未启用时立刻返回 disabled，不碰任何外部资源。"""
    android = (cfg or {}).get("android") or {}
    if not (android.get("enabled") or False):
        return {"state": "disabled", "detail": "App 渠道未启用",
                "steps": [], "next": ""}

    ac = normalize(android)
    install_dir = resolve_install_dir(ac)

    if not install_dir:
        return {
            "state": "not_installed",
            "detail": "未检测到 MuMu 模拟器（约 7 GB，需一次管理员确认）",
            "steps": [{"key": k, "label": v,
                       "done": k == "check"} for k, v in STEPS],
            "next": f"从 {MUMU_DOWNLOAD_HINT} 下载安装 MuMu 12，"
                    f"装完后回到本页点「重新检测」",
        }

    mgr = MuMuManager(install_dir=install_dir, vm_index=ac.vm_index,
                      timeout=ac.action_timeout_s)
    if not mgr.available():
        return {"state": "error", "detail": f"安装目录不完整：{install_dir}",
                "steps": [], "next": "检查 android.install_dir 配置"}

    info = mgr.info() or {}
    started = bool(info.get("is_android_started"))
    adb_port = info.get("adb_port") or ""

    if not started:
        return {
            "state": "stopped",
            "detail": "MuMu 已安装，实例未启动",
            "steps": [{"key": k, "label": v, "done": k in ("check", "install")}
                      for k, v in STEPS],
            "next": "启动实例后可开始采集（首轮会自动拉起）",
        }

    state = {
        "state": "ready",
        "detail": f"实例已就绪 · adb {info.get('adb_host_ip', '127.0.0.1')}"
                  f":{adb_port}",
        "steps": [{"key": k, "label": v,
                   "done": k in ("check", "install", "boot")}
                  for k, v in STEPS],
        "next": "",
    }
    return state


def ensure_android_runtime(platforms, cfg, logger=None) -> str:
    """启动时调用：只在启用 App 渠道时检查，返回一句可打印的状态。

    与 `ensure_chromium` 的差别：**不自动下载**。MuMu 体积与权限要求
    都不适合静默安装，越界替用户装会被当成流氓行为。
    """
    from crawlers import APP_CHANNELS
    wanted = set(platforms or []) & set(APP_CHANNELS)
    if not wanted:
        return ""

    st = status(cfg)
    state = st["state"]
    if state == "disabled":
        msg = ("已勾选 App 渠道但「Android 真机通道」未启用——"
               "到 配置 · 全局参数 → Android 真机通道 打开总开关")
    elif state == "not_installed":
        msg = f"未检测到 MuMu：{st['next']}"
    elif state == "ready":
        msg = st["detail"]
    else:
        msg = f"{st['detail']}：{st.get('next', '')}".strip("：")

    if logger is not None:
        try:
            (logger.warning if state in ("disabled", "not_installed")
             else logger.info)("[app] %s", msg)
        except Exception:
            pass
    return msg
