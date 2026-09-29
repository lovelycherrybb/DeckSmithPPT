"""guizang_ppt 产物结构守卫（ADR-021）。

**为什么存在**。引擎原先只判"文件存在且非空"（`_artifact_ready`），看不到"能不能翻"。
实测案例 c7f3edd4：产物 133KB、12 页齐全、`</html>` 完整，但 agent 分批 Edit 时把
注入区之外的骨架元素 `<div id="nav"></div>` 一起替换掉了——导航脚本
`getElementById('nav')` 拿到 null 当场抛错，deck 永远停在封面。技能自带的
`validate-presenter-mode.mjs` 只管演讲备注，拦不住这类骨架损伤，于是坏产物以
"0 errors + completed" 静默出厂。历史 8 份真实产物中 1 份中招（≈12.5%），
属 LLM 多轮 Edit 锚点选择的**概率性**失手。

**职责边界**。本模块是纯函数：进（产物文本 + 已闭合页数）、出（校验结论 + 可选的
修复后文本），不碰文件系统、不起子进程、不 import engine（避免环依赖）。
只判"结构上能不能用"，不判内容质量（文字对错、版式美观、备注对齐都不归它管）。

**处置三档**（详见 ADR-021）：
1. 唯一已知的可确定性修复形态（只缺 `#nav`、锚点健在）→ 就地自愈，零 token 成本；
2. 其余缺陷 → 如实报告，由引擎判 failed；
3. 一律**不自动重跑**——单次生成实测 378 万 tokens，重跑与否交给人（retry 入口）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 骨架三件套：两份上游模板（magazine / swiss）中均**恰好出现 1 次**（实测 grep）。
#: 产物偏离即说明注入过程越界破坏了 `#deck` 之外的骨架。
SKELETON_IDS: tuple[str, ...] = ("deck", "nav", "hint")

#: 自愈锚点：模板里 `#nav` 的落点就在"演讲备注"注释之前。该注释在 template.html:543
#: 与 template-swiss.html:1343 均**唯一出现 1 次**（实测），且位于 `#deck` 闭合之后，
#: 跨两种风格同构 —— 所以守卫不需要按风格分支。
NOTES_ANCHOR = "<!-- 演讲备注"

#: 补回的元素内容与模板原文一字不差（不发明新结构）
NAV_ELEMENT = '<div id="nav"></div>'

#: 注入占位注释：prompt 的产出协议第 5 条要求 agent 自检 `grep -c SLIDES_HERE` 为 0，
#: 引擎侧用同一口径兜底（agent 自报不可信，本次案例它就报了这个检查"通过"）。
SLIDES_HERE_MARKER = "SLIDES_HERE"


@dataclass(frozen=True)
class Defect:
    """一条结构缺陷。message 面向用户：任务语义打头、技术事实在括号里。"""

    code: str
    message: str
    healable: bool


@dataclass
class GuardResult:
    """守卫结论。ok 为真时 text 即最终产物文本（可能已被自愈改写）。"""

    ok: bool
    text: str
    healed: list[str] = field(default_factory=list)
    defects: list[Defect] = field(default_factory=list)

    @property
    def error_message(self) -> str:
        """写进 job.json error / DB error 的聚合文案（界面直接展示）。"""
        return "产物结构不完整，无法正常使用：" + "；".join(d.message for d in self.defects)


def _skeleton_defects(html_text: str) -> list[Defect]:
    """R4：骨架三件套必须各恰 1 次。缺 → 视元素而定；多 → 结构被重复注入，不猜。"""
    defects: list[Defect] = []
    for sid in SKELETON_IDS:
        n = html_text.count(f'id="{sid}"')
        if n == 1:
            continue
        if n == 0:
            if sid == "nav":
                defects.append(Defect(
                    code="skeleton_missing:nav",
                    message="缺少翻页导航容器（id=\"nav\" 被注入过程误删，页面无法翻页）",
                    healable=True,
                ))
            elif sid == "deck":
                defects.append(Defect(
                    code="skeleton_missing:deck",
                    message="缺少页面主容器（id=\"deck\" 缺失，翻页结构已破坏）",
                    healable=False,
                ))
            else:
                defects.append(Defect(
                    code="skeleton_missing:hint",
                    message="缺少操作提示条（id=\"hint\" 缺失，骨架不完整）",
                    healable=False,
                ))
            continue
        defects.append(Defect(
            code=f"skeleton_duplicated:{sid}",
            message=f"骨架元素被重复写入（id=\"{sid}\" 出现 {n} 次，应恰好 1 次）",
            healable=False,
        ))
    return defects


def validate(html_text: str, *, closed_pages: int) -> list[Defect]:
    """跑全部结构校验（R1–R4），返回缺陷清单；空列表 = 产物结构可用。

    closed_pages 由调用方用 `engine.impl.guizang_ppt.engine.scan_slides()` 算出
    （只数**已完整闭合**的页），本模块不重复实现解析器——页计数保持单一事实源，
    避免出现"守卫说 4 页、页墙显示 3 格"这种两套逻辑的边界差。
    """
    defects: list[Defect] = []
    # R1 完整性：截断形态（网关掐断时 result 仍报 success，只看状态位会被骗）
    if "</html>" not in html_text:
        defects.append(Defect(
            code="truncated",
            message="产物文件不完整（没有 </html> 收尾，疑似写入中途被打断）",
            healable=False,
        ))
    # R2 至少有一页能渲染
    if closed_pages < 1:
        defects.append(Defect(
            code="no_slides",
            message=f"产物里没有完整写成的页面（{closed_pages} 页，可能只写了半截）",
            healable=False,
        ))
    # R3 注入占位必须被替换干净
    if SLIDES_HERE_MARKER in html_text:
        defects.append(Defect(
            code="slides_here_residue",
            message="页面未注入完整（模板占位注释 SLIDES_HERE 仍残留在产物中）",
            healable=False,
        ))
    defects.extend(_skeleton_defects(html_text))
    return defects


def check_and_heal(html_text: str, *, closed_pages: int) -> GuardResult:
    """校验产物结构；命中"唯一已知可自愈形态"时补回 `#nav` 并复检。

    自愈刻意**只认这一种组合**：全部缺陷恰好是"缺 nav"一条，且锚点唯一存在。
    多种缺陷并存时不修（那说明注入过程破坏面更大，补一个 nav 只是掩盖问题）；
    锚点也不在时不修（没有可靠落点，硬插等于猜）。修完必须重跑全量校验，
    通过才采用——自愈不是免检通道。
    """
    defects = validate(html_text, closed_pages=closed_pages)
    if not defects:
        return GuardResult(ok=True, text=html_text)

    only_nav = (
        len(defects) == 1
        and defects[0].code == "skeleton_missing:nav"
        and html_text.count('id="deck"') == 1
        and html_text.count('id="hint"') == 1
    )
    if not only_nav:
        return GuardResult(ok=False, text=html_text, defects=defects)

    if html_text.count(NOTES_ANCHOR) != 1:
        # 锚点缺失或被复制：落点不可靠，退回如实报告
        return GuardResult(ok=False, text=html_text, defects=defects)

    healed_text = html_text.replace(
        NOTES_ANCHOR, f"{NAV_ELEMENT}\n\n{NOTES_ANCHOR}", count=1)
    residual = validate(healed_text, closed_pages=closed_pages)
    if residual:
        # 补完仍不过 → 不采用这次修复，把复检出的真实缺陷报出去
        return GuardResult(ok=False, text=html_text, defects=residual)
    return GuardResult(ok=True, text=healed_text, healed=["skeleton_missing:nav"])
