"""草稿安全检查（提示词注入防护）。

按 vc_design/interfaces/01-系统接口-SystemAPI.md 的检查规则实现：
- 系统指令注入（忽略之前的指令、执行命令等）
- 敏感内容
- 未授权资源访问
- 可疑的代码执行指令

这是「两阶段调用」的门禁：检查通过才允许把内容交给 Agent。
规则基于正则，快速、确定性；后续可扩展为 Agent 辅助判定（vc_design 的 PptMasterFlow 方案）。

检测前先剥离代码块（```围栏）与行内代码（`…`）：技术文档的架构图/代码片段
是正当素材，注入意图只会出现在正文散文里，规则只对散文生效。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("decksmith.security")

# 代码剥离：fenced 代码块（含未闭合到文末的截断场景）+ 行内代码 span
_FENCED_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_UNCLOSED_FENCE_RE = re.compile(r"```[^\n]*\n.*\Z", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def strip_code(text: str) -> str:
    """剥离代码块与行内代码，只留正文散文。"""
    text = _FENCED_RE.sub(" ", text)
    text = _UNCLOSED_FENCE_RE.sub(" ", text)
    return _INLINE_CODE_RE.sub(" ", text)


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
            # 祈使句式须带指示宾语（执行以下命令）或礼貌指令（请执行脚本）——
            # 「执行脚本」裸出现多为描述句（如"AI Agent 按步骤执行脚本"），不拦
            r"((请|请先|请立即|请马上|请务必)?执行(以下|下面|如下|下列)(的)?(命令|代码|脚本|shell))"
            r"|(请(先|立即|马上|务必)?执行(命令|代码|脚本|shell))"
            r"|(run|execute|eval)\s+(the\s+)?(following\s+)?(command|code|script|shell)"
            r"|(rm\s+-rf|sudo\s|curl\s+.*\|\s*(ba)?sh|wget\s+.*\|\s*(ba)?sh)"
            # 带语言标签的围栏：正常路径下 strip_code 已把整块剥掉，此分支只在「```python
            # 后无换行」的退化情况命中。刻意保留作纵深防御——若 strip_code 被误删或改动，
            # 显式声明为 shell/python 的代码块仍能兜住（见 test_strip_code_unclosed_fence）。
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


# 规则人话名：界面只说这条，规则原名与命中位置只进日志
_RULE_DISPLAY_NAMES = {
    "系统指令注入": "让 AI 忽略规则的指令",
    "代码执行指令": "要求执行命令的内容",
    "未授权资源访问": "要求读取系统文件的内容",
    "敏感内容标记": "密码或密钥内容",
}


def check_draft_content(content: str) -> tuple[bool, str | None]:
    """检查草稿内容是否安全。

    Returns:
        (passed, reason)：通过时 reason 为 None；不通过时给出规则的人话名，
        由调用处拼成完整界面句子。不回显命中的正文片段。
    """
    if not content:
        return True, None
    content = strip_code(content)
    for rule_name, pattern in _RULES:
        m = pattern.search(content)
        if m:
            logger.info("草稿安全检查未通过：规则=%s，位置=%d", rule_name, m.start())
            return False, _RULE_DISPLAY_NAMES.get(rule_name, "不合适的内容")
    return True, None


def list_rules() -> list[str]:
    """返回规则名列表（供 SecurityCheckResult.rules_checked 用）。"""
    return [name for name, _ in _RULES]
