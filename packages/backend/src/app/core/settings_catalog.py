"""系统设置项注册表（ADR-020）：**设置面唯一的知识源**。

设计要点：这一张表就是 `GET /api/admin/settings` 返回的 `fields[]`。前端按它通用渲染，
**不写死任何键名**（与引擎参数 `params_schema` 同一套"描述即渲染"的原则）。
以后加设置项——包括 agent CLI 那一组——只在这里加一条 `SettingField`，
契约、后端服务、前端渲染器都不用改。

分组本期只有 `scheduler`。`agent` 分组待用户明确要放哪些键时再加，
加的时候顺带落地"调度器 fork 子进程时注入该子进程 env"的机制
（那是 agent 类配置能热生效的机制前提，见 ADR-020 决策 3）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from .config import (
    get_job_timeout_seconds,
    get_max_concurrent_jobs,
    get_max_unfinished_per_identity,
    get_scheduler_interval,
)

logger = logging.getLogger("decksmith.settings")

TYPES = ("int", "bool", "string", "secret")


@dataclass(frozen=True)
class SettingField:
    """一个设置项的元数据。字段名与契约 SystemSettingsField 一一对应。"""

    key: str
    group: str
    label: str
    type: str
    description: str
    #: 回落值来源（读环境变量或内置默认）。做成 callable 而不是常量，
    #: 是为了"运维改了 .env 但没人覆盖"时界面上的默认值也跟着变，不会显示一个过期数。
    default: Callable[[], Any]
    unit: str | None = None
    min: int | None = None
    max: int | None = None
    #: 改动是否需要重启容器才对**所有**在途任务生效。调度器每拍读取的项是 False。
    #: 这一项必须如实填：管理员改完看到"已生效"却在排队，比看到"需重启"更破坏信任。
    restart_required: bool = False

    @property
    def is_secret(self) -> bool:
        return self.type == "secret"

    def default_value(self) -> Any:
        """回落值。env 写超界时夹回注册表声明的区间并告警。

        为什么在这里夹：环境变量是运维手写的，可能给出比注册表更宽的值（实测踩过：
        `DECKSMITH_MAX_UNFINISHED_PER_IDENTITY=500` 而注册表上限 200）。若不夹，
        就会出现"生效值 500、而设置页的输入框最大只能填 200"这种两边各说各话，
        管理员一碰这个框反而把系统调严了。边界只有注册表一个真源。
        """
        try:
            value = self.default()
        except Exception:  # noqa: BLE001 - 默认值取不到不该让设置页整页 500
            return None
        if self.type == "int" and isinstance(value, int) and not isinstance(value, bool):
            if self.min is not None and value < self.min:
                logger.warning("设置项 %s 的默认值 %s 低于下限，按 %s 处理",
                               self.key, value, self.min)
                value = self.min
            if self.max is not None and value > self.max:
                logger.warning("设置项 %s 的默认值 %s 高于上限，按 %s 处理",
                               self.key, value, self.max)
                value = self.max
        return value


GROUPS: tuple[dict[str, str], ...] = (
    {
        "id": "scheduler",
        "label": "任务调度",
        "description": "同时生成几个任务、排队怎么限、多久轮询一次。改动对下一个任务即生效。",
    },
)

FIELDS: tuple[SettingField, ...] = (
    SettingField(
        key="max_concurrent_jobs",
        group="scheduler",
        label="同时生成任务数",
        type="int",
        unit="个",
        min=1,
        max=32,
        default=get_max_concurrent_jobs,
        # 刻意不提 claude：这道闸计的是"处于生成中的任务条数"，与引擎用什么实现无关。
        # 现在三个输出引擎恰好都靠 claude 子进程跑，将来接别的引擎（或本地模型、
        # 其他 agent CLI）时这句话依然成立。把实现细节写进语义里，换引擎就成假话。
        description="同一时刻允许处于生成中的任务条数，超出的任务在队列里等待。"
                    "调大会同时多吃机器内存与模型额度、也可能触发网关限流；调小则更多时间在排队。",
    ),
    SettingField(
        key="max_unfinished_per_identity",
        group="scheduler",
        label="单用户未完成任务上限",
        type="int",
        unit="个",
        min=1,
        max=200,
        default=get_max_unfinished_per_identity,
        description="同一个人（登录用户或访客）排队中 + 生成中的任务总数上限，超了提交会被拒。"
                    "用来防止一个人把队列占满。",
    ),
    SettingField(
        key="scheduler_interval_seconds",
        group="scheduler",
        label="调度轮询间隔",
        type="int",
        unit="秒",
        min=1,
        max=300,
        default=lambda: int(get_scheduler_interval()),
        description="调度器每隔多久认领一次任务、刷新一次状态。调小任务启动更快、空转更频繁；"
                    "调大更省资源，但排队任务被认领的延迟会变长。",
    ),
    SettingField(
        key="job_timeout_seconds",
        group="scheduler",
        label="单任务超时",
        type="int",
        unit="秒",
        min=60,
        max=86400,
        default=get_job_timeout_seconds,
        description="从任务真正开始生成算起，超过这个时间就判超时并终止其引擎子进程。"
                    "复杂 PPT 动辄十几分钟，不要设得太短。",
    ),
)

BY_KEY: dict[str, SettingField] = {f.key: f for f in FIELDS}


def validate(field: SettingField, raw: Any) -> tuple[bool, Any, str | None]:
    """校验并归一化一个提交值。返回 (是否合法, 归一化后的值, 人话原因)。

    刻意做得严格：越界、类型错、空串一律拒绝，错误文案直接给管理员看——
    "部分成功"或"静默夹到边界值"都会让人以为改成了而实际没有。
    """
    if raw is None:
        return True, None, None  # null = 恢复默认，由调用方删覆盖行

    if field.type == "bool":
        if isinstance(raw, bool):
            return True, raw, None
        return False, None, f"「{field.label}」需要 true/false"

    if field.type in ("string", "secret"):
        if not isinstance(raw, str) or not raw.strip():
            return False, None, f"「{field.label}」需要非空文本"
        return True, raw.strip(), None

    # int
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return False, None, f"「{field.label}」需要整数"
    try:
        value = int(str(raw).strip())
    except ValueError:
        return False, None, f"「{field.label}」需要整数"
    if field.min is not None and value < field.min:
        return False, None, f"「{field.label}」不能小于 {field.min}{field.unit or ''}"
    if field.max is not None and value > field.max:
        return False, None, f"「{field.label}」不能大于 {field.max}{field.unit or ''}"
    return True, value, None
