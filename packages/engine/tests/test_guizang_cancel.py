"""guizang 引擎的撤销语义回归（ADR-019 决策 5）。

guizang 是唯一带 supervisor 的引擎：子进程退出后如果没写出产物，它会用
`claude --resume <session>` **自动续跑**（最多 2 次）。这个自动续跑在"网关中途掐线"
的场景下是救命的，但它必须让位于用户的撤销 —— 否则用户在界面上点了撤销，
supervisor 又亲手把 claude 拉起来继续烧额度，等于"撤销是假的"。

这里不起真子进程：把 subprocess.Popen 打桩成记录器，只看 supervisor 的决策。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_PACKAGES = Path(__file__).resolve().parents[2]
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))

from engine.impl.guizang_ppt.engine import GuizangPptEngine  # noqa: E402


class _ProcAlreadyGone:
    """模拟"子进程已经退出了"：wait() 立刻返回 0。"""

    pid = 4242    # _resume_run 会把 pid 写回 job.json，假对象也得有

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _engine(tmp_path, job_id, job_json):
    eng = GuizangPptEngine(jobs_root=tmp_path / "jobs")
    d = tmp_path / "jobs" / job_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(json.dumps(job_json), encoding="utf-8")
    return eng, d


def test_撤销后supervisor不再resume续跑(tmp_path, monkeypatch):
    spawned: list = []
    monkeypatch.setattr(
        "engine.impl.guizang_ppt.engine.subprocess.Popen",
        lambda *a, **k: spawned.append(a) or _ProcAlreadyGone(),
    )
    eng, d = _engine(tmp_path, "j-cancel", {
        "status": "running", "cancel_requested": True, "supervisor_pid": 4321,
        "started_at": 1, "claude_session_id": "sess-abc", "engine_params": {},
    })

    eng._supervise("j-cancel", _ProcAlreadyGone(), 10, 0)

    assert spawned == [], "已撤销的任务绝不能被 --resume 拉起来（否则撤销 = 假撤销）"
    data = json.loads((d / "job.json").read_text(encoding="utf-8"))
    assert data["status"] == "running", "终态归编排层落，supervisor 不得覆写"


def test_未撤销时仍会自动续跑(tmp_path, monkeypatch):
    """对照组：这条恰恰证明上一条不是"因为测试写歪了才没 spawn"。"""
    spawned: list = []
    monkeypatch.setattr(
        "engine.impl.guizang_ppt.engine.subprocess.Popen",
        lambda *a, **k: spawned.append(a) or _ProcAlreadyGone(),
    )
    now = datetime.now(timezone.utc).timestamp()
    eng, _d = _engine(tmp_path, "j-retry", {
        "status": "running", "supervisor_pid": 4321, "started_at": now,
        "claude_session_id": "sess-abc", "engine_params": {}, "resume_attempt": 0,
    })

    eng._supervise("j-retry", _ProcAlreadyGone(), 3600, 0)

    assert spawned, "产物没写出且未撤销 → 按原设计续跑一次"
    assert "--resume" in spawned[0][0]


def test_cancel钩子先立牌再杀进程(tmp_path, monkeypatch):
    """顺序很重要：先杀后立牌会给 supervisor 留下续跑窗口。"""
    eng, d = _engine(tmp_path, "j-kill", {"status": "running", "pid": 999999})
    events: list = []
    monkeypatch.setattr(
        "engine.impl.guizang_ppt.engine.terminate_tree",
        lambda pid: events.append("terminate") or True,
    )
    real_write = eng._write_job

    def spy_write(job_id, **fields):
        if "cancel_requested" in fields:
            events.insert(0, "flag")
        return real_write(job_id, **fields)

    monkeypatch.setattr(eng, "_write_job", spy_write)

    assert eng.cancel("j-kill") is True
    assert events == ["flag", "terminate"], f"必须先立 cancel_requested 再杀进程，实际 {events}"
    data = json.loads((d / "job.json").read_text(encoding="utf-8"))
    assert data["status"] == "failed", "引擎侧记自己的终态；cancelled 由编排层落 DB"
