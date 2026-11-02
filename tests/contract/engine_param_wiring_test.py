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
    """一段 Python 源码里被真实取值用到的键名。

    只认三种形态，**不接受"键名作为普通字符串出现过"**——2026-11-02 做破坏验证时发现
    那种宽松写法有洞：把 `if ep.get("primary_language")` 整行删掉、只留下 prompt 里那句
    文案，检查照样绿，等于假接线糊弄过了真判据。

    1. **直接取值**：`ep.get("mode", ...)` 或 `ep["mode"]`。
    2. **动态选键**：键名先赋给变量、该变量再被取值。例如 guizang 的
       `theme_key = "magazine_theme" if style == "magazine" else "swiss_theme"`
       后跟 `params.get(theme_key)`——它确实接了线，形态 1 抓不到，故单列。
    3. 形态 2 的赋值只认"直接字符串"或"三元/或运算里的字符串"，避免把无关常量卷进来。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    direct: set[str] = set()
    # 变量名 → 该变量可能携带的键名
    keyed_vars: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            consts = {
                c.value for c in ast.walk(node.value)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            }
            if consts and isinstance(
                node.value, (ast.Constant, ast.IfExp, ast.BoolOp)
            ):
                keyed_vars.setdefault(node.targets[0].id, set()).update(consts)
        # 循环取键：`for key, label in (("audience", "受众"), ...)` 或 `for key in ("a", "b")`
        # ——不认这种写法就会对合法代码假红，而假红会让人去放宽判据，那才是真事故。
        elif isinstance(node, ast.For):
            consts: set[str] = set()
            if isinstance(node.iter, (ast.Tuple, ast.List)):
                for elt in node.iter.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        consts.add(elt.value)
                    elif isinstance(elt, (ast.Tuple, ast.List)) and elt.elts:
                        first = elt.elts[0]
                        if isinstance(first, ast.Constant) and isinstance(first.value, str):
                            consts.add(first.value)
            names: list[str] = []
            if isinstance(node.target, ast.Name):
                names = [node.target.id]
            elif isinstance(node.target, ast.Tuple) and node.target.elts:
                # 解包赋值时键在第一位（`for key, label in ...`），后面的标签位不算键
                first_target = node.target.elts[0]
                if isinstance(first_target, ast.Name):
                    names = [first_target.id]
            for n in names:
                keyed_vars.setdefault(n, set()).update(consts)

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                direct.add(arg.value)
            elif isinstance(arg, ast.Name):
                direct |= keyed_vars.get(arg.id, set())
        elif isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                direct.add(sl.value)
            elif isinstance(sl, ast.Name):
                direct |= keyed_vars.get(sl.id, set())
    return direct


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

# 参数控件代码段的起止锚点。只此一处：生成弹窗按契约 schema 动态渲染参数，
# （原先还有一个 EngineParamsForm.tsx，全仓无引用、且会把枚举首项与 false 当默认值
#  预填进表单，违反"未选不下发"，2026-11-02 经龚成同意删除。）
_PARAM_WIDGET_SOURCES = {
    "packages/frontend/src/components/workspace/DraftsPanel.tsx": (
        "const renderEngineParam = (", "\n  };\n",
    ),
}


def test_engine_param_selects_are_all_clearable() -> None:
    offenders: list[str] = []
    for rel, (start_marker, end_marker) in _PARAM_WIDGET_SOURCES.items():
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        start = text.index(start_marker)
        end = text.index(end_marker, start)
        # 只截参数控件那一段：文件后面还有别的下拉（如弹窗底部的其它控件），算进来就是假红
        window = text[start:end]
        widgets = window.count("<Select") + window.count("<AutoComplete")
        clearable = window.count("allowClear")
        assert widgets > 0, f"{rel} 里没找到参数控件，检查锚点是否失效"
        if widgets != clearable:
            offenders.append(f"{rel}: 控件 {widgets} 个，可清空 {clearable} 个")
    assert not offenders, (
        f"引擎参数控件清不掉，用户回不到「没选」：{offenders}。"
        "参数区的每个控件（下拉与可写输入框）都必须带 allowClear。"
    )


# ── ADR-031 扩面之后：新参数不许只改契约、文档与界面各漏一处 ────────

def _visible_params(engine_name: str) -> dict:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    props = data["$defs"][engine_name].get("properties", {})
    return {k: v for k, v in props.items() if not v.get("x-hidden")}


def _frontend_label_tables() -> tuple[dict[str, str], list[str]]:
    """解析前端的中文名表与"已有基础字段"名单——判据必须跟界面同一口径。"""
    ts = (REPO_ROOT / "packages" / "frontend" / "src" / "lib" / "engineParams.ts").read_text(encoding="utf-8")
    block = ts.split("PARAM_LABELS", 1)[1].split("};", 1)[0]
    labels = dict(re.findall(r"^  ([a-z_]+): '([^']+)',", block, re.MULTILINE))
    base_dup = re.findall(r"'([a-z_]+)'", ts.split("BASE_DUP_PARAM_KEYS", 1)[1].split("]", 1)[0])
    return labels, base_dup


def _param_label(key: str, spec: dict, labels: dict[str, str]) -> str:
    """复刻前端 paramLabel()：固定表优先 → 去条件说明 → 去括号注释 → 冒号句号截断。"""
    if labels.get(key):
        return labels[key]
    desc = (spec.get("description") or "").strip()
    desc = re.sub(r"（[^（）]*仅\s*[a-zA-Z_][\w.]*\s*=[^（）]+?时有效[^（）]*）", "", desc)
    desc = re.sub(r"（[^）]*）", "", desc)
    return desc.split("。")[0].split("：")[0].strip()


def _checkable_params() -> list[tuple[str, str, dict]]:
    """要对外露出的参数：隐藏旋钮与"已有基础字段"都不算。"""
    _, base_dup = _frontend_label_tables()
    return [
        (name, key, spec)
        for name in output_engine_names()
        for key, spec in _visible_params(name).items()
        if key not in base_dup
    ]


def test_visible_params_have_chinese_labels() -> None:
    """界面标签拿得到中文，否则用户看到的是英文参数名。"""
    labels, _ = _frontend_label_tables()
    bare = [
        f"{name}.{key}"
        for name, key, spec in _checkable_params()
        if not any("一" <= ch <= "鿿" for ch in _param_label(key, spec, labels))
    ]
    assert not bare, f"这些参数在界面上会显示成英文键名，用户读不懂：{bare}"


def test_visible_params_are_documented() -> None:
    """每个对外可见的参数都必须能在 style-guide.md 里按它的中文名查到。

    查的是"界面实际显示的那个中文名"，不是英文键、也不是没清洗过的描述——判据跟用户
    看到的东西对齐，才不会一边假红、一边真漏。
    """
    labels, _ = _frontend_label_tables()
    guide = (REPO_ROOT / "docs" / "contracts" / "style-guide.md").read_text(encoding="utf-8")
    missing = [
        f"{name}.{key}（界面显示「{_param_label(key, spec, labels)}」）"
        for name, key, spec in _checkable_params()
        if _param_label(key, spec, labels) not in guide
    ]
    assert not missing, (
        f"这些参数界面上有、说明文档里查不到：{missing}。"
        "用户照着文档找不到这一项该怎么填（AGENTS 规则 9）。"
    )


def test_divergence_presets_match_between_contract_and_doc() -> None:
    """发散度的三句示例：契约里给界面的预设与文档里写的必须是同一三句。"""
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    presets = [str(p) for p in data["$defs"]["ppt_master_v6"]["properties"]["content_divergence"]["x-presets"]]
    guide = (REPO_ROOT / "docs" / "contracts" / "style-guide.md").read_text(encoding="utf-8")
    section = guide.split("### 材料发散度的三句示例", 1)[1].split("留空＝", 1)[0]
    documented = re.findall(r"^\d+\.\s*(.+)$", section, re.MULTILINE)
    assert presets == documented, (
        f"发散度预设句两处不一致：契约 {presets} / 文档 {documented}"
    )


def test_adr031_params_are_actually_emitted_when_selected() -> None:
    """九项参数逐项验：选了就必须出现在 prompt 里；一个都不选时一行都不许出现。"""
    from engine.impl.ppt_master_v6.engine import PptMasterV6Engine as V6

    cases = {
        "content_divergence": "严格贴着素材来",
        "delivery_purpose": "presentation",
        "allow_web_research": False,
        "primary_language": "en-US",
        "image_usage": ["provided", "placeholder"],
        "narration_audio": True,
        "audience": "业主方管理层",
        "communication_intent": "推动立项决策",
        "core_message": "现在投入成本最低",
    }
    prompt = V6._build_prompt("标题", 10, "内容", cases)
    # 逐项验"选了必须落到 prompt 里"。布尔项落到的是翻译后的指令句，不是字面 true/false，
    # 所以判据按每项该出现的那句话写，不按键名写。
    expected = (
        "content_divergence（材料发散度）：严格贴着素材来",
        "delivery_purpose（阅读模式）：presentation",
        "外部补料：禁止——只用用户给的素材",
        "primary_language（主语言）：en-US",
        "image_usage（配图来源）：provided、placeholder",
        "proactive_narration_audio（旁白音频）：true",
        "audience（受众）：业主方管理层",
        "communication_intent（沟通意图）：推动立项决策",
        "core_message（核心信息）：现在投入成本最低",
    )
    for token in expected:
        assert token in prompt, f"选了却没进 prompt：{token}"

    empty = V6._build_prompt("标题", 10, "内容", {})
    for token in ("材料发散度", "阅读模式", "外部补料", "主语言", "配图来源", "旁白音频", "受众", "沟通意图", "核心信息"):
        assert token not in empty, f"用户什么都没选，prompt 里却出现了「{token}」"


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


# 只在文案里提了一句键名、根本没从入参取值——2026-11-02 破坏验证时抓出来的假接线形状
_MENTION_ONLY_SOURCE = '''
class FakeWired:
    def _build_prompt(self, ep):
        return "- primary_language（主语言）：auto\\n- mode：pyramid"
'''


def test_mention_in_prompt_text_does_not_count_as_reading() -> None:
    """键名只出现在发给技能的文案里，不算接线。"""
    read = read_param_keys(_MENTION_ONLY_SOURCE)
    assert unwired(["primary_language", "mode"], read) == ["primary_language", "mode"], (
        f"守卫有洞：光在 prompt 里提一句就被判成已接线：{sorted(read)}"
    )


def test_guard_passes_the_fixed_engine() -> None:
    """对照组的另一半：修好后同一判据必须放行，否则判据本身是坏的。"""
    assert unwired_params("ppt_master", engine_class("ppt_master")) == [], (
        "ppt_master 仍有未接线的契约参数"
    )
