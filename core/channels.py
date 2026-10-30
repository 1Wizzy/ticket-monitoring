# -*- coding: utf-8 -*-
"""渠道清单的**零依赖单一事实源**。

为什么单独一个模块：
- `crawlers/__init__.py` 要 import 各 crawler，而那些 crawler 又 import
  core 的模块（models/pacing/…）。若 core 反过来 import crawlers 取
  App 渠道清单，就会 `core → crawlers → core` 循环导入。
- 于是这个清单曾在 `crawlers/__init__.py`、`core/pacing.py`、
  `core/health_diag.py` 各写一份。加第二个 App 渠道时必须三处同改，
  漏一处就是「渠道能采但不在节流表里 / 不进诊断卡」的静默缺陷。

本模块只放常量，不 import 任何项目内模块，因此谁都可以安全引用。
新增 App 渠道时**只改这里**（外加 REGISTRY 注册与 crawler 实现）。
"""
from __future__ import annotations

# 浏览器渠道（Playwright）
BROWSER_CHANNELS = ("ctrip", "fliggy", "qunar", "tongcheng", "tuniu")

# App 渠道（Android 模拟器 + adb）：需 android.enabled 且用户 platforms
# 显式列出才会被实例化。
APP_CHANNELS = ("app_zhixing",)

# 显示名（中文）。App 渠道必须在这里，否则界面会露出裸 id（app_zhixing）。
CHANNEL_CN = {
    "ctrip": "携程", "qunar": "去哪儿", "fliggy": "飞猪",
    "tongcheng": "同程", "tuniu": "途牛",
    "app_zhixing": "智行 App",
}


def plat_cn(p: str) -> str:
    """渠道 id → 显示名；未知渠道原样返回。"""
    return CHANNEL_CN.get(p) or p
