"""引擎运行前置条件的统一入口：遍历注册表调用可选钩子 `prepare_runtime`。

设计约束（ADR-012 决策 2）：**不出现任何 `if engine_name == ...`**。
能力探测复用 registry.implements_hook()（沿 MRO 判真实实现，Protocol 上的占位与
显式置 None 都不算实现），所以新增引擎只要在自己类里实现该钩子，本模块与
容器 entrypoint 一律无需改动。

三态语义见 engine.core.protocol.OutputEngine.prepare_runtime 文档：
    None      → 就绪
    str       → 可降级告警（记 warn，不影响启动）
    抛异常     → 硬缺失（记 fail，`python -m` 时以非零码退出）

两种消费方式：
    1) 后端启动（main.py lifespan）调用 prepare_all() 并把结果打进日志——只告警不拦停，
       避免开发环境因为缺个 claude 就整个起不来；
    2) 容器 entrypoint 执行 `python -m app.engines.runtime`——有硬缺失即非零退出，
       把"镜像装好了但跑起来才发现有引擎不可用"提前到启动期。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .factory import get_engine
from .registry import ENGINE_REGISTRY, implements_hook

RUNTIME_HOOK = "prepare_runtime"

logger = logging.getLogger("decksmith.engines.runtime")


@dataclass
class RuntimeCheck:
    """一次前置条件检查的结果。"""

    engine_names: tuple[str, ...]   # 同一个实现类可能注册了多个名字
    status: str                     # ok | notice | fail
    detail: str | None = None       # notice/fail 的人读说明


def prepare_all(job_dir: Path | None = None) -> list[RuntimeCheck]:
    """对注册表里每个**实现了钩子的**引擎调用 prepare_runtime，返回逐项结果。

    按**实现类对象**去重：`ppt_master` 与 `ppt_master_draft` 指向同一个类，
    不去重会把同一次检查跑两遍、并在日志里留下两条看起来像不同结论的记录。
    """
    # 注册名 -> 实现类 -> 该类的全部注册名
    groups: dict[type, list[str]] = {}
    broken: dict[str, str] = {}
    for name in ENGINE_REGISTRY:
        try:
            engine = get_engine(name)
        except Exception as exc:  # 装配本身失败：算硬缺失，不静默跳过
            broken[name] = f"引擎实例化失败：{exc}"
            continue
        groups.setdefault(type(engine), []).append(name)

    results: list[RuntimeCheck] = []
    for name, detail in sorted(broken.items()):
        results.append(RuntimeCheck((name,), "fail", detail))

    for engine_cls, names in groups.items():
        if not implements_hook(engine_cls, RUNTIME_HOOK):
            continue  # 未声明前置条件 = 无需准备，不计入结果
        engine = get_engine(names[0])
        try:
            notice = getattr(engine, RUNTIME_HOOK)(job_dir)
        except Exception as exc:
            results.append(RuntimeCheck(tuple(sorted(names)), "fail", str(exc)))
            continue
        results.append(
            RuntimeCheck(
                tuple(sorted(names)),
                "notice" if notice else "ok",
                notice or None,
            )
        )
    return sorted(results, key=lambda c: c.engine_names)


def report(job_dir: Path | None = None) -> int:
    """跑一遍预检、打日志并返回进程退出码（有 fail 即 1）。供 entrypoint / CLI 使用。"""
    checks = prepare_all(job_dir)
    for c in checks:
        label = "/".join(c.engine_names)
        if c.status == "ok":
            logger.info("前置条件就绪：%s", label)
        elif c.status == "notice":
            logger.warning("前置条件告警（可降级）：%s — %s", label, c.detail)
        else:
            logger.error("前置条件缺失（该引擎不可运行）：%s — %s", label, c.detail)
    if not checks:
        logger.info("没有引擎声明 prepare_runtime 钩子，跳过运行前置检查")
    return 1 if any(c.status == "fail" for c in checks) else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[runtime] %(levelname)s %(message)s")
    raise SystemExit(report())
