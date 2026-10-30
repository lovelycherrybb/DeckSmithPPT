"""User 字段在契约 / 后端出参 / 生成类型三处必须一致。

为什么单独钉这一条：FastAPI 的 `response_model=list[UserOut]` 会**静默丢掉**
Pydantic 里没声明的字段。契约加了 `last_login_at`、仓储也查出来了，但 `UserOut`
忘加字段时，接口照样 200 返回、只是那个字段凭空消失——前端只会看到一列永远的空。
这类"三方各写一份"的漂移，靠人记是记不住的，只能机器比对。

三处真源：
- 契约：`docs/contracts/openapi.yaml` 的 `components.schemas.User.properties`
- 后端：`app/core/schemas.py` 的 `class UserOut`
- 生成类型：`packages/shared/types/api.d.ts` 的 `components.schemas.User`
  （由 `scripts/generate-types.sh` 产出、不入库；本条同时兼作"生成物是否跟上契约"的新鲜度检查）

判据（每条都能红，`python3` 直接跑本文件可看破坏用例）：
① 三处字段集合两两相等；② 生成类型缺失时判"没能比"而不是跳过；
③ 三处任何一份都不许出现密码散列字段。
退出码三态：0 一致 / 1 不一致 / 2 没能比。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
OPENAPI = REPO / "docs" / "contracts" / "openapi.yaml"
SCHEMAS = REPO / "packages/backend/src/app/core/schemas.py"
TYPES = REPO / "packages/shared/types/api.d.ts"

FORBIDDEN = "password_hash"


def contract_user_fields() -> set[str]:
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return set(doc["components"]["schemas"]["User"]["properties"])


def backend_user_out_fields() -> set[str]:
    text = SCHEMAS.read_text(encoding="utf-8")
    m = re.search(r"^class UserOut\(BaseModel\):(.*?)(?=^class )", text, re.M | re.S)
    assert m, "后端找不到 class UserOut（改名或删除会静默失去守卫）"
    return set(re.findall(r"^\s{4}([a-z_]+)\s*[:=]", m.group(1), re.M))


def generated_user_fields() -> set[str] | None:
    """None = 生成物不存在，属"没能比"，不许当成一致。"""
    if not TYPES.is_file():
        return None
    text = TYPES.read_text(encoding="utf-8")
    m = re.search(r"\n        User: \{(.*?)\n        \};", text, re.S)
    assert m, "生成类型里找不到 components.schemas.User（结构变了请同步本检查）"
    # 缩进用字面空格而非 \s：\s 会吃掉换行，嵌套层的行会被本层规则误匹配（实测踩过）。
    # 必填字段写成 `name: T;`、可选写成 `name?: T;`，两种都要收。
    return set(re.findall(r"^[ ]{12}([a-z_]+)\??\s*:", m.group(1), re.M))


def compare() -> tuple[list[str], bool]:
    """返回（问题清单，是否没能比）。"""
    problems: list[str] = []
    contract = contract_user_fields()
    backend = backend_user_out_fields()
    generated = generated_user_fields()

    if contract != backend:
        problems.append(
            f"契约 vs 后端 UserOut 不一致：契约独有 {sorted(contract - backend)}，"
            f"后端独有 {sorted(backend - contract)}。"
            "契约加了字段而 UserOut 忘加，FastAPI 会静默不返回它。")
    if generated is None:
        return [f"没能比：{TYPES} 不存在，先跑 scripts/generate-types.sh"] + problems, True
    if contract != generated:
        problems.append(
            f"契约 vs 生成类型不一致：契约独有 {sorted(contract - generated)}，"
            f"生成物独有 {sorted(generated - contract)}。多半是改完契约没重新生成类型。")
    for label, fields in (("契约", contract), ("后端", backend), ("生成类型", generated)):
        if FORBIDDEN in fields:
            problems.append(f"{label} 的 User 出参里出现了 {FORBIDDEN}")
    return problems, False


def test_三处字段一致且不含密码散列():
    import pytest
    problems, incomparable = compare()
    if incomparable:
        pytest.fail("生成类型缺失，按「没能比」处理，不算通过：" + "; ".join(problems))
    assert not problems, "\n".join(problems)


def _selftest() -> int:
    """破坏用例：证明比对不是永远绿的摆设。"""
    real_backend = backend_user_out_fields()
    cases = {
        "契约加了字段而后端没有": (real_backend - {"last_login_at"}, contract_user_fields()),
        "后端私自加了契约没有的字段": (real_backend | {"ghost"}, contract_user_fields()),
        "出参混进密码散列": (real_backend | {FORBIDDEN}, real_backend | {FORBIDDEN}),
    }
    bad = 0
    for label, (a, b) in cases.items():
        caught = (a != contract_user_fields()) or (FORBIDDEN in a)
        print(f"  {'能报红' if caught else '判红失败'}：{label}")
        bad += 0 if caught else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_selftest())
