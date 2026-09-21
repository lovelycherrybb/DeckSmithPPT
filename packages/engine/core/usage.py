"""任务级 token 消耗（token_usage）的取值、累加与派生规则。

契约见 docs/contracts/openapi.yaml 的 TokenUsage 与 docs/contracts/engine.schema.json
的 $defs.TokenUsage，口径决策见 docs/adr/ADR-016。

放在 core 供各引擎共用：四个引擎都是 `claude -p --output-format stream-json` 起子进程，
result 事件里 usage 的抽取与累加规则若各写一遍，resume 续跑的口径迟早写歪。

三条不变式：
1. 分项是 int 或 None。None 表示「引擎/网关未回报该项」，不是 0——本机走第三方兼容网关时
   cache_* 实测就是 null，把 null 记成 0 会让"这轮没吃缓存"的假象混进数字。
2. 四项全为 None 时整体为 None（未记录），绝不产出 total_tokens=0 的对象。
3. 累加时 None 按加法单位元处理且被已知值覆盖（首跑没报、续跑报了 cache_read，
   结果应保留 cache_read，而不是因为加过 None 就退回未知）。
"""

from __future__ import annotations

# 分项字段（顺序即详情展示顺序；与 CLI result.usage 的键一致）
USAGE_PARTS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def _as_int(value: object) -> int | None:
    """把上报值收敛成非负 int 或 None。

    网关可能给出 float、字符串、负数或缺字段；除 None 外只认非负整数，其余按未回报处理，
    避免脏值把派生总数带偏。bool 是 int 的子类，须先挡掉（True 记成 1 是假数据）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def usage_from_result(event: dict) -> dict | None:
    """从 claude stream-json 的 result 事件里取出 token_usage。

    result.usage 是整段会话的累计值（CLI 运行时构造处为 usage:this.totalUsage），
    不是末轮快照，所以一次任务内每个 result 事件只计一次、多个 result 之间做累加。

    Args:
        event: type=="result" 的事件 dict（或其等价物，含 usage 子字典）。

    Returns:
        TokenUsage 形态 dict（分项 + total_tokens）；四项都拿不到时返回 None。
    """
    raw = event.get("usage") if isinstance(event, dict) else None
    if not isinstance(raw, dict):
        return None
    return _build({key: _as_int(raw.get(key)) for key in USAGE_PARTS})


def add_usage(total: dict | None, incoming: dict | None) -> dict | None:
    """按分项累加一次上报（同一任务 --resume 续跑会产生多个 result）。

    未知项（None）不参与求和、也不覆盖已知项；两边都为 None 的分项保持 None。
    incoming 为 None（该次未上报）时原样返回 total，不抹掉已有数字。
    """
    if incoming is None:
        return total
    if total is None:
        return dict(incoming)
    merged: dict[str, int | None] = {}
    for key in USAGE_PARTS:
        known, extra = total.get(key), incoming.get(key)
        if known is None:
            merged[key] = extra
        elif extra is None:
            merged[key] = known
        else:
            merged[key] = known + extra
    return _build(merged)


def _build(parts: dict[str, int | None]) -> dict | None:
    """由分项派生 total_tokens；四项全缺则整体为 None（未记录）。"""
    known = [v for v in parts.values() if v is not None]
    if not known:
        return None
    return {**parts, "total_tokens": sum(known)}
