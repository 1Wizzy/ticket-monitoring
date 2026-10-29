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

## 实测基线（2026-10-30）

| 指标 | 值 | 说明 |
| --- | --- | --- |
| 实例冷启动 | 24–36 s | `MuMuManager info` 轮询到 `is_android_started` |
| 单次采集（长窗口日历） | **42 s** | 含启 App、设航线、开日历弹层、解析 |
| `screencap` | 63–91 ms | 网页内嵌登录画面（P3）的依据 |
| `uiautomator dump` | 75–77 ms | 元素定位足够实时 |
| 智行包 | `cn.suanya.zhixing` 10.25.8，104 MB，`arm64-v8a` | 必须用 64 位包 |
| 长窗口日历 | **73 格 / 37 格有价，跨两个月** | 2026-11-19 实测采到 ¥360 |

## 已知限制

1. **未登录只能拿日历价**：智行列表里的单班票价被遮成 `¥3??`，
   只有日历的每日最低价是真实数字。所以 App 渠道在未登录时产出的
   是**日历族**结果（每天一条最低价、无航班号），不能当明细用。
2. **长窗口靠「选择日期」弹层**：首页日期条只有 7–8 天，点开弹层后
   是跨两个月的完整日历、每天有价。入口在首页，**一旦点了「机票查询」
   进列表页就没有日历图标了**——所以流程必须是「设航线 → 停在首页
   → 读弹层」，不能先查询。
3. 单设备串行：多个 App 渠道共用一台模拟器，采集是排队执行的。
4. App 改版会导致元素定位失效；届时账本会记具体状态，不会伪装成无票。
5. 弹层只渲染已加载的月份，滚动更远的月份需要额外交互（当前未做）。

## 弹窗状态（三类，互不冒充）

| 状态 | 含义 | 处置 |
| --- | --- | --- |
| `ad` | 营销弹层 | 自动点关闭（≤2 次）后继续采；关不掉则上报 |
| `captcha` | 验证码/滑块/点选 | **不自动破解**，停下等人工接管 |
| `face` | 人脸/活体核验 | 生物特征，只能本人操作；优先级最高 |

判据说明：真实航班页顶部本来就有「登录解锁更低价」横幅，但数据能采，
所以**登录只在页面没有可采数据时才算拦路**，否则会把好数据误判成登录墙。

## 首次使用（引导）

装好框架后，App 渠道是**可选项**，不会自动启用。推荐顺序：

```bash
# 1) 体检：看运行时状态与下一步（只读，不安装任何东西）
python main.py --app-status

# 2) 按提示装 MuMu（约 7 GB，需一次管理员确认），装完再体检
python main.py --app-status

# 3) 配置页打开「Android 真机通道」总开关，并在用户里勾选「智行 App」
```

`--app-status` 输出五个阶段（检测 / 安装 / 启动 / 装 App / 验证），
已完成的打勾。它**先于实例锁返回**——已有实例在跑时也能查，那正是
用户想确认状态的时刻。

## 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 状态 `not_installed` | 找不到 MuMu | 按 `--app-status` 的提示装，或配 `install_dir` |
| 状态 `stopped` | 实例未启动 | 首轮采集会自动拉起；也可在 MuMu 里手动启动 |
| 状态 `error` + `adb 未连接` | adb 会话断了 | 重开实例；`adb kill-server` 后重试 |
| 账本 `login` | 需要登录 | 未登录只能拿日历价，明细需登录 |
| 账本 `captcha` | 验证码触发 | 人工接管，不做自动破解 |
| 账本 `face` | 人脸/活体核验 | 只能本人操作；本条渠道在该账号下不可自动化 |
| 账本 `timeout` + 「无法识别的页面状态」 | App 可能改版 | 需更新解析器，不是"没票" |
| 账本「N 个目标日期在日历窗口外」 | 目标日期超出两个月 | 缩短监控日期，或等窗口推进 |

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
