"""草稿任务异步提交回归：submit 立即返回 running，提取在后台线程完成。

钉住这次改造的动机——多文件/大文件的 source_to_md 提取不能再阻塞 POST 请求线程
（前端表现为「提交后一直转圈」）。用 monkeypatch 把提取放慢，验证 submit 不等待它。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/

from engine.impl.ppt_master.engine import PptMasterEngine  # noqa: E402


def test_草稿提交立即返回running_提取在后台完成(tmp_path, monkeypatch):
    eng = PptMasterEngine(jobs_root=tmp_path / "jobs")

    def slow_build(content, title, pages, sources, engine_params):
        time.sleep(0.6)  # 模拟 source_to_md 的秒级耗时
        return "# 草稿\n正文", "从素材提取的标题"

    monkeypatch.setattr(eng, "_build_draft", slow_build)

    t0 = time.monotonic()
    job_id = eng.submit(content="x", title=None, pages=5,
                        sources=[{"type": "text", "value": "x"}], engine_type="draft")
    elapsed = time.monotonic() - t0

    # submit 不阻塞在提取上（提取在后台线程）
    assert elapsed < 0.3, f"submit 阻塞了 {elapsed:.2f}s，未做到异步"
    # 提取未完成前，status 必须是 running（不能被「无子进程→判失败」的租约逻辑误杀）
    assert eng.status(job_id).value == "running"

    # 等后台线程完成
    deadline = time.monotonic() + 5
    while eng.status(job_id).value == "running" and time.monotonic() < deadline:
        time.sleep(0.05)

    assert eng.status(job_id).value == "completed"
    data = eng._read_job(job_id)  # noqa: SLF001
    assert data["title"] == "从素材提取的标题"  # 提取出的标题回填
    assert (eng._job_dir(job_id) / "draft.md").exists()  # noqa: SLF001


def test_草稿提取抛异常置failed(tmp_path, monkeypatch):
    eng = PptMasterEngine(jobs_root=tmp_path / "jobs")

    def boom(*a, **k):
        raise RuntimeError("转换炸了")

    monkeypatch.setattr(eng, "_build_draft", boom)
    job_id = eng.submit(content="x", title="t", pages=5,
                        sources=[{"type": "text", "value": "x"}], engine_type="draft")
    deadline = time.monotonic() + 5
    while eng.status(job_id).value == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert eng.status(job_id).value == "failed"
    assert "转换炸了" in eng._read_job(job_id)["error"]  # noqa: SLF001
