"""前端/后端/契约三处状态集合的一致性守卫（ADR-018 消费侧口径）。

为什么要有这一条：`npx tsc` 只能保证 `Record<JobStatus, …>` 的映射穷尽，
保证不了**前端以为的终态**和**契约定义的终态**是同一批字符串。而"判是否终态"
这件事散在前端 9 处消费点上，历史上正是靠 `status !== 'running'` 表达 ——
引入 queued 之后那个写法就悄悄错了。集合比对是唯一能把它钉住的机器手段。

三处真源：
- 契约：`docs/contracts/openapi.yaml` 的 `components.schemas.JobStatus.enum`
- 后端：`app/service/jobs.py` 的 PENDING/TERMINAL/RETRYABLE_STATUSES 三个常量
- 前端：`src/lib/jobStatus.ts` 的同名三个常量
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
BACKEND_JOBS = REPO / "packages/backend/src/app/service/jobs.py"
FRONTEND_STATUS = REPO / "packages/frontend/src/lib/jobStatus.ts"
OPENAPI = REPO / "docs/contracts/openapi.yaml"

NAMES = ("PENDING_STATUSES", "TERMINAL_STATUSES", "RETRYABLE_STATUSES")


def _contract_enum() -> set[str]:
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return set(doc["components"]["schemas"]["JobStatus"]["enum"])


def _py_set(name: str) -> set[str]:
    """后端：`XXX_STATUSES = ("a", "b")` 形式的元组字面量。"""
    text = BACKEND_JOBS.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\s*=\s*\(([^)]*)\)", text, re.M | re.S)
    assert m, f"后端找不到 {name} 的定义（改名或删除会静默失去守卫）"
    return set(re.findall(r'"([a-z_]+)"', m.group(1)))


def _ts_set(name: str) -> set[str]:
    """前端：`export const XXX_STATUSES: readonly JobStatus[] = [...]`。"""
    text = FRONTEND_STATUS.read_text(encoding="utf-8")
    m = re.search(rf"export const {name}[^=]*=\s*\[([^\]]*)\]", text, re.S)
    assert m, f"前端找不到 {name} 的定义（改名或删除会静默失去守卫）"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_前后端三组集合逐一相等():
    for name in NAMES:
        assert _py_set(name) == _ts_set(name), (
            f"{name} 前后端不一致：后端 {sorted(_py_set(name))} vs 前端 {sorted(_ts_set(name))}"
        )


def test_契约枚举被未结束与终态完整覆盖():
    """非终态 ∪ 终态 == 契约全集。漏一个值 = 有一类任务两边都不认。"""
    enum = _contract_enum()
    assert _py_set("PENDING_STATUSES") | _py_set("TERMINAL_STATUSES") == enum, (
        f"两半并集与契约枚举不等：并集 {sorted(_py_set('PENDING_STATUSES') | _py_set('TERMINAL_STATUSES'))}"
        f" vs 契约 {sorted(enum)}"
    )


def test_非终态与终态互斥():
    assert not (_py_set("PENDING_STATUSES") & _py_set("TERMINAL_STATUSES")), "一个状态不该既在排队又在终态"


def test_可重试是未成功的终态():
    retryable = _py_set("RETRYABLE_STATUSES")
    assert retryable <= _py_set("TERMINAL_STATUSES"), "可重试必须是终态子集（跑到一半不许重试）"
    assert "completed" not in retryable, (
        "completed 不可重试：给已成功出片的历史任务开重试入口，等于开二次烧额度的口子"
    )
    assert retryable == {"failed", "timeout", "cancelled"}, "可重试集合的定义变了要同步契约与 UI"


def test_排队与取消只存在于任务态():
    """与 ADR-018 分层枚举同源：queued/cancelled 属编排层，引擎态里不该有它们。"""
    schema = __import__("json").loads((REPO / "docs/contracts/engine.schema.json").read_text(encoding="utf-8"))
    engine_states = set(schema["$defs"]["EngineJobStatus"]["enum"])
    assert engine_states <= _contract_enum(), "引擎态必须是任务态的子集"
    assert not (engine_states & {"queued", "cancelled"}), "引擎不产出排队/取消态"
