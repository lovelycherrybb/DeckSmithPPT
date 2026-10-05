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
    ok = '[{"index":1,"content":"<svg/>"}]'
    assert json.loads(clean_pages("```json\n" + ok + "\n```"))[0]["content"] == "<svg/>"
    with pytest.raises(RuntimeError):
        clean_pages("not json")
    with pytest.raises(RuntimeError):
        clean_pages('{"index":1,"content":"x"}')  # 非数组


def test_空输入直接抛错_不碰claude():
    with pytest.raises(ValueError):
        revise_pages_with_claude([], "改")
    with pytest.raises(ValueError):
        revise_pages_with_claude([{"index": 1, "content": "x"}], "   ")


def test_prompt_含指令页名与json要求():
    p = _build_prompt([{"index": 1, "name": "a.svg", "content": "<svg/>"}], "把标题改红")
    assert "把标题改红" in p and "a.svg" in p and "JSON" in p
