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
    resume_id: str | None = None,
) -> Iterator[dict]:
    """运行 claude 并逐段产出事件。

    Args:
        resume_id: 上一轮的对话标识。带上＝接着上一轮说（agent 自己认这条线），
            不带＝开一段新对话。服务端不保存它，由调用方从 `session` 事件里取走再带回来
            （ADR-034 决策 5）。

    Yields:
        {"session": "..."}         本轮对话标识（首轮即新开会话的标识，续轮沿用）
        {"thinking": "..."}      思考增量（Claude 推理过程，先于正文）
        {"delta": "..."}         正文增量
        {"done": True, "text": "..."}  最终完整文本
        {"error": "..."}         失败原因（终止，附带 stderr 尾部便于排障）
    """
    cmd = [
        claude_bin, "-p",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    if resume_id:
        cmd += ["--resume", resume_id]
    cmd.append(prompt)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # 不并入 stdout：CLI 噪音会混进 stream-json，非 JSON 行被静默丢弃
            text=True,
        )
    except FileNotFoundError:
        yield {"error": "生成服务未就绪，请联系管理员"}
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
    seen_session: str | None = None
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
            # 对话标识：init 事件起就带，实测每条事件都有同一个值——取第一次见到的即可
            sid = d.get("session_id")
            if sid and sid != seen_session:
                seen_session = str(sid)
                yield {"session": seen_session}
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
                elif resume_id:
                    # 接不上上文（服务重启过、或那段对话在服务器上已经没了）：
                    # 说清下一步，不糊成"请重试"
                    error = "这次对话接不上前面的内容了，请点「新对话」重新开始"
                else:
                    # 原始 subtype/result 文本不外报（英文枚举值用户查不到），只说定档句
                    error = "AI 处理未成功，请重试"
        proc.wait(timeout=timeout)
    except Exception:  # noqa: BLE001
        error = "AI 处理未成功，请重试"
    finally:
        if proc.poll() is None:
            proc.kill()

    if error:
        # stderr 尾部等技术细节不再拼进界面句子（本模块无输出通道承载它们）
        yield {"error": error}
    elif full_result is not None:
        yield {"done": True, "text": full_result}
    else:
        yield {"error": "AI 未返回内容，请重试"}
