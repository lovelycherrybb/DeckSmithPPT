"""上传区：按身份分格存放，引用时只认自己那一格（ADR-035 决策 2）。

之前这里是公共投递口：`POST /api/upload` 不看身份，谁都能塞；引用侧只验
"路径在 uploads 里面"，不验是不是你自己的（`routes.py:167`、`agent.py:49` 两处各写一套）。
现在布局是 `uploads/<身份段>/<上传号>/<文件名>`，身份段由 `ownership.space_key` 一处算出。

三条纪律写在这里，调用方不许各写一套：

1. **口径只有一处**：目录名规则、越界判定、拒绝时说什么句子，全在本模块。
   三个引用点（上传、拟题、生成）都调同一个函数，改一处就是改全部。
2. **拒绝的句子沿用既有说法**：越界说「该文件不在本次上传范围内，请重新上传」，
   没身份说「请先登录后再上传」——都是项目里已经出现过的句式，不新造（AGENTS.md 规则 11）。
3. **不搬历史**：改成按身份分格之后，旧的扁平目录里的文件不再可引用。
   素材是一次性用的（上传→提交→生成完），故不做搬迁，也不假装兼容。
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import HTTPException

from .db import JOBS_ROOT

#: 能按文本读出来的素材后缀。docx/pdf 是二进制，文字要等引擎在任务里转出来，
#: 那一层本模块**查不到**——边界写在 ADR-035 决策 1 与遗留 1，不许含混说成"已全面检查"。
TEXT_SOURCE_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".csv", ".tsv"})

_KEY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

NO_IDENTITY = "请先登录后再上传"
OUT_OF_SCOPE = "该文件不在本次上传范围内，请重新上传"


def uploads_root() -> Path:
    return JOBS_ROOT / "uploads"


def _key_or_401(identity) -> str:
    """身份段。没有可归属的身份就是 401，不许退化成"公共格子"。"""
    from .ownership import space_key
    key = space_key(identity)
    if not key or not _KEY_SEGMENT_RE.match(key):
        raise HTTPException(status_code=401, detail=NO_IDENTITY)
    return key


def own_dir(identity) -> Path:
    """当前身份专属的上传目录（可能还不存在——上传时才建）。"""
    return uploads_root() / _key_or_401(identity)


def store(identity, data: bytes, safe_name: str) -> tuple[str, Path]:
    """把一份文件落到"自己那一格"里，返回 (上传号, 落点目录)。"""
    base = own_dir(identity)
    upload_id = uuid.uuid4().hex
    dest_dir = base / upload_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    # 双保险：确认落点仍在自己那一格内（文件名消毒后越界就是攻击）
    if not dest.resolve().is_relative_to(base.resolve()):
        raise HTTPException(status_code=400, detail="文件名无法使用，请重命名后重新上传")
    dest.write_bytes(data)
    return upload_id, dest


def guard(raw_path: str, identity) -> Path:
    """把请求里带来的路径钉回"自己那一格"。越界与不存在都按同一句拒。"""
    base = own_dir(identity)
    try:
        p = Path(raw_path).resolve()
    except OSError:
        raise HTTPException(status_code=400, detail=OUT_OF_SCOPE) from None
    if not p.is_relative_to(base.resolve()):
        raise HTTPException(status_code=400, detail=OUT_OF_SCOPE)
    return p


def read_material_text(raw_path: str, identity) -> str | None:
    """按文本读出上传素材的正文；不是可文本读的后缀就返回 None（＝这一层查不到，不是没内容）。"""
    p = guard(raw_path, identity)
    if not p.exists() or p.suffix.lower() not in TEXT_SOURCE_SUFFIXES:
        return None
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
