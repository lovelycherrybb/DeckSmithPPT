"""子演示文件数据层回归（ADR-023）：建表、CRUD、可分叉的父子指针、项目级联删除。"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.models import Base, make_engine, make_session_factory  # noqa: E402
from app.repository.jobs import PresentationRepo, SubPresentationRepo  # noqa: E402


def _now():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'sub.db'}")
    Base.metadata.create_all(engine)
    # 新表必须由 create_all 建出来（无 _MIGRATIONS 条目）
    assert "sub_presentations" in Base.metadata.tables
    s = make_session_factory(engine)()
    yield s
    s.close()


@pytest.fixture
def seeded(db):
    """建一个项目下的一份根演示文件，返回其 presentation_id。"""
    from app.models import Project
    db.add(Project(project_id="p1", project_name="P", created_at=_now(), updated_at=_now()))
    db.commit()
    pres = PresentationRepo().create(
        db, presentation_id="root1", project_id="p1", task_id="j-gen",
        file_name="P_1.pptx", file_format="pptx", file_path="/jobs/j-gen/exports/a.pptx",
        created_at=_now())
    return pres["presentation_id"]


def _mk(db, sid, parent, ts):
    return SubPresentationRepo().create(
        db, sub_presentation_id=sid, presentation_id="root1",
        parent_sub_presentation_id=parent, project_id="p1", task_id=f"j-{sid}",
        file_name=f"P_{ts}.pptx", file_format="pptx",
        file_path=f"/jobs/j-{sid}/exports/{sid}.pptx", created_at=ts)


def test_create_get_list(db, seeded):
    _mk(db, "s1", None, "2026-10-05T00:00:01")           # 直接改自根
    _mk(db, "s2", "s1", "2026-10-05T00:00:02")           # 改自 s1
    rows = SubPresentationRepo().list_by_presentation(db, seeded)
    assert [r["sub_presentation_id"] for r in rows] == ["s1", "s2"]  # 按保存时间升序
    assert rows[0]["parent_sub_presentation_id"] is None            # 根直连
    assert rows[1]["parent_sub_presentation_id"] == "s1"            # 父子指针
    assert SubPresentationRepo().get(db, "s2")["project_id"] == "p1"
    assert SubPresentationRepo().count_by_presentation(db, seeded) == 2


def test_fork_same_parent_many_children(db, seeded):
    _mk(db, "s1", None, "2026-10-05T00:00:01")
    _mk(db, "a", "s1", "2026-10-05T00:00:02")   # 同一父 s1 派生两个子 → 分叉
    _mk(db, "b", "s1", "2026-10-05T00:00:03")
    kids = [r for r in SubPresentationRepo().list_by_presentation(db, seeded)
            if r["parent_sub_presentation_id"] == "s1"]
    assert {k["sub_presentation_id"] for k in kids} == {"a", "b"}


def test_project_delete_cascades_subfiles(db, seeded):
    from app.repository.jobs import ProjectRepo
    _mk(db, "s1", None, "2026-10-05T00:00:01")
    # seeded 项目未设 owner_user_id（=未认领访客项目），符合本期可删条件
    assert ProjectRepo().delete(db, "p1") == "deleted"
    assert SubPresentationRepo().list_by_presentation(db, "root1") == []
