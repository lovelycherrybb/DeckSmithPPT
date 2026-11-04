"""Agent 设置的接线守卫（ADR-032）：注册表 ↔ 磁盘文件 ↔ 契约，三处必须一一对得上。

这个文件守的是三类"只写代码不看闸就会漂"的事：

1. **不许有幽灵参数**：注册表声明了某个字段能配，就必须真的写进生效文件；
   反过来文件里出现的托管键必须每一个都来自注册表。少一边就是"界面能填但没人读"
   或"凭空多出一个谁也说不清来源的值"。
2. **不许在注册表之外写死 agent 名或环境变量名**（ADR-026 同一条纪律）：
   写死一处，加一个 agent 或改一个变量名就漏一处，而且漏的那处永远绿。
3. **契约与实现双向相等**：契约声明的管理端接口必须真有实现，
   实现里长出来的管理端接口必须都写进契约——单向检查只会漏掉后半句。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BACKEND_SRC = REPO / "packages" / "backend" / "src"
for p in (str(BACKEND_SRC), str(REPO / "packages")):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.core.agent_registry import AGENTS, FIELD_TYPES, agent_spec  # noqa: E402
from app.main import app  # noqa: E402
from app.service.agents import AgentSettingsService  # noqa: E402

DOC = yaml.safe_load((REPO / "docs" / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))

#: 注册表里那个 agent 的档案全填值（字段名取自注册表，本文件不写死任何环境变量名）
FULL_VALUES = {
    "base_url": "https://gw.example.com",
    "api_key": "",
    "auth_token": "sk-super-secret-token-987654",
    "model": "some-model",
    "default_sonnet_model": "some-model",
    "default_opus_model": "some-model-max",
    "default_haiku_model": "some-model-mini",
}


@pytest.fixture
def env_case(tmp_path, monkeypatch):
    """一整套"沙箱配置目录 + 临时库 + 服务"，返回 (spec, env_dict, 服务, db, 配置文件)。"""
    from app.models import Base, make_engine, make_session_factory

    cfg = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    for key in list(os.environ):
        if key.startswith("ANTHROPIC_"):
            monkeypatch.delenv(key, raising=False)

    engine = make_engine(f"sqlite:///{tmp_path / 'guard.db'}")
    Base.metadata.create_all(engine)
    db = make_session_factory(engine)()
    service = AgentSettingsService()
    monkeypatch.setattr(service, "_detect_cli_version", lambda: "2.1.197")
    spec = AGENTS[0]
    yield spec, service, db, cfg / "settings.json"
    db.close()


def _read_env(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("env") or {}


# ───────────────── 1. 声明的字段必须真的生效 ─────────────────

def test_注册表声明的每个字段都有落点(env_case):
    spec, service, db, file_path = env_case
    values = {k: v for k, v in FULL_VALUES.items() if v}
    created = service.create_provider(
        db, {"agent_id": spec.id, "name": "全填", "values": values}, "admin")
    service.activate_provider(db, created["id"], "admin")

    env = _read_env(file_path)
    # 填了值就必须落地（漏一边＝"界面能填但没人读"的幽灵参数）；
    # 落地了却没人声明也不行（＝谁也说不清来源的野键）
    expected = {f.env_var for f in spec.fields if values.get(f.key)}
    assert expected == set(env), (
        f"注册表与生效文件必须逐项相等。只在注册表：{sorted(expected - set(env))}；"
        f"只在文件：{sorted(set(env) - expected)}")
    # 值也必须真对得上，不能只比键名
    for field in spec.fields:
        if values.get(field.key):
            assert env[field.env_var] == values[field.key]


def test_留空的字段必须从文件里消失而不是留着旧值(env_case):
    spec, service, db, file_path = env_case
    created = service.create_provider(
        db, {"agent_id": spec.id, "name": "全填",
             "values": {k: v for k, v in FULL_VALUES.items() if v}}, "admin")
    service.activate_provider(db, created["id"], "admin")
    assert _read_env(file_path)

    # 部分提交口径下"清空"必须显式传空串（不传＝不改），这里就是按界面会发的那种请求测
    cleared = {f.key: (FULL_VALUES[f.key] if f.key == "base_url" else "")
               for f in spec.fields}
    service.update_provider(
        db, created["id"], {"agent_id": spec.id, "name": "只留地址", "values": cleared}, "admin")
    env = _read_env(file_path)
    assert set(env) == {f.env_var for f in spec.fields if f.key == "base_url"}, \
        "档案里显式清空的项必须真的从文件里拿掉（同 AGENTS.md 规则 10 的口径）"


def test_每个凭据字段都自带认证头声明(env_case):
    """探针靠这条声明发头；漏声明就会静默退回某一种头 —— 那是最难归因的 401。"""
    spec = env_case[0]
    for field in spec.fields:
        if field.type == "secret":
            assert field.auth_header, f"凭据字段「{field.label}」没声明认证头"
        else:
            assert not field.auth_header, f"非凭据字段「{field.label}」不该带认证头"


# ───────────────── 2. 注册表之外不许写死 ─────────────────

def test_后端源码不得在注册表之外写死agent名或变量名():
    registry = (BACKEND_SRC / "app" / "core" / "agent_registry.py").resolve()
    offenders: list[str] = []
    for path in sorted(BACKEND_SRC.rglob("*.py")):
        if path.resolve() == registry or "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bANTHROPIC_[A-Z_]+\b", line) or re.search(r"[\"']claude_code[\"']", line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()[:80]}")
    assert not offenders, (
        "这些行在注册表之外写死了 agent 名或环境变量名，加一项就会漏一处：\n" + "\n".join(offenders))


def test_注册表字段类型只能是契约声明的那几种():
    enum = set(DOC["components"]["schemas"]["AgentField"]["properties"]["type"]["enum"])
    assert enum == set(FIELD_TYPES), f"注册表类型 {FIELD_TYPES} 与契约枚举 {sorted(enum)} 不相等"


def test_渲染路径下不许出现需重启才生效的字段(env_case):
    """本 ADR 的卖点是"改完下一个任务即生效"。哪天有人加了个必须重建镜像的字段，
    这条会逼他如实标 restart_required，而不是让它冒充热生效。"""
    for spec in AGENTS:
        for field in spec.fields:
            if field.restart_required:
                pytest.fail(f"{spec.id} 的「{field.label}」标了需重启，界面必须如实显示，"
                            f"同时本测试要重新确认这条承诺是否还成立")


# ───────────────── 3. 契约与实现双向相等 ─────────────────

def _impl_admin_routes() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/admin/"):
            continue
        for method in getattr(route, "methods", set()) or ():
            if method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                out.add((method.lower(), path))
    return out


def _contract_admin_routes() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for path, item in DOC["paths"].items():
        if not path.startswith("/api/admin/"):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            if method in item:
                out.add((method, path))
    return out


def test_契约声明的管理端接口都有实现():
    missing = _contract_admin_routes() - _impl_admin_routes()
    assert not missing, f"契约里有这些管理端接口，后端没有实现：{sorted(missing)}"


def test_后端长出的管理端接口都已写进契约():
    extra = _impl_admin_routes() - _contract_admin_routes()
    assert not extra, f"后端有这些管理端接口但契约没写：{sorted(extra)}"


@pytest.mark.parametrize("schema_name", [
    "AgentInfo", "AgentField", "SettingsFileMeta", "SettingsFileView",
    "SettingsFileUpdate", "AgentProviderProfile", "AgentProviderProfileIn",
    "AgentProviderList", "ProviderActivateResult", "ProviderProbeResult",
])
def test_出参模型的字段与契约一一对应(schema_name):
    """契约写了实现没有的字段 = 契约在说谎；实现长了契约没有的字段 = 偷偷扩面。

    本仓库的 api_contract_test 只挑"访客也能读的只读接口"做真实响应形状校验，
    管理端接口进不了那个集合（要登录、要权限），所以这里的静态比对是它们唯一的外科检查。
    """
    from app.core import schemas

    contract_props = set(DOC["components"]["schemas"][schema_name].get("properties") or {})
    model = getattr(schemas, {
        "AgentInfo": "AgentInfoOut",
        "AgentField": "AgentFieldOut",
        "SettingsFileMeta": "SettingsFileMetaOut",
        "SettingsFileView": "SettingsFileViewOut",
        "SettingsFileUpdate": "SettingsFileUpdateRequest",
        "AgentProviderProfile": "AgentProviderProfileOut",
        "AgentProviderProfileIn": "AgentProviderProfileIn",
        "AgentProviderList": "AgentProviderListOut",
        "ProviderActivateResult": "ProviderActivateResultOut",
        "ProviderProbeResult": "ProviderProbeResultOut",
    }[schema_name])
    impl_props = set(model.model_fields)
    assert contract_props == impl_props, (
        f"{schema_name} 契约有但实现没有：{sorted(contract_props - impl_props)}；"
        f"实现有但契约没有：{sorted(impl_props - contract_props)}")


def test_agent注册表与接口视图同源(env_case):
    """接口回的元素必须逐个来自注册表遍历——本测试用注册表反推，写死就红。"""
    spec, service, db, _ = env_case
    view = service.list_agents(db)
    assert [a["id"] for a in view["agents"]] == [s.id for s in AGENTS]
    for agent in view["agents"]:
        declared = agent_spec(agent["id"])
        assert [f["key"] for f in agent["fields"]] == [f.key for f in declared.fields]
        assert [f["name"] for f in agent["files"]] == [f.name for f in declared.files]
