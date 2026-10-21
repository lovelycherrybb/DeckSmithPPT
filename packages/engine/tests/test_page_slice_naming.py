"""逐页切片文件名的取页规则回归。

钉住的缺陷：取页规则原本只认 `NN_slug.svg`（数字加下划线打头），而上游技能对页文件名
没有强制约定，模型会起 `P01.svg` 这类名字。真实后果是任务 391354cf23124177af99183e5cd03749
十页产物在界面上"生成完成却一页都看不到"，逐页修改也挑不出页——两处用的是同一个规则。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/

from engine.impl.ppt_master.engine import PptMasterEngine, _page_index_of  # noqa: E402


# ── 取号规则本身 ──

def test_两种命名风格都取得到页序():
    assert _page_index_of("01_cover.svg") == 1
    assert _page_index_of("15_last_section.svg") == 15
    assert _page_index_of("P01.svg") == 1
    assert _page_index_of("P10.svg") == 10
    assert _page_index_of("p03.svg") == 3


def test_非页文件不误收():
    # 数字落在名字尾部或压根没数字的，都不算页——放宽规则后这条边界必须守住
    assert _page_index_of("logo2.svg") is None
    assert _page_index_of("icon_arrow.svg") is None
    assert _page_index_of("backup.svg") is None
    assert _page_index_of("01_cover.png") is None
    # 字母前缀超过两个，说明数字不是页序而是名字的一部分
    assert _page_index_of("page01.svg") is None


# ── 走真实取页路径 ──

def _engine_with_pages(tmp_path, names, delays=()):
    """造一个把 svg_output_dir 钉好的任务目录，页文件按 names 写入。"""
    eng = PptMasterEngine(jobs_root=tmp_path / "jobs")
    svg_dir = tmp_path / "proj" / "svg_output"
    svg_dir.mkdir(parents=True)
    for i, n in enumerate(names):
        f = svg_dir / n
        f.write_text(f"<svg data-page='{n}'></svg>", encoding="utf-8")
        if i < len(delays) and delays[i]:
            time.sleep(0.02)  # 拉开 mtime，供去重断言
    (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)
    return eng, svg_dir


def _pin(eng, job_id, svg_dir):
    job_dir = eng._job_dir(job_id)  # noqa: SLF001
    job_dir.mkdir(parents=True, exist_ok=True)
    eng._write_job(job_id, status="completed", svg_output_dir=str(svg_dir))  # noqa: SLF001


def test_P格式页名能取到全部页且按页序排(tmp_path):
    eng, svg_dir = _engine_with_pages(
        tmp_path, [f"P{n:02d}.svg" for n in range(1, 11)]
    )
    _pin(eng, "jobP", svg_dir)

    pages = eng.get_pages("jobP")
    assert len(pages) == 10, f"P 格式页名仍取不到页：{len(pages)}"
    assert [p["index"] for p in pages] == list(range(1, 11))
    assert pages[0]["name"] == "P01.svg"
    assert pages[9]["name"] == "P10.svg"


def test_新旧命名混在一个目录也各归其位(tmp_path):
    eng, svg_dir = _engine_with_pages(
        tmp_path, ["01_cover.svg", "P02.svg", "03_body.svg", "logo2.svg", "notes.svg"]
    )
    _pin(eng, "jobMix", svg_dir)

    pages = eng.get_pages("jobMix")
    assert [p["name"] for p in pages] == ["01_cover.svg", "P02.svg", "03_body.svg"]
    assert [p["index"] for p in pages] == [1, 2, 3]


def test_同一页号只保留最后写入的那份(tmp_path):
    eng, svg_dir = _engine_with_pages(
        tmp_path, ["P01.svg", "01_cover.svg"], delays=[0.02, 0]
    )
    _pin(eng, "jobDup", svg_dir)

    pages = eng.get_pages("jobDup")
    assert len(pages) == 1, "同号页应去重成一条"
    assert pages[0]["name"] == "01_cover.svg", "应保留后写入的那份"
