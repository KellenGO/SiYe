# -*- coding: utf-8 -*-
"""抖音搜索响应诊断字段：只断言"形状"，绝不包含任何凭据值。

这批字段是抖音"搜不到内容"时唯一的真凭实据（实测：登录正常 + 已带 msToken 时，
平台会返回 status_code=0、logid 有、data 为空的"软风控"响应）。
"""
from media_platform.douyin.client import DouYinClient


def _client() -> DouYinClient:
    """只测纯记录逻辑：不走浏览器、不发请求。"""
    client = DouYinClient.__new__(DouYinClient)
    client._last_xmst_present = True
    client._last_xmst_len = 172
    client.last_search_diag = {}
    return client


def test_record_search_diag_captures_shape_without_credential_values():
    client = _client()
    client._record_search_diag({
        "status_code": 0,
        "data": [],
        "has_more": 0,
        "cursor": 0,
        "extra": {"logid": "LOGID-VALUE-SHOULD-NOT-BE-STORED"},
    }, offset=0)

    diag = client.last_search_diag
    assert diag["status_code"] == 0
    assert diag["data_len"] == 0
    assert diag["has_more"] == 0
    assert diag["cursor_present"] is True
    assert diag["logid_present"] is True
    assert diag["xmst_present"] is True
    assert diag["xmst_len"] == 172
    # 只记"有没有"，不记值本身
    assert "LOGID-VALUE-SHOULD-NOT-BE-STORED" not in repr(diag)


def test_record_search_diag_marks_empty_xmst_and_missing_data():
    client = _client()
    client._last_xmst_present = False
    client._last_xmst_len = 0
    client._record_search_diag({"status_code": 0}, offset=0)

    assert client.last_search_diag["xmst_present"] is False
    assert client.last_search_diag["data_len"] == -1  # data 缺失（不是空数组）


def test_record_search_diag_tolerates_non_dict_response():
    client = _client()
    client._record_search_diag(["not", "a", "dict"], offset=0)
    assert client.last_search_diag == {"shape": "list"}
