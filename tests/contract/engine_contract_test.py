"""引擎契约测试：所有注册的引擎必须满足同一契约。

覆盖两条由 ADR-012 立下的口径：

1. **产物落点契约**（决策 1、3）：任一输出引擎的任务目录与产物路径必须落在调用方
   分配的 `jobs_root` 之内，不得写进 `lib/`（上游只读，AGENTS 规则 4）。
   这里断言的是"落点归属"而非"产物能否被解析出来"——所以页数是否为空都在预期内，
   只要出现的每个路径都在 jobs_root 下即通过。用假产物驱动，不起子进程、不烧额度。

2. **运行前置条件钩子**（决策 2）：`prepare_runtime` 是鸭子类型可选钩子，
   调用方一律遍历探测；出现按引擎名分支即为契约违背。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# 路径引导：与运行期同一套约定（engine 在 packages/，app 在 packages/backend/src），
# 使本测试不依赖调用方是否已经导好 PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT / "packages", REPO_ROOT / "packages" / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.engines.registry import implements_hook  # noqa: E402
from app.engines.runtime import RUNTIME_HOOK, prepare_all, report  # noqa: E402
from engine.core.protocol import OutputEngine  # noqa: E402
from engine.impl import guizang_ppt  # noqa: E402
from engine.impl.guizang_ppt import engine as gz  # noqa: E402
from engine.impl.guizang_ppt import GuizangPptEngine  # noqa: E402
from engine.impl.ppt_master import engine as pm  # noqa: E402
from engine.impl.ppt_master.engine import PptMasterEngine  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "docs" / "contracts" / "engine.schema.json"


def test_engine_contract_exists() -> None:
    assert SCHEMA_PATH.exists(), "缺少 engine.schema.json 契约"


def test_runtime_hooks_registered_in_contract() -> None:
    """钩子必须先登记进契约再落代码（AGENTS 规则 1）。"""
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    hooks = data["$defs"]["OutputEngine"].get("x-runtime-hooks", {})
    assert RUNTIME_HOOK in hooks, (
        f"{RUNTIME_HOOK} 未登记在 engine.schema.json 的 OutputEngine 上，违背契约先行"
    )


# ── 1. 产物落点契约 ──────────────────────────────────────────────

@pytest.mark.parametrize("engine_cls", [GuizangPptEngine, PptMasterEngine])
def test_job_dir_is_directly_under_jobs_root(engine_cls, tmp_path: Path) -> None:
    engine = engine_cls(jobs_root=tmp_path / "jobs")
    derived = engine._job_dir("abc123")
    assert derived.parent == (tmp_path / "jobs").resolve(), (
        f"任务目录不在 jobs_root 下：{derived}"
    )
    assert derived.name == "abc123", "任务目录必须是 jobs_root/<job_id>，不得嵌套他处"


def test_guizang_products_stay_inside_jobs_root(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    engine = GuizangPptEngine(jobs_root=jobs_root)
    job_dir = engine._job_dir("g1")
    job_dir.mkdir(parents=True)
    (job_dir / "result.html").write_text(
        "<html><body><section class='slide'>a</section></body></html>", encoding="utf-8"
    )

    assert jobs_root.resolve() in engine._result_path("g1").parents
    root = engine.get_preview_root("g1")
    assert root is not None and jobs_root.resolve() in root.parents


def test_ppt_master_pages_stay_inside_jobs_root(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    engine = PptMasterEngine(jobs_root=jobs_root)
    project = engine._job_dir("p1") / "projects" / "deck_20260101"
    (project / "svg_output").mkdir(parents=True)
    (project / "svg_output" / "01_cover.svg").write_text("<svg/>", encoding="utf-8")
    (engine._job_dir("p1") / "job.json").write_text(
        json.dumps({"started_at": 1, "status": "running"}), encoding="utf-8"
    )

    root = engine.get_preview_root("p1")
    assert root is not None and jobs_root.resolve() in root.parents, (
        f"逐页预览根越出任务目录：{root}——cwd 归位后不得再回 lib/ 下找产物"
    )
    for page in engine.get_pages("p1") or []:
        assert jobs_root.resolve() in Path(page["path"]).parents, f"页文件越界：{page['path']}"


def test_ppt_master_never_mixes_legacy_root_with_job_root(tmp_path: Path, monkeypatch) -> None:
    """新口径命中即止：不得与历史 lib 目录混在一起比 mtime（否则串页风险原地回来）。"""
    jobs_root = tmp_path / "jobs"
    # 用 tmp 下的假目录冒充 lib/ppt-master，避免测试写进真实上游只读副本
    legacy = tmp_path / "legacy-lib"
    engine = PptMasterEngine(jobs_root=jobs_root)

    fresh = engine._job_dir("j1") / "projects" / "deck_new"
    (fresh / "svg_output").mkdir(parents=True)
    (fresh / "svg_output" / "01_a.svg").write_text("<svg/>", encoding="utf-8")

    # 历史目录故意造得**更新**：两级混比就会被它抢走
    decoy = legacy / "projects" / "deck_old"
    (decoy / "svg_output").mkdir(parents=True)
    (decoy / "svg_output" / "01_a.svg").write_text("<svg/>", encoding="utf-8")
    future = 4_000_000_000
    os.utime(decoy, (future, future))

    monkeypatch.setattr(pm, "_PPT_MASTER_DIR", legacy)
    (engine._job_dir("j1") / "job.json").write_text(
        json.dumps({"started_at": 1, "status": "running"}), encoding="utf-8"
    )

    root = engine.get_preview_root("j1")
    assert root is not None and jobs_root.resolve() in root.parents, (
        f"选中了历史目录 {root}：高优先级根有结果时不得回退旧目录参与 mtime 比较"
    )


# ── 2. 运行前置条件钩子 ─────────────────────────────────────────

def test_hook_detection_is_not_fooled_by_protocol_placeholder() -> None:
    """用 `hasattr` 探测能力位是错的：Protocol 上的占位定义会恒为 True。

    本条是实测踩出来的——曾经用 hasattr 判断引擎是否实现 get_pages，得到与事实相反的
    结论。真实实现判定必须走 implements_hook()（沿 MRO、排除 Protocol 定义与显式 None）。
    """
    class Bare(OutputEngine):   # 只继承协议、不覆写任何可选钩子
        pass

    assert hasattr(Bare(), "get_pages"), "前提：Protocol 占位确实使 hasattr 恒真"
    for hook in ("get_pages", "get_preview_root", RUNTIME_HOOK):
        assert not implements_hook(Bare(), hook), f"{hook} 被误判为已实现"


def test_prepare_runtime_raises_when_skill_files_missing(tmp_path: Path, monkeypatch) -> None:
    """技能文件缺失必须是硬缺失（抛异常），不能只留一行 warn。

    成因见 ADR-012 决策 1：cwd 归位任务目录后，技能发现依赖 prompt 注入的绝对路径；
    缺文件时任务不会报错，而是静默降级成无流程生成、状态照样 completed。
    """
    empty = tmp_path / "no-skill"
    empty.mkdir()

    monkeypatch.setattr(gz, "_SKILL_DIR", empty)
    with pytest.raises(RuntimeError):
        GuizangPptEngine(jobs_root=tmp_path / "jobs").prepare_runtime()

    monkeypatch.setattr(pm, "_PPT_MASTER_DIR", empty)
    with pytest.raises(RuntimeError):
        PptMasterEngine(jobs_root=tmp_path / "jobs").prepare_runtime()


def test_prepare_runtime_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    """可重复调用（启动期预检与 submit 前各一次），且第二次不再产生新告警。"""
    cfg = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    engine = GuizangPptEngine(jobs_root=tmp_path / "jobs")

    first = engine.prepare_runtime()
    second = engine.prepare_runtime()
    link = cfg / "skills" / "guizang-ppt-skill"
    assert first is None and second is None, f"幂等被破坏：{first!r} / {second!r}"
    assert link.is_symlink() and link.resolve() == gz._SKILL_DIR.resolve()


def test_runtime_iteration_skips_engines_without_hook(monkeypatch) -> None:
    """调用方只遍历探测、不按引擎名分支：stub 无钩子时结果为空、预检退出码 0。"""
    monkeypatch.setenv("DECKSMITH_FORCE_STUB", "1")
    assert prepare_all() == [], "未实现钩子的引擎被误计入结果"
    assert report() == 0, "无前置条件需求时预检不应判失败"
