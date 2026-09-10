"""草稿安全检查（提示词注入防护）。

按 vc_design/interfaces/01-系统接口-SystemAPI.md 的检查规则实现：
- 系统指令注入（忽略之前的指令、执行命令等）
- 敏感内容
- 未授权资源访问
- 可疑的代码执行指令

这是「两阶段调用」的门禁：检查通过才允许把内容交给 Agent。
规则基于正则，快速、确定性；后续可扩展为 Agent 辅助判定（vc_design 的 PptMasterFlow 方案）。
"""
from __future__ import annotations

import re

# 规则表：(规则名, 正则)
_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "系统指令注入",
        re.compile(
            r"(忽略(以上|之前|前面|上面)?(的)?(所有)?(指令|提示|规则))"
            r"|(ignore (all )?(previous|prior|above) (instructions|prompts|rules))"
            r"|(disregard (all )?(previous|prior) (instructions))"
            r"|(你(现在)?(是|不再是).{0,10}(系统|助手|AI)|you are now (a |an )?)"
            r"|(system\s*prompt|你的系统提示)",
            re.IGNORECASE,
        ),
    ),
    (
        "代码执行指令",
        re.compile(
            r"(执行(以下|下面|如下)?(命令|代码|脚本|shell))"
            r"|(run|execute|eval)\s+(the\s+)?(following\s+)?(command|code|script|shell)"
            r"|(rm\s+-rf|sudo\s|curl\s+.*\|\s*(ba)?sh|wget\s+.*\|\s*(ba)?sh)"
            r"|(```(bash|sh|shell|python|zsh))"
            r"|(\beval\s*\()|(\bexec\s*\()|(\bos\.system\()|(\bsubprocess\.)",
            re.IGNORECASE,
        ),
    ),
    (
        "未授权资源访问",
        re.compile(
            r"(读取|访问|查看|删除)(以下|下面)?(的)?(文件系统|任意文件|服务器|数据库|环境变量)"
            r"|(/etc/(passwd|shadow)|\.ssh/|\.env\b|id_rsa)"
            r"|(read|access|list|delete)\s+(all\s+)?(files?|server|database|environment)",
            re.IGNORECASE,
        ),
    ),
    (
        "敏感内容标记",
        re.compile(
            r"(密码|口令|私钥|密钥对)\s*[:=]\s*\S"
            r"|(password|secret|private[_\s]?key|api[_\s]?key)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
]


def check_draft_content(content: str) -> tuple[bool, str | None]:
    """检查草稿内容是否安全。

    Returns:
        (passed, reason)：通过时 reason 为 None；不通过时给出命中的规则名。
    """
    if not content:
        return True, None
    for rule_name, pattern in _RULES:
        m = pattern.search(content)
        if m:
            # 截取命中上下文便于用户定位
            ctx = content[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")
            return False, f"命中规则「{rule_name}」：…{ctx}…"
    return True, None


def list_rules() -> list[str]:
    """返回规则名列表（供 SecurityCheckResult.rules_checked 用）。"""
    return [name for name, _ in _RULES]
