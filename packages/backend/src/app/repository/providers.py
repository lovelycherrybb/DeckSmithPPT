"""供应商档案的存取（ADR-032）。

只负责"库里怎么放"，不懂配置文件、不懂校验 —— 那些在 `service/agents.py`。
`is_active` 的互斥由表上的部分唯一索引兜底（见 models.AgentProvider），
本文件只提供"在一个事务里换生效行"的操作，不额外做"先查再写"的假防重。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from ..models import AgentProvider


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def decode_values(raw: str | None) -> dict:
    """values 列是 JSON 文本。解不出来当空档案处理并交给上层报错，绝不静默半生效。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class AgentProviderRepo:

    def list(self, db, agent_id: str | None = None) -> list[AgentProvider]:
        stmt = select(AgentProvider).order_by(AgentProvider.created_at)
        if agent_id:
            stmt = stmt.where(AgentProvider.agent_id == agent_id)
        return list(db.execute(stmt).scalars().all())

    def get(self, db, provider_id: str) -> AgentProvider | None:
        return db.execute(
            select(AgentProvider).where(AgentProvider.provider_id == provider_id)
        ).scalar_one_or_none()

    def active(self, db, agent_id: str) -> AgentProvider | None:
        return db.execute(
            select(AgentProvider).where(
                AgentProvider.agent_id == agent_id, AgentProvider.is_active == 1)
        ).scalar_one_or_none()

    def create(self, db, agent_id: str, name: str, values: dict,
               operator_id: str | None) -> AgentProvider:
        now = _now()
        row = AgentProvider(
            provider_id=f"prv_{uuid.uuid4().hex[:16]}",
            agent_id=agent_id,
            name=name,
            values=json.dumps(values, ensure_ascii=False),
            is_active=0,
            created_at=now,
            updated_at=now,
            updated_by=operator_id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def save_values(self, db, provider_id: str, name: str | None, values: dict,
                    operator_id: str | None) -> AgentProvider | None:
        row = self.get(db, provider_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        row.values = json.dumps(values, ensure_ascii=False)
        row.updated_at = _now()
        row.updated_by = operator_id
        db.commit()
        db.refresh(row)
        return row

    def activate(self, db, provider_id: str, operator_id: str | None) -> AgentProvider | None:
        """把该档案设为生效、同 agent 其余行清零 —— 全在一个事务里。

        分两条语句中途崩溃会留下"两套都生效"或"一套都没有"，那比报错更糟。
        """
        row = self.get(db, provider_id)
        if row is None:
            return None
        try:
            db.execute(
                update(AgentProvider)
                .where(AgentProvider.agent_id == row.agent_id,
                       AgentProvider.provider_id != row.provider_id)
                .values(is_active=0)
            )
            row.is_active = 1
            row.updated_at = _now()
            row.updated_by = operator_id
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(row)
        return row

    def delete(self, db, provider_id: str) -> bool:
        row = self.get(db, provider_id)
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True
