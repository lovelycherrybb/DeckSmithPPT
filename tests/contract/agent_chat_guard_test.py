"""模型对话入口的接线守卫（ADR-034）。

这个文件守的是四类"只写代码不看闸就会漂"的事，全部对应 ADR-034 的五条决策：

1. **契约与注册表双向相等**（规则 9）：契约声明了 `chat` 而后端没注册，界面点了就是
   "操作类型不支持"；后端注册了而契约没写，加的那条能力永远没人知道、也永远不进类型。
2. **`resume_id` 不是假控件**：契约声明了这个参数，就必须真的变成 AI 工具的命令行参数。
   这里不看代码字面，用假工具把真实 argv 抓出来对——"声明了但没人读"就是这么露出来的。
3. **仅管理员是真闸**：未登录 401、非管理员 403，都在真请求路径上打；
   同时反向钉一条——这道闸**不许**顺手把非管理员原有的改稿/拟题能力一起拦掉。
4. **界面说人话、不粉饰**（规则 11）：入口文案在位，且面向使用者的字符串里
   不许出现只有开发者才懂的词。

判据只钉"必须做到"，不钉实现写法：改文案要同步改这里，改命令行拼装也会红在这里。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BACKEND_SRC = REPO / "packages" / "backend" / "src"
FRONTEND = REPO / "packages" / "frontend" / "src" / "components" / "tabs"
for p in (str(BACKEND_SRC), str(REPO / "packages")):
    if p not in sys.path:
        sys.path.insert(0, p)

DOC = yaml.safe_load((REPO / "docs" / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))


# ── 1. 契约 ↔ 后端注册表：两个方向都要相等 ──

def _contract_kinds() -> set[str]:
    return set(DOC["components"]["schemas"]["AgentExecuteRequest"]["properties"]["kind"]["enum"])


def test_kind_enum_and_registry_are_equal_both_ways():
    """漏注册（契约有、后端没有）和漏写契约（后端有、契约没有）都必须红。"""
    from app.api.agent import _KINDS

    contract = _contract_kinds()
    code = set(_KINDS)
    assert contract == code, (
        f"契约与后端注册表不等：只在契约里={sorted(contract - code)}，"
        f"只在代码里={sorted(code - contract)}")


def test_chat_is_declared_as_admin_only_in_contract():
    """契约里那句"仅管理员"是界面与权限的唯一说明处，删了等于文档失联。"""
    desc = DOC["components"]["schemas"]["AgentExecuteRequest"]["properties"]["kind"]["description"]
    assert "chat" in desc and "管理员" in desc


def test_resume_id_is_declared_in_contract():
    props = DOC["components"]["schemas"]["AgentExecuteRequest"]["properties"]
    assert "resume_id" in props, "契约没有 resume_id，界面就没有'接着上一轮说'的依据"
    assert "首轮不带" in props["resume_id"]["description"]


def test_stream_event_enum_covers_session_event():
    """`session` 事件是续接标识的唯一来路；契约的事件枚举必须列全实现真会发的类型。"""
    types = set(DOC["components"]["schemas"]["AgentSessionStreamEvent"]["properties"]["type"]["enum"])
    assert "session" in types
    # 实现里真发出来的类型（session_manager.append_output 的第二参）一个都不许漏
    from app.api import agent as agent_api
    emitted = set(re.findall(r'append_output\([^,]+,\s*[^,]+,\s*"(\w+)"\)',
                             Path(str(agent_api.__file__)).read_text(encoding="utf-8")))
    # ping 由 session_manager 内部发，progress/thinking/result/text/session/error/done 由本模块发
    assert emitted <= types, f"实现发出但契约没列：{sorted(emitted - types)}"


# ── 2. resume_id 真下发：抓 argv，不看代码字面 ──

class _FakeProc:
    def __init__(self, lines: list[str]):
        self.stdout = iter(lines)
        self.stderr = iter([])

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


def _run_stream(resume_id: str | None) -> tuple[list[str], list[dict]]:
    """用假工具替掉真子进程，返回（真实 argv，产出的事件列表）。"""
    from app.agents import claude_stream

    captured: list[str] = []
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "SESS-ABC"}),
        json.dumps({"type": "stream_event",
                    "event": {"type": "content_block_delta",
                              "delta": {"type": "text_delta", "text": "好的"}}}),
        json.dumps({"type": "result", "subtype": "success",
                    "result": "好的", "session_id": "SESS-ABC"}),
    ]

    def fake_popen(cmd, *a, **kw):
        captured.extend(cmd)
        return _FakeProc(lines)

    class FakeSubprocess:
        PIPE = "<pipe>"  # 真 subprocess 的管道哨兵，本模块按位置传参时会用到

        @staticmethod
        def Popen(cmd, *a, **kw):  # noqa: N802 - 与真 subprocess.Popen 同名
            return fake_popen(cmd, *a, **kw)

    original = claude_stream.subprocess
    claude_stream.subprocess = FakeSubprocess
    try:
        events = list(claude_stream.stream_claude_text("你好", resume_id=resume_id))
    finally:
        claude_stream.subprocess = original
    return captured, events


def test_resume_id_reaches_command_line_when_given():
    argv, events = _run_stream("SESS-PREV")
    assert "--resume" in argv, "契约声明了 resume_id 却没拼进命令行＝假控件"
    assert argv[argv.index("--resume") + 1] == "SESS-PREV"


def test_no_resume_flag_on_first_turn():
    """首轮不许凭空带 --resume：那会让第一句话接到一个不存在的对话上、当场失败。"""
    argv, _ = _run_stream(None)
    assert "--resume" not in argv


def test_session_event_carries_the_id_back():
    """续接标识必须有出口：界面拿不到它，"接着上一轮说"就永远做不到。"""
    _, events = _run_stream(None)
    sessions = [e["session"] for e in events if "session" in e]
    assert sessions == ["SESS-ABC"], f"session 事件异常：{sessions}"


def test_resume_failure_says_what_to_do_next():
    """接不上上文时不许糊成"请重试"——要说清下一步（规则 11 的去黑话不等于去信息）。"""
    from app.agents import claude_stream

    lines = [json.dumps({"type": "result", "subtype": "error_during_execution",
                         "session_id": "SESS-ABC"})]

    class FakeProc:
        stdout = iter(lines)
        stderr = iter([])

        def wait(self, timeout=None):
            return 1

        def poll(self):
            return 1

    class FakeSubprocess:
        PIPE = "<pipe>"

        @staticmethod
        def Popen(cmd, *a, **kw):
            return FakeProc()

    original = claude_stream.subprocess
    claude_stream.subprocess = FakeSubprocess
    try:
        events = list(claude_stream.stream_claude_text("继续", resume_id="GONE"))
    finally:
        claude_stream.subprocess = original
    errors = [e["error"] for e in events if "error" in e]
    assert errors and "新对话" in errors[0], f"失败句没给下一步：{errors}"


# ── 3. chat 是原话直给，且过安全检查 ──

def test_chat_handler_passes_message_verbatim_and_checks_it():
    from app.api.agent import _handle_chat
    from app.core.schemas import AgentExecuteRequest

    req = AgentExecuteRequest(kind="chat", prompt="用三句话说明你能做什么")
    instruction, context, checks = _handle_chat(req, None, None)
    assert instruction == "用三句话说明你能做什么", "对话不许被套进提示词模板"
    assert context == "", "服务端不该为对话另存上下文"
    assert [label for _, label in checks] == ["对话内容"], "对话内容必须进安全检查清单"


def test_chat_rejects_empty_message():
    from fastapi import HTTPException

    from app.api.agent import _handle_chat
    from app.core.schemas import AgentExecuteRequest

    with pytest.raises(HTTPException) as exc:
        _handle_chat(AgentExecuteRequest(kind="chat", prompt="   "), None, None)
    assert exc.value.status_code == 400


# ── 4. 仅管理员：真请求路径上打，且不许误伤别的 kind ──

def _identity(role: str | None):
    from app.core.auth import Identity
    return Identity(user=SimpleNamespace(role=role) if role else None, visitor_id="v-1")


def test_admin_gate_matrix_on_unit():
    from app.api.agent import _ADMIN_ONLY_KINDS, _guard_admin_only
    from app.core.schemas import AgentExecuteRequest

    assert "chat" in _ADMIN_ONLY_KINDS
    assert "modify_draft" not in _ADMIN_ONLY_KINDS, "把普通能力一起锁进管理员＝误伤"

    chat = AgentExecuteRequest(kind="chat", prompt="hi")
    with pytest.raises(Exception) as anon:
        _guard_admin_only(chat, _identity(None))
    assert anon.value.status_code == 401
    with pytest.raises(Exception) as plain:
        _guard_admin_only(chat, _identity("vip"))
    assert plain.value.status_code == 403
    _guard_admin_only(chat, _identity("manager"))  # 不抛＝放行


def test_admin_gate_is_on_the_real_request_path():
    """闸必须在真接口上生效：只在单测里自称拦住了，等于没拦。"""
    from fastapi.testclient import TestClient

    from app.api.agent import _get_db
    from app.core.auth import get_identity
    from app.main import app

    holder: dict[str, object] = {}
    app.dependency_overrides[_get_db] = lambda: None
    app.dependency_overrides[get_identity] = lambda: _identity(holder.get("role"))
    client = TestClient(app)

    def post(kind: str, **body):
        return client.post("/api/agent/execute-async",
                           json={"kind": kind, **body}, headers={"Origin": "http://testserver"})

    try:
        holder["role"] = None
        assert post("chat", prompt="hi").status_code == 401, "未登录也能发起对话＝闸没在路径上"

        holder["role"] = "vip"
        assert post("chat", prompt="hi").status_code == 403, "vip 也能发起对话＝只藏了按钮没设闸"

        # 反向一条：这道闸不许把 vip 原有的能力一起关掉
        resp = post("modify_draft", prompt="改一下")
        assert resp.status_code == 400, f"vip 的改稿能力被误伤：{resp.status_code}"

        # 管理员放行后立刻撞上参数校验（空话），证明闸开了、且不会真起 AI 工具
        holder["role"] = "manager"
        resp = post("chat", prompt="   ")
        assert resp.status_code == 400
        assert "要问的内容" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(_get_db, None)
        app.dependency_overrides.pop(get_identity, None)


# ── 5. 界面：入口在位、说人话、关掉即清空 ──

def _strip_js(text: str) -> str:
    """剥掉注释再查黑话：注释是写给维护者的，不算界面文案。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return re.sub(r"\s//.*$", "", text, flags=re.M)


