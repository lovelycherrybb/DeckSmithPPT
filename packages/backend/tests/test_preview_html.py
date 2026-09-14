"""HTML 逐页预览的服务层回归测试。

覆盖三件事：
1. 预览类型白名单放行 .html、继续挡住 job 目录里的审计文件（prompt.md / job.json / run.log）；
2. 引导脚本注入位置正确（<head> 开标签之后，早于 deck 自身脚本）且幂等；
3. 注入的脚本语义：存储垫片（严格沙箱下 localStorage 不可用时不崩）+ ?slide 定位 + ?static 低功耗。

不发子进程、不碰真实 jobs 目录（engine 指向 tmp）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.service.jobs import JobService  # noqa: E402
from app.repository.jobs import JobRepo  # noqa: E402
from engine.impl.guizang_ppt import GuizangPptEngine  # noqa: E402


def test_白名单放行html且审计文件仍被挡():
    m = JobService._PREVIEW_MEDIA_TYPES  # noqa: SLF001
    assert m[".html"] == "text/html" and m[".htm"] == "text/html"
    # job 目录里与产物同级的审计文件不得被同源读走（白名单是唯一屏障）
    for blocked in (".md", ".json", ".log", ".txt"):
        assert blocked not in m, f"{blocked} 不该出现在预览白名单"


def test_引导脚本注入在head之后且幂等(tmp_path):
    src = (
        "<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
        "<script>window.__playSlide = function () {};</script></head>"
        "<body><section class=\"slide\" data-slide-id=\"s1\">x</section></body></html>"
    )
    p = tmp_path / "result.html"
    p.write_text(src, encoding="utf-8")

    out = JobService.build_html_preview(p)
    assert 'id="decksmith-preview-boot"' in out
    # 引导脚本必须早于 deck 自身脚本（在 </head> 之前），否则存储垫片来不及生效
    assert out.index("decksmith-preview-boot") < out.index("__playSlide")
    # 不重复注入
    assert JobService.build_html_preview(p).count("decksmith-preview-boot") == 1


def test_无head时退化为html标签后注入(tmp_path):
    src = "<html><body>ok</body></html>"
    p = tmp_path / "bare.html"
    p.write_text(src, encoding="utf-8")
    out = JobService.build_html_preview(p)
    assert out.index("decksmith-preview-boot") < out.index("<body>")


def test_引导脚本语义完整():
    boot = JobService._PREVIEW_BOOTSTRAP  # noqa: SLF001
    # 存储垫片：探测失败才换内存实现（正常环境不该劫持）
    assert "localStorage" in boot and "sessionStorage" in boot
    assert "__decksmith_probe__" in boot
    # 页定位与低功耗
    assert "__playSlide(slide - 1)" in boot
    assert "__setLowPowerMode(true)" in boot
    assert "static" in boot  # ?static=1 缩略图省 GPU


def test_审计文件与穿越路径仍被拒(tmp_path):
    from fastapi import HTTPException

    engine, db = _seed_job(tmp_path)
    svc = JobService(JobRepo(), engine_factory=lambda name: engine)

    # result.html：放行
    target, media = svc.resolve_preview_file(db, "pv01", "result.html")
    assert media == "text/html" and target.name == "result.html"
    # 审计文件：415
    for rel in ("prompt.md", "job.json", "run.log", "draft.md"):
        with pytest.raises(HTTPException) as ei:
            svc.resolve_preview_file(db, "pv01", rel)
        assert ei.value.status_code == 415, rel
    # 穿越与不存在：400 / 404
    with pytest.raises(HTTPException) as ei:
        svc.resolve_preview_file(db, "pv01", "../escape.html")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        svc.resolve_preview_file(db, "pv01", "nope.html")
    assert ei.value.status_code == 404


def _seed_job(tmp_path: Path):
    """在 tmp 里伪造一个 guizang 任务目录 + 对应 DB 行（内存库）。返回 (引擎, session)。"""
    from app.models import Base, make_engine as sa_engine, make_session_factory

    engine_db = sa_engine("sqlite://")
    Base.metadata.create_all(engine_db)
    db = make_session_factory(engine_db)()

    jobs_root = tmp_path / "jobs"
    engine = GuizangPptEngine(jobs_root=jobs_root)
    job_dir = engine._job_dir("pv01")  # noqa: SLF001
    job_dir.mkdir(parents=True)
    (job_dir / "result.html").write_text(
        '<html><head></head><body><section class="slide" data-slide-id="s1">x</section></body></html>',
        encoding="utf-8",
    )
    (job_dir / "prompt.md").write_text("prompt", encoding="utf-8")
    (job_dir / "run.log").write_text("log", encoding="utf-8")
    (job_dir / "draft.md").write_text("draft", encoding="utf-8")
    ts = "2026-09-14T12:00:00+00:00"
    JobRepo().create(
        db, job_id="pv01", project_id="p1", task_type="output", status="completed",
        title="预览测试", engine="guizang_ppt", agent="claude",
        content_style="marketing_promo", engine_params={}, created_at=ts, updated_at=ts,
    )
    db.commit()
    return engine, db
