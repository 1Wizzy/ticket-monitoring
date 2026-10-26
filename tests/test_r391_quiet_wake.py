# -*- coding: utf-8 -*-
"""r391：静默窗结束即补采（避免窗口结束后仍陈旧半小时）。

夜间静默窗内 sweep 直接 return（只有 force 绕过），跳过轮不落轮次标记；
若照常按 24–37 分钟间隔接力，07:00 窗口结束后最坏还要再等半小时才有
第一轮数据（此时数据已陈旧 6 小时）。现在窗口内跳过时把「距窗口结束
秒数」写进 rt["_wake_at"]，调度取 min(常规间隔, 窗口结束+1 分钟)。
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pacing import Pacing  # noqa: E402

_LOG = logging.getLogger("t_r391")


def _at(hhmm):
    return time.mktime(time.strptime("2026-10-27 %s:00" % hhmm,
                                     "%Y-%m-%d %H:%M:%S"))


def _p():
    return Pacing({"schedule": {"scan_quiet_start": "01:00",
                                "scan_quiet_end": "07:00"}})


def test_seconds_until_quiet_end_inside_window():
    p = _p()
    assert p.quiet_now(_at("01:30")) is True
    left = p.seconds_until_quiet_end(_at("01:30"))
    assert left == 5.5 * 3600, left


def test_seconds_until_quiet_end_outside_window():
    p = _p()
    assert p.quiet_now(_at("07:30")) is False
    assert p.seconds_until_quiet_end(_at("07:30")) is None


def test_seconds_until_quiet_end_just_before_end():
    p = _p()
    left = p.seconds_until_quiet_end(_at("06:59"))
    assert left == 60.0, left


def test_seconds_until_quiet_end_cross_midnight():
    p = Pacing({"schedule": {"scan_quiet_start": "23:00",
                             "scan_quiet_end": "07:00"}})
    assert p.quiet_now(_at("02:00")) is True
    assert p.seconds_until_quiet_end(_at("02:00")) == 5 * 3600


def test_seconds_until_quiet_end_disabled_window():
    p = Pacing({})
    assert p.seconds_until_quiet_end(_at("02:00")) is None


def test_scheduler_honours_wake_at(monkeypatch):
    """调度把下一轮提前到窗口结束（比常规 30 分钟间隔短时取短）。"""
    import core.scheduler as sch

    class _Job:
        pass

    class _Sched:
        timezone = None

        def __init__(self):
            self.modified = None

        def get_job(self, _id):
            return _Job()

        def modify_job(self, _id, next_run_time=None):
            self.modified = next_run_time

    from datetime import datetime as _dt
    s = _Sched()
    s.timezone = _dt.now().astimezone().tzinfo
    state = {"interval_minutes": 30, "jitter_minutes": 0, "wake_at": 180}
    before = _dt.now(s.timezone)
    nxt = sch._schedule_next(s, state)
    assert nxt is not None and s.modified is not None
    delta_min = (nxt - before).total_seconds() / 60.0
    assert delta_min <= 3.5, delta_min          # 180s ≈ 3 分钟，不是 30 分钟
    assert "wake_at" not in state, "一次性键必须消费掉"
