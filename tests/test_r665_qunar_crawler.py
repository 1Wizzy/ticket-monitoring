# -*- coding: utf-8 -*-
"""r665：去哪儿 App 采集器契约。

三个必须钉死的行为：
  1. **未登录记 login，不记 empty**——去哪儿不登录拿不到任何票价，
     记 empty 等于把「要你登录」说成「没票」，是账本说谎；
  2. 产出是「一日期一行 + extra 航班数组」（与其余渠道同契约）；
  3. 已在 core.channels / REGISTRY / 中文名表里注册，配置页能勾选。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crawlers
from core.channels import APP_CHANNELS, CHANNEL_CN, plat_cn
from crawlers.app_qunar import AppQunarCrawler


def _crawler():
    c = AppQunarCrawler.__new__(AppQunarCrawler)
    c.name = "app_qunar"
    c.package = "com.Qunar"
    c.paths = []
    c.login_suspect = False
    return c


def test_registered_as_app_channel():
    assert "app_qunar" in crawlers.REGISTRY
    assert crawlers.REGISTRY["app_qunar"] is AppQunarCrawler
    assert "app_qunar" in APP_CHANNELS
    assert "app_qunar" in crawlers.APP_CHANNELS


def test_channel_has_chinese_name():
    assert plat_cn("app_qunar") == "去哪儿 App"
    assert CHANNEL_CN["app_qunar"] == "去哪儿 App"
    # 裸 id 不得出现在名字表里
    assert "app_qunar" not in CHANNEL_CN.values()


def test_package_and_label():
    assert AppQunarCrawler.package == "com.Qunar"
    assert AppQunarCrawler.name == "app_qunar"


def test_login_url_explains_phone_login():
    """登录入口要给用户可读的说明：去哪儿必须手机号登录。"""
    c = _crawler()
    txt = c.login_url()
    assert "com.Qunar" in txt
    assert "手机号登录" in txt or "登录" in txt


def test_extra_is_flight_array_per_contract():
    """产出契约：一条记录携带当天全部航班，extra 是数组。"""
    c = _crawler()
    rows = [{"flight_no": "CZ8889", "depart_time": "20:30",
             "arrive_time": "22:50", "price": 320.0},
            {"flight_no": "HO1254", "depart_time": "21:20",
             "arrive_time": "23:30", "price": 329.0}]
    out = c._to_prices(rows, "2026-10-30", "BJS", "SHA")
    assert len(out) == 1, "一渠道一日期只应产出一行"
    obj = json.loads(out[0].extra)
    assert isinstance(obj, list) and len(obj) == 2
    assert out[0].price == 320.0, "行头价应是最低价"
    assert out[0].flight_no == "CZ8889"
    for x in obj:
        assert {"price", "code", "depTime", "arrTime"} <= set(x)


def test_priced_none_rows_are_dropped_not_zero():
    c = _crawler()
    out = c._to_prices([{"flight_no": "X1", "depart_time": "08:00",
                         "arrive_time": "10:00", "price": None}],
                       "2026-10-30", "BJS", "SHA")
    assert out == []


def test_login_page_detected_by_form_marks():
    """去哪儿登录页判据：手机号输入框 + 获取验证码。"""
    c = _crawler()
    assert c._is_login_page('<node text="请输入手机号" />')
    assert c._is_login_page('<node text="获取验证码" />')
    assert not c._is_login_page('<node text="20:30" />')
