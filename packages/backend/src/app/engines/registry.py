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
import json
import sys
from pathlib import Path

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
    if implements_hook(engine_cls, REVISE_HOOK):
        caps.append("svg_edit")
    return caps


# 引擎注册表：name 必须与 DeckRequest.engine 的枚举值一致
ENGINE_REGISTRY = {
    "ppt_master": {
        "name": "ppt_master",
        "display_name": "PPT Master",
        "description": "基于 AI Agent 的 PPT 生成引擎，支持多风格/多尺寸",
        "engine_type": "output",  # 输出引擎
        "params_schema": PPT_MASTER_PARAMS,
    },
    "ppt_master_v6": {
        "name": "ppt_master_v6",
        "display_name": "PPT Master 6.6",
        "description": "基于 ppt-master v6.6.0 技能的 PPT 生成引擎，全量支持路由 profile/生成模式/打包结构/画布/备注/动画",
        "engine_type": "output",  # 输出引擎
        "params_schema": PPT_MASTER_V6_PARAMS,
    },
    "ppt_master_draft": {
        "name": "ppt_master_draft",
        "display_name": "默认引擎",
        "suit_for": "适合已经梳理完成的稿件",
        "description": (
            "稿件本身已经成型，直接转成草稿，内容和顺序照搬原文，改动最少、不消耗额度。"
            "支持 word/excel/ppt/pdf/markdown/txt 及网页链接。"
        ),
        "engine_type": "draft",  # 草稿引擎
        "params_schema": PPT_MASTER_DRAFT_PARAMS,
    },
    "claude_draft": {
        "name": "claude_draft",
        "display_name": "Claude 引擎",
        "suit_for": "适合整理长篇、混合型稿件",
        "description": (
            "交 AI 通读后归纳成分章节的草稿大纲，成稿更规整，耗时数分钟并消耗额度。"
            "单次通读约一万五千字，多份素材合并后超长部分只取头尾、中间不纳入。"
        ),
        "engine_type": "draft",  # 草稿引擎
        "params_schema": CLAUDE_DRAFT_PARAMS,
    },
    "guizang_ppt": {
        "name": "guizang_ppt",
        "display_name": "Guizang HTML PPT",
        "description": "基于 guizang-ppt-skill 的 HTML 演示文稿引擎，输出单文件网页 PPT",
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
