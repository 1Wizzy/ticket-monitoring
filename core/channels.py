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


# 采集通路（fetch_paths.entry_path）→ 显示名。
#
# 账本里这张表的值是各 crawler 自己写的字面量：浏览器渠道大多已是中文
# （日历/H5/PC/窗历/往返/H5·API），App 渠道却是英文 id（app-list /
# android-runtime / snap-restore …）。健康卡的「分通路」小标和
# core.pacing 的路径劣化提示都直接把这些键名打到屏幕上，读者看到
# 「android-runtime 无有效样本」根本不知道是哪一步卡住。
#
# 键必须与 path_note() 的实参逐字一致（test_r658 会扫源码对账）：
# 新增通路时这里不同步，界面就退回裸 id。
PATH_CN = {
    # 浏览器渠道
    "H5": "H5",                 # 已是行业通用写法，保留
    "PC": "PC",
    "H5/API": "H5 · API",
    "日历": "日历",
    "窗历": "窗历",
    "往返": "往返",
    "航班明细": "航班明细",
    "PC可见": "PC 可见",
    # 其余散落在单个 crawler 里的通路名（r658 门禁扫 path_note 字面量
    # 对账时补出，此前没人意识到它们也会上屏）
    "H5列表": "H5 列表",
    "API": "API 直连",
    "QUERY": "查询请求",
    "TRIP": "往返查询",
    # App 渠道（智行）：这条真机链路分段入账，每段都可能成为瓶颈
    "app-route": "App 设航线",
    "app-list": "App 航班列表",
    "app-blocker": "App 拦路弹窗",
    "app-dump": "App 界面快照",
    "app-error": "App 解析",
    "android-runtime": "模拟器运行时",
    "android-device": "模拟器设备",
    "snap-restore": "登录态快照恢复",
}


def path_cn(p: str) -> str:
    """通路 id → 显示名；未知通路原样返回（宁缺勿错，不编造名字）。"""
    return PATH_CN.get(p) or p

