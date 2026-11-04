"""任务状态文件 job.json 的读写竞态契约：读到半截记录时不得判失败、不得覆盖。

起因（2026-11-02 实测，支线交接件 docs/research/2026-11-02-job状态文件竞态-支线交接.md）：
两个 ppt_master 引擎的 `_write_job` 直接整文件覆盖写（无临时文件无 os.replace），
后台线程写状态与 `status()` 轮询并发时，读者可能撞上写了一半的文件；而 `_read_job`
把解析失败一律吞成 `{}`，「读到半截」和「任务从没写过记录」无法区分。`status()` 拿到
`{}` 后走「进程不在→找产物→判失败」的租约逻辑，把后台线程刚写进去的真实失败原因
覆盖成一句无关的「claude 子进程结束但未找到产物」——test_draft_async.py 隔离单跑
四次红一次的根因，同形代码在 ppt_master_v6 上一样潜伏。

判据（两条防线，各对应竞态的两半）：

1. **记录存在但解析不了（半截），status() 必须按「还在跑」处理**：返回 running、
   不写盘（半截文件原样保留），把落定留给真正写状态的那一方。理由：拿不到完整
   记录就没有资格下「失败」的结论——宁可少说，不可编造。确定性用例，直接把
   半截内容摆上盘，不靠碰运气。
2. **写入必须原子替换**（临时文件 + os.replace，同一目录）：写入期间读者要么看到
   旧全文、要么看到新全文，永远看不到半截或空文件。

对照组（test_修复前形状_半截吞成空字典会造出假失败）：把修复前的 `_read_job` /
`_write_job` 原样内嵌，接上现行 `status()` 复现出假失败——证明这条检查不是摆设，
也钉住「竞态机制本身」防止后人看着奇怪把它改回去。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT / "packages",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from engine.impl.ppt_master.engine import PptMasterEngine  # noqa: E402
from engine.impl.ppt_master_v6.engine import PptMasterV6Engine  # noqa: E402


# ── 半截记录摆上盘：status() 必须按 running 处理，且不许动文件 ────

def _tear_record(full: dict) -> str:
    """把一条真实记录拦腰截断，模拟读者撞上写到一半的文件。

    截断点取全文中段——半截 JSON 永远解析不了，与真实竞态里读者看到的内容同形。
    """
    raw = json.dumps(full, ensure_ascii=False, indent=2)
    return raw[: len(raw) // 2]


def test_半截记录_status按running处理_不写盘_ppt_master(tmp_path):
    eng = PptMasterEngine(jobs_root=tmp_path / "jobs")
    job_id = "torn-draft"
    p = eng._job_dir(job_id) / "job.json"  # noqa: SLF001
    p.parent.mkdir(parents=True, exist_ok=True)
    # 草稿任务的真实记录形状（submit 同步落盘、后台线程稍后改写失败原因）
    torn = _tear_record({
        "status": "running", "engine_type": "draft", "title": "t",
        "pages": 5, "pid": None, "started_at": 1234.5,
    })
    p.write_text(torn, encoding="utf-8")

    got = eng.status(job_id)

    assert got.value == "running", (
        f"记录读成半截时 status() 判成了 {got.value}——没有完整记录就没资格下结论，"
        "把失败原因留给真正写状态的那一方"
    )
    assert p.read_text(encoding="utf-8") == torn, (
        "status() 在记录解析不了时改写了 job.json——后台线程刚写的真实失败原因会被这一笔冲掉"
    )


def test_半截记录_status按running处理_不写盘_ppt_master_v6(tmp_path):
    eng = PptMasterV6Engine(jobs_root=tmp_path / "jobs")
    job_id = "torn-output"
    p = eng._job_dir(job_id) / "job.json"  # noqa: SLF001
    p.parent.mkdir(parents=True, exist_ok=True)
    # 输出任务刚落盘时的记录形状（v6 的草稿是同步完成，竞态暴露在输出任务上）
    torn = _tear_record({
        "status": "running", "pid": None, "started_at": 1234.5, "title": "t",
    })
    p.write_text(torn, encoding="utf-8")

    got = eng.status(job_id)

    assert got.value == "running", (
        f"记录读成半截时 status() 判成了 {got.value}——没有完整记录就没资格下结论"
    )
    assert p.read_text(encoding="utf-8") == torn, (
        "status() 在记录解析不了时改写了 job.json"
    )


# ── 写入原子性：写入期间读者只许看到完整记录 ─────────────────────

def _hammer_atomic_write(cls, tmp_path: Path) -> list[str]:
    eng = cls(jobs_root=tmp_path / "jobs")
    job_id = "hammer"
    p = eng._job_dir(job_id) / "job.json"  # noqa: SLF001
    p.parent.mkdir(parents=True, exist_ok=True)  # 真实流程由 submit 建目录，这里自己补
    padding = "x" * 2_000_000  # 把单次写撑大，让「truncate→写完」的窗口大到接得住
    eng._write_job(job_id, status="running", pid=999999, padding=padding)

    torn: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                raw = p.read_text(encoding="utf-8")
            except (FileNotFoundError, OSError):
                continue  # 读瞬间目标不存在/被打断：原子替换下不会发生，出现即算数
            if raw == "":
                torn.append("读到空文件（truncate 后未写完）")
                continue
            try:
                json.loads(raw)
            except json.JSONDecodeError:
                torn.append(f"读到半截：len={len(raw)}，尾 40 字符={raw[-40:]!r}")

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for i in range(40):
            eng._write_job(job_id, note=f"round-{i}", status="running",
                           pid=999999, padding=padding)
    finally:
        stop.set()
        t.join(timeout=10)

    leftovers = [q.name for q in p.parent.glob(".job-*")]
    assert not torn, (
        f"写入期间读者撞见非完整记录 {len(torn)} 次，写侧不是原子替换：{torn[:2]}"
    )
    assert not leftovers, f"写完留下了临时文件残骸：{leftovers}"
    return torn


def test_写入期间读者只见完整记录_ppt_master(tmp_path):
    _hammer_atomic_write(PptMasterEngine, tmp_path)


def test_写入期间读者只见完整记录_ppt_master_v6(tmp_path):
    _hammer_atomic_write(PptMasterV6Engine, tmp_path)


# ── 对照组：修复前形状 + 现行 status()，假失败必须被确定性复现 ────

def test_修复前形状_半截吞成空字典会造出假失败(tmp_path):
    """把修复前的 `_read_job`/`_write_job` 逐字内嵌（impl/ppt_master/engine.py 192-206 行），
    接上现行 status()：半截记录吞成 {} → 租约逻辑误判 → 假失败覆盖文件。
    现行引擎（上面两条用例）必须不再走出这个结果，否则守卫失效。
    """

    class PreFixEngine(PptMasterEngine):
        # ↓↓ 修复前原样：解析失败一律吞成 {}，写侧整文件覆盖
        def _read_job(self, job_id):
            p = self._job_dir(job_id) / "job.json"
            if not p.exists():
                return {}
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}

        def _write_job(self, job_id, **fields):
            p = self._job_dir(job_id) / "job.json"
            data = self._read_job(job_id)
            data.update(fields)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return data

    eng = PreFixEngine(jobs_root=tmp_path / "jobs")
    job_id = "torn-draft"
    p = eng._job_dir(job_id) / "job.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    torn = _tear_record({
        "status": "running", "engine_type": "draft", "title": "t",
        "pages": 5, "pid": None, "started_at": 1234.5,
    })
    p.write_text(torn, encoding="utf-8")

    got = eng.status(job_id)

    assert got.value == "failed", "对照组失效：修复前形状本该造出假失败，实际没有"
    body = json.loads(p.read_text(encoding="utf-8"))
    assert body["status"] == "failed" and "未得到结果文件" in body["error"], (
        "对照组失效：修复前形状本该用无关文案覆盖原记录"
    )
