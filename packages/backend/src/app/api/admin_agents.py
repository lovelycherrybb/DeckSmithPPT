"""Agent 设置路由（ADR-032）。

权限一律后端强制：全部端点走 `require_admin`，前端隐藏入口只是体验。
路径形状照契约 `docs/contracts/openapi.yaml`，本文件不做任何"字段名/agent 名"的假设——
可接受的 agent_id 与文件名都由注册表在 service 层校验，拼错的名字当场 400，不静默降级。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from ..core.auth import get_db, require_admin
from ..core.schemas import (
    AgentListOut,
    AgentProviderListOut,
    AgentProviderProfileIn,
    AgentProviderProfileOut,
    ProviderActivateResultOut,
    ProviderProbeResultOut,
    SettingsFileUpdateRequest,
    SettingsFileViewOut,
)
from ..service.agents import agent_settings_service

router = APIRouter()


@router.get("/api/admin/agents", response_model=AgentListOut)
def list_agents(db: Session = Depends(get_db), user=Depends(require_admin)):
    """已注册的 agent CLI 及其配置说明书（注册表遍历，不是写死的名单）。"""
    return agent_settings_service.list_agents(db)


@router.get("/api/admin/agents/{agent_id}/settings-file", response_model=SettingsFileViewOut)
def get_settings_file(agent_id: str, name: str, db: Session = Depends(get_db),
                      user=Depends(require_admin)):
    """读原始配置文件（凭据脱敏），并逐项说明每个值现在由谁说话。"""
    return agent_settings_service.read_file(db, agent_id, name)


@router.put("/api/admin/agents/{agent_id}/settings-file", response_model=SettingsFileViewOut)
def save_settings_file(agent_id: str, name: str, body: SettingsFileUpdateRequest,
                       db: Session = Depends(get_db), user=Depends(require_admin)):
    """写回配置文件：JSON 校验 → 时间戳备份 → 原子替换；托管键不许在这里改。"""
    return agent_settings_service.write_file(db, agent_id, name, body.raw,
                                             body.expected_sha256)


@router.get("/api/admin/providers", response_model=AgentProviderListOut)
def list_providers(db: Session = Depends(get_db), user=Depends(require_admin)):
    """供应商档案清单（凭据脱敏）。哪套生效只由档案表的 is_active 决定。"""
    return agent_settings_service.list_providers(db)


@router.post("/api/admin/providers", response_model=AgentProviderProfileOut,
             status_code=status.HTTP_201_CREATED)
def create_provider(body: AgentProviderProfileIn, db: Session = Depends(get_db),
                    user=Depends(require_admin)):
    """新建档案。允许存残缺草稿，但激活时会把缺口回报出来。"""
    return agent_settings_service.create_provider(db, body.model_dump(),
                                                  getattr(user, "user_id", None))


@router.put("/api/admin/providers/{provider_id}", response_model=AgentProviderProfileOut)
def update_provider(provider_id: str, body: AgentProviderProfileIn,
                    db: Session = Depends(get_db), user=Depends(require_admin)):
    """修改档案（部分提交）。改的正是生效中的那套时，立即重渲染配置文件。"""
    return agent_settings_service.update_provider(db, provider_id, body.model_dump(),
                                                  getattr(user, "user_id", None))


@router.delete("/api/admin/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(provider_id: str, db: Session = Depends(get_db),
                    user=Depends(require_admin)):
    """删除档案。生效中的档案拒删（409）——删掉正在用的那套不是"删一条记录"。"""
    agent_settings_service.delete_provider(db, provider_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/admin/providers/{provider_id}/activate",
             response_model=ProviderActivateResultOut)
def activate_provider(provider_id: str, db: Session = Depends(get_db),
                      user=Depends(require_admin)):
    """切为生效（并渲染进配置文件）。对下一个任务即生效，不需要重启容器。"""
    return agent_settings_service.activate_provider(db, provider_id,
                                                    getattr(user, "user_id", None))


@router.post("/api/admin/providers/{provider_id}/probe", response_model=ProviderProbeResultOut)
def probe_provider(provider_id: str, db: Session = Depends(get_db),
                   user=Depends(require_admin)):
    """对某套档案做一次网关连通探测。**会真发一次请求、消耗付费额度**，故只在管理员点击时跑。"""
    return agent_settings_service.probe_provider(db, provider_id)
