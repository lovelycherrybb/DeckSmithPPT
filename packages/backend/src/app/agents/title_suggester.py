"""Claude Code Agent：标题拟题能力（同步、纯文本，与 draft_modifier 同模式）。

场景：用户上传多个文件后，调 Agent 理解文件内容并拟一个贴切的演示文件标题。
与 modify 的区别：输入是多个文件的内容摘要，输出是一行标题（非 Markdown）。
安全同 draft_modifier：不加 --dangerously-skip-permissions，纯文本改写任务。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# 仓库根：packages/backend/src/app/agents/title_suggester.py
_REPO_ROOT = Path(__file__).resolve().parents[5]
_LOG_DIR = _REPO_ROOT / "outputs" / "agent-logs"

_DEFAULT_TIMEOUT = 120  # 拟题最多 2 分钟
_MAX_CONTENT_PER_FILE = 1500  # 每个文件给 Agent 看的内容上限（字符），防超长


def _build_prompt(filenames: list[str], contents: list[str]) -> str:
    """构建拟题提示词：文件名 + 每个文件的内容开头。"""
    parts = []
    for name, content in zip(filenames, contents):
        snippet = (content or "").strip()[:_MAX_CONTENT_PER_FILE]
        parts.append(f"【文件：{name}】\n{snippet}")
    files_block = "\n\n".join(parts)

    return f"""你是一个演示文件命名助手。用户上传了以下文件，准备生成一份 PPT 演示文件。
请通读这些文件的内容，为这份演示文件拟一个标题。

## 标题要求

1. **概括核心主题**：标题要概括所有文件的核心主题，而不是简单罗列文件名
2. **正式专业**：像一份正式演示文件的标题，适合商业或学术场景
3. **长度适中**：中文，8-20个字
4. **简洁有力**：语言精炼，易于记忆

## 输出要求

- 只输出标题本身
- 不要引号、不要解释、不要任何其他文字
- 不要添加"标题："或"演示文件："等前缀

## 文件内容

{files_block}
"""


def clean_title(raw: str) -> str:
    """清洗模型输出为纯标题：去引号/围栏/前缀，取首行，上限 60 字。"""
    result = (raw or "").strip()
    for wrapper in ('"', "'", '“', '”', '「', '」'):
        result = result.strip(wrapper)
    if result.startswith("标题：") or result.startswith("标题:"):
        result = result.split("：", 1)[-1].split(":", 1)[-1].strip()
    if result.startswith("```"):
        lines = result.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        result = "\n".join(lines).strip()
    result = result.splitlines()[0].strip() if result else ""
    return result[:60]


def suggest_title_with_claude(
    filenames: list[str],
    contents: list[str],
    claude_bin: str = "claude",
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """调 Claude Code 基于多个文件内容拟标题。

    Args:
        filenames: 文件名列表
        contents: 与文件名对应的文件内容（可传空字符串，此时仅凭文件名）

    Returns:
        拟定的标题（已去空白/引号/围栏）。

    Raises:
        ValueError: 输入为空。
        RuntimeError: 子进程失败或超时。
    """
    if not filenames:
        raise ValueError("文件列表不能为空")

    # 防御：剥掉内容里的控制字符（含 null byte，subprocess 参数不允许）
    safe_contents = [
        "".join(ch for ch in (c or "") if ch >= " " or ch in "\n\r\t")
        for c in contents
    ]

    prompt = _build_prompt(filenames, safe_contents)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            [claude_bin, "-p", "--output-format", "text", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"未找到 claude CLI（{claude_bin}），请确认已安装 Claude Code") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Agent 拟题超时（>{timeout}s）") from exc

    _LOG_DIR.joinpath("last-title.log").write_text(
        f"=== prompt ===\n{prompt[:2000]}\n\n=== stdout ===\n{proc.stdout}\n\n=== stderr ===\n{proc.stderr}\n",
        encoding="utf-8",
    )

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise RuntimeError(f"Claude 调用失败（exit={proc.returncode}）：{detail}")

    result = clean_title(proc.stdout)
    if not result:
        raise RuntimeError("Claude 返回了空标题")
    return result
