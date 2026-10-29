# -*- coding: utf-8 -*-
"""r623：新增渠道必须同步到「日志解析」与「告警名表」。

`core/health.py` 的两条正则和 `core/channel_alert.py` 的 `CN` 都把渠道名
**硬编码**在源码里。新增一个渠道时（如智行 App），只要漏掉其中任一处：

- `_PLAT` / `_SENT` 漏了 → 该渠道的日志行解析不出来，健康卡上直接**没有它**，
  看起来像「这个渠道没在跑」，而不是「解析规则没跟上」；
- `CN` 漏了 → 告警文案里露出 `app_xxx` 原始 id。

这两处漏配都是**静默**的：没有报错、没有空态提示，只是数据凭空少了。
r613 的注释里写了「新增渠道要同步三处」，这份文件把第四、五处也钉住。

判据：`crawlers.REGISTRY` 的每个键都必须出现在上述三处。
负控：从正则里删掉 `app_zhixing` 即红。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8",
                   newline="").read()


def _alt_list(src, anchor):
    """取某个 `re.compile(` 调用里所有 r"..." 片段中的渠道名。

    正则可能跨行书写（`_SENT` 就是两段拼接），所以先把该 compile 调用
    的整段参数取出来（到匹配的右括号），再收集其中所有原始字符串。
    """
    i = src.index(anchor)
    # 从 anchor 起找配对的右括号
    j = src.index("(", i + len(anchor) - 1)
    depth = 0
    k = j
    while k < len(src):
        ch = src[k]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        k += 1
    body = src[j:k]
    names = set()
    for part in re.findall(r'r"([^"]*)"', body):
        names.update(p for p in re.split(r"[|()\\\[\]^$]+", part)
                     if re.fullmatch(r"[a-z_][a-z0-9_]*", p))
    return names


def test_health_log_regex_covers_every_registered_channel():
    from crawlers import REGISTRY
    src = _src(os.path.join("core", "health.py"))
    plat = _alt_list(src, "_PLAT = re.compile(")
    sent = _alt_list(src, "_SENT = re.compile(")
    assert plat, "解析不到 _PLAT 的渠道列表（本门禁需复核）"
    assert sent, "解析不到 _SENT 的渠道列表（本门禁需复核）"
    for label, got in (("_PLAT", plat), ("_SENT", sent)):
        missing = sorted(set(REGISTRY) - got)
        assert not missing, (
            "%s 漏了这些已注册渠道：%s —— 它们的日志行会被静默丢弃，"
            "健康卡上像「没在跑」" % (label, missing))


def test_alert_name_table_covers_every_registered_channel():
    from crawlers import REGISTRY
    src = _src(os.path.join("core", "channel_alert.py"))
    i = src.index("CN = {")
    j = src.index("}", i)
    names = set(re.findall(r"\"([a-z_][a-z0-9_]*)\"\s*:", src[i:j]))
    missing = sorted(set(REGISTRY) - names)
    assert not missing, (
        "告警名表 CN 漏了这些渠道（告警里会露原始 id）：%s" % missing)
    # 名表里的键也不能凭空多出来（否则是死条目，同 r622 的教训）
    ghost = sorted(names - set(REGISTRY))
    assert not ghost, "告警名表里有 REGISTRY 中不存在的渠道：%s" % ghost
