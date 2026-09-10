"""Agent 能力层：驱动各 AI agent CLI（原生接口，不封装复杂逻辑）。

按 vc_design/interfaces/02-AgentCLI-AgentCLI.md 实现：
- 直接调用原生 CLI（claude -p）
- 输出重定向到日志文件
- 会话标识可追溯
"""
