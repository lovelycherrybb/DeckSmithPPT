"""Agent 设置的接口层验收（ADR-032）。

`tests/contract/api_contract_test.py` 只挑"访客也能读的只读接口"做真实响应形状校验，
管理端接口进不了那个集合（要登录、要权限）。所以这一层单独跑真请求，钉四件事：

1. 权限门是真的：未登录 401、非管理员 403 —— 前端隐藏入口不算防护。
2. 契约里的 8 个端点逐个打得通，且**响应形状就是契约声明的形状**（用 jsonschema 实校）。
3. 凭据明文不出接口：整份响应文本里搜不到它。
4. 生效链在真进程里成立：启用档案 → 磁盘上出现配置文件 → 文件里的值与档案一致。

这一条是"界面说的是真话"的下半场：不是我只在单测里自称写过了。
"""
from __future__ import annotations

import copy
import json
import os
import uuid

import jsonschema
import pytest
import yaml
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = yaml.safe_load((REPO / "docs" / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))

ADMIN_USER = "agent-admin-it"
ADMIN_PW = "pw-for-integration-test"
PLAIN_USER = "agent-plain-it"
PLAIN_PW = "pw-for-integration-test"

SECRET = "sk-integration-secret-123456"
GATEWAY_VALUES = {
    "base_url": "https://gw.example.com",
    "auth_token": SECRET,
    "model": "some-model",
    "default_sonnet_model": "some-model",
    "default_opus_model": "some-model-max",
    "default_haiku_model": "some-model-mini",
}


def _normalize_nullable(node):
    """与 api_contract_test 同一套归一化：3.0 的 nullable 在 3.1 里是联合类型。"""
    if isinstance(node, dict):
        for value in list(node.values()):
            _normalize_nullable(value)
        if node.pop("nullable", None) is True:
            type_ = node.get("type")
            if isinstance(type_, str):
                node["type"] = [type_, "null"]
    elif isinstance(node, list):
        for value in node:
            _normalize_nullable(value)
    return node


def _force_closed(node):
    if isinstance(node, dict):
        if node.get("type") == "object" or ("properties" in node and "type" not in node):
            node.setdefault("additionalProperties", False)
        for key, value in list(node.items()):
            if key != "additionalProperties":
                node[key] = _force_closed(value)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            node[index] = _force_closed(value)
    return node


SCHEMA_ROOT = _force_closed(_normalize_nullable(copy.deepcopy(DOC["components"])))


def _assert_shape(method: str, path: str, payload) -> None:
    """真实响应必须落在契约声明的形状内（多字段、少字段都算不合格）。"""
    op = DOC["paths"][path][method]
    media = op["responses"]["200"]["content"]["application/json"]
    schema = _normalize_nullable(copy.deepcopy(media["schema"]))
    jsonschema.validate(payload, {"allOf": [schema], "components": SCHEMA_ROOT})


@pytest.fixture(scope="module", autouse=True)
def agent_cfg(tmp_path_factory):
    """把 CLI 配置目录挪进沙箱——**必须 autouse**。

    这些测试真的会往磁盘写 settings.json。autouse 才能保证在本模块任何一个用例
    （包括先跑的启用动作）之前就把目录换掉，绝不误写开发机的真实 ~/.claude。
    """
    home = tmp_path_factory.mktemp("agent-cli-home") / ".claude"
    previous = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = str(home)
    yield home
    if previous is None:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
    else:
        os.environ["CLAUDE_CONFIG_DIR"] = previous


@pytest.fixture(scope="module")
def no_cli_probe():
    """版本探测会真起子进程（本机装了 claude，慢且不稳定），这里钉成固定返回值。"""
    from app.service.agents import agent_settings_service

    original = agent_settings_service._detect_cli_version
    agent_settings_service._detect_cli_version = lambda: "2.1.197-test"
    yield
    agent_settings_service._detect_cli_version = original


@pytest.fixture
def admin(client, no_cli_probe):
    response = client.post("/api/auth/login",
                           json={"username": ADMIN_USER, "password": ADMIN_PW})
    assert response.status_code == 200, response.text
    yield client
    client.post("/api/auth/logout")


@pytest.fixture(scope="module", autouse=True)
def seed_users(client):
    """自己造账号，不动播种出来的内置管理员——也就不需要知道它的口令。

    管理员直接用 ORM 落库：`UserRepo.create` 按设计只造普通/高级用户（管理员归 bootstrap），
    测试要一个 manager 身份，就按同一张表的形状自己插。
    """
    import uuid as _uuid

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.core.passwords import hash_password
    from app.models import User

    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    for username, password, role in (
        (ADMIN_USER, ADMIN_PW, "manager"),
        (PLAIN_USER, PLAIN_PW, "normal"),
    ):
        session = SessionLocal()
        try:
            exists = session.execute(
                select(User).where(User.username == username)).scalar_one_or_none()
            if exists is None:
                session.add(User(
                    user_id=_uuid.uuid4().hex, username=username, nickname=username,
                    role=role, status="enabled", password_hash=hash_password(password),
                    created_at=_now(), updated_at=_now(),
                ))
                session.commit()
        finally:
            session.close()
    yield