#: AGENTS.md 规则 11 点名的开发者词
_JARGON = ["引擎", "下发", "落盘", "产物", "容器", "会话", "源码", "能力位", "终态",
           "回传", "脱敏", "掩码", "网关", "凭据", "CLI", "补丁", "档案"]


def test_chat_ui_strings_have_no_developer_jargon():
    for name in ("AgentSettingsPanel.tsx", "AgentChatPanel.tsx"):
        body = _strip_js((FRONTEND / name).read_text(encoding="utf-8"))
        hits = [w for w in _JARGON if w in body]
        assert not hits, f"{name} 的界面文案里有只有开发者才懂的词：{hits}"


def test_experience_copy_approved_on_2026_11_07_is_in_place():
    """体验改造五条口径（2026-11-07 龚成逐字批过）在位，且不许悄悄改回去。

    文案逐字钉：他要改字，得连这条一起改——防止"顺手改口语"或删掉说明。
    """
    chat = (FRONTEND / "AgentChatPanel.tsx").read_text(encoding="utf-8")
    for wording in (
        "AI 正在回答，可先写下一条，发送后自动排队",
        "已有一条在排队，等本轮结束",
        "已排队，本轮回答结束后自动发送",
        "撤回", "复制", "重试", "已复制", "思考过程",
    ):
        assert wording in chat, f"批过的界面文字不见了：{wording}"


