"""后端配置（集中管理可变设置）。

部署时改这里或对应环境变量即可，不用翻代码：
- jobs_root：任务文件（草稿/产物）存放目录
- DATABASE_URL：SQLite 数据库文件位置
"""
import os
from pathlib import Path

# 仓库根目录：packages/backend/src/app/core/config.py
# parents[0]=core [1]=app [2]=src [3]=backend [4]=packages [5]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[5]

# 默认任务根目录：outputs/jobs（草稿 .md 和生成的 .pptx 都放这里）
_DEFAULT_JOBS_ROOT = REPO_ROOT / "outputs" / "jobs"

# 默认数据库：outputs/decksmith.db（SQLite 文件）
_DEFAULT_DB_URL = f"sqlite:///{REPO_ROOT / 'outputs' / 'decksmith.db'}"


def _load_dotenv() -> None:
    """从仓库根 .env 读取变量（若存在），不覆盖已设的环境变量。"""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_jobs_root() -> Path:
    """返回任务根目录：环境变量 DECKSMITH_JOBS_ROOT > .env > 默认值。"""
    _load_dotenv()
    env_value = os.environ.get("DECKSMITH_JOBS_ROOT")
    if env_value:
        return Path(env_value).resolve()
    return _DEFAULT_JOBS_ROOT


def get_database_url() -> str:
    """返回数据库连接串：环境变量 DATABASE_URL > 默认值。"""
    _load_dotenv()
    return os.environ.get("DATABASE_URL") or _DEFAULT_DB_URL


def get_app_version() -> str | None:
    """返回当前部署版本：环境变量 DECKSMITH_VERSION（发版时由 compose 用 IMAGE_TAG 注入）> .env > None。

    版本是"已发布安装包"的属性：开发态（npm run dev、未设该 env）返回 None，
    前端据此不显示版本——避免把未确认的工作树状态当成版本号。
    """
    _load_dotenv()
    return (os.environ.get("DECKSMITH_VERSION") or "").strip() or None


# 默认 agent（提交任务时不指定就用它）
AGENT_DEFAULT = "claude"
