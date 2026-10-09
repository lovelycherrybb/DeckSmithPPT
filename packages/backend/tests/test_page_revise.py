"""revise_pages（对话改稿）后端回归：kind 注册、入参校验、上下文/安全对、清洗、空输入。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.api.agent import _KINDS, _handle_revise_pages  # noqa: E402
from app.core.schemas import AgentExecuteRequest, AgentExecutePage  # noqa: E402
from app.agents.page_revise import _build_prompt, clean_pages, revise_pages_with_claude  # noqa: E402


def test_注册了_revise_pages():
    assert "revise_pages" in _KINDS


def test_缺_pages_或缺_指令_报400():
    with pytest.raises(HTTPException) as e:
        _handle_revise_pages(AgentExecuteRequest(kind="revise_pages", instruction="改红"), None)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _handle_revise_pages(
            AgentExecuteRequest(kind="revise_pages",
                                pages=[AgentExecutePage(index=1, content="<svg/>")]), None)
    assert e.value.status_code == 400


def test_handler_产出上下文与安全对():
    req = AgentExecuteRequest(kind="revise_pages", instruction="标题改红",
        pages=[AgentExecutePage(index=1, content="<svg><text>hi</text></svg>", name="01.svg")])
    instr, context, checks = _handle_revise_pages(req, None)
    assert instr == "标题改红"
    assert json.loads(context)[0]["index"] == 1
    assert len(checks) == 2  # 指令 + 1 页内容都过安全检查


def test_clean_pages_剥围栏并校验结构():
    src = [{"index": 1, "name": "01.svg", "content": '<svg><text fill="#111">hi</text></svg>'}]
    ok = '[{"index":1,"content":"<svg/>"}]'
    assert json.loads(clean_pages("```json\n" + ok + "\n```", src))[0]["content"] == "<svg/>"
    with pytest.raises(RuntimeError):
        clean_pages("not json", src)
    with pytest.raises(RuntimeError):
        clean_pages('{"index":1,"content":"x"}', src)  # 非数组


def test_补丁形态_应用后仍回传整页():
    src = [{"index": 1, "name": "01.svg",
            "content": '<svg><text fill="#111">hi</text><text fill="#111">ho</text></svg>'}]
    raw = '[{"index":1,"patches":[{"search":"fill=\\"#111\\">hi","replace":"fill=\\"#f00\\">hi"}]}]'
    out = json.loads(clean_pages(raw, src))
    assert out[0]["index"] == 1 and out[0]["name"] == "01.svg"
    assert 'fill="#f00">hi' in out[0]["content"]
    assert 'fill="#111">ho' in out[0]["content"]  # 未涉及的原样保留


def test_默认只换第一处_replace_all才全换():
    src = [{"index": 1, "name": "01.svg",
            "content": '<svg><text fill="#111">a</text><text fill="#111">b</text></svg>'}]
    one = json.loads(clean_pages(
        '[{"index":1,"patches":[{"search":"fill=\\"#111\\"","replace":"fill=\\"#00f\\""}]}]', src))[0]["content"]
    assert one.count('fill="#00f"') == 1 and 'fill="#111">b' in one
    allc = json.loads(clean_pages(
        '[{"index":1,"patches":[{"search":"fill=\\"#111\\"","replace":"fill=\\"#00f\\"","replace_all":true}]}]',
        src))[0]["content"]
    assert allc.count('fill="#00f"') == 2 and "#111" not in allc


def test_补丁失配与非法回传都抛错():
    src = [{"index": 1, "name": "01.svg", "content": "<svg><text>hi</text></svg>"}]
    with pytest.raises(RuntimeError, match="没匹配上"):
        clean_pages('[{"index":1,"patches":[{"search":"不存在的东西","replace":"x"}]}]', src)
    with pytest.raises(RuntimeError, match="不在待修改"):
        clean_pages('[{"index":9,"content":"<svg/>"}]', src)
    with pytest.raises(RuntimeError, match="多次"):
        clean_pages('[{"index":1,"content":"<svg/>"},{"index":1,"content":"<svg/>"}]', src)
    with pytest.raises(RuntimeError, match="为空"):
        clean_pages('[{"index":1,"content":"  "}]', src)
    with pytest.raises(RuntimeError, match="既没有"):
        clean_pages('[{"index":1}]', src)


def test_空输入直接抛错_不碰claude():
    with pytest.raises(ValueError):
        revise_pages_with_claude([], "改")
    with pytest.raises(ValueError):
        revise_pages_with_claude([{"index": 1, "content": "x"}], "   ")


def test_prompt_含指令页名与补丁协议():
    p = _build_prompt([{"index": 1, "name": "a.svg", "content": "<svg/>"}], "把标题改红")
    assert "把标题改红" in p and "a.svg" in p and "patches" in p and "逐字" in p
