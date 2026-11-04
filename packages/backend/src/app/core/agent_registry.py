"""Agent CLI 注册表（ADR-032）：**agent 类配置面唯一的知识源**。

与引擎注册表、系统设置注册表同一套原则：

- 接口、界面、渲染逻辑都只是**这份声明的执行者**，任何地方都不许硬写 agent 名字、
  环境变量名或"有哪些字段"（ADR-026 点名的病：检查闸写死枚举，加一项就假绿灯）。
- 一个 agent 自己声明：认哪些字段、每个字段渲染进**哪个环境变量**、
  它有哪几个配置文件、哪个文件允许界面改。

本期只注册 `claude_code`。将来接 Codex / Hermes（`docs/vc_design/interfaces/02-AgentCLI-AgentCLI.md`
已预留），只在这里加一条 `AgentSpec`，表结构、接口形状、前端组件都不动。

环境变量名的依据：官方文档 `code.claude.com/docs/en/settings`、`/docs/en/env-vars`
（`env` 块覆盖同名 shell 变量；`ANTHROPIC_API_KEY` 发 `x-api-key`、
`ANTHROPIC_AUTH_TOKEN` 发 `Authorization: Bearer`，两者不等价），
以及 `deploy/.env.example:6-26` 里已按实测写下的档位映射说明。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# engine 包的父目录是 packages/，加入 sys.path 后即可 `import engine`。
# 与 engines/factory.py、engines/stub.py 同一做法：谁要 import engine，谁自己引导一次。
_PACKAGES_DIR = Path(__file__).resolve().parents[4]
if str(_PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_DIR))

from engine.core.config import get_claude_config_dir, get_claude_home_file  # noqa: E402

FIELD_TYPES = ("string", "secret")

#: 官方端点。填了这个（或留空）＝走官方订阅/官方 Key，不要求档位映射。
OFFICIAL_BASE_URL = "https://api.anthropic.com"


@dataclass(frozen=True)
class AgentFieldSpec:
    """档案里的一个字段，以及它落到哪个环境变量。

    `env_var` 是"这个字段不是假控件"的物证：声明了就必须真写进配置文件，
    守卫测试 `tests/contract/agent_settings_guard_test.py` 逐条核对。
    """

    key: str
    env_var: str
    label: str
    type: str
    description: str
    #: 凭据类字段发的是哪个认证头（`x-api-key` / `Authorization: Bearer`）。
    #: 这条知识只在注册表里存一份：探针与渲染都从这里取，调用方不许自己写死变量名比较
    #: （写死了就等于"注册表改了就漏"的第二处真源，守卫测试会红）。
    auth_header: str = ""
    #: 走第三方网关时必填。缺了会命中 claude 内置的官方模型名，网关当场报 model_not_found
    #: （这条坑写在 deploy/.env.example:20-26）。走官方时不算缺。
    required_for_gateway: bool = False
    #: 渲染进配置文件＝下一个任务即生效，故一律 False。若某项确实要重建镜像才生效，
    #: 必须在这里如实标 True，不许伪装成热生效（ADR-020 决策 2 同一条纪律）。
    restart_required: bool = False

    @property
    def is_secret(self) -> bool:
        return self.type == "secret"


@dataclass(frozen=True)
class AgentFileSpec:
    """一个配置文件的存在性与可编辑性。"""

    name: str
    #: 路径解析器：每次调用现算，因为 CLAUDE_CONFIG_DIR / HOME 可能因部署而异，
    #: 在模块导入时钉死会把容器里的真实路径搞错。
    resolve_path: Callable[[], Path]
    editable: bool
    #: 该文件是否有 `env` 块（只有它才参与托管键的读写）。
    holds_env: bool = False
    #: 为什么允许/禁止界面改，直接给管理员看。
    note: str = ""

    def path(self) -> Path:
        return self.resolve_path()


@dataclass(frozen=True)
class AgentSpec:
    id: str
    label: str
    fields: tuple[AgentFieldSpec, ...]
    files: tuple[AgentFileSpec, ...]
    #: 判"是否走网关"看哪个字段（None = 这个 agent 没有网关概念）
    base_url_key: str | None = None
    #: 互斥字段组：同组内最多填一个（两种鉴权头不等价，混填表现为 401 或行为怪异）
    exclusive_groups: tuple[tuple[str, ...], ...] = ()

    def field(self, key: str) -> AgentFieldSpec | None:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def file(self, name: str) -> AgentFileSpec | None:
        for f in self.files:
            if f.name == name:
                return f
        return None

    @property
    def env_keys(self) -> tuple[str, ...]:
        """本 agent 托管的全部环境变量名（渲染与漂移检测的唯一来源）。"""
        return tuple(f.env_var for f in self.fields)

    def is_gateway(self, values: dict) -> bool:
        if not self.base_url_key:
            return False
        url = str(values.get(self.base_url_key) or "").strip().rstrip("/")
        if not url:
            return False
        return not url.startswith(OFFICIAL_BASE_URL.rstrip("/"))

    def gaps(self, values: dict) -> list[str]:
        """走网关却缺必填档位映射时，返回缺的字段 key（空列表＝没有缺口）。"""
        if not self.is_gateway(values):
            return []
        missing = []
        for f in self.fields:
            if f.required_for_gateway and not str(values.get(f.key) or "").strip():
                missing.append(f.key)
        return missing

    def conflicts(self, values: dict) -> list[str]:
        """互斥字段同时填了 → 返回人话原因列表。"""
        reasons: list[str] = []
        for group in self.exclusive_groups:
            filled = [k for k in group if str(values.get(k) or "").strip()]
            if len(filled) > 1:
                labels = "、".join(self.field(k).label if self.field(k) else k for k in filled)
                reasons.append(f"「{labels}」只能填一种，两者作用不同，请清空其中一个")
        return reasons


_CLAUDE_CODE_FIELDS: tuple[AgentFieldSpec, ...] = (
    AgentFieldSpec(
        key="base_url",
        env_var="ANTHROPIC_BASE_URL",
        label="服务地址",
        type="string",
        description="AI 服务地址。未填写时使用官方地址。"
                    "使用第三方服务时按服务方给出的地址填写，系统会自动补全路径。",
    ),
    AgentFieldSpec(
        key="api_key",
        env_var="ANTHROPIC_API_KEY",
        label="密钥（常规方式）",
        type="secret",
        auth_header="x-api-key",
        description="以常规方式提交密钥，官方与多数第三方服务都支持。"
                    "填写该项后，将使用该密钥而不是订阅账号。",
    ),
    AgentFieldSpec(
        key="auth_token",
        env_var="ANTHROPIC_AUTH_TOKEN",
        label="密钥（Bearer 方式）",
        type="secret",
        auth_header="Authorization: Bearer",
        description="以 Bearer 方式提交密钥，第三方服务常见。"
                    "与常规方式二选一；填写方式不一致时不会提示缺少密钥，任务会在开始后很快失败。",
    ),
    AgentFieldSpec(
        key="model",
        env_var="ANTHROPIC_MODEL",
        label="模型名称",
        type="string",
        description="主要使用的模型名称，须与 AI 服务支持的名称一致。",
        required_for_gateway=True,
    ),
    AgentFieldSpec(
        key="default_sonnet_model",
        env_var="ANTHROPIC_DEFAULT_SONNET_MODEL",
        label="Sonnet 档位模型名",
        type="string",
        description="常用档位的模型名称。使用第三方服务时必填，"
                    "未填写会在用到该档位时报错。",
        required_for_gateway=True,
    ),
    AgentFieldSpec(
        key="default_opus_model",
        env_var="ANTHROPIC_DEFAULT_OPUS_MODEL",
        label="Opus 档位模型名",
        type="string",
        description="中档模型名称。使用第三方服务时必填，未填写会在用到该档位时报错。",
        required_for_gateway=True,
    ),
    AgentFieldSpec(
        key="default_haiku_model",
        env_var="ANTHROPIC_DEFAULT_HAIKU_MODEL",
        label="Haiku 档位模型名",
        type="string",
        description="小型任务模型名称，用于生成标题、摘要等轻量任务。使用第三方服务时必填。",
        required_for_gateway=True,
    ),
)

CLAUDE_CODE = AgentSpec(
    id="claude_code",
    label="Claude Code",
    fields=_CLAUDE_CODE_FIELDS,
    base_url_key="base_url",
    exclusive_groups=(("api_key", "auth_token"),),
    files=(
        AgentFileSpec(
            name="settings.json",
            resolve_path=lambda: get_claude_config_dir() / "settings.json",
            editable=True,
            holds_env=True,
            note="该文件中与 AI 服务相关的几项由本系统管理，其余内容仅显示不修改。",
        ),
        AgentFileSpec(
            name=".claude.json",
            resolve_path=get_claude_home_file,
            editable=False,
            holds_env=False,
            note="这是命令行工具自身维护的状态文件（登录信息等），仅供查看。"
                 "该文件一旦损坏，工具会无法启动，因此本系统不提供修改。",
        ),
    ),
)

AGENTS: tuple[AgentSpec, ...] = (CLAUDE_CODE,)

_BY_ID: dict[str, AgentSpec] = {a.id: a for a in AGENTS}


def agent_ids() -> tuple[str, ...]:
    return tuple(_BY_ID)


def agent_spec(agent_id: str) -> AgentSpec:
    """按注册名取 agent；未注册名抛 KeyError，由接口层翻成 400。

    与引擎工厂同一口径：未知名字**不静默降级**，否则拼错的名字会被当成"没有这个 agent"，
    管理员以为改成了而实际没有。
    """
    spec = _BY_ID.get(agent_id)
    if spec is None:
        raise KeyError(f"未找到名称为「{agent_id}」的 AI 工具配置，请核对名称")
    return spec
