#!/usr/bin/env python3
"""契约漂移检查：`docs/contracts/openapi.yaml`（人写的契约）vs 后端代码自述的 OpenAPI。

**为什么是这两个方向比**。AGENTS 规则 1 说契约是唯一事实来源，但"唯一事实来源"
只有在**代码真的跟着它**时才成立。此前 `check-drift.sh` 只 echo 一行 TODO，
于是契约与实现各走各的、没有任何一层会报错（同类事故已发生过一次：
`cancel` 能力位契约写了、代码没派生，界面上的按钮永远不出现，见 ADR-019 决策 5）。

比的是"接口集合"这一层，两个方向都查：

- **契约有、代码无** → 前端按契约打过去就是 404。这是契约在说谎。
- **代码有、契约无** → 接口在裸奔：没有类型生成、没有文档、没有对齐校验。

刻意不查的东西（避免假阳性，也避免把本脚本变成第二个 check-alignment.py）：

- 不查字段级 schema 差异 —— 那是 `check-alignment.py`（vc_design ↔ contracts）
  与 `tests/contract/api_contract_test.py`（真实响应 ↔ 契约）的职责。
- 路径参数**名**不参与比对，只比形状：`/api/jobs/{job_id}` 与 `/api/jobs/{id}`
  视为同一路径。理由是参数名不进 URL 模式，名字不同不构成接口漂移；
  归一化在输出里明写出来，不偷偷做掉。
- 只比 `/api/` 前缀下的路径。FastAPI 自带的 `/docs`、`/openapi.json` 等
  不属于对外契约。

退出码：0 = 无漂移；1 = 有漂移或取不到可比对的接口集合。
本脚本任何情况下都不许"没比上也算过"——取不到数据就是失败。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = REPO_ROOT / "docs" / "contracts" / "openapi.yaml"

_PARAM_SHAPE = re.compile(r"\{[^/}]+\}")


def _fail(msg: str) -> int:
    print(f"[check-drift] 无法完成比对：{msg}", file=sys.stderr)
    return 1


def _normalize(path: str) -> str:
    """把路径参数名抹平成 `{}`，只比 URL 形状。"""
    return _PARAM_SHAPE.sub("{}", path)


#: OpenAPI path item 里合法的 HTTP 方法键。**用白名单而不是黑名单**：
#: 首版用黑名单漏掉了 `operationId` / `responses`，于是契约里一处缩进错误
#: （方法键没写、操作直接挂在 path item 上）被本脚本读成了"两个幽灵接口"。
#: 黑名单永远追不上 path item 的合法键，白名单一次到位。
_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
_IGNORED_METHODS = {"trace", "options", "head"}


def _methods_of(item: object) -> set[str]:
    """OpenAPI path item 里的方法键。非 HTTP 键（parameters/responses/…）一律不算接口。"""
    if not isinstance(item, dict):
        return set()
    return {m.lower() for m in item
            if m.lower() in _HTTP_METHODS and m.lower() not in _IGNORED_METHODS}


def contract_endpoints() -> tuple[set[tuple[str, str]], dict]:
    import yaml  # 第三方包，与 ci-contract.yml 的安装清单同步

    doc = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    paths = (doc or {}).get("paths") or {}
    return {(_normalize(p), m) for p, item in paths.items() for m in _methods_of(item)}, paths


def code_endpoints() -> tuple[set[tuple[str, str]], dict]:
    """从后端代码取它自己声明的接口集合（不读磁盘上的 yaml，避免自证）。"""
    for p in (REPO_ROOT / "packages", REPO_ROOT / "packages" / "backend" / "src"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from app.main import app  # 导入即注册路由；建表在 lifespan，不在导入期

    doc = app.openapi()
    paths = (doc or {}).get("paths") or {}
    return {(_normalize(p), m) for p, item in paths.items() for m in _methods_of(item)
            if p.startswith("/api/")}, paths


def dangling_refs(paths: dict) -> list[str]:
    """契约里指向不存在 schema 的 `$ref`（删了 schema 忘了改引用的典型症状）。"""
    import yaml
    api = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    have = set((api or {}).get("components", {}).get("schemas", {}).keys())

    bad: list[str] = []

    def walk(node: object, where: str) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[-1]
                if name not in have:
                    bad.append(f"{where} → {ref}")
            for k, v in node.items():
                walk(v, f"{where}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")

    walk(paths, "paths")
    return bad


def main() -> int:
    if not CONTRACT.exists():
        return _fail(f"契约文件不存在：{CONTRACT}")

    try:
        c_set, c_paths = contract_endpoints()
    except Exception as exc:  # noqa: BLE001 - 契约解析不了就是漂移检查失败，不能算过
        return _fail(f"解析 openapi.yaml 失败：{type(exc).__name__}: {exc}")

    try:
        k_set, k_paths = code_endpoints()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"导入后端取路由失败：{type(exc).__name__}: {exc}")

    if not c_set:
        return _fail("契约里一个接口都没解析出来（paths 段为空或结构不对）")
    if not k_set:
        return _fail("后端一个 /api/ 接口都没注册（路由没挂上？）")

    only_contract = sorted(c_set - k_set)
    only_code = sorted(k_set - c_set)

    print("=== 契约漂移检查：openapi.yaml ↔ 后端路由 ===")
    print(f"  契约声明 {len(c_set)} 个 (path, method)")
    print(f"  代码注册 {len(k_set)} 个 /api/ (path, method)")
    print("  路径参数只比形状（{job_id} 与 {id} 视为同一），方法不含 head/options/trace")

    rc = 0
    if only_contract:
        rc = 1
        print(f"\n❌ 契约有、后端没有（前端按契约打过来就是 404）：{len(only_contract)} 个")
        for p, m in only_contract:
            print(f"   {m.upper():6} {p}")
    if only_code:
        rc = 1
        print(f"\n❌ 后端有、契约没登记（裸奔接口，不参与类型生成与对齐校验）：{len(only_code)} 个")
        for p, m in only_code:
            print(f"   {m.upper():6} {p}")

    bad = dangling_refs(c_paths)
    if bad:
        rc = 1
        print(f"\n❌ 契约里的悬空 $ref（指向已不存在的 schema）：{len(bad)} 处")
        for line in bad:
            print(f"   {line}")

    if rc == 0:
        print("\n✅ 接口集合与契约一致，无悬空引用")
    else:
        print("\n处置：要么把契约补上/删掉对应条目，要么把后端补齐。"
              "契约是唯一事实来源（AGENTS 规则 1），改哪一侧都必须在同一笔提交里做完。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
