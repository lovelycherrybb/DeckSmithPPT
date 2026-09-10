"""Claude Code 流式输出：把 claude -p 的 stream-json 增量转成 SSE 事件。

claude CLI 流式参数：--output-format stream-json --include-partial-messages --verbose
事件结构：
  {"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}}
  {"type":"result","subtype":"success","result":"完整文本"}

本模块只负责"跑一次纯文本任务并把增量吐出来"，安全门禁/参数校验由调用方（路由层）负责。
"""
from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Iterator

_DEFAULT_TIMEOUT = 300


def stream_claude_text(
    prompt: str,
    claude_bin: str = "claude",
    timeout: int = _DEFAULT_TIMEOUT,
) -> Iterator[dict]:
    """运行 claude 并逐段产出事件。

    Yields:
        {"thinking": "..."}      思考增量（Claude 推理过程，先于正文）
        {"delta": "..."}         正文增量
        {"done": True, "text": "..."}  最终完整文本
        {"error": "..."}         失败原因（终止，附带 stderr 尾部便于排障）
    """
    try:
        proc = subprocess.Popen(
            [
                claude_bin, "-p",
                "--output-format", "stream-json",
                "--include-partial-messages",
                "--verbose",
                prompt,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # 不并入 stdout：CLI 噪音会混进 stream-json，非 JSON 行被静默丢弃
            text=True,
        )
    except FileNotFoundError:
        yield {"error": f"未找到 claude CLI（{claude_bin}），请确认已安装 Claude Code"}
        return

    stderr_lines: list[str] = []

    def _drain_stderr():
        # 独立线程排空 stderr，防管道写满把 claude 阻塞住
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)

    threading.Thread(target=_drain_stderr, daemon=True).start()

    full_result: str | None = None
    error: str | None = None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = d.get("type")
            if t == "stream_event":
                ev = d.get("event", {})
                delta = ev.get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield {"delta": delta["text"]}
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    yield {"thinking": delta["thinking"]}
            elif t == "result":
                if d.get("subtype") == "success":
                    full_result = str(d.get("result", ""))
                else:
                    error = str(d.get("result") or d.get("subtype") or "未知错误")
        proc.wait(timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        error = f"流式调用异常：{exc}"
    finally:
        if proc.poll() is None:
            proc.kill()

    if error:
        stderr_tail = "".join(stderr_lines).strip()[-500:]
        yield {"error": f"{error}（stderr 尾部：{stderr_tail}）" if stderr_tail else error}
    elif full_result is not None:
        yield {"done": True, "text": full_result}
    else:
        yield {"error": "Claude 未返回结果"}
