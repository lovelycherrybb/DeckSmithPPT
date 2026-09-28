"""设置项**生效值**的解析入口：读表优先、env 兜底（ADR-020 决策 1）。

这是全系统唯一允许问"现在闸门是多少"的地方。`core/config.py` 只负责读环境变量
（默认值），任何业务代码都不该再直接读 env 来决定行为——否则就会出现有的地方读 env、
有的地方读表，同一个值两个来源互相不知道对方存在，那是本决策最容易翻车的地方。

数据库会话在函数内延迟导入：`core.db` 依赖 `core.config` 与 `models`，
若本模块在顶层导入 `core.db` 就会形成环。

表还不存在（全新库尚未建表、单测只建了部分表）或读失败时，一律按"无覆盖"处理并
退回默认值——设置面坏掉不该把任务调度一起拖死。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .settings_catalog import BY_KEY

logger = logging.getLogger("decksmith.settings")


def load_overrides() -> dict[str, Any]:
    """一次读出全部覆盖值（key → 已解码的值）。任何异常都退化成空覆盖。"""
    try:
        from ..repository.settings import SystemSettingRepo
        from .db import SessionLocal
    except Exception:  # noqa: BLE001 - 导入不了就是还没准备好，按无覆盖跑
        return {}
    db = None
    try:
        db = SessionLocal()
        return SystemSettingRepo().load_all(db)
    except Exception as exc:  # noqa: BLE001 - 表缺失/库锁都不该让调用方崩
        logger.debug("读取系统设置覆盖值失败，按默认值运行：%s", exc)
        return {}
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


def effective(key: str) -> Any:
    """某个设置项当前的生效值。覆盖值不合法（例如后来收紧了边界）时退回默认并告警。"""
    field = BY_KEY.get(key)
    if field is None:
        raise KeyError(f"未注册的系统设置项：{key}")
    override = load_overrides().get(key)
    if override is None:
        return field.default_value()
    from .settings_catalog import validate
    ok, value, reason = validate(field, override)
    if not ok:
        logger.warning("设置项 %s 的覆盖值不合法（%s），按默认值运行", key, reason)
        return field.default_value()
    return value


def _number(key: str, cast) -> Any:
    """取数值型生效值；库里存了脏东西时退回默认，而不是让调度循环整拍异常。"""
    try:
        return cast(effective(key))
    except (TypeError, ValueError):
        return cast(BY_KEY[key].default_value())


# ── 类型化访问器（调用方不必记 key 字符串，也便于测试打桩）──

def max_concurrent_jobs() -> int:
    return max(1, _number("max_concurrent_jobs", int))


def max_unfinished_per_identity() -> int:
    return max(1, _number("max_unfinished_per_identity", int))


def scheduler_interval() -> float:
    return max(1.0, _number("scheduler_interval_seconds", float))


def job_timeout_seconds() -> int:
    return max(60, _number("job_timeout_seconds", int))


def decode(raw: str | None) -> Any:
    """存储列是 JSON 文本；解不出来按无覆盖处理。"""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
