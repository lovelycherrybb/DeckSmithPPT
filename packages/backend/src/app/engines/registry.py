"""引擎注册表：登记可用引擎及其参数说明书（供 GET /api/engines 返回）。

参数说明书直接来自 docs/contracts/engine.schema.json 的 $defs.ppt_master，
保证与契约单一事实来源一致——改契约后无需改这里的代码。

引擎类型：
- draft：草稿生成引擎，将用户文件转化为 Markdown 草稿
- output：输出引擎，将草稿内容生成最终展示文件

能力位（capabilities）不写死：由「引擎类是否真实实现逐页枚举钩子 get_pages」
自动派生（见 implements_hook），新增引擎只要实现钩子就自动点亮，
前端与路由零改动——这是鸭子类型探测，不是按引擎名门控。
"""
import importlib
import inspect
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger("decksmith.engines.registry")

# 本文件位于 packages/backend/src/app/engines/registry.py
# parents[0]=engines [1]=app [2]=src [3]=backend [4]=packages [5]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[5]
_PACKAGES_DIR = Path(__file__).resolve().parents[4]
if str(_PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_DIR))
SCHEMA_PATH = REPO_ROOT / "docs" / "contracts" / "engine.schema.json"


def _load_ppt_master_params() -> dict:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data["$defs"]["ppt_master"]


def _load_ppt_master_v6_params() -> dict:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data["$defs"]["ppt_master_v6"]


def _load_ppt_master_draft_params() -> dict:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data["$defs"]["ppt_master_draft"]


def _load_claude_draft_params() -> dict:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data["$defs"]["claude_draft"]


def _load_guizang_ppt_params() -> dict:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data["$defs"]["guizang_ppt"]


PPT_MASTER_PARAMS = _load_ppt_master_params()
PPT_MASTER_V6_PARAMS = _load_ppt_master_v6_params()
PPT_MASTER_DRAFT_PARAMS = _load_ppt_master_draft_params()
CLAUDE_DRAFT_PARAMS = _load_claude_draft_params()
GUIZANG_PPT_PARAMS = _load_guizang_ppt_params()

# 引擎注册名 -> 实现类（模块路径, 类名）。经 `engine_class()` 作为**唯一解析入口**，
# 同时供实例化（factory.get_engine）与能力探测（_derive_capabilities）使用；
# 新增引擎只需在此与 ENGINE_REGISTRY 各加一项，factory 与 entrypoint 无需改动。
_ENGINE_CLASSES: dict[str, tuple[str, str]] = {
    "ppt_master": ("engine.impl.ppt_master.engine", "PptMasterEngine"),
    # 专供上游 ppt-master v6.6.0 的独立引擎（ADR-015 双引擎策略），不继承 ppt_master
    "ppt_master_v6": ("engine.impl.ppt_master_v6.engine", "PptMasterV6Engine"),
    # 草稿与输出分属两个类：能力位按实现类探测，同类注册两名会让草稿被误标 page_preview
    "ppt_master_draft": ("engine.impl.ppt_master.engine", "PptMasterDraftEngine"),
    "claude_draft": ("engine.impl.ppt_master.claude_draft", "ClaudeDraftEngine"),
    "guizang_ppt": ("engine.impl.guizang_ppt", "GuizangPptEngine"),
}

# 逐页预览的枚举钩子；实现它即点亮 page_preview 能力
PAGE_PREVIEW_HOOK = "get_pages"
# 中断运行中任务的钩子；实现它即点亮 cancel 能力（前端据此决定"停止生成"按钮可见性，
# ADR-019 决策 5）。能力位仍按实现类探测，不写死任何引擎名。
CANCEL_HOOK = "cancel"
# 逐页修改重导出的钩子；实现它即点亮 svg_edit 能力（前端据此决定"修改"入口，ADR-023）。
REVISE_HOOK = "revise"


def implements_hook(target: object, hook: str) -> bool:
    """target（引擎类或实例）是否**真实实现**可选钩子 hook。

    hasattr 语义的精细化（保持鸭子类型、不按引擎名门控）：
    - 沿 MRO 找到第一个在类 __dict__ 里定义该钩子的类：
      - 定义在 Protocol 上（如 OutputEngine 文档化的可选钩子占位）→ 不算实现；
      - 显式置 None（如 ClaudeDraftEngine 声明退出）→ 不算实现；
      - 其余 → 算真实实现。
    - 找不到定义 → 未实现。
    """
    cls = target if isinstance(target, type) else type(target)
    for klass in getattr(cls, "__mro__", (cls,)):
        if hook in vars(klass):
            fn = vars(klass)[hook]
            if fn is None:
                return False
            return not getattr(klass, "_is_protocol", False)
    return False


