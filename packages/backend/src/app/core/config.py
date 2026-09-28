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

# ── 调度器配置（ADR-019）────────────────────────────────────────────
# 默认 2 是**刻意保守**：每条任务 = 一个几百 MB 的 claude Node 进程 + 一整条 agent
# 工作流，而第三方 Anthropic 兼容网关的实际配额现场才知道，不敢替它定一个"看起来
# 专业"的数。上线后按实测上调即可，闸门口径是 DB 里 running 的行数，重启不丢。
_DEFAULT_MAX_CONCURRENT_JOBS = 2
# per-identity 粗闸：同一身份（登录用户 / 访客令牌）未完成任务（queued + running）上限。
# 用它取代加权公平调度——本期真正的风险只是"一个人把队排到 50 个"（ADR-019 决策 7）。
_DEFAULT_MAX_UNFINISHED_PER_IDENTITY = 10
_DEFAULT_SCHEDULER_INTERVAL = 5.0
_DEFAULT_JOB_TIMEOUT = 3600
# 调度器的超时预算比引擎自己的超时多这么几秒：让引擎内的 supervisor（guizang）
# 先按它更了解子进程的方式收尾，调度器只当**没有 supervisor 的引擎的兜底**，
# 避免两边同一瞬间抢同一行。抢了也不会错（CAS 先到先得），只是日志难看。
JOB_TIMEOUT_GRACE_SECONDS = 120


def _env_number(name: str, default: float, *, minimum: float) -> float:
    """读一个数值型环境变量，非法值退回默认并夹到下限。

    宁可用默认值跑着，也不要因为运维手滑写了 0 就把并发闸关掉、或把调度节拍变成 0。
    """
    _load_dotenv()
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        return float(default)
    return max(minimum, value)


def get_max_concurrent_jobs() -> int:
    """全局同时在跑的任务数（并发闸）。"""
    return int(_env_number("DECKSMITH_MAX_CONCURRENT_JOBS",
                           _DEFAULT_MAX_CONCURRENT_JOBS, minimum=1))


def get_max_unfinished_per_identity() -> int:
    """单身份未完成任务上限（粗闸）。"""
    return int(_env_number("DECKSMITH_MAX_UNFINISHED_PER_IDENTITY",
                           _DEFAULT_MAX_UNFINISHED_PER_IDENTITY, minimum=1))


def get_scheduler_interval() -> float:
    """调度节拍（秒）。"""
    return _env_number("DECKSMITH_SCHEDULER_INTERVAL", _DEFAULT_SCHEDULER_INTERVAL, minimum=1.0)


def get_job_timeout_seconds() -> int:
    """单任务超时预算（秒），沿用原 _DEFAULT_TIMEOUT_SECONDS 口径。"""
    return int(_env_number("DECKSMITH_JOB_TIMEOUT", _DEFAULT_JOB_TIMEOUT, minimum=60))
