# App 渠道（可选）操作手册

> 状态：P1 已完成（运行时 + 设备原语 + 智行日历族采集）。
> 设计：`docs/superpowers/specs/2026-10-29-app-channels-design.md`
> 计划：`docs/superpowers/plans/2026-10-29-app-channels-p1.md`

## 这是什么

框架默认只跑 5 个浏览器渠道。App 渠道（智行、美团）是**额外可选**能力：
它们没有可用 Web 面，只能通过 Android App 取数，因此需要本机跑一个
Android 模拟器（MuMu），由框架通过 adb 驱动。

**不启用时，框架行为与以前完全一致**——不 import、不检测设备、不显示 UI。

## 前置条件

| 项 | 要求 |
| --- | --- |
| 系统 | Windows（MuMu 仅 Windows；Linux/macOS 上 App 渠道自动不可用） |
| 模拟器 | MuMu 12（本机实测 `D:\Program Files\Netease\MuMu`，约 7.1 GB） |
| 虚拟化 | 需开启 VT；MuMu 与 Hyper-V/VBS 可共存（实测 `hyperv_enabled: true`） |
| 磁盘 | 引擎 + 实例约 5 GB 起 |

## 配置

```yaml
android:
  enabled: true                    # 总开关，默认 false
  provider: mumu
  install_dir: ""                  # 空=自动探测常见安装位置
  vm_index: 0                      # 多开时选第几个实例
  boot_timeout_s: 180              # 冷启动实测 24–36s，留足余量
  action_timeout_s: 60
  snapshot: true                   # 预留：登录态快照（P3）

users:
  - platforms: [qunar, fliggy, app_zhixing]   # 显式加入才启用
```

渠道 id：`app_zhixing`（智行）。

## 实测基线（2026-10-29）

| 指标 | 值 | 说明 |
| --- | --- | --- |
| 实例冷启动 | 24–36 s | `MuMuManager info` 轮询到 `is_android_started` |
| 单次采集（SHA→BJS，3 天） | **17.6 s** | 含启 App、等首页、dump、解析 |
| `screencap` | 63–91 ms | 网页内嵌登录画面（P3）的依据 |
| `uiautomator dump` | 75–77 ms | 元素定位足够实时 |
| 智行包 | `cn.suanya.zhixing` 10.25.8，104 MB，`arm64-v8a` | 必须用 64 位包 |

## 已知限制

1. **未登录只能拿日历价**：智行列表里的单班票价被遮成 `¥3??`，
   只有顶部日历条的每日最低价是真实数字。所以 App 渠道在未登录时
   产出的是**日历族**结果（每天一条最低价、无航班号），不能当明细用。
2. 日历条只覆盖 7 天左右（首屏可见范围），不是 61 天那种长窗口。
3. 单设备串行：多个 App 渠道共用一台模拟器，采集是排队执行的。
4. App 改版会导致元素定位失效；届时路径账本会记 `blocked`，不会伪装成无票。

## 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 状态 `not_installed` | 找不到 MuMu | 检查 `install_dir`，或先装 MuMu |
| 状态 `starting` 卡住 | 实例启动慢/卡死 | 看 `boot_timeout_s`；在 MuMu 里手动启动一次 |
| 状态 `error` + `adb 未连接` | adb 会话断了 | 重开实例；`adb kill-server` 后重试 |
| 采集返回 0 条、路径 `login` | 需要登录 | 走登录流程（P3 会做成网页内嵌画面） |
| 采集返回 0 条、路径 `blocked` | 页面结构不认识 | App 可能改版，需要更新解析器 |

## 实现结构

```
core/android/config.py    配置归一化（缺省关闭、数值钳制）
core/android/mumu.py      MuMuManager 封装（发现/查询/控制，失败不抛）
core/android/device.py    ADB 原语（screencap/tap/input/dump/app_start）
core/android/runtime.py   单例 + 设备锁 + ensure_ready 编排
crawlers/app_base.py      AppCrawler 基类（账本契约对齐 BaseCrawler）
crawlers/app_zhixing.py   智行渠道
crawlers/app_zhixing_parse.py  dump 解析（纯函数，离线可测）
```

## 测试

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  tests/test_android_config.py tests/test_android_mumu.py \
  tests/test_android_device.py tests/test_android_runtime.py \
  tests/test_app_crawler_base.py tests/test_app_zhixing_parse.py \
  tests/test_app_zhixing_crawler.py -q
```
