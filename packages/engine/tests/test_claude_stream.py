"""claude_stream 判定器回归：识破网关谎报 success 的断流。"""
from __future__ import annotations

import sys
from pathlib import Path

_PACKAGES = Path(__file__).resolve().parents[2]
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))

from engine.core.claude_stream import classify_result, looks_like_api_error  # noqa: E402


def test_干净成功判_ok():
    ok, note = classify_result({"type": "result", "subtype": "success", "is_error": False}, "正常输出")
    assert ok is True and note == ""


def test_is_error_判失败():
    ok, note = classify_result({"subtype": "success", "is_error": True, "result": "限流了"}, "")
    assert ok is False and "限流了" in note


def test_非_success_subtype_判失败_带原文():
    ok, note = classify_result({"subtype": "error_during_execution", "result": "模型限流"}, "")
    assert ok is False and "模型限流" in note


def test_谎报_success_但结果里有断流特征():
    # 正是第三方网关断流：subtype=success，但 result 文本露馅
    ok, note = classify_result(
        {"subtype": "success", "is_error": False,
         "result": "API Error: Unable to connect to API (ENOTFOUND)"}, "")
    assert ok is False and "断流" in note


def test_谎报_success_但流式文本里有特征():
    ok, note = classify_result(
        {"subtype": "success", "is_error": False, "result": ""},
        "前面正常输出…… API Error: terminated")
    assert ok is False


def test_markers_命中判定():
    assert looks_like_api_error("got ECONNRESET") is True
    assert looks_like_api_error("一切正常") is False
    assert looks_like_api_error(None) is False
