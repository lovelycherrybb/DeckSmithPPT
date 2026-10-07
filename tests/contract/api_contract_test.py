"""API 契约测试：后端**真实响应**必须符合 `docs/contracts/openapi.yaml` 声明的形状。

前身只有 12 行——断言契约文件存在，然后写着 `TODO: 选型后接入 schema 校验`。
那是第四盏假绿灯：它的文件名就叫"API 契约测试"，看 CI 的人会以为响应形状有人守着。

**选型**：不引入 schemathesis。它是属性级 fuzz（自动生成随机入参打接口），价值在
探边界，但它要 ASGI transport + 一堆额外依赖，且失败面太宽（随机入参打到未加固的
接口上，红得不稳定，红一次就被当成 flaky 忽略掉）。这里要守的是更窄的一件事：
**出参形状与契约一致**，用 `jsonschema` 直接校验真实响应即可，零随机性、可复现。
决策与取舍见 ADR-026。

**校验强度**：`additionalProperties: false` 是刻意加的。FastAPI 的 `response_model`
会把输出裁剪到 pydantic 模型的字段集，所以"后端多吐了契约里没有的字段"这条物理上
不会发生；反过来"契约声明了但模型没有的字段"一定会在这里红——而那正是契约在说谎。

**为什么要把 `nullable` 归一化**：契约头写的是 `openapi: 3.1.0`，但正文里有 18 处
3.0 语法的 `nullable: true`。3.1 对齐 JSON Schema，合法写法是 `type: [T, "null"]`。
`jsonschema` 不认识 `nullable`，会当未知关键字忽略，于是"可为 null"的字段一旦真返回
null 就被判违规——**假红**。本文件先归一化再校验，同时把这件事登记成一条独立断言，
免得它永远只活在注释里。
"""
from __future__ import annotations

import copy
from pathlib import Path

import jsonschema
import yaml

REPO = Path(__file__).resolve().parents[2]
OPENAPI = REPO / "docs" / "contracts" / "openapi.yaml"

HTTP_METHODS = ("get", "put", "post", "patch", "delete")

#: (方法, 路径) → 该接口在空库 + 访客身份下必须返回 200 且形状合规。
#: 只挑**无需入参、无需既有数据**的读接口：这类接口的响应完全由代码决定，
#: 校验它才是"契约 vs 实现"的比对，而不是"测试数据 vs 测试数据"。
RESPONSE_CASES: tuple[tuple[str, str], ...] = (
    ("get", "/api/version"),
    ("get", "/api/engines"),
    ("get", "/api/settings/capacity"),
    ("get", "/api/auth/me"),
    ("get", "/api/projects"),
    ("get", "/api/jobs"),
)


def _load_doc() -> dict:
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))


def _normalize_nullable(node):
    """把 3.0 语法的 `nullable: true` 改写成 3.1/JSON Schema 的联合类型。"""
    if isinstance(node, dict):
        for v in list(node.values()):
            _normalize_nullable(v)
        if node.pop("nullable", None) is True:
            t = node.get("type")
            if isinstance(t, str):
                node["type"] = [t, "null"]
            elif t is None:
                node.setdefault("type", [
                    "string", "number", "integer", "boolean", "object", "array", "null"])
    elif isinstance(node, list):
        for v in node:
            _normalize_nullable(v)
    return node


DOC = _normalize_nullable(copy.deepcopy(_load_doc()))
COMPONENTS = DOC.get("components", {})


def _force_closed(node):
    """给**有显式 properties 的** object schema 补 `additionalProperties: false`。

    只认有 properties 的对象，理由是 `type: object` 而无 properties 在 OpenAPI 里
    表示"自由对象"（本契约的 `EngineInfo.params_schema` 就是——它装的是一整份
    JSON Schema，键名天然不可枚举）。对它也关门会造成假红。
    """
    if isinstance(node, dict):
        if "properties" in node:
            node.setdefault("additionalProperties", False)
        for k, v in list(node.items()):
            if k != "additionalProperties":
                node[k] = _force_closed(v)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            node[i] = _force_closed(v)
    return node


# 关门作用在 components 上，于是所有 `$ref` 目标一并收紧；
# 不这么做的话"严格"只覆盖响应最外一层，DTO 里层的字段漂移照样漏过。
_force_closed(COMPONENTS)


def _operation(method: str, path: str) -> dict:
    item = DOC["paths"][path]
    op = item.get(method)
    assert isinstance(op, dict), f"契约里 {method.upper()} {path} 没有操作对象（缩进或方法键写漏了）"
    return op


def _success_schema(method: str, path: str) -> dict | None:
    """取该操作 200 响应的 application/json schema；没有就返回 None。"""
    responses = _operation(method, path).get("responses") or {}
    spec = responses.get("200") or responses.get("2XX") or {}
    content = spec.get("content") or {}
    for ct, media in content.items():
        if ct.endswith("json"):
            return media.get("schema")
    return None


def _validator_schema(op_schema: dict, *, strict: bool) -> dict:
    """把操作级 schema 包进带 components 的根里，让 `#/components/schemas/X` 解析得到。

    `allOf` 而不是直接塞：`$ref` 在根上会替换整个 schema，包一层才保留 components。
    """
    s = copy.deepcopy(op_schema)
    if strict:
        s = _force_closed(s)
    return {"allOf": [s], "components": COMPONENTS}


