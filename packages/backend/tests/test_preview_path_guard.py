"""预览路径归属守卫：产物根目录是软链时，越界判断仍必须两侧 resolve 后再比。

对应 ADR-012 决策 6。这条纪律原先只能靠人工评审兜，本文件把它变成机制保护。

两条用例是**配对**设计的，去掉任一侧 `.resolve()` 都必红其中一条：

    root_resolved = root.resolve()                 # ← 去掉这行的 resolve
    target = (root_resolved / rel_path).resolve()  # ← 或去掉这行的 resolve

- 只去掉 target 侧 → 软链逃逸用例放行外部文件（安全漏洞），合法用例仍通过；
- 只去掉 root 侧   → 合法用例被误判为越界（预览整片挂掉）。

因此不存在"两条一起被无声改掉"的空间。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (REPO_ROOT / "packages", REPO_ROOT / "packages" / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.repository.jobs import JobRepo  # noqa: E402
from app.service.jobs import JobService  # noqa: E402

SVG = "<svg xmlns='http://www.w3.org/2000/svg'/>"


class _SymlinkRootEngine:
    """最小引擎替身：只声明 get_preview_root，返回**软链形态**的产物根。

    实现该钩子即会被 implements_hook 认作真实实现（本类不是 Protocol、也没置 None），
    与真实引擎走同一条探测路径。
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def get_preview_root(self, job_id):  # noqa: D102
        return self._root

    def get_pages(self, job_id):  # noqa: D102
        """只报一个已落盘页，路径给软链形态——与真实引擎读盘结果一致。"""
        return [{
            "index": 1,
            "name": "01_cover.svg",
            "path": self._root / "svg_output" / "01_cover.svg",
            "kind": "image",
        }]


def _seed(db, job_id="job1", engine="symeng"):
    ts = "2026-09-14T12:00:00+00:00"
    JobRepo().create(
        db, job_id=job_id, project_id="p1", task_type="output", status="completed",
        title="软链归属守卫", engine=engine, agent="claude", content_style="marketing_promo",
        engine_params={}, created_at=ts, updated_at=ts,
    )


@pytest.fixture()
def db(tmp_path):
    from app.models import Base, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{tmp_path / 'guard.db'}")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    yield session
    session.close()


def _service(tmp_path, root_link, engine_name="symeng"):
    svc = JobService(
        JobRepo(),
        engine_factory=lambda name: (
            _SymlinkRootEngine(root_link) if name == engine_name else None
        ),
    )
    return svc


def _make_symlinked_products(tmp_path: Path) -> tuple[Path, Path]:
    """造出 `卷内真实目录` + `指向它的软链产物根`，页文件放在软链根之下。

    返回 (软链根, 真实产物目录)。这正是 B 方案（bind mount / 软链外置）的现场形态，
    也是运维把 data 目录挂到别处时的常见形态。
    """
    real = tmp_path / "volume" / "deck_a"
    (real / "svg_output").mkdir(parents=True)
    (real / "svg_output" / "01_cover.svg").write_text(SVG, encoding="utf-8")
    link = tmp_path / "linkroot"
    link.symlink_to(real, target_is_directory=True)
    return link, real


def test_合法页文件在软链根下仍被放行(tmp_path, db):
    """root 侧必须 resolve：否则 target 已归一而 root 未归一，误判越界、预览整片挂。"""
    link, _real = _make_symlinked_products(tmp_path)
    _seed(db)
    svc = _service(tmp_path, link)

    target, media = svc.resolve_preview_file(db, "job1", "svg_output/01_cover.svg")

    assert media == "image/svg+xml"
    assert target.is_file()


def test_经软链逃逸到根外的文件必须被拒(tmp_path, db):
    """target 侧必须 resolve：否则 root/escape.svg 看着在根内，实为外部文件被读出。

    这条是安全侧：漏掉 resolve 不会报错，只会**悄悄放行**，故必须显式覆盖。
    """
    link, real = _make_symlinked_products(tmp_path)
    secret = tmp_path / "outside" / "keys.svg"
    secret.parent.mkdir(parents=True)
    secret.write_text("<svg><text>/etc 里的东西</text></svg>", encoding="utf-8")
    # 产物根内放一个指向根外的软链——经典"链出去"攻击面
    (real / "escape.svg").symlink_to(secret)
    _seed(db)
    svc = _service(tmp_path, link)

    with pytest.raises(HTTPException) as exc:
        svc.resolve_preview_file(db, "job1", "escape.svg")

    assert exc.value.status_code == 400, (
        f"软链逃逸未被拦（实得 {exc.value.status_code}）：路径归属判断退化成字符串前缀比较"
    )


def test_相对穿越仍被拒(tmp_path, db):
    """配套的常规穿越用例：`../` 也必须 400，而不是 404/415。"""
    link, _real = _make_symlinked_products(tmp_path)
    _seed(db)
    svc = _service(tmp_path, link)

    with pytest.raises(HTTPException) as exc:
        svc.resolve_preview_file(db, "job1", "../linkroot/svg_output/01_cover.svg")

    assert exc.value.status_code == 400


def test_页墙url在软链根下仍正确换算(tmp_path, db):
    """守住同一纪律在 `list_pages` 里的**第二处**实现。

    那里 root 侧 resolve 被去掉时，`relative_to` 抛 ValueError 会被外层
    `except Exception` 吞成"空页墙"——不报错、不记日志，只让用户看到 0 页。
    所以必须单独断言 url 与页数，不能只靠上面三条。
    """
    link, _real = _make_symlinked_products(tmp_path)
    _seed(db)
    svc = _service(tmp_path, link)

    res = svc.list_pages(db, "job1")

    assert res.supports_page_preview is True
    assert res.pages_generated == len(res.pages) == 1, (
        f"页墙在软链产物根下静默变空：{res.pages}"
    )
    assert res.pages[0].url == "/api/jobs/job1/preview/svg_output/01_cover.svg"