def revise_accepts_deck(target: object) -> bool:
    """引擎的 revise 是否真的接得住**整页清单**（deck：哪几页保留、新增、删除、调顺序）。

    为什么不能只看"实现了 revise"（2026-11-04 真跑抓出来的）：ppt_master 实现了 revise，
    签名里却没有 deck 参数。界面「保存为新版本」这条通路必须提交整页清单，于是门禁按
    "有没有实现"放行、用户一点就炸：
        TypeError: revise() got an unexpected keyword argument 'deck'
    任务被创建出来再失败，界面上只剩一个看不出原因的报错。

    能力位（svg_edit）与提交门禁统一改看这里，仍然只探测实现、不按引擎名写死（AGENTS 规则 2）。
    """
    fn = getattr(target, "revise", None)
    if fn is None:
        return False
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):        # 拿不到签名（C 实现等）→ 按不支持处理，宁关不误放
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True                        # 收 **kwargs 视为能接住
    deck = params.get("deck")
    return deck is not None and deck.kind in (
        inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD
    )


def engine_class(name: str):
    """按注册名取引擎实现类。

    公开给测试与能力探测使用，避免各处重复 `importlib + _ENGINE_CLASSES` 那段私有的导入舞蹈
    （同一知识只应有一个解析入口）。解析失败时抛异常，由调用方决定是否容忍。
    """
    module_path, cls_name = _ENGINE_CLASSES[name]
    return getattr(importlib.import_module(module_path), cls_name)


def output_engine_names() -> list[str]:
    """所有 `engine_type == "output"` 的注册名。

    排序返回，保证测试与日志的枚举顺序稳定（否则新增引擎会让 diff 里的失败列表乱跳）。
    产物落点契约测试据此做**注册表驱动**的枚举：新注册一个输出引擎，若没登记它的产物
    形状，测试当场失败——机制纳管，不靠人记得去加。
    """
    return sorted(n for n, v in ENGINE_REGISTRY.items() if v.get("engine_type") == "output")


def _derive_capabilities(name: str) -> list[str]:
    """按引擎实现类的钩子实现情况派生能力位；类不可用时保守返回空。"""
    caps: list[str] = []
    try:
        engine_cls = engine_class(name)
    except Exception:
        return caps
    if implements_hook(engine_cls, PAGE_PREVIEW_HOOK):
        caps.append("page_preview")
    if implements_hook(engine_cls, CANCEL_HOOK):
        caps.append("cancel")
    if implements_hook(engine_cls, REVISE_HOOK) and revise_accepts_deck(engine_cls):
        caps.append("svg_edit")   # 还要接得住整页清单，否则入口开了也保存不了（见上函数）
    return caps


# 引擎注册表：name 必须与 DeckRequest.engine 的枚举值一致
ENGINE_REGISTRY = {
    "ppt_master": {
        "name": "ppt_master",
        "display_name": "PPT Master 标准版",
        "description": "PPT Master 标准版生成方式，支持多种风格和画面比例",
        "engine_type": "output",  # 输出引擎
        "params_schema": PPT_MASTER_PARAMS,
    },
    "ppt_master_v6": {
        "name": "ppt_master_v6",
        "display_name": "PPT Master 6.6 版",
        "description": "PPT Master 6.6 版生成方式，比标准版多出内容类型、配图来源等可选设置",
        "engine_type": "output",  # 输出引擎
        "params_schema": PPT_MASTER_V6_PARAMS,
    },
    "ppt_master_draft": {
        "name": "ppt_master_draft",
        "display_name": "PPT Master 快速转草稿",
        "suit_for": "适合已经梳理完成的稿件",
        "description": (
            "稿件本身已经成型，直接转成草稿，内容和顺序照搬原文，改动最少、不消耗积分。"
            "支持 Word、Excel、PPT、PDF、Markdown、TXT 和网页链接。"
        ),
        "engine_type": "draft",  # 草稿引擎
        "params_schema": PPT_MASTER_DRAFT_PARAMS,
    },
    "claude_draft": {
        "name": "claude_draft",
        "display_name": "Claude 通读整理",
        "suit_for": "适合整理长篇、混合型稿件",
        "description": (
            "交 AI 通读后归纳成分章节的草稿大纲，成稿更规整，耗时数分钟并消耗积分。"
            "素材超过一万五千字时，只保留开头和结尾部分。"
        ),
        "engine_type": "draft",  # 草稿引擎
        "params_schema": CLAUDE_DRAFT_PARAMS,
    },
    "guizang_ppt": {
        "name": "guizang_ppt",
        "display_name": "Guizang 网页演示",
        "description": "生成网页形式的演示文稿，单个文件即可放映",
        "engine_type": "output",  # 输出引擎
        "params_schema": GUIZANG_PPT_PARAMS,
    },
}