def _force_closed(node):
    """递归补 `additionalProperties: false`（仅对 object 型 schema）。"""
    if isinstance(node, dict):
        if node.get("type") == "object" or ("properties" in node and "type" not in node):
            node.setdefault("additionalProperties", False)
        for k, v in list(node.items()):
            if k != "additionalProperties":
                node[k] = _force_closed(v)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            node[i] = _force_closed(v)
    return node


# ── 契约自身的健康度 ──

def test_契约可解析且声明了接口() -> None:
    assert DOC.get("openapi", "").startswith("3."), "契约不是 OpenAPI 3.x"
    assert DOC.get("paths"), "契约里没有 paths 段"


def test_契约不再用_3_0_的_nullable_语法() -> None:
    """登记式断言：3.1 文档里不该再有 `nullable`。

    现在它**已知为红**吗？不——本文件不打算留一条永远红的测试。这一条查的是
    "归一化之前契约里还有多少处 3.0 语法"，并把数字钉死：只准减少，不准增加。
    等哪天全部改完，把上限调成 0。这是刻意的过渡口径：一次性改 18 处契约写法
    不在"把假绿灯改成真检查"这一批的范围里，但也不能让它继续无声增长。
    """
    raw = OPENAPI.read_text(encoding="utf-8")
    found = sum(1 for line in raw.splitlines() if line.strip().startswith("nullable:"))
    assert found <= 18, (
        f"`nullable:` 出现 {found} 处，超过已登记的 18 处。openapi: 3.1.0 的合法写法是 "
        f"type: [T, \"null\"]；新增处数会让 jsonschema 把它当未知关键字忽略，"
        f"于是可为 null 的字段一旦真返回 null 就假红。")


def test_契约里每个_components_ref_都存在() -> None:
    have = set(COMPONENTS.get("schemas", {}))
    bad: list[str] = []

    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[-1]
                if name not in have:
                    bad.append(f"{where} → {ref}")
            for k, v in node.items():
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")

    walk(DOC.get("paths", {}), "paths")
    assert not bad, "契约存在悬空 $ref：\n  " + "\n  ".join(bad)


# ── 真实响应 vs 契约 ──

def test_接口在契约里都有声明() -> None:
    missing = [f"{m.upper()} {p}" for m, p in RESPONSE_CASES
               if p not in DOC.get("paths", {}) or m not in (DOC["paths"].get(p) or {})]
    assert not missing, f"用例表里的接口没在契约中声明：{missing}"


def test_真实响应符合契约声明的形状(client, visitor) -> None:
    """逐个打接口，拿契约声明的 200 schema 校验真实字节。"""
    problems: list[str] = []
    checked = 0
    for method, path in RESPONSE_CASES:
        schema = _success_schema(method, path)
        if schema is None:
            problems.append(f"{method.upper()} {path}：契约没声明 200 的 JSON schema")
            continue
        resp = client.request(method, path)
        if resp.status_code != 200:
            problems.append(f"{method.upper()} {path}：期望 200，实得 {resp.status_code}"
                            f"（{resp.text[:120]}）")
            continue
        try:
            jsonschema.validate(resp.json(), _validator_schema(schema, strict=True))
        except jsonschema.ValidationError as exc:
            trail = "$" + "".join(f"[{p!r}]" for p in list(exc.absolute_path)[:6])
            problems.append(f"{method.upper()} {path}：{trail} {exc.message}")
        except Exception as exc:  # noqa: BLE001 - 解析不了响应体同样是不合规
            problems.append(f"{method.upper()} {path}：响应无法校验（{type(exc).__name__}: {exc}）")
            continue
        checked += 1

    assert checked >= 5, f"实际被校验的接口太少（{checked}），这条测试等于没跑"
    assert not problems, "真实响应与契约不符：\n  " + "\n  ".join(problems)


def test_引擎列表的每个参数说明书都符合引擎契约(client) -> None:
    """/api/engines 回的是"参数说明书"，它的事实源是 engine.schema.json 的 `$defs`。

    这里不重做 check-alignment.py 的活，只守一条最容易破的：注册表吐出的每个引擎
    都必须带着契约里对应的参数定义，且 `engine_type` 只能是 draft / output 两值之一。
    键名以契约为准（`engine_type` / `params_schema`），不要凭印象写成 `type` / `params`
    —— 上一版就写错过一次，被自己的严格校验当场打回。
    """
    import json

    schema = json.loads((REPO / "docs" / "contracts" / "engine.schema.json")
                        .read_text(encoding="utf-8"))
    defs = schema.get("$defs", {})
    rows = client.get("/api/engines").json()
    assert rows, "引擎列表为空——注册表没挂上，这条测试就失去了对象"
    for e in rows:
        assert e["engine_type"] in ("draft", "output"), \
            f"{e['name']} 的 engine_type 非法：{e.get('engine_type')}"
        assert e["name"] in defs, (
            f"引擎 {e['name']} 在 engine.schema.json 的 $defs 里没有定义，"
            f"但已经进了注册表（前端拿不到参数表单）")
        assert e.get("params_schema"), f"引擎 {e['name']} 没带回参数说明书"