def _new_profile(client, name: str, values: dict) -> str:
    created = client.post("/api/admin/providers",
                          json={"agent_id": "claude_code", "name": name, "values": values})
    assert created.status_code == 201, created.text
    return created.json()["id"]


# ───────────────── 1. 权限门 ─────────────────

def test_未登录打不开agent设置(client):
    client.post("/api/auth/logout")  # 只清会话，访客 Cookie 留着不影响别的用例
    for path in ("/api/admin/agents", "/api/admin/providers"):
        response = client.get(path)
        assert response.status_code == 401, f"{path} 对未登录身份回了 {response.status_code}"


def test_非管理员打不开agent设置(client, no_cli_probe):
    login = client.post("/api/auth/login",
                        json={"username": PLAIN_USER, "password": PLAIN_PW})
    assert login.status_code == 200
    try:
        for path in ("/api/admin/agents", "/api/admin/providers"):
            response = client.get(path)
            assert response.status_code == 403, f"{path} 对普通人回了 {response.status_code}"
        # 写接口同样挡在门外，不是只挡读
        blocked = client.post("/api/admin/providers",
                              json={"agent_id": "claude_code", "name": "越权", "values": {}})
        assert blocked.status_code == 403
    finally:
        client.post("/api/auth/logout")


# ───────────────── 2. 形状与生效链 ─────────────────

def test_注册表接口形状符合契约(admin):
    body = admin.get("/api/admin/agents").json()
    _assert_shape("get", "/api/admin/agents", body)
    assert [a["id"] for a in body["agents"]] == ["claude_code"]
    agent = body["agents"][0]
    assert agent["cli_version"] == "2.1.197-test"
    assert agent["files"][1]["editable"] is False, ".claude.json 必须只读"


def test_档案全流程走接口且响应里搜不到凭据(admin):
    provider_id = _new_profile(admin, "接口验收档案", GATEWAY_VALUES)
    listed = admin.get("/api/admin/providers").json()
    _assert_shape("get", "/api/admin/providers", listed)
    assert SECRET not in json.dumps(listed, ensure_ascii=False)

    updated = admin.put(f"/api/admin/providers/{provider_id}",
                        json={"agent_id": "claude_code", "name": "接口验收档案改",
                              "values": {"model": "second-model"}}).json()
    assert updated["name"] == "接口验收档案改"
    assert updated["values"]["model"] == "second-model"

    activated = admin.post(f"/api/admin/providers/{provider_id}/activate")
    assert activated.status_code == 200, activated.text
    _assert_shape("post", "/api/admin/providers/{provider_id}/activate", activated.json())
    assert activated.json()["gaps"] == []
    assert activated.json()["restart_required"] is False


def test_启用后磁盘上的文件真等于档案(admin, agent_cfg):
    provider_id = _new_profile(admin, "落盘验收", GATEWAY_VALUES)
    admin.post(f"/api/admin/providers/{provider_id}/activate")

    file_path = agent_cfg / "settings.json"
    assert file_path.exists(), "启用档案必须真的写出配置文件"
    env = json.loads(file_path.read_text(encoding="utf-8"))["env"]
    assert env["ANTHROPIC_AUTH_TOKEN"] == SECRET, "文件是给 CLI 读的，这里必须是真的"
    assert oct(file_path.stat().st_mode)[-3:] == "600", "含凭据的文件不该给同机其他用户读"

    view = admin.get("/api/admin/agents/claude_code/settings-file",
                    params={"name": "settings.json"})
    assert view.status_code == 200, view.text
    _assert_shape("get", "/api/admin/agents/{agent_id}/settings-file", view.json())
    assert SECRET not in view.text, "出接口必须脱敏"
    assert "ANTHROPIC_AUTH_TOKEN" in view.json()["masked_keys"]


def test_原始视图改托管键被拒且文件完好(admin, agent_cfg):
    file_path = agent_cfg / "settings.json"
    before = file_path.read_text(encoding="utf-8")
    tampered = json.loads(before)
    tampered["env"]["ANTHROPIC_BASE_URL"] = "https://sneaky.example.com"
    rejected = admin.put("/api/admin/agents/claude_code/settings-file",
                         params={"name": "settings.json"},
                         json={"raw": json.dumps(tampered), "expected_sha256": None})
    assert rejected.status_code == 400, rejected.text
    assert file_path.read_text(encoding="utf-8") == before


