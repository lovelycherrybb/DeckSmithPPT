"""guizang 产物结构守卫回归（ADR-021）。

钉住的行为只有两条主线：
1. **结构坏的产物不许标 completed** —— 原先 `_artifact_ready` 只看"存在且非空"，
   133KB 的坏产物和好产物在这一关完全等价（实测案例 c7f3edd4：缺 `<div id="nav">`，
   封面能渲染、翻页全失效，却以"0 errors + completed"出厂）。
2. **能确定性修好的就地修、不重跑** —— 单次生成实测 378 万 tokens，
   自动重跑等于"失手必双倍烧钱"，且重跑掷的是同一个概率骰子。

不起真 claude 子进程：直接写 job.json + result.html，然后调 `status()`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PACKAGES = Path(__file__).resolve().parents[2]
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))

from engine.core.models import EngineJobStatus  # noqa: E402
from engine.impl.guizang_ppt.engine import GuizangPptEngine, scan_slides  # noqa: E402
from engine.impl.guizang_ppt.guard import check_and_heal, validate  # noqa: E402

# ── 合成夹具：与两份上游模板同构的最小可判定产物 ──────────────────────
# 锚点 "<!-- 演讲备注" 唯一出现 1 次、位于 #deck 闭合之后 —— 这是自愈落点的前提，
# 实测 template.html:543 与 template-swiss.html:1343 都是这个形态。
_SLIDE = '<section class="slide light" data-slide-id="p1"><div>第一页</div></section>'
_NOTES = "<!-- 演讲备注：每页一条 -->\nconst SPEAKER_NOTES = [];"


def _deck(*, nav: bool = True, hint: bool = True, deck_id: bool = True,
          nav_count: int = 1, hint_count: int = 1, deck_count: int = 1,
          slides: str = _SLIDE, notes_anchor: bool = True,
          residue: bool = False, close_html: bool = True) -> str:
    def once(elem: str, n: int) -> str:
        return "\n".join(elem for _ in range(n))

    head = "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head><style>/*" + ("x" * 2100) + "*/</style></head>\n<body>\n"
    parts = [head]
    if hint:
        parts.append(once('<div id="hint" role="toolbar"></div>', hint_count) + "\n")
    parts.append(once('<div id="deck">\n', deck_count) if deck_id else "\n")
    parts.append(slides + "\n")
    if residue:
        parts.append("<!-- SLIDES_HERE -->\n")
    parts.append("</div>\n\n")
    if nav:
        parts.append(once('<div id="nav"></div>', nav_count) + "\n\n")
    parts.append(_NOTES if notes_anchor else "const SPEAKER_NOTES = [];\n")
    parts.append("\n</body>\n")
    if close_html:
        parts.append("</html>\n")
    return "".join(parts)


# ── ① 纯函数：校验项 ────────────────────────────────────────────────

def test_健康产物零缺陷():
    html = _deck()
    assert validate(html, closed_pages=1) == []
    assert check_and_heal(html, closed_pages=1).ok


def test_缺html收尾判截断():
    codes = {d.code for d in validate(_deck(close_html=False), closed_pages=1)}
    assert "truncated" in codes


def test_零闭合页判无内容():
    defects = validate(_deck(slides=""), closed_pages=0)
    assert any(d.code == "no_slides" for d in defects)


def test_SLIDES_HERE残留被拦():
    defects = validate(_deck(residue=True), closed_pages=1)
    assert any(d.code == "slides_here_residue" for d in defects)


def test_骨架缺失与重复都有独立缺陷码():
    assert any(d.code == "skeleton_missing:nav" for d in validate(_deck(nav=False), closed_pages=1))
    assert any(d.code == "skeleton_missing:hint" for d in validate(_deck(hint=False), closed_pages=1))
    assert any(d.code == "skeleton_missing:deck" for d in validate(_deck(deck_id=False), closed_pages=1))
    dup = validate(_deck(nav_count=2), closed_pages=1)
    assert any(d.code == "skeleton_duplicated:nav" for d in dup)


def test_失败文案说人话且技术事实在括号里():
    verdict = check_and_heal(_deck(residue=True), closed_pages=1)
    msg = verdict.error_message
    assert msg.startswith("产物结构不完整，无法正常使用：")
    assert "SLIDES_HERE" in msg  # 技术事实可查


# ── ② 纯函数：自愈边界 ──────────────────────────────────────────────

def test_只缺nav时自愈并复检通过():
    html = _deck(nav=False)
    assert "id=\"nav\"" not in html
    verdict = check_and_heal(html, closed_pages=1)
    assert verdict.ok and verdict.healed == ["skeleton_missing:nav"]
    assert verdict.text.count('id="nav"') == 1
    # 补在模板原文位置：演讲备注锚点之前
    assert verdict.text.index('id="nav"') < verdict.text.index("<!-- 演讲备注")


def test_自愈幂等_重放不重复插入():
    once = check_and_heal(_deck(nav=False), closed_pages=1)
    twice = check_and_heal(once.text, closed_pages=1)
    assert twice.ok and twice.healed == []
    assert twice.text.count('id="nav"') == 1


def test_锚点不在时不硬插():
    html = _deck(nav=False, notes_anchor=False)
    verdict = check_and_heal(html, closed_pages=1)
    assert not verdict.ok
    assert verdict.text == html, "落点不可靠时必须原样退回，绝不猜位置"


def test_多缺陷并存不修_避免掩盖更大破坏():
    html = _deck(nav=False, residue=True)
    verdict = check_and_heal(html, closed_pages=1)
    assert not verdict.ok
    assert {d.code for d in verdict.defects} == {"slides_here_residue", "skeleton_missing:nav"}
    assert verdict.text == html, "组合缺陷不逐条试修"


def test_hint也缺时不自愈():
    verdict = check_and_heal(_deck(nav=False, hint=False), closed_pages=1)
    assert not verdict.ok and verdict.healed == []


def test_nav重复时不修():
    verdict = check_and_heal(_deck(nav_count=2), closed_pages=1)
    assert not verdict.ok


# ── ③ 引擎集成：status() 不再把坏产物判成 completed ──────────────────

def _job(tmp_path, job_id, html: str | None = None, *, raw: bytes | None = None) -> tuple:
    eng = GuizangPptEngine(jobs_root=tmp_path / "jobs")
    d = tmp_path / "jobs" / job_id
    d.mkdir(parents=True, exist_ok=True)
    # started_at=0 → _artifact_ready 的 mtime 下界不生效；pid=None → 进程视为已退出
    (d / "job.json").write_text(json.dumps({
        "status": "running", "started_at": 0, "pid": None,
        "supervisor_pid": None, "engine_params": {}, "token_usage": {"total_tokens": 3777928},
    }), encoding="utf-8")
    if raw is not None:
        (d / "result.html").write_bytes(raw)
    elif html is not None:
        (d / "result.html").write_text(html, encoding="utf-8")
    return eng, d


def test_引擎_健康产物照常completed且不写guard字段(tmp_path):
    eng, d = _job(tmp_path, "ok", _deck())
    assert eng.status("ok") is EngineJobStatus.COMPLETED
    data = json.loads((d / "job.json").read_text(encoding="utf-8"))
    assert data["status"] == "completed" and data.get("guard") is None
    assert data["token_usage"]["total_tokens"] == 3777928, "已烧额度不因守卫而隐瞒"


def test_引擎_缺nav产物被自愈成completed并留痕(tmp_path):
    eng, d = _job(tmp_path, "heal", _deck(nav=False))
    assert eng.status("heal") is EngineJobStatus.COMPLETED
    data = json.loads((d / "job.json").read_text(encoding="utf-8"))
    assert data["status"] == "completed"
    assert data["guard"]["healed"] == ["skeleton_missing:nav"]
    # 修复真的落盘了（不是只记了个日志）
    assert (d / "result.html").read_text(encoding="utf-8").count('id="nav"') == 1
    assert "结构守卫自愈" in (d / "run.log").read_text(encoding="utf-8")


def test_引擎_结构损坏判failed并带用户可读原因(tmp_path):
    eng, d = _job(tmp_path, "bad", _deck(residue=True))
    assert eng.status("bad") is EngineJobStatus.FAILED
    data = json.loads((d / "job.json").read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert data["error"].startswith("产物结构不完整，无法正常使用：")
    assert data["guard"]["defects"] == ["slides_here_residue"]
    log = (d / "run.log").read_text(encoding="utf-8")
    assert "结构守卫拦截" in log
    assert (d / "result.html").read_text(encoding="utf-8") == _deck(residue=True), "拦截不得偷改产物"


def test_引擎_非utf8产物判failed不崩(tmp_path):
    # 必须过 _artifact_ready 的大小下限（2048B），否则根本走不到守卫
    eng, d = _job(tmp_path, "bin", raw=b"\xff\xfe\x00" + b"\x80" * 2100 + b"</html>")
    assert eng.status("bin") is EngineJobStatus.FAILED
    assert "UTF-8" in json.loads((d / "job.json").read_text(encoding="utf-8"))["error"]


def test_引擎_已终态行重放不重复跑守卫(tmp_path):
    eng, d = _job(tmp_path, "replay", _deck(nav=False))
    assert eng.status("replay") is EngineJobStatus.COMPLETED
    before = (d / "result.html").read_text(encoding="utf-8")
    assert eng.status("replay") is EngineJobStatus.COMPLETED
    assert (d / "result.html").read_text(encoding="utf-8") == before, "终态短路，守卫不重入"


# ── ④ 真实案例回归锚（产物不在则跳过，不造第二个事实源）──────────────

_REAL = Path(__file__).resolve().parents[3] / "outputs" / "jobs" / \
    "c7f3edd4ae414623b45977b3cb8e49e3" / "result.html"


@pytest.mark.skipif(not _REAL.is_file(), reason="真实产物未随仓库分发（outputs/ 不入库）")
def test_真实案例_缺nav产物经守卫转为可用(tmp_path):
    """拿**真实世界的 12 页杂志产物**当夹具：注释里的假页、真实标签嵌套这些干扰项，
    合成夹具模拟不出来。

    缺陷是**从真文件反向造**的（删掉那一行 nav），而不是断言"文件本来就是坏的"——
    该产物已被守卫修好并入库在 outputs/ 下，若断言原始形态，修好产物这一刻就会把
    这条测试弄坏（曾经真的这样自毁过一次）。
    """
    html = _REAL.read_text(encoding="utf-8")
    pages = len(scan_slides(html))
    assert pages == 12 and 'id="nav"' in html, "基准：真实产物当前是完好形态"

    broken = html.replace('<div id="nav"></div>\n\n', '', count=1)
    assert 'id="nav"' not in broken, "缺陷已按预期复现"

    verdict = check_and_heal(broken, closed_pages=len(scan_slides(broken)))
    assert verdict.ok and verdict.healed == ["skeleton_missing:nav"]
    assert verdict.text == html, "修复结果应与完好产物逐字节一致"
    assert len(scan_slides(verdict.text)) == pages, "自愈不动内容，页数不变"
