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
from collections.abc import Callable
from pathlib import Path

import pytest

# 路径引导：与运行期同一套约定（engine 在 packages/，app 在 packages/backend/src），
# 使本测试不依赖调用方是否已经导好 PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT / "packages", REPO_ROOT / "packages" / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.engines.registry import (  # noqa: E402
    ENGINE_REGISTRY,
    _ENGINE_CLASSES,
    _derive_capabilities,
    engine_class,
    implements_hook,
    output_engine_names,
)
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


# ── 1. 产物落点契约（注册表驱动：新增输出引擎自动被考） ─────────────

JOB_ID = "probe-job"


def _plant_single_html(job_dir: Path) -> None:
    """单文件形状：`<jobs_root>/<job_id>/result.html`（guizang 一类）。"""
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "result.html").write_text(
        "<html><body><section class='slide'>a</section></body></html>", encoding="utf-8"
    )


def _plant_page_svgs(job_dir: Path) -> None:
    """逐页文件形状：`<jobs_root>/<job_id>/projects/<项目>/svg_output/NN_*.svg`（ppt_master 一类）。"""
    project = job_dir / "projects" / "deck_20260101"
    (project / "svg_output").mkdir(parents=True, exist_ok=True)
    (project / "svg_output" / "01_cover.svg").write_text("<svg/>", encoding="utf-8")
    (job_dir / "job.json").write_text(
        json.dumps({"started_at": 1, "status": "running"}), encoding="utf-8"
    )


# 按**注册名**登记产物形状。这不是业务代码里的引擎名分支，而是测试夹具清单：
# 下面的 fail-closed 用例会要求"每个输出引擎都必须在此登记"，所以新增引擎不登记就当场红，
# 并直接说明该补什么——把"约定"变成"机制"（ADR-012 决策 3 的落地形态）。
LANDING_SHAPES: dict[str, Callable[[Path], None]] = {
    "guizang_ppt": _plant_single_html,
    "ppt_master": _plant_page_svgs,
    "ppt_master_v6": _plant_page_svgs,  # 逐页形状同 ppt_master；v6 额外要求 svg_output 非空才落定 pin
}


def _missing_shapes() -> list[str]:
    return [n for n in output_engine_names() if n not in LANDING_SHAPES]


def test_every_output_engine_has_landing_shape() -> None:
    missing = _missing_shapes()
    assert not missing, (
        f"输出引擎 {missing} 未登记产物形状。请在 LANDING_SHAPES 为它登记一种"
        "（复用 _plant_single_html / _plant_page_svgs，或新写一个 plant 函数），"
        "否则产物必须落在 jobs_root 内这条约定对它没有任何约束。"
    )


def test_landing_guard_actually_catches_new_engine(monkeypatch) -> None:
    """自证守卫有牙：临时注册一个未登记形状的输出引擎，枚举必须当场抓到它。"""
    from app.engines.registry import ENGINE_REGISTRY

    monkeypatch.setitem(ENGINE_REGISTRY, "temp_probe_engine", {"engine_type": "output"})
    assert "temp_probe_engine" in output_engine_names(), "枚举未覆盖新注册引擎"
    assert "temp_probe_engine" in _missing_shapes(), (
        "守卫失效：新输出引擎没登记形状却仍然全绿，等于契约测试形同虚设"
    )


