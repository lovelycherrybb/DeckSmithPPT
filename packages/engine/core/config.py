"""引擎运行配置。

部署时可改路径的关键：环境变量（ENV）> .env 文件 > 默认值。

- 开发时：在项目根建 `.env` 写 `DECKSMITH_JOBS_ROOT=outputs/jobs`
- 部署时：在服务器上设置环境变量即可，不改代码
- 不设置：使用默认值 `outputs/jobs`
"""

import os
from pathlib import Path

# 环境变量名：部署时只需改这一个
_JOBS_ROOT_ENV = "DECKSMITH_JOBS_ROOT"
_DEFAULT_JOBS_ROOT = "outputs/jobs"


def _load_dotenv(root: Path) -> None:
    """从项目根 .env 读取变量（若存在）。不引入第三方依赖。"""
    env_file = root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 不覆盖已存在的环境变量（环境变量优先级更高）
        if key and key not in os.environ:
            os.environ[key] = value


def get_jobs_root() -> Path:
    """返回任务根目录（绝对路径）。"""
    # 1. 环境变量（部署时设这个）
    env_value = os.environ.get(_JOBS_ROOT_ENV)
    if env_value:
        return Path(env_value).resolve()

    # 2. .env 文件（开发时用）
    repo_root = Path(__file__).resolve().parents[3]  # packages/engine/core -> 仓库根
    _load_dotenv(repo_root)
    env_value = os.environ.get(_JOBS_ROOT_ENV)
    if env_value:
        return Path(env_value).resolve()

    # 3. 默认值
    return (repo_root / _DEFAULT_JOBS_ROOT).resolve()


def ensure_jobs_root() -> Path:
    """确保任务根目录存在，返回它。"""
    root = get_jobs_root()
    root.mkdir(parents=True, exist_ok=True)
    return root