def test_未知agent与未知文件名都报400而不是静默(admin):
    wrong_agent = admin.get("/api/admin/agents/codex_cli/settings-file",
                            params={"name": "settings.json"})
    assert wrong_agent.status_code == 400
    wrong_file = admin.get("/api/admin/agents/claude_code/settings-file",
                           params={"name": "nope.json"})
    assert wrong_file.status_code == 400


def test_生效中的档案删不掉草稿可以删(admin):
    active_id = _new_profile(admin, "在用", GATEWAY_VALUES)
    admin.post(f"/api/admin/providers/{active_id}/activate")
    refuse = admin.delete(f"/api/admin/providers/{active_id}")
    assert refuse.status_code == 409, refuse.text

    draft_id = _new_profile(admin, "草稿", {"base_url": "https://draft.example.com"})
    accept = admin.delete(f"/api/admin/providers/{draft_id}")
    assert accept.status_code == 204, accept.text
    assert admin.get("/api/admin/providers").json()["active_provider_id"] == active_id


def test_两种凭据同时填被400拒绝并说清原因(admin):
    response = admin.post("/api/admin/providers", json={
        "agent_id": "claude_code", "name": "混填",
        "values": {**GATEWAY_VALUES, "api_key": "sk-other-secret"}})
    assert response.status_code == 400
    assert "只能填一种" in response.json()["detail"]
    assert "sk-other-secret" not in response.text


def test_APIKey类凭据发的是xapikey头而不是Bearer(admin, monkeypatch):
    """两种凭据发不同的头，这条测另一种。

    只测 Bearer 的话，发错头不会红——而发错头在线上表现成"能连上但一直 401"，
    是最难归因的那类故障（deploy/.env.example:8-11 就是为它写的）。
    """
    import app.service.agents as agents_module

    provider_id = _new_profile(admin, "APIKey 档",
                               {**GATEWAY_VALUES, "auth_token": "", "api_key": SECRET})
    captured: dict[str, str] = {}

    class FakeResponse:
        status = 200

        def read(self, _limit):
            return b'{"id":"msg_1","content":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None, context=None):  # context：探针显式指定根证书库
        captured["x_api_key"] = request.get_header("X-api-key") or ""
        captured["authorization"] = request.get_header("Authorization") or ""
        return FakeResponse()

    monkeypatch.setattr(agents_module.urllib.request, "urlopen", fake_urlopen)
    result = admin.post(f"/api/admin/providers/{provider_id}/probe")
    assert result.status_code == 200, result.text
    assert captured["x_api_key"] == SECRET, "声明了 x-api-key 就必须发这个头"
    assert captured["authorization"] == "", "不该同时发两种认证头"
    assert SECRET not in result.text


def test_探针缺凭据时不发请求也不谎报花了额度(admin, monkeypatch):
    import app.service.agents as agents_module

    def explode(*args, **kwargs):  # 真发了网络请求就该在这里炸
        raise AssertionError("不该在没有凭据时发网络请求")

    monkeypatch.setattr(agents_module.urllib.request, "urlopen", explode)
    draft_id = _new_profile(admin, f"无凭据-{uuid.uuid4().hex[:6]}",
                            {"base_url": "https://gw.example.com", "model": "some-model"})
    result = admin.post(f"/api/admin/providers/{draft_id}/probe").json()
    _assert_shape("post", "/api/admin/providers/{provider_id}/probe", result)
    assert result["ok"] is False
    assert result["consumed_quota"] is False, "没打出去却报花了额度，是替系统撒谎"


def test_探测通过与否逐项回报且不带出明文(admin, monkeypatch):
    provider_id = _new_profile(admin, "可探测", GATEWAY_VALUES)

    class FakeResponse:
        status = 200

        def __init__(self, body):
            self._body = body

        def read(self, _limit):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    captured: dict[str, str] = {}

    def fake_urlopen(request, timeout=None, context=None):  # context：探针显式指定根证书库
        # urllib 会把请求头首字母大写，取头必须走 get_header，不能按小写字典键猜
        captured["auth"] = request.get_header("Authorization") or ""
        return FakeResponse(b'{"id":"msg_1","content":[{"type":"text","text":"ok"}]}')

    import app.service.agents as agents_module
    monkeypatch.setattr(agents_module.urllib.request, "urlopen", fake_urlopen)

    result = admin.post(f"/api/admin/providers/{provider_id}/probe")
    assert result.status_code == 200, result.text
    body = result.json()
    _assert_shape("post", "/api/admin/providers/{provider_id}/probe", body)
    assert body["ok"] is True and body["consumed_quota"] is True
    # 头是按注册表声明发的（Bearer），不是代码里写死变量名猜的
    assert captured["auth"].startswith("Bearer "), captured["auth"]
    assert SECRET not in result.text, "探针响应里不得回显凭据，包括它发出去的那个头"