@pytest.mark.parametrize("engine_name", output_engine_names())
def test_products_stay_inside_jobs_root(engine_name: str, tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    try:
        engine = engine_class(engine_name)(jobs_root=jobs_root)
    except TypeError as exc:
        pytest.fail(
            f"输出引擎 {engine_name} 必须接受 jobs_root 构造参数，否则部署期无法外置"
            f"产物根（ADR-012 决策 1/4）：{exc}"
        )

    # 按约定路径造产物（不调引擎私有方法）：约定本身就是被测对象
    LANDING_SHAPES[engine_name](jobs_root / JOB_ID)

    root = engine.get_preview_root(JOB_ID)
    assert root is not None, f"{engine_name} 在约定落点放了产物却定位不到"
    assert jobs_root.resolve() in root.parents, (
        f"{engine_name} 预览根越出 jobs_root：{root}——产物必须落在调用方分配的"
        " job_dir 内，不得回写 lib/ 等代码树（AGENTS 规则 4）"
    )
    for page in engine.get_pages(JOB_ID) or []:
        assert jobs_root.resolve() in Path(str(page["path"])).parents, (
            f"{engine_name} 页文件越出 jobs_root：{page['path']}"
        )


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


# ── 3. 工厂与能力位（注册表驱动，加引擎只改注册表） ─────────────────

def test_draft_engines_never_claim_page_preview() -> None:
    """草稿任务不产逐页物，故草稿注册名一律不得带 page_preview 标签。

    这条曾经是真的：`ppt_master_draft` 与 `ppt_master` 同类注册两名，而能力位按**实现类**
    探测，导致草稿被误标。修法不是按名字分支，而是给草稿一个声明退出钩子的薄子类
    （PptMasterDraftEngine）。本用例守住这个不变量，防止再退回去。
    """
    from app.engines.registry import _derive_capabilities

    drafts = [n for n, v in ENGINE_REGISTRY.items() if v.get("engine_type") == "draft"]
    wrong = [n for n in drafts if "page_preview" in _derive_capabilities(n)]
    assert not wrong, f"草稿引擎被误标 page_preview：{wrong}"


@pytest.mark.parametrize("engine_name", sorted(ENGINE_REGISTRY))
def test_factory_resolves_every_registered_engine(engine_name: str) -> None:
    """每个注册名都必须能被工厂解析出实例，且具备协议基本方法。

    枚举取自注册表：新增引擎不需要在本文件登记即可被覆盖（这是 factory 收口后的验收面）。
    """
    from app.engines.factory import get_engine

    engine = get_engine(engine_name)
    for attr in ("submit", "status", "result"):
        assert callable(getattr(engine, attr, None)), (
            f"{engine_name} 的实例缺少协议方法 {attr}"
        )


def test_unknown_engine_still_raises() -> None:
    """未注册名不静默降级（含 stub 兜底也不允许吞掉拼错的引擎名）。"""
    from app.engines.factory import get_engine

    with pytest.raises(ValueError):
        get_engine("no_such_engine")


@pytest.mark.parametrize("engine_name", sorted(ENGINE_REGISTRY))
def test_force_stub_covers_every_registered_engine(engine_name: str, monkeypatch) -> None:
    """DECKSMITH_FORCE_STUB 必须覆盖每个注册引擎——离线 CI 漏一个就会真拉子进程烧额度。

    这条是变异验证暴露出来的缺陷：factory 原先维护一张硬编码白名单，新引擎不在表里就绕过
    钩子。现判据取自注册表本身，故本用例按注册表枚举，新引擎自动纳入。
    """
    from app.engines.factory import get_engine
    from app.engines.stub import StubPptMasterEngine

    monkeypatch.setenv("DECKSMITH_FORCE_STUB", "1")
    assert isinstance(get_engine(engine_name), StubPptMasterEngine), (
        f"{engine_name} 在 FORCE_STUB 下仍会落真引擎（检查 factory 的注册表判据）"
    )


def test_factory_seamlessly_admits_a_new_engine(monkeypatch) -> None:
    """收口的自证：只往注册表加一个引擎，解析/能力派生/stub 钩子三项立刻自动生效。

    若哪天有人把名字清单搬回 factory，这条会红。
    """
    extra = "temp_added_engine"
    # 注入两张表 = 模拟"开发者只改注册表"这一唯一动作
    monkeypatch.setitem(_ENGINE_CLASSES, extra,
                        ("engine.impl.guizang_ppt", "GuizangPptEngine"))
    monkeypatch.setitem(ENGINE_REGISTRY, extra,
                        {"name": extra, "display_name": "临时", "description": "",
                         "engine_type": "output", "params_schema": {}})

    from app.engines.factory import get_engine
    from app.engines.registry import _derive_capabilities

    assert engine_class(extra).__name__ == "GuizangPptEngine", "解析未覆盖新注册名"
    assert get_engine(extra) is not None, "工厂未覆盖新注册名（是否残留名字分支？）"
    assert "page_preview" in _derive_capabilities(extra), "能力位未随实现自动派生"

    monkeypatch.setenv("DECKSMITH_FORCE_STUB", "1")
    from app.engines.stub import StubPptMasterEngine

    assert isinstance(get_engine(extra), StubPptMasterEngine), "stub 钩子未覆盖新注册名"