def test_queue_holds_only_one_and_retry_uses_the_earlier_context():
    """两条设计口径：一次只排一条；重试＝退回那句话发出之前的上下文重问。"""
    chat = (FRONTEND / "AgentChatPanel.tsx").read_text(encoding="utf-8")
    # 已有排队那句时，再发直接返回（不是排成两条）
    assert "if (!text || queuedRef.current) return;" in chat, "排队上限那条不成立了"
    # 重试用的是那一轮记下的旧标识，不是当前标识
    assert re.search(r"void runTurn\(text, turn\.retryFrom \?\? null\);", chat), \
        "重试没按「那句话发出之前」的上下文走"
    # 思考过程默认收起：正文只在被点开时才渲染
    assert re.search(r"\{thinkingOpen\.has\(t\.id\) && \(", chat), "思考过程改成常开了"
    # 往上翻读前文时不许被拽回底部
    assert re.search(r"if \(box && stickToBottom\.current\)", chat), "自动滚底又变成无条件了"
    # 本轮收尾把排队那句交出去时，排队状态必须一起清掉——否则界面会挂着"已排队"却再也不会发
    handoff = re.search(r"const next = queuedRef\.current;\n(.*?)void runTurn\(next", chat, re.S)
    assert handoff, "找不到收尾接排的那段"
    assert "queuedRef.current = null" in handoff.group(1) and "setQueued(null)" in handoff.group(1), \
        "接排之后没清排队状态"


