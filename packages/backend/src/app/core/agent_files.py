"""Agent 配置文件的读、渲染、原子写（ADR-032）。

这个文件**不碰数据库**，只管磁盘上那份文件。三件事在这里定死：

1. **托管键（managed keys）**：来自注册表声明的环境变量名。渲染时只增改这些键，
   文件里其他任何内容（`permissions`、`hooks`、别的 env）原样保留。
2. **没填就删**（AGENTS.md 规则 10 同一条纪律）：档案里留空的托管键必须从文件里移除，
   不给它留旧值——否则"我明明清掉了"却在下一代任务里继续生效，是最难查的一种骗人。
3. **写坏了比不写更糟**：先 JSON 校验，再把原文件按时间戳备份，最后临时文件 +
   `os.replace` 原子替换。中途失败磁盘上仍是完整旧文件，不会出现半截 JSON。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..core.agent_registry import AgentSpec

MASK = "••••已隐藏••••"


def sha256_of(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_document(path: Path) -> tuple[bool, dict | None, str | None, str | None]:
    """返回 (是否存在, 解析后的对象, 原文, 解析错误)。

    解析失败时**不回原文**：坏文件里同样躺着凭据明文，回出去就违反"明文不出后端"。
    """
    if not path.exists():
        return False, None, None, None
    text = path.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        return True, None, None, f"文件不是合法 JSON（第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}）"
    if not isinstance(doc, dict):
        return True, None, None, "文件顶层不是对象，Claude Code 会拒绝加载"
    return True, doc, text, None


def render_env(doc: dict | None, values: dict, spec: AgentSpec) -> tuple[dict, list[str], list[str]]:
    """按注册表把档案值渲染进文件的 `env` 块。

    Returns:
        (新文档, 写入的键, 删除的键)
    """
    out = dict(doc or {})
    env = dict(out.get("env") or {})
    written: list[str] = []
    removed: list[str] = []

    for field in spec.fields:
        raw = values.get(field.key)
        text = "" if raw is None else str(raw).strip()
        if text:
            if env.get(field.env_var) != text:
                env[field.env_var] = text
            written.append(field.env_var)
        elif field.env_var in env:
            env.pop(field.env_var)
            removed.append(field.env_var)

    if env:
        out["env"] = env
    elif "env" in out and doc is not None:
        # 原来有 env 块、现在一个键都不剩：留一个空对象比留旧值干净，也比删掉整块保守
        out["env"] = {}
    return out, written, removed


def mask_document(doc: dict, secret_env_keys: set[str]) -> tuple[dict, list[str]]:
    """把凭据类环境变量的值换成掩码。返回 (脱敏文档, 被掩码的键)。"""
    out = json.loads(json.dumps(doc, ensure_ascii=False))  # 深拷贝，不碰原对象
    env = out.get("env")
    masked: list[str] = []
    if isinstance(env, dict):
        for key in list(env):
            if key in secret_env_keys and env[key] not in (None, ""):
                value = str(env[key])
                env[key] = _mask_value(value)
                masked.append(key)
    return out, masked


def _mask_value(plain: str) -> str:
    if len(plain) <= 8:
        return MASK
    return f"{plain[:3]}…{plain[-4:]}"


def backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return path.with_name(f"{path.name}.decksmith-bak.{stamp}")


def atomic_write(path: Path, doc: dict) -> Path | None:
    """校验 → 备份 → 原子替换。返回备份文件路径（原本没有文件则 None）。

    落盘用 `indent=2 + 末尾换行`：这是给人看的文件，压成一行会让运维手工核对时
    完全读不动，而"界面显示的和磁盘上的对不上"正是本项目最忌讳的那类事。
    """
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    # 先自检：我们生成的东西必须自己解得开，否则等于把坏文件推给 CLI
    json.loads(text)

    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    bak = None
    if existed:
        bak = backup_path(path)
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    tmp = path.with_name(f".{path.name}.decksmith-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)  # 文件里可能有凭据明文，别给同机其他用户留读口
    os.replace(tmp, path)
    return bak
