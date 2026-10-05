"""系统设置的业务层：组装表单元数据 + 生效值、校验并保存覆盖值、只读容量提示。

三条不可让的口径（都写在契约里，这里落实）：

1. **校验失败整次 400**，不做部分成功，未知键也 400 —— 静默忽略会让管理员以为改成了。
2. **secret 明文永不出接口**：只回 `configured` 与 `masked`，`value` 恒 null。
3. **`restart_required` 逐项如实**，界面据此说"已生效"还是"需重启后生效"。
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..core import runtime_settings
from ..core.settings_catalog import BY_KEY, FIELDS, GROUPS, validate
from ..repository.jobs import JobRepo
from ..repository.settings import SystemSettingRepo


def _mask(plain: str) -> str:
    """掩码：只露首尾各几个字符，够管理员确认"是不是这一个"。"""
    s = str(plain)
    if len(s) <= 8:
        return "••••"
    return f"{s[:3]}…{s[-4:]}"


class SystemSettingsService:

    def __init__(self) -> None:
        self.repo = SystemSettingRepo()
        self.jobs = JobRepo()

    # ── 读取 ──
    def view(self, db: Session) -> dict:
        """返回契约 SystemSettings：分组 + 每项元数据与当前生效值。"""
        overrides = self.repo.load_all(db)
        fields: list[dict] = []
        for f in FIELDS:
            overridden = f.key in overrides and overrides[f.key] is not None
            entry: dict = {
                "key": f.key,
                "group": f.group,
                "label": f.label,
                "description": f.description,
                "type": f.type,
                "unit": f.unit,
                "min": f.min,
                "max": f.max,
                "default": f.default_value(),
                "overridden": overridden,
                "source": "override" if overridden else "default",
                "restart_required": f.restart_required,
            }
            if f.is_secret:
                # 明文不出后端：value 恒 null，只给"配没配"和掩码
                raw = overrides.get(f.key)
                entry["value"] = None
                entry["configured"] = bool(raw)
                entry["masked"] = _mask(raw) if raw else None
            else:
                entry["value"] = overrides.get(f.key) if overridden else f.default_value()
                entry["configured"] = None
                entry["masked"] = None
            fields.append(entry)
        return {
            "groups": [dict(g) for g in GROUPS],
            "fields": fields,
            "updated_at": self.repo.last_updated_at(db),
        }

    # ── 保存 ──
    def save(self, db: Session, values: dict, operator_id: str | None) -> dict:
        """部分提交。values 里出现的键才动；值为 null 表示删除覆盖、回落默认。"""
        if not isinstance(values, dict):
            raise HTTPException(status_code=400, detail="values 必须是对象")
        if not values:
            raise HTTPException(status_code=400, detail="values 为空，没有要保存的改动")

        unknown = sorted(k for k in values if k not in BY_KEY)
        if unknown:
            # 拼错的键名必须报错，静默丢掉就是"你以为改了其实没改"
            raise HTTPException(
                status_code=400,
                detail=f"未知设置项：{'、'.join(unknown)}（可提交键见 GET /api/admin/settings）")

        # 先全部校验，通过才落库 —— 不做部分成功
        prepared: list[tuple[str, object | None]] = []
        errors: list[str] = []
        for key, raw in values.items():
            field = BY_KEY[key]
            ok, normalized, reason = validate(field, raw)
            if not ok:
                errors.append(reason or f"「{field.label}」取值不合法")
                continue
            prepared.append((key, normalized))
        if errors:
            raise HTTPException(status_code=400, detail="；".join(errors))

        for key, normalized in prepared:
            if normalized is None:
                self.repo.delete(db, key)
            else:
                self.repo.upsert(db, key, normalized, operator_id)
        return self.view(db)

    # ── 只读容量（任意身份，见 ADR-020 决策 7）──
    def capacity(self, db: Session) -> dict:
        """只暴露三个数：上限 + 当前在跑/排队。不给配置项清单、不给密钥状态。

        running/queued 一律按**生成通道（output）**计——max_concurrent_jobs 就是生成闸的上限，
        分母分子必须同口径；走快通道的草稿/修改不占这个额度、不进这里的计数（ADR-024）。
        """
        return {
            "max_concurrent_jobs": runtime_settings.max_concurrent_jobs(),
            "running": self.jobs.count_generation(db, "running"),
            "queued": self.jobs.count_generation(db, "queued"),
        }


settings_service = SystemSettingsService()
