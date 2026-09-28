"""系统设置覆盖值的存取（ADR-020）。

只存"覆盖"：表里没有的键即表示走 env/内置默认。因此"恢复默认"就是删掉这一行，
不存在"存了一个恰好等于默认的值"这种歧义状态。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, select

from ..core.runtime_settings import decode
from ..models import SystemSetting


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SystemSettingRepo:

    def load_all(self, db) -> dict[str, object]:
        """全部覆盖值（key → 已解码值）。表里没有的行不出现。"""
        rows = db.execute(select(SystemSetting)).scalars().all()
        return {r.key: decode(r.value) for r in rows}

    def get(self, db, key: str) -> object | None:
        row = db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        ).scalar_one_or_none()
        return decode(row.value) if row else None

    def upsert(self, db, key: str, value: object, updated_by: str | None) -> SystemSetting:
        """写入/更新一个覆盖值。调用方负责先校验。"""
        row = db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        ).scalar_one_or_none()
        payload = json.dumps(value, ensure_ascii=False)
        if row is None:
            row = SystemSetting(key=key, value=payload, updated_by=updated_by,
                                updated_at=_now())
            db.add(row)
        else:
            row.value = payload
            row.updated_by = updated_by
            row.updated_at = _now()
        db.commit()
        return row

    def delete(self, db, key: str) -> bool:
        """删除覆盖 = 恢复默认。返回是否真删了一行。"""
        res = db.execute(delete(SystemSetting).where(SystemSetting.key == key))
        db.commit()
        return res.rowcount > 0

    def last_updated_at(self, db) -> str | None:
        row = db.execute(select(SystemSetting.updated_at).order_by(
            SystemSetting.updated_at.desc()).limit(1)).scalar_one_or_none()
        return row