# 能力位自动派生（同上，不写死任何引擎名 -> 能力的映射）
for _entry in ENGINE_REGISTRY.values():
    _entry["capabilities"] = _derive_capabilities(_entry["name"])


def list_engines(engine_type: str | None = None) -> list[dict]:
    """返回引擎列表，支持按类型筛选。"""
    engines = list(ENGINE_REGISTRY.values())
    if engine_type:
        engines = [e for e in engines if e.get("engine_type") == engine_type]
    return engines


def get_engine_info(name: str) -> dict | None:
    return ENGINE_REGISTRY.get(name)


def _param_display_name(key: str, spec: dict) -> str:
    """给用户看的参数名：取契约 description 第一句（去掉括号注释），截不出就用占位名并记日志。

    与前端 paramLabel 同口径。两端各实现一次，是因为契约才是唯一事实来源，
    界面文案不靠任何一侧的代码复制。界面上不露英文键名（规则 3），
    原始键只进日志给运维看。
    """
    desc = (spec.get("description") or "").strip()
    head = re.split(r"[。：]", re.sub(r"（[^）]*）", "", desc), maxsplit=1)[0].strip()
    if not head:
        logger.debug("参数 %s 的契约说明截不出中文名，界面按「未命名选项」显示", key)
        return "未命名选项"
    return head


def _value_display_name(key: str, item: object, spec: dict) -> str:
    """取值中文名：契约 x-value-labels，取不到用占位名并记日志（界面不露英文原值）。"""
    labels = spec.get("x-value-labels") or {}
    display = labels.get(str(item))
    if display is None:
        logger.debug("参数 %s 的取值 %r 没有中文名，界面按「未命名选项」显示", key, item)
        return "未命名选项"
    return display


def find_unavailable_usage(engine: str, params: dict | None) -> list[str]:
    """提交期复核：这批参数有没有踩到「当前兑现不了」的项。返回给用户看的说明，空表＝没踩到。

    为什么界面置灰之后后端还要拦一道：置灰只挡得住从表单点的人，挡不住直接调接口的请求
    （ADR-033 决策 3）。判据全部从契约的 `x-unavailable` / `x-unavailable-values` 读，
    不写死任何引擎名或参数名——所以以后解除标记只改契约，这里的代码不用动。

    **「选了不用」不算踩到**：旁白音频选「关闭」是用户真实的取舍，不做配音本来就做得到，
    拦它等于连"明确不要"都不许表达。所以只在值确实要求启用时才拒——
    假值、空值、空列表一律放行（与规则 10 的布尔例外是同一条道理）。
    """
    entry = ENGINE_REGISTRY.get(engine) or {}
    props = (entry.get("params_schema") or {}).get("properties") or {}
    reasons: list[str] = []
    for key, value in (params or {}).items():
        spec = props.get(key) or {}
        name = _param_display_name(key, spec)
        whole_reason = spec.get("x-unavailable")
        if whole_reason:
            if value is None or value == "" or value is False or value == []:
                continue
            reasons.append(f"{name}暂不支持：{whole_reason}")
            continue
        banned = spec.get("x-unavailable-values") or {}
        picked = value if isinstance(value, list) else [value]
        for item in picked:
            why = banned.get(str(item))
            if why:
                reasons.append(
                    f"{name}的「{_value_display_name(key, item, spec)}」暂不支持：{why}"
                )
    return reasons
