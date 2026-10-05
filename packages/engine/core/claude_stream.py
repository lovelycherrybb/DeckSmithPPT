"""claude stream-json 结果判定的共用工具。

第三方 Anthropic 兼容网关在超长输出中途掐连接时，会话里留下 "API Error: ..." 之类，
而 stream 的 `result` 事件**仍可能报 subtype=success**——只看 subtype 会把网络故障
误判成"agent 干完了"。三处引擎（ppt_master / ppt_master_v6 / guizang）共用本模块的
断流特征词与判定，避免各写一套、口径漂移（实测教训见 ADR-021 与 guizang 引擎注释）。
"""
from __future__ import annotations

# 网关/CLI 断流特征词（小写匹配）
API_ERROR_MARKERS: tuple[str, ...] = (
    "API Error", "terminated", "ECONNRESET", "socket hang up",
    "overloaded", "rate limit", "timed out", "timeout", "unable to connect",
    "enotfound", "econnrefused", "network",
)


def looks_like_api_error(text: str | None) -> bool:
    """输出/结果文本里是否出现断流特征词。"""
    if not text:
        return False
    low = text.lower()
    return any(m.lower() in low for m in API_ERROR_MARKERS)


def classify_result(result_event: dict, streamed_text: str = "") -> tuple[bool, str]:
    """判定一个 claude `result` 事件是否为"真的成功"。

    返回 (ok, note)：ok=True 表示可信的成功、note 为空；ok=False 时 note 是人读的失败原因。
    判失败的情形：subtype 非 success、is_error 为真，或结果/流式文本里出现断流特征词
    （后者正是网关谎报 success 的经典场景）。
    """
    subtype = result_event.get("subtype")
    is_error = bool(result_event.get("is_error"))
    body = str(result_event.get("result") or "")
    if subtype != "success" or is_error:
        return False, body or str(subtype or "") or "引擎执行失败"
    combined = f"{body}\n{streamed_text}"
    if looks_like_api_error(combined):
        hit = next((m for m in API_ERROR_MARKERS if m.lower() in combined.lower()), "")
        return False, f"疑似网关断流（命中特征 {hit!r}）；result 虽报 success 但不可信"
    return True, ""
