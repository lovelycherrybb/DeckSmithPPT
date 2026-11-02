"""引擎参数接线契约：契约对外承诺的参数，引擎必须真的读。

起因（2026-10-31 实测）：`ppt_master` 的 `params_schema` 声明了 5 个参数，前端照声明
渲染出 5 个控件，但引擎代码里只读了 `style` 一个——`mode` / `format` / `notes` /
`animation` 四个选了等于没选，界面上是四个假控件。既有契约测试只校验 schema 形状，
对"声明与实现是否对得上"完全无感，所以全绿照样漏。

判据（AGENTS 规则 1「契约是唯一事实来源」的机器化）：
    对每个**输出引擎**，`params_schema.properties` 里的每个键，都必须在该引擎实现类
    （含其 impl 包内的父类）源码里被真实引用到——直接取值（`ep.get("键")` / `ep["键"]`）
    或动态选键（键名先存进变量再取值）都算。少一个即报红。

刻意不收紧的地方：
- 反方向也管，但只认精确形态（从 `ep` / `params` / `engine_params` 上取值），不去猜
  所有字符串常量——否则 job.json 字段、状态名都会误判成引擎参数，假红一片。
- `x-hidden` 的参数照样要接（它们只是不在 UI 显示，不是不生效）。
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT / "packages", REPO_ROOT / "packages" / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.engines.registry import engine_class, output_engine_names  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "docs" / "contracts" / "engine.schema.json"
IMPL_ROOT = (REPO_ROOT / "packages" / "engine" / "impl").resolve()


# ── 判据本体：纯函数，不碰文件系统，便于自证有牙 ─────────────────

def read_param_keys(source: str) -> set[str]:
    """一段 Python 源码里被真实引用到的键名。

    两种写法都算数（缺一不可，否则换个写法就假红）：

    1. **直接取值**：`ep.get("mode", ...)` 或 `ep["mode"]`。
    2. **动态选键**：键名作为独立字符串常量出现，例如 guizang 的
       `theme_key = "magazine_theme" if style == "magazine" else "swiss_theme"`
       ——它先把键名存进变量再取值，AST 上并不构成形态 1，但确实是接了线的。

    刻意**不算**的：注释（AST 根本看不见它）、以及把多个键名混进同一句话的字符串
    （`label = "mode format notes animation"` 是一个常量，不等于其中任何一个键名）。
    所以"写行注释糊弄过去"这条路是堵住的；代价是"凭空赋值一个没人用的同名常量"
    糊得过去——那不是本检查要防的失败模式（历史上的 bug 是压根没写）。
    """
    keys: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return keys
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            keys.add(node.value)
    return keys


def unwired(declared: list[str], read: set[str]) -> list[str]:
    """声明了但没读的键。"""
    return [k for k in declared if k not in read]


# ── 解析：把"实现类"换成它的源码，Protocol / 基类占位不算实现 ─────

def class_sources(cls) -> list[str]:
    """沿 MRO 收集本仓库 impl 包内的类源码。

    只认 `packages/engine/impl/` 之下的类：协议与基类里的占位定义不算实现
    （与 `registry.implements_hook` 同一口径）。
    """
    out: list[str] = []
    for klass in getattr(cls, "__mro__", (cls,)):
        try:
            module_file = Path(inspect.getsourcefile(klass) or "")
        except TypeError:
            continue
        if not module_file.is_file() or not module_file.resolve().is_relative_to(IMPL_ROOT):
            continue
        try:
            out.append(inspect.getsource(klass))
        except (OSError, TypeError, IndentationError):
            continue
    return out


def declared_params(engine_name: str) -> list[str]:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return sorted(data["$defs"][engine_name].get("properties", {}))


def unwired_params(engine_name: str, impl_cls) -> list[str]:
    read: set[str] = set()
    for src in class_sources(impl_cls):
        read |= read_param_keys(src)
    return unwired(declared_params(engine_name), read)


# ── 反方向：引擎从 engine_params 取的键，必须有地方能把它送进来 ─────
#
# 正向判据管"契约承诺了、引擎没接"；这条管"引擎伸手取了、但没人能喂"。
# content_style 就是被这条抓出来的：三个引擎都读它，可契约没声明、界面没入口、
# 认领时也没并进 engine_params——每一单都只拿到硬编码默认值。
#
# 只认从已知入参变量（ep / params / engine_params）上取值这一种精确形态，
# 不去猜所有字符串常量：否则 job.json 的字段、状态名都会被判成"引擎参数"，假红一片。

_PARAMS_VARS = {"ep", "params", "engine_params"}


def params_reads(source: str) -> set[str]:
    """该源码里从 engine_params 字典上真实取用的键名。"""
    keys: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return keys
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in _PARAMS_VARS
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in _PARAMS_VARS
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return keys


def _deck_request_fields() -> list[str]:
    """openapi 里 DeckRequest 的顶层字段（通用层，跨引擎）。"""
    import yaml  # noqa: PLC0415 - 只有这一条用例需要它，不提到文件头污染其余路径

    spec = yaml.safe_load((REPO_ROOT / "docs" / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    return sorted(spec["components"]["schemas"]["DeckRequest"]["properties"])


def test_every_param_the_engine_reads_can_actually_be_delivered() -> None:
    """引擎取的键，要么该引擎契约里声明了，要么是通用层字段且后端确实并进引擎入参。"""
    generic = set(_deck_request_fields())
    from app.service.scheduler import _params_for_engine  # noqa: PLC0415

    # 通用层字段要能落到引擎手里，必须在这里被显式合并——没合并就等于通路仍断着
    merged = _params_for_engine({"content_style": "briefing", "engine_params": {}})
    injected = set(merged)
    unreachable: list[str] = []
    for name in output_engine_names():
        declared = set(declared_params(name))
        for key in sorted(params_reads_from_all(name)):
            if key in declared:
                continue
            if key in generic and key in injected:
                continue
            unreachable.append(f"{name}.{key}")
    assert not unreachable, (
        f"引擎读了但没人能喂的键：{unreachable}。要么在 "
        "docs/contracts/engine.schema.json 该引擎的 params_schema 里声明它（界面才会出现入口），"
        "要么在 app/service/scheduler.py 的 _params_for_engine 里把通用层字段并进引擎入参，"
        "要么就别读它——留着读取点只会让默认值冒充用户的选择。"
    )


def params_reads_from_all(engine_name: str) -> set[str]:
    keys: set[str] = set()
    for src in class_sources(engine_class(engine_name)):
        keys |= params_reads(src)
    return keys


def test_reverse_guard_catches_the_ghost_param_shape() -> None:
    """把世界还原成修复前（契约不声明 + 认领不并入），反向判据必须点名 content_style。

    这条是"守卫有牙"的自证：不写它，上面那条正向检查全绿也证明不了反方向查得出来。
    """
    from app.service.scheduler import _params_for_engine  # noqa: PLC0415

    keys = params_reads_from_all("ppt_master")
    assert "content_style" in keys, "前提：引擎确实在从 engine_params 取 content_style"

    declared_without = set(declared_params("ppt_master")) - {"content_style"}
    injected_without = set(
        _params_for_engine({"content_style": "briefing", "engine_params": {}})
    ) - {"content_style"}
    generic = set(_deck_request_fields())
    orphan = [
        k for k in sorted(keys)
        if k not in declared_without and not (k in generic and k in injected_without)
    ]
    assert orphan == ["content_style"], (
        f"反向判据没牙：修复前的形状本该被抓出 content_style，实际得到 {orphan}"
    )


# ── 取值集合唯一来源：openapi 是权威，别处只许相等、不许另立 ───────

def test_content_style_value_set_has_one_owner() -> None:
    """内容风格的取值集合：openapi 是唯一来源，契约说明表 / 引擎契约 / 前端文案表必须逐项相等。

    这条同时管"文档说的是不是假话"——修它之前，`style-guide.md` 里那五个风格 ID
    （business_report / educational / …）在代码里一处都找不到，是纯过期文档。
    """
    import yaml  # noqa: PLC0415

    spec = yaml.safe_load((REPO_ROOT / "docs" / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    authority = spec["components"]["schemas"]["DeckRequest"]["properties"]["content_style"]["enum"]

    ts = (REPO_ROOT / "packages" / "frontend" / "src" / "components" / "ui" / "StatusTag.tsx").read_text(encoding="utf-8")
    block = ts.split("CONTENT_STYLE_LABELS", 1)[1].split("};", 1)[0]
    # 只认 `值: '中文名'` 这种条目行，声明行本身（`Record<string, string> = {`）也带冒号，别混进来
    labels = re.findall(r"^\s*([a-z_]+):\s*'", block, re.MULTILINE)

    guide = (REPO_ROOT / "docs" / "contracts" / "style-guide.md").read_text(encoding="utf-8")
    section = guide.split("## 内容风格", 1)[1].split("\n## ", 1)[0]
    doc_ids = re.findall(r"^\|\s*([a-z_]+)\s*\|", section, re.MULTILINE)

    for name in output_engine_names():
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        prop = data["$defs"][name].get("properties", {}).get("content_style")
        if prop is None:
            continue  # 该引擎不声明这个参数，自然不必对齐
        assert prop["enum"] == authority, f"{name} 契约里的 content_style 取值集合与 openapi 不一致"

    assert sorted(labels) == sorted(authority), (
        f"前端内容风格文案表与 openapi 的取值集合不一致：前端 {sorted(labels)} / 契约 {sorted(authority)}"
    )
    assert sorted(doc_ids) == sorted(authority), (
        f"style-guide.md 的内容风格表与 openapi 不一致：文档 {sorted(doc_ids)} / 契约 {sorted(authority)}"
        "——文档写错比没写更坏，用户照着它选不到东西"
    )


# ── 未选即不下发：留空必须是"没表态"，不是"替用户选了默认值" ──────

def test_engine_omits_unselected_params() -> None:
    from engine.impl.ppt_master.engine import PptMasterEngine
    from engine.impl.ppt_master_v6.engine import PptMasterV6Engine

    for cls in (PptMasterEngine, PptMasterV6Engine):
        empty = cls._build_prompt("标题", 10, "内容", {})
        for token in ("视觉风格", "叙事模式", "画布尺寸", "演讲备注", "内容风格"):
            assert token not in empty, f"{cls.__name__} 在用户没选时仍把「{token}」写进了 prompt"

        picked = cls._build_prompt("标题", 10, "内容", {"mode": "pyramid", "notes": False})
        assert "叙事模式" in picked, f"{cls.__name__} 丢了用户真选的模式"
        assert "演讲备注" in picked and "false" in picked, (
            f"{cls.__name__} 把 notes=false 当成没选丢掉了——关是用户的真实选择"
        )
        assert "视觉风格" not in picked, f"{cls.__name__} 替没选风格的会话填了默认值"


# ── 支持不选：参数区每个下拉都必须能清空 ────────────────────────
#
# 2026-10-31 浏览器验收时实测发现的真问题：参数一旦选上就没有清除入口，
# 用户回不到"没选"。后端与引擎都按"未选不下发"实现了，界面上却清不掉，
# 等于这条规则只做了一半。判据取"参数区里 <Select 的个数 == allowClear 的个数"，
# 与 tests/contract/table_actions_fixed_test.py 同一类源码看守。

_PARAM_WIDGET_SOURCES = {
    "packages/frontend/src/components/workspace/DraftsPanel.tsx": "genParamEntries.map(",
    "packages/frontend/src/components/ui/EngineParamsForm.tsx": "entries.map(",
}


def test_engine_param_selects_are_all_clearable() -> None:
    offenders: list[str] = []
    for rel, marker in _PARAM_WIDGET_SOURCES.items():
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        start = text.index(marker)
        # 只看参数渲染区，前面的「输出引擎」是必填项、本来就不该能清空
        window = text[start:]
        selects = window.count("<Select")
        clearable = window.count("allowClear")
        if selects != clearable:
            offenders.append(f"{rel}: 下拉 {selects} 个，可清空 {clearable} 个")
    assert not offenders, (
        f"引擎参数控件清不掉，用户回不到「没选」：{offenders}。"
        "参数区的每个 Select 都必须带 allowClear。"
    )


# ── 通用层字段 → 引擎入参的合并本身要能红 ────────────────────────

def test_generic_field_reaches_engine_params() -> None:
    from app.service.scheduler import _params_for_engine

    assert _params_for_engine({"content_style": "briefing", "engine_params": {}}) == {
        "content_style": "briefing"
    }, "行上的内容风格没并进引擎入参（幽灵参数原地回来）"
    assert _params_for_engine({"content_style": "briefing", "engine_params": {"content_style": "event_promo"}}) == {
        "content_style": "event_promo"
    }, "提交期显式传参被行上的值覆盖了"
    assert _params_for_engine({"content_style": None, "engine_params": {}}) == {}, (
        "用户没选时不该凭空造出一个键"
    )


# ── 主判据：按注册表枚举，新引擎自动纳入 ─────────────────────────

def test_every_declared_engine_param_is_actually_read() -> None:
    broken: list[str] = []
    for name in output_engine_names():
        missing = unwired_params(name, engine_class(name))
        if missing:
            broken.append(f"{name}: {missing}")
    assert not broken, (
        "以下输出引擎的契约参数没有接进引擎（界面上选了等于没选）："
        + "；".join(broken)
        + "。修法二选一：在 `_build_prompt` 里把它显式写进 prompt，或从 "
        "docs/contracts/engine.schema.json 该引擎的 params_schema 里摘掉——"
        "不许留着假控件（AGENTS 规则 1：契约是唯一事实来源）。"
    )


# ── 自证有牙：漏接必须能被抓出，否则这条检查形同虚设 ─────────────

# 2026-10-31 修复前的真实形状（只读 style），用作对照组
_PRE_FIX_SOURCE = '''
class PptMasterEngine:
    @staticmethod
    def _build_prompt(title, style, pages, content):
        ep = {}
        style = ep.get("style", "swiss-minimal")
        content_style = ep.get("content_style", "marketing_promo")
        return f"{title}{style}{pages}{content}{content_style}"
'''

# 把参数名混进一句注释或一整串文字里，不算引用
_DECORATION_ONLY_SOURCE = '''
class Decorated:
    def _build_prompt(self, ep):
        # ep.get("mode") 曾经在这里
        label = "mode format notes animation"
        return label
'''

# guizang 的真实形状：键名先进变量，再拿去取值。放宽判据就是为它，钉住防再假红
_DYNAMIC_KEY_SOURCE = '''
class Guizang:
    def _build_prompt(self, params, style):
        theme_key = "magazine_theme" if style == "magazine" else "swiss_theme"
        return params.get(theme_key)
'''


def test_guard_catches_the_historical_bug() -> None:
    """修复前那份源码必须被点名漏掉四个参数——一个都不能放过。"""
    read = read_param_keys(_PRE_FIX_SOURCE)
    assert {"style", "content_style"} <= read, f"对照组直接取值解析异常：{sorted(read)}"
    assert unwired(declared_params("ppt_master"), read) == [
        "animation",
        "format",
        "mode",
        "notes",
    ], "守卫失效：修复前的形状本该报红却没报"


def test_comments_and_prose_strings_do_not_count_as_reading() -> None:
    """糊弄路径要堵死：注释 AST 看不见，整串文字不等于其中任一键名。"""
    read = read_param_keys(_DECORATION_ONLY_SOURCE)
    smeared = [k for k in declared_params("ppt_master") if k in read]
    assert not smeared, f"守卫可被绕过：注释或整串文字被当成已接线：{smeared}"


def test_dynamic_key_selection_counts_as_reading() -> None:
    """先存变量再取值的写法算已接线（否则 guizang 会被判假红）。"""
    read = read_param_keys(_DYNAMIC_KEY_SOURCE)
    assert unwired(["magazine_theme", "swiss_theme"], read) == [], (
        "判据过硬：动态选键被误判为漏接"
    )


def test_guard_passes_the_fixed_engine() -> None:
    """对照组的另一半：修好后同一判据必须放行，否则判据本身是坏的。"""
    assert unwired_params("ppt_master", engine_class("ppt_master")) == [], (
        "ppt_master 仍有未接线的契约参数"
    )
