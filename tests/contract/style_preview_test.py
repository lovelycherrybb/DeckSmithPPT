"""引擎参数外观表与契约的对齐检查（生成弹窗的悬停预览）。

钉住四件事：

1. **不许漏条目**：遍历**所有已注册引擎**的参数表（不写死引擎名），凡 `style` 枚举与
   `*_theme` 枚举里的每一个值，外观表必须有同名条目。悬停预览的行为是"查不到就不切换"，
   漏一个不会报错、只会那个选项静默没反应——正是 ADR-026 点名的假绿灯。
   上一版这里写死了 ppt_master / ppt_master_v6 两个名字，结果整块漏掉了 guizang_ppt
   的两个风格和九个主题色还照报绿，这个教训就是本条存在的理由。

2. **不许串味**：外观表是"值→外观"的单层表，所以同一个值不得同时属于两个不同参数名
   （例如某引擎的 style 叫 green、另一个引擎的主题色也叫 green）。真发生就必须改成
   按「参数名＋值」两级索引。

3. **不许留孤儿**：外观表里每个键都必须被某个引擎的枚举真正用到，防止改名后留下死条目。

4. **离线交付约束**：产品要装到没外网的客户机，外观表不许引外部字体/图片/url()；
   色值必须是合法十六进制（防手滑混进异形字符）。

注：早期版本另有一条"两个 PPT Master 引擎的风格必须同集"。改成遍历所有引擎之后它已多余
——各引擎风格集本就允许不同（guizang 是 magazine/swiss 两套），而"分叉导致某引擎部分风格
没有条目"这件事，第 1 条已经全覆盖。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT / "packages", REPO_ROOT / "packages" / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.engines.registry import ENGINE_REGISTRY  # noqa: E402

PREVIEW_PATH = REPO_ROOT / "packages" / "frontend" / "src" / "lib" / "stylePreview.json"

# 每条至少要有这五个变量，否则弹窗只换个字体、看不出配色差异
REQUIRED_VARS = ("--bg-card", "--bg-input", "--text-primary", "--border", "--primary")

HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
# 必须用 \w（含非 ASCII）整段抓出来：用 [0-9a-zA-Z] 会把混进十六进制的异体字符当断点截掉，
# 于是 "#b98d६f" 被截成 "#b98d" 判为合法——正是这条闸要拦的那个错，不能自己漏掉。
HEX_TOKEN_RE = re.compile(r"#[\w-]+")
# 离线交付只禁「要联网才拿得到」的东西：远程地址、@import、data: 内联，以及非本站根路径的 url()。
# 同源绝对路径 url('/xxx.webp') 放行——留给将来真要用随包图形的情况。目前 29 条外观全部纯 CSS，
# 没有任何条目在用图（杂志风试过上游真图，实测不如 CSS，已撤）。
NETWORK_URL_RE = re.compile(r"(?:https?:)?//|@import|data:", re.IGNORECASE)
URL_CALL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")
PUBLIC_DIR = REPO_ROOT / "packages" / "frontend" / "public"


def _load_preview() -> dict:
    return json.loads(PREVIEW_PATH.read_text(encoding="utf-8"))


def _enum_params_by_kind() -> dict[str, dict[str, list[str]]]:
    """按参数名归集所有已注册引擎的枚举取值：{参数名: {引擎名: [值...]}}。"""
    out: dict[str, dict[str, list[str]]] = {}
    for engine_name, entry in ENGINE_REGISTRY.items():
        props = (entry.get("params_schema") or {}).get("properties", {})
        for param, spec in props.items():
            if not _is_previewable_param(param):
                continue
            values = spec.get("enum") or []
            if values:
                out.setdefault(param, {})[engine_name] = list(values)
    return out


def _is_previewable_param(param: str) -> bool:
    """参与悬停预览的参数：视觉风格，以及各类主题色。"""
    return param == "style" or param.endswith("_theme")


def test_所有引擎的风格与主题色都必须有外观条目():
    preview = set(_load_preview())
    missing = []
    for param, by_engine in _enum_params_by_kind().items():
        for engine_name, values in by_engine.items():
            gap = [v for v in values if v not in preview]
            if gap:
                missing.append(f"{engine_name}.{param}: {gap}")
    assert not missing, "以下枚举值没有悬停外观条目（选了不会有任何反应）：" + "；".join(missing)


def test_同一个值不得同时属于两个不同参数名():
    owner: dict[str, set[str]] = {}
    for param, by_engine in _enum_params_by_kind().items():
        for values in by_engine.values():
            for value in values:
                owner.setdefault(value, set()).add(param)
    clash = {v: sorted(p) for v, p in owner.items() if len(p) > 1}
    assert not clash, (
        f"外观表按值单层索引会串味：{clash}。需要改成按「参数名＋值」两级索引后再登记"
    )


def test_外观表不得留孤儿条目():
    used = {
        value
        for by_engine in _enum_params_by_kind().values()
        for values in by_engine.values()
        for value in values
    }
    orphan = sorted(set(_load_preview()) - used)
    assert not orphan, f"外观表里有 {orphan} 没有任何引擎枚举在用（多半是改风格名留下的死条目）"


def test_每条外观必须有最小可用字段():
    for name, entry in _load_preview().items():
        assert isinstance(entry.get("dark"), bool), f"{name} 缺 dark 布尔标记"
        css_vars = entry.get("cssVars")
        assert isinstance(css_vars, dict) and css_vars, f"{name} 缺 cssVars"
        missing = [v for v in REQUIRED_VARS if v not in css_vars]
        assert not missing, f"{name} 缺关键变量 {missing}"
        for key in css_vars:
            assert key.startswith("--"), f"{name} 的 cssVars 键 {key} 不是 CSS 变量"
        font = entry.get("fontFamily")
        if font is not None:
            assert isinstance(font, str) and font.strip(), f"{name} 的 fontFamily 是空值，要么删掉要么给值"


def test_每条外观必须有底纹且底纹铺满整块弹窗():
    """只换纸色不算换风格：底纹是识别度的大头。同时要求标题栏透明，
    否则标题那层不透明底色会把底纹拦腰切断。"""
    for name, entry in _load_preview().items():
        css_vars = entry["cssVars"]
        surface = css_vars.get("--modal-surface")
        assert surface and surface.strip(), f"{name} 缺 --modal-surface（弹窗没有底纹）"
        assert css_vars.get("--modal-header-bg") == "transparent", (
            f"{name} 有底纹但 --modal-header-bg 不是 transparent，标题栏会把底纹切断"
        )


def test_底纹引用的图片必须真的在前端产物里():
    for name, entry in _load_preview().items():
        blob = json.dumps(entry, ensure_ascii=False)
        for path in URL_CALL_RE.findall(blob):
            assert path.startswith("/"), f"{name} 的 url({path}) 不是站内绝对路径，离线机上取不到"
            target = PUBLIC_DIR / path.lstrip("/")
            assert target.exists(), f"{name} 引用的底纹图不存在：{path}（应为 {target}）"


def test_外观表不得引外部资源且色值合法():
    for name, entry in _load_preview().items():
        blob = json.dumps(entry, ensure_ascii=False)
        assert not NETWORK_URL_RE.search(blob), f"{name} 含需联网获取的字体/图片/内联资源，离线交付机上会失效"

        for key, value in list(entry["cssVars"].items()) + list(entry.get("contentStyle", {}).items()):
            for token in HEX_TOKEN_RE.findall(str(value)):
                assert HEX_COLOR_RE.match(token), f"{name} 的 {key} 里色值不合法：{token}"
