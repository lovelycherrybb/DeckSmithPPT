"""启用供应商档案的顺序：先写配置文件、后改生效状态（ADR-032 补强）。

起因（2026-11-04 实测）：逐页修改后导出一直失败，界面错误句子说"模型名称不认识"，
查下去发现 `~/.claude/settings.json` 里的模型名是 `glm-4.6`——一个不属于任何现存档案的值。
根因是启用顺序反了：原先先 `repo.activate()`（把"这套生效"记进库）再 `_apply_profile()`
（渲染写文件），而后者在"现有文件不是合法 JSON"时直接 raise 400 退出。于是库里已经说
"新档案生效"、文件里仍是上一个供应商的值，两边各说一套；界面上那一行显示「使用中」，
用户与运维都看不出异常，只会看到生成任务反复失败。

这三条测试钉住：
1. 写文件失败 → 状态必须一步都不走（旧顺序会在这一条上红）；
2. 正常启用 → 状态与文件必须同时变，不许只改一边（对照组，防"永远红"式假判据）；
3. 失败句子必须仍给出可做的下一步，不许退回裸异常。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for _p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.models import AgentProvider, Base, make_engine, make_session_factory  # noqa: E402
from app.service.agents import AgentSettingsService  # noqa: E402

OLD = {
    "base_url": "https://gw.example.com",
    "auth_token": "sk-old-token-abcdef123456",
    "model": "glm-4.6",
}
NEW = {
    "base_url": "https://api.deepseek.com/anthropic",
    "api_key": "sk-new-token-abcdef123456",
    "model": "deepseek-flash",
}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """CLI 配置目录指向沙箱，绝不写开发机的真实 ~/.claude。"""
    home = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return home


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'activate.db'}")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    yield session
    session.close()


@pytest.fixture
def svc(monkeypatch):
    service = AgentSettingsService()
    monkeypatch.setattr(service, "_detect_cli_version", lambda: "2.1.197")
    return service


def settings_file(cfg: Path) -> Path:
    return cfg / "settings.json"


def active_names(db) -> list[str]:
    return sorted(r.name for r in db.query(AgentProvider).filter(AgentProvider.is_active == True).all())  # noqa: E712


def make_pair(db, svc):
    a = svc.create_provider(db, {"agent_id": "claude_code", "name": "旧网关", "values": OLD}, "admin")
    b = svc.create_provider(db, {"agent_id": "claude_code", "name": "新直连", "values": NEW}, "admin")
    return a, b


def test_配置文件损坏时启用整件事不生效(db, svc, cfg):
    """核心判据：写文件失败，就一步都不许改状态。

    旧顺序下这条会红——库里已记成「新直连」生效，文件却还是旧网关的值，
    两边各说一套，界面上只看得见「使用中」。
    """
    a, b = make_pair(db, svc)
    svc.activate_provider(db, a["id"], "admin")
    assert settings_file(cfg).exists(), "前置：正常启用应当写出文件"

    settings_file(cfg).write_text("{ 这不是合法的 JSON", encoding="utf-8")

    with pytest.raises(HTTPException) as err:
        svc.activate_provider(db, b["id"], "admin")
    assert err.value.status_code == 400

    # 生效状态必须原地不动，且文件保持损坏原样（系统不许偷偷修复）
    assert active_names(db) == ["旧网关"], "写文件失败却把生效档案改了＝状态与文件脱节"
    assert settings_file(cfg).read_text(encoding="utf-8").startswith("{ 这不是")


def test_正常启用时状态与文件一起变(db, svc, cfg):
    """对照组：判据不许只会在坏情况下红——正常路径必须两边同步落地。"""
    a, b = make_pair(db, svc)
    svc.activate_provider(db, a["id"], "admin")
    svc.activate_provider(db, b["id"], "admin")

    env = json.loads(settings_file(cfg).read_text(encoding="utf-8")).get("env") or {}
    assert env.get("ANTHROPIC_MODEL") == NEW["model"], "文件没跟着换成新档案的值"
    assert env.get("ANTHROPIC_BASE_URL") == NEW["base_url"]
    assert active_names(db) == ["新直连"], "库里生效档案没切过去"


def test_失败句子给出可做的下一步(db, svc, cfg):
    """拒绝句子必须让人知道该怎么办，不许退回裸异常文本。"""
    a, _b = make_pair(db, svc)
    svc.activate_provider(db, a["id"], "admin")
    settings_file(cfg).write_text("not json at all", encoding="utf-8")

    with pytest.raises(HTTPException) as err:
        svc.activate_provider(db, _b["id"], "admin")
    detail = str(err.value.detail)
    assert "不是可识别的 JSON" in detail
    assert "移出" in detail or "重新启用" in detail, f"这句没给下一步：{detail}"