def test_entry_button_and_second_confirmation_are_in_place():
    """按钮与二次确认是 ADR-034 决策 3 的全部兑现处，删一条即不合格。"""
    panel = (FRONTEND / "AgentSettingsPanel.tsx").read_text(encoding="utf-8")
    assert "发起对话" in panel
    assert "开始对话" in panel, "二次确认的确认按钮不见了"
    assert "执行命令" in panel, "二次确认必须如实写明它能执行命令（决策 1：不粉饰能力面）"
    assert "消耗积分" in panel, "计费口径是积分，且必须提前告知"


def test_panel_clears_everything_on_close():
    """不留记录（决策 5）：关闭必须真的把该清的都清掉。

    判据锚在"清空那段做了什么"，不锚在具体某个 setState 的写法上——
    续接标识的清理挪进 `adopt(null)` 这种等价改写，不该让这条变红，
    但少清一样（对话、标识、排队那一句、展开状态）必须变红。
    """
    chat = (FRONTEND / "AgentChatPanel.tsx").read_text(encoding="utf-8")
    reset = re.search(r"const resetAll = useCallback\(\(\) => \{(.*?)\n  \}, \[", chat, re.S)
    assert reset, "找不到关闭时清空的那段"
    body = reset.group(1)
    assert "setTurns([])" in body, "对话内容没清"
    assert "adopt(null)" in body or "setResumeId(null)" in body, "续接标识没清"
    assert "queuedRef.current = null" in body and "setQueued(null)" in body, "排队中的那一句没清"
    assert "setThinkingOpen(new Set())" in body, "思考过程的展开状态没清"

    close = re.search(r"const close = \(\) => \{(.*?)\n  \};", chat, re.S)
    assert close and "resetAll()" in close.group(1), "关闭没有走清空这条路"


def test_contract_kinds_match_the_ui_copy():
    """界面能选的能力与契约声明的能力不许各说一套（规则 9 的三表相等在对话上的那一面）。"""
    api = (REPO / "packages" / "frontend" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    m = re.search(r"export type AgentKind = ([^;]+);", api)
    assert m, "api.ts 里找不到 AgentKind"
    ts_kinds = set(re.findall(r"'(\w+)'", m.group(1)))
    assert ts_kinds == _contract_kinds(), f"前端能力清单与契约不等：{sorted(ts_kinds ^ _contract_kinds())}"
