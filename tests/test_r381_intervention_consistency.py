# -*- coding: utf-8 -*-
"""r381: 干预后一致性（intervention consistency）。

核心命题：**干预成功 ≠ 状态已恢复**。如果自检/补采成功了却不清熔断、
不刷 TTL、不落账，用户会看到"自检 ✓N 条"和"仍在冷却/劣化"同时出现，
并且重启后完全无法追溯。这组测试钉住这条链。
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as M  # noqa: E402
from core.storage import PriceStorage  # noqa: E402


class _Store:
    def __init__(self, path):
        self.db_path = path
        self.paths = []
        self.saved = []
        self.ivs = []

    def latest_prices(self, *a, **k):
        return []

    def path_stats(self, *a, **k):
        return {}

    def save_fetch_path(self, *a, **k):
        self.paths.append(k)
        return 1

    def save_many(self, rows):
        self.saved.extend(list(rows))

    def save_intervention(self, *a, **k):
        self.ivs.append((a, k))

    def refresh_daily(self, *a, **k):
        pass

    def refresh_flight_daily(self, *a, **k):
        pass

    def purge(self, *a, **k):
        return 0


class _Crawler:
    name = "qunar"
    use_mobile = True
    warmup_url = "https://touch.qunar.com/h5/flight/"
    warmup_cookie_ok = True
    warmup_cookies = ["QN1"]
    RISK_COOKIES = ("QN1",)
    last_busy = False
    retry_after_s = 0
    last_block_url = ""
    last_block_api = ""
    last_scene = {}
    last_query = ("SHA", "PEK", "2027-06-01")
    paths = []
    rows = 2

    def __init__(self, cfg, logger):
        self.logger = logger

    def warmup(self, headless=True):
        return True

    def safe_fetch(self, fc, tc, dates, ret_date=""):
        from core.models import FlightPrice
        self.paths = [{"path": "H5", "status": "ok", "rows": self.rows,
                       "mobile": True}]
        return [FlightPrice(platform=self.name, from_city=fc, to_city=tc,
                            depart_date=dates[0], price=900.0)] if self.rows \
            else []


def _job(tmp_path, monkeypatch, crawler=None):
    monkeypatch.setattr(M, "REGISTRY",
                        {"qunar": crawler or _Crawler})
    cfg = {"schedule": {"scan_quiet_start": "", "scan_quiet_end": ""},
           "crawler": {"warmup": {"enabled": True,
                                  "platforms": ["qunar"]}},
           "output": {}, "web": {},
           "users": [{"name": "u", "platforms": ["qunar"], "notifier": {},
                      "schedule": {},
                      "routes": [{"from": "SHA", "from_name": "上海",
                                  "to": "PEK", "to_name": "北京",
                                  "dates": ["2027-06-01"],
                                  "alert_direct": 900}]}]}
    rt = {"users": M.build_users(cfg), "alerters": [], "cfg": cfg}
    st = _Store(str(tmp_path / "p.db"))
    M.make_sweep_job(cfg, rt, logging.getLogger("r381"),
                     st, lambda *a, **k: {})
    return rt, st


def test_successful_probe_clears_cooldown_and_refreshes_ttl(tmp_path,
                                                            monkeypatch):
    rt, st = _job(tmp_path, monkeypatch)
    pac = rt["pacing"]
    # 先制造"冷却中"的现场
    pac.trip("qunar", "ip")
    assert pac.snapshot()["channels"]["qunar"]["state"] == "cooldown"
    out = rt["probe_channel"]("qunar", scope="dom")
    assert out["ok"] is True and out["rows"] >= 1
    ch = pac.snapshot()["channels"]["qunar"]
    assert ch["state"] == "ok", "自检成功后必须解除冷却"
    assert ch["strikes"] == 0
    # TTL 应被刷新：同一 key 立刻 plan 应落在 skip（刚采过）
    tier, _why = pac.plan_query("qunar",
                                ("SHA", "PEK", out["query"][2]), 900)
    assert tier == "skip", "自检出数必须刷新该键 TTL"
    # 干预账必须落一条
    assert any(a and a[1] == "probe" for a, _k in st.ivs)


def test_failed_probe_keeps_cooldown_visible(tmp_path, monkeypatch):
    class _Fail(_Crawler):
        rows = 0

        def safe_fetch(self, fc, tc, dates, ret_date=""):
            self.paths = [{"path": "H5", "status": "blocked", "rows": 0,
                           "mobile": True, "detail": "验证页"}]
            return []

    rt, st = _job(tmp_path, monkeypatch, crawler=_Fail)
    pac = rt["pacing"]
    pac.trip("qunar", "ip")
    out = rt["probe_channel"]("qunar", scope="dom")
    assert out["ok"] is True and out["rows"] == 0
    # 没出数不得解除冷却（否则会把"还没修好"显示成"已恢复"）
    assert pac.snapshot()["channels"]["qunar"]["state"] == "cooldown"


def test_refill_success_clears_breaker_and_ttl(tmp_path, monkeypatch):
    """补采出数也必须复位熔断并刷新 TTL（否则补采成功仍显示冷却）。"""
    rt, st = _job(tmp_path, monkeypatch)
    pac = rt["pacing"]
    pac.trip("qunar", "ip")
    rt["failed"] = {"qunar": {("SHA", "PEK", "2027-06-01")}}
    out = rt["refetch_failed"]("qunar")
    assert out["rows"] >= 1, out
    assert pac.snapshot()["channels"]["qunar"]["state"] == "ok"
    assert pac.plan_query("qunar", ("SHA", "PEK", "2027-06-01"),
                          900)[0] == "skip"


def test_intervention_ledger_roundtrip(tmp_path):
    st = PriceStorage(str(tmp_path / "prices.db"))
    st.save_intervention("qunar", "probe", ok=True, rows=65, scope="dom",
                         note="H5 ok×65", ms=7700)
    st.save_intervention("qunar", "verify", ok=False, rows=0,
                         note="验证未通过", ms=12000)
    st.save_intervention("ctrip", "warmup", ok=True, note="cookies=cticket")
    rows = st.interventions("qunar", 10)
    assert [r["action"] for r in rows] == ["verify", "probe"]   # 倒序
    assert rows[1]["rows"] == 65 and rows[1]["ok"] == 1
    assert rows[0]["ok"] == 0
    allrows = st.interventions("", 10)
    assert len(allrows) == 3
    # 重启等价：新连接仍可读（持久化而非内存）
    st2 = PriceStorage(str(tmp_path / "prices.db"))
    assert len(st2.interventions("", 10)) == 3


def test_probe_persists_attempt_id_and_evidence(tmp_path, monkeypatch):
    rt, st = _job(tmp_path, monkeypatch)
    rt["probe_channel"]("qunar", scope="dom")
    assert st.paths, "自检路径必须写账"
    assert any(k.get("evidence_level") == "probe" for k in st.paths)
    assert any(str(k.get("attempt_id", "")).startswith("probe-")
               for k in st.paths)


def test_trace_api_exposes_interventions():
    import inspect
    import webui
    src = inspect.getsource(webui)
    assert "interventions" in src
    assert "干预记录" in src


def test_hysteresis_requires_consecutive_success():
    """一次成功不清 strikes；连续两次才复位（避免冷却/恢复横跳）。"""
    from core.pacing import Pacing
    p = Pacing({})
    p.trip("qunar", "ip")
    assert p.snapshot()["channels"]["qunar"]["strikes"] == 1
    p.note_ok("qunar")
    ch = p.snapshot()["channels"]["qunar"]
    assert ch["strikes"] == 1, "单次成功不得清零"
    assert ch["ok_streak"] == 1
    p.note_ok("qunar")
    ch2 = p.snapshot()["channels"]["qunar"]
    assert ch2["strikes"] == 0 and ch2["state"] == "ok"
    assert ch2["ok_streak"] == 2


def test_success_clears_soft_warmup_flag_immediately():
    from core.pacing import Pacing
    p = Pacing({})
    p.observe("qunar", ["soft_reject"], "")
    assert p.snapshot()["channels"]["qunar"]["needs_warmup"] is True
    p.note_ok("qunar", need=2)
    ch = p.snapshot()["channels"]["qunar"]
    assert ch["needs_warmup"] is False
    assert ch["reason"] == ""


def test_degradation_needs_valid_samples():
    """4 次售罄 + 1 次拦截：有效样本不足，不判路径劣化。"""
    from core.pacing import advise
    paths = {"H5": {"attempts": 5, "ok": 0, "empty": 4, "blocked": 1,
                    "rate": 0.0, "rate_excl_empty": 0.0}}
    a = advise("qunar", {"state": "ok"}, paths)
    assert a["state"] != "path_degraded"
    # 有效样本够时仍要判
    paths2 = {"H5": {"attempts": 8, "ok": 0, "empty": 1, "blocked": 7,
                     "rate": 0.0, "rate_excl_empty": 0.0}}
    b = advise("qunar", {"state": "ok"}, paths2)
    assert b["state"] == "path_degraded"


def test_manual_reset_verifies_and_reports_truth():
    """解除熔断不能直接记成功：必须带真实单查结果。"""
    import inspect
    import webui
    src = inspect.getsource(webui._Handler._do_POST)
    block = src.split('self.path == "/api/pacing/reset"', 1)[1][:3000]
    assert "probe_cb" in block, "解除后必须触发单查"
    assert 'save_intervention(' in block
    assert '"reset", ok=_ok' in block, "干预账的 ok 必须来自探测结果"
    assert '"probe": _probe' in block, "响应里要带回探测结果"


def test_round_coverage_counts_channel_level_success():
    """一轮成功率 = 有数渠道 / 实发渠道，与路径行数无关。"""
    from core.pacing import Pacing
    p = Pacing({})
    p.begin_round("19:00:00")
    for c in ("ctrip", "qunar", "tunghu"):
        p.note_sent(c)
    p.note_result("ctrip", ok=True)
    p.note_result("qunar", ok=False, soft=True)
    cov = p.snapshot()["coverage"]
    assert cov["ok"] == ["ctrip"]
    assert cov["soft"] == ["qunar"]
    assert cov["sent"] == ["ctrip", "qunar", "tunghu"]
    assert cov["ratio"] == round(1 / 3, 3)


def test_panel_shows_coverage_row():
    import inspect
    import webui
    src = inspect.getsource(webui)
    assert "本轮覆盖率" in src


def test_intervention_effect_before_after(tmp_path):
    st = PriceStorage(str(tmp_path / "prices.db"))
    st.save_fetch_path("qunar", "SHA", "PEK", "2027-06-01", "H5", "blocked",
                       attempt_id="a1")
    st.save_intervention("qunar", "verify", ok=True, rows=10)
    st.save_fetch_path("qunar", "SHA", "PEK", "2027-06-01", "H5", "ok",
                       attempt_id="a2")
    eff = st.intervention_effect("qunar")
    assert eff["has"] is True and eff["action"] == "verify"
    assert eff["after"] is not None and eff["after_n"] >= 1
    # 无干预渠道如实返回 has=False
    assert st.intervention_effect("ctrip")["has"] is False


def test_effect_sql_bindings_are_consistent(tmp_path):
    """回归：曾因占位符数量不符抛 ProgrammingError。"""
    st = PriceStorage(str(tmp_path / "prices.db"))
    st.save_intervention("qunar", "reset", ok=False)
    st.save_fetch_path("qunar", "SHA", "PEK", "2027-06-01", "H5", "ok",
                       attempt_id="z1")
    eff = st.intervention_effect("qunar")
    assert eff["ok"] is True and "before" in eff and "after" in eff
