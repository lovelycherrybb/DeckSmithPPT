"""Agent 设置与供应商档案切换的回归（ADR-032）。

钉住的都是"只有真去读磁盘文件、真去点接口才会露"的行为，逐条对应决策：

1. **没有生效档案时一个字都不写**（决策 6 兼容底线）：升级完代码，现网出片路径不变。
2. **没填就删**（决策 6 + AGENTS.md 规则 10）：档案里清空的托管键必须从文件里消失，
   不留旧值——否则"我明明清掉了"却继续生效，是最难查的骗人。
3. **真源只有一个**：哪套生效由档案表 `is_active` 决定，互斥由**部分唯一索引**在库层掐死，
   不靠服务层"记得先清零"的纪律。
4. **明文不出接口**：整份响应里搜不到凭据；落盘是设计允许的（与 `.env` 同风险等级）。
5. **原始视图两条护栏**：托管键不许在那里改；掩码不许被当成新值写回磁盘。
6. **逐项如实显示来源**：档案 / 文件里的外来键 / `.env` 继承，三者分开报，不许静默生效。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.core.agent_registry import agent_ids, agent_spec  # noqa: E402
from app.models import AgentProvider, Base, make_engine, make_session_factory  # noqa: E402
from app.service.agents import AgentSettingsService  # noqa: E402

GATEWAY = {
    "base_url": "https://gw.example.com",
    "auth_token": "sk-gateway-token-abcdef123456",
    "model": "glm-4.6",
    "default_sonnet_model": "glm-4.6",
    "default_opus_model": "glm-4.6",
    "default_haiku_model": "glm-4-mini",
}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """把 CLI 配置目录指到沙箱：绝不写开发机的真实 ~/.claude。"""
    home = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    return home


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    yield session
    session.close()


@pytest.fixture
def svc(monkeypatch):
    service = AgentSettingsService()
    # 版本号探测会真起子进程（本机装了 claude，慢且不稳定），测试里钉死
    monkeypatch.setattr(service, "_detect_cli_version", lambda: "2.1.197")
    return service


def settings_file(cfg: Path) -> Path:
    return cfg / "settings.json"


def read_env(cfg: Path) -> dict:
    return json.loads(settings_file(cfg).read_text(encoding="utf-8")).get("env") or {}


# ───────────────── 1. 兼容底线：没生效档案就不写文件 ─────────────────

def test_没有任何生效档案时配置文件不存在(db, svc, cfg):
    svc.create_provider(db, {"agent_id": "claude_code", "name": "草稿",
                             "values": GATEWAY}, "admin")
    assert not settings_file(cfg).exists(), "只建档不启用，绝不能改磁盘"


def test_未注册agent名一律400不静默降级(db, svc):
    with pytest.raises(HTTPException) as err:
        svc.create_provider(db, {"agent_id": "codex_cli", "name": "x", "values": {}}, "admin")
    assert err.value.status_code == 400


# ───────────────── 2. 激活即渲染，留空即删除 ─────────────────

def test_激活后托管键真写进文件(db, svc, cfg):
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "公司网关",
                                   "values": GATEWAY}, "admin")
    res = svc.activate_provider(db, out["id"], "admin")
    env = read_env(cfg)
    assert env["ANTHROPIC_BASE_URL"] == GATEWAY["base_url"]
    assert env["ANTHROPIC_AUTH_TOKEN"] == GATEWAY["auth_token"]
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-4-mini"
    assert res["gaps"] == [] and res["restart_required"] is False
    assert res["written_files"][0]["path"].endswith("settings.json")
    # 没填的另一种凭据不许被写进去
    assert "ANTHROPIC_API_KEY" not in env


def test_档案里清空的项必须从文件里消失(db, svc, cfg):
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                   "values": GATEWAY}, "admin")
    svc.activate_provider(db, out["id"], "admin")
    assert "ANTHROPIC_AUTH_TOKEN" in read_env(cfg)

    cleared = {**GATEWAY, "auth_token": ""}
    svc.update_provider(db, out["id"], {"agent_id": "claude_code", "name": "A",
                                        "values": cleared}, "admin")
    assert "ANTHROPIC_AUTH_TOKEN" not in read_env(cfg), \
        "改了生效中的那套必须立刻重渲染，且留空的键要真删掉"


def test_文件里非托管内容原样保留(db, svc, cfg):
    settings_file(cfg).parent.mkdir(parents=True, exist_ok=True)
    settings_file(cfg).write_text(json.dumps({
        "env": {"SOME_OTHER": "keep"},
        "permissions": {"allow": ["Bash(git status)"]},
    }), encoding="utf-8")
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                   "values": GATEWAY}, "admin")
    svc.activate_provider(db, out["id"], "admin")
    doc = json.loads(settings_file(cfg).read_text(encoding="utf-8"))
    assert doc["permissions"]["allow"] == ["Bash(git status)"]
    assert doc["env"]["SOME_OTHER"] == "keep"
    assert doc["env"]["ANTHROPIC_MODEL"] == "glm-4.6"


def test_切换档案重写文件且只有一行生效(db, svc, cfg):
    a = svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                 "values": GATEWAY}, "admin")
    b_vals = {**GATEWAY, "base_url": "https://another.example.com", "model": "deepseek-chat",
              "default_sonnet_model": "deepseek-chat", "default_opus_model": "deepseek-chat",
              "default_haiku_model": "deepseek-chat"}
    b = svc.create_provider(db, {"agent_id": "claude_code", "name": "B",
                                 "values": b_vals}, "admin")
    svc.activate_provider(db, a["id"], "admin")
    res = svc.activate_provider(db, b["id"], "admin")
    assert read_env(cfg)["ANTHROPIC_BASE_URL"] == "https://another.example.com"
    assert res["active_provider_id"] == b["id"]
    listed = svc.list_providers(db)
    assert listed["active_provider_id"] == b["id"]
    assert sum(1 for p in listed["profiles"] if p["is_active"]) == 1


# ───────────────── 3. 互斥由库层掐死，不靠纪律 ─────────────────

def test_两行同时生效会被唯一索引拦下(db):
    now = "2026-11-02T00:00:00+00:00"
    db.add(AgentProvider(provider_id="p1", agent_id="claude_code", name="一",
                         values="{}", is_active=1, created_at=now, updated_at=now))
    db.add(AgentProvider(provider_id="p2", agent_id="claude_code", name="二",
                         values="{}", is_active=1, created_at=now, updated_at=now))
    with pytest.raises(IntegrityError):
        db.commit()


# ───────────────── 4. secret 明文不出接口 ─────────────────

def test_档案响应里搜不到凭据明文(db, svc):
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                   "values": GATEWAY}, "admin")
    blob = json.dumps(out, ensure_ascii=False)
    assert GATEWAY["auth_token"] not in blob
    assert out["values"]["auth_token"] is None
    assert out["secret_states"]["auth_token"]["configured"] is True
    assert out["secret_states"]["auth_token"]["masked"].startswith("sk-")

    listed = json.dumps(svc.list_providers(db), ensure_ascii=False)
    assert GATEWAY["auth_token"] not in listed


def test_两种凭据同时填被拒(db, svc):
    both = {**GATEWAY, "api_key": "sk-ant-othersecret"}
    with pytest.raises(HTTPException) as err:
        svc.create_provider(db, {"agent_id": "claude_code", "name": "A", "values": both}, "admin")
    assert err.value.status_code == 400
    assert "只能填一种" in err.value.detail


def test_未知字段被拒而不是静默丢弃(db, svc):
    with pytest.raises(HTTPException) as err:
        svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                 "values": {**GATEWAY, "typo_key": "x"}}, "admin")
    assert "未知字段" in err.value.detail


# ───────────────── 5. 走网关缺档位映射 → 激活时如实报缺口 ─────────────────

def test_网关档案缺档位映射时激活回报缺口(db, svc, cfg):
    partial = {k: v for k, v in GATEWAY.items() if k != "default_opus_model"}
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "缺档位",
                                   "values": partial}, "admin")
    res = svc.activate_provider(db, out["id"], "admin")
    assert "Opus 档位模型名" in res["gaps"]
    assert [p for p in svc.list_providers(db)["profiles"] if p["is_active"]][0]["gaps"]


# ───────────────── 6. 文件视图：脱敏 + 三类来源 ─────────────────

def test_文件视图掩码并列出托管外来与继承键(db, svc, cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://legacy.env.example.com")
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                   "values": GATEWAY}, "admin")
    svc.activate_provider(db, out["id"], "admin")
    # 手工塞一个本系统不管的 ANTHROPIC_ 键，模拟运维直接改过文件
    doc = json.loads(settings_file(cfg).read_text(encoding="utf-8"))
    doc["env"]["ANTHROPIC_CUSTOM_HEADER"] = "yes"
    settings_file(cfg).write_text(json.dumps(doc), encoding="utf-8")

    view = svc.read_file(db, "claude_code", "settings.json")
    assert GATEWAY["auth_token"] not in view["raw"]
    assert view["masked"] is True and "ANTHROPIC_AUTH_TOKEN" in view["masked_keys"]
    assert "ANTHROPIC_CUSTOM_HEADER" in view["foreign_env_keys"]
    assert set(view["managed_keys"]) == set(agent_spec("claude_code").env_keys)


def test_档案没填而env还在的项进告警(db, svc, cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "legacy-model")
    values = {k: v for k, v in GATEWAY.items() if k != "model"}
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "A", "values": values},
                              "admin")
    res = svc.activate_provider(db, out["id"], "admin")
    assert any("部署时的配置" in w and "ANTHROPIC_MODEL" in w for w in res["warnings"]), \
        "保留 .env 兼容的代价就是必须让管理员看见它还在说话"


def test_claude_json只读(db, svc, cfg):
    with pytest.raises(HTTPException) as err:
        svc.write_file(db, "claude_code", ".claude.json", "{}", None)
    assert err.value.status_code == 400
    files = {f["name"]: f for f in svc.list_agents(db)["agents"][0]["files"]}
    assert files[".claude.json"]["editable"] is False
    # 只读不等于不可见：仍然能读出来给管理员看
    assert svc.read_file(db, "claude_code", ".claude.json")["exists"] is False


# ───────────────── 7. 原始视图写回的两条护栏 + 原子性 ─────────────────

def test_托管键不许在原始视图里改(db, svc, cfg):
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                   "values": GATEWAY}, "admin")
    svc.activate_provider(db, out["id"], "admin")
    before = settings_file(cfg).read_text(encoding="utf-8")
    tampered = json.loads(before)
    tampered["env"]["ANTHROPIC_BASE_URL"] = "https://sneaky.example.com"
    with pytest.raises(HTTPException) as err:
        svc.write_file(db, "claude_code", "settings.json", json.dumps(tampered), None)
    assert err.value.status_code == 400
    assert settings_file(cfg).read_text(encoding="utf-8") == before, "被拒时磁盘必须一字不动"


def test_把掩码原样提交回来不许覆盖真凭据(db, svc, cfg):
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                   "values": GATEWAY}, "admin")
    svc.activate_provider(db, out["id"], "admin")
    view = svc.read_file(db, "claude_code", "settings.json")
    edited = json.loads(view["raw"])
    edited.setdefault("env", {})["SOME_OTHER"] = "keep"
    svc.write_file(db, "claude_code", "settings.json", json.dumps(edited), view["sha256"])
    assert read_env(cfg)["ANTHROPIC_AUTH_TOKEN"] == GATEWAY["auth_token"], \
        "视图是脱敏的，原样提交回来时必须还原成磁盘上的真值"


def test_并发修改被409拦下(db, svc, cfg):
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                   "values": GATEWAY}, "admin")
    svc.activate_provider(db, out["id"], "admin")
    view = svc.read_file(db, "claude_code", "settings.json")
    with pytest.raises(HTTPException) as err:
        svc.write_file(db, "claude_code", "settings.json",
                       json.dumps({"env": {"X": "1"}}), "deadbeef")
    assert err.value.status_code == 409
    assert svc.read_file(db, "claude_code", "settings.json")["sha256"] == view["sha256"]


def test_非法JSON提交被拒且原文件完好(db, svc, cfg):
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                   "values": GATEWAY}, "admin")
    svc.activate_provider(db, out["id"], "admin")
    before = settings_file(cfg).read_text(encoding="utf-8")
    with pytest.raises(HTTPException) as err:
        svc.write_file(db, "claude_code", "settings.json", "{ not json", None)
    assert err.value.status_code == 400
    assert settings_file(cfg).read_text(encoding="utf-8") == before


def test_磁盘上已是坏文件时不做增量改写(db, svc, cfg):
    settings_file(cfg).parent.mkdir(parents=True, exist_ok=True)
    settings_file(cfg).write_text("{ broken", encoding="utf-8")
    view = svc.read_file(db, "claude_code", "settings.json")
    assert view["exists"] is True and view["raw"] is None and view["parse_error"]
    with pytest.raises(HTTPException) as err:
        svc.write_file(db, "claude_code", "settings.json", json.dumps({"a": 1}), None)
    assert err.value.status_code == 400


def test_每次写入都留下带时间戳的备份(db, svc, cfg):
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "A",
                                   "values": GATEWAY}, "admin")
    svc.activate_provider(db, out["id"], "admin")
    values2 = {**GATEWAY, "model": "second-model"}
    svc.update_provider(db, out["id"], {"agent_id": "claude_code", "name": "A",
                                        "values": values2}, "admin")
    backups = list(cfg.glob("settings.json.decksmith-bak.*"))
    assert backups, "改生效中的档案必须留可回滚的现场"
    assert json.loads(backups[0].read_text(encoding="utf-8"))["env"]["ANTHROPIC_MODEL"] == "glm-4.6"


# ───────────────── 8. 删除护栏与注册表遍历 ─────────────────

def test_生效中的档案不许删(db, svc):
    out = svc.create_provider(db, {"agent_id": "claude_code", "name": "在用",
                                   "values": GATEWAY}, "admin")
    svc.activate_provider(db, out["id"], "admin")
    with pytest.raises(HTTPException) as err:
        svc.delete_provider(db, out["id"])
    assert err.value.status_code == 409
    svc.delete_provider(db, svc.create_provider(
        db, {"agent_id": "claude_code", "name": "草稿", "values": {}}, "admin")["id"])


def test_接口视图完全由注册表生成(db, svc):
    view = svc.list_agents(db)
    assert [a["id"] for a in view["agents"]] == list(agent_ids())
    for agent in view["agents"]:
        # 每个声明的字段都带环境变量落点，否则就是"界面能填但没人读"的幽灵参数
        assert all(f["env_var"] for f in agent["fields"])
        assert agent["cli_version"] == "2.1.197"
