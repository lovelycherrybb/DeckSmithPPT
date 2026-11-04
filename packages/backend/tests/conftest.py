"""单测层的全局沙箱兜底。

为什么这一层也要一份：`tests/conftest.py` 只管得到 tests/ 下面（contract、e2e 各是独立进程），
而 `packages/backend/tests` 与 `packages/engine/tests` 是**另一批进程**，从不读那个 conftest。

漏掉它踩过一次真事故（2026-11-04）：档案启用会把模型名等渲染进 `~/.claude/settings.json`，
某个用例没走沙箱，于是**每跑一轮单测就把开发机的真实供应商配置改写成测试常量**
（`ANTHROPIC_MODEL=glm-4.6`、`ANTHROPIC_BASE_URL=https://gw.example.com`）。
后果不是测试失败，而是测试全绿、真实生成却一直报"模型名称不认识"——排查方向被整个带偏，
一度怀疑是有人在界面上乱点启用。

各测试文件自带的 cfg 夹具仍然有效（它们在自己的用例里再指一次），这一层兜的是漏网的那些。
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_SANDBOX = Path(tempfile.mkdtemp(prefix="decksmith-unit-sandbox-"))
atexit.register(shutil.rmtree, _SANDBOX, True)

# 与 tests/conftest.py 同一条道理：必须早于任何 app 模块导入
os.environ["CLAUDE_CONFIG_DIR"] = str(_SANDBOX / "claude-config")
os.environ["DECKSMITH_JOBS_ROOT"] = str(_SANDBOX / "jobs")
