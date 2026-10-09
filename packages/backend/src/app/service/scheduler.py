"""任务调度循环：用一条专职线程取代"每任务一条轮询线程"（ADR-019）。

**为什么存在**。`submit()` 原先用 FastAPI 的 BackgroundTasks 挂一个同步的
`_poll_until_done`，每条任务全程独占一条线程、最长 3600 秒；而所有路由也都是同步 `def`，
与它共用 anyio 默认 **40-token** 线程池。用同构最小服务实测：约 36 条并发之后，
新提交要等 29.8 秒才返回，连一个不碰磁盘不碰库的只读接口也要 29.5 秒——是整站冻结，
不是降级。把轮询收进一条线程后，请求线程只做"校验 + 入库"，并发闸也顺带有了天然的串行点。

**线程模型**。本循环跑在一条 daemon 线程里，所以 `engine.submit`（fork 子进程）和
`engine.status`（`ps` 探测）这类阻塞调用可以直接调，不必再回线程池去抢那 40 个 token。

**状态权威在 DB，不在内存**：每拍都从 DB 重建跟踪集，所以进程重启后能自愈——这是
"幽灵 running"消失的根本原因（线上曾有一条被人工标记的 47 小时无输出记录）。

**并发闸不需要信号量**：闸门值就是 DB 里 `status='running'` 的行数，跨重启可见。

**所有状态写入一律带前置条件**（`repository.jobs` 的 CAS 系列），抢不到就放弃覆写。
于是"用户撤队 / 调度器 fork 成功 / 引擎判超时"三方竞态都有确定答案，不需要任何锁。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from datetime import datetime

from ..core import runtime_settings
from ..core.config import JOB_TIMEOUT_GRACE_SECONDS
from ..core.db import SessionLocal
from ..engines.factory import get_engine   # 它会把 packages/ 塞进 sys.path，engine 包因此可导入
from ..repository.jobs import JobRepo
from .jobs import JobService
from engine.core.models import EngineJobStatus

logger = logging.getLogger("decksmith.scheduler")

#: 引擎态 → 任务态的唯一映射处。引擎永远不知道 queued/cancelled 存在（ADR-018 分层枚举），
#: 所以 cancelled 只可能由编排层（cancel 服务方法）写入，不可能从这张表里出来。
_ENGINE_TO_TASK_STATUS = {
    EngineJobStatus.COMPLETED: "completed",
    EngineJobStatus.FAILED: "failed",
    EngineJobStatus.TIMEOUT: "timeout",
}


def _epoch_of(iso: str | None) -> float | None:
    """ISO 字符串 → epoch；解析不了就 None（宁可少判一次超时，也不要误杀在跑的任务）。"""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return None


#: 快通道每拍认领上限（ADR-024）。只是安全阀：防"一拍里 fork 出一堆子进程"，不是并发闸。
#: 全局防灌水交给提交期 per-identity 未结束粗闸兜底，所以这里给足量。
FAST_LANE_MAX_PER_SWEEP = 8


class Scheduler:
    """认领 + 轮询 + 收尸 + 终态收割。一条线程，一个进程内单例。"""

    def __init__(
        self,
        service: JobService | None = None,
        *,
        interval: float | None = None,
        max_concurrent: int | None = None,
        timeout_seconds: int | None = None,
        fast_lane_max: int | None = None,
        engine_factory=None,
        session_factory=None,
    ) -> None:
        self.service = service or JobService(JobRepo(), get_engine)
        self.repo = JobRepo()
        # 两个注入点（engine_factory / session_factory）不是为测试而测试：调度器一旦
        # 用模块级 SessionLocal，单测里跑一拍 sweep 就会**打到开发库**（outputs/decksmith.db），
        # 真去认领那里的任务。测试必须能把它指向临时库。
        self.engine_factory = engine_factory or get_engine
        self.session_factory = session_factory or SessionLocal
        # None = 不覆盖，每拍去读系统设置的生效值；给了具体值 = 钉死（测试与特殊部署用）
        self._interval = interval
        self._max_concurrent = max_concurrent
        self._timeout = timeout_seconds
        # 快通道每拍认领上限（ADR-024）：只是防"一拍里 fork 太多"的安全阀，不是并发闸；
        # 真正防灌水的是提交期的 per-identity 未结束粗闸。给足量，别把快通道又做成了新瓶颈。
        self.fast_lane_max = fast_lane_max if fast_lane_max is not None else FAST_LANE_MAX_PER_SWEEP
        #: 认领者标识：host:pid。写进 claimed_by，于是"谁占的位"在 DB 里看得见，
        #: 多副本或同机多进程时也能判定该不该按陈旧处理。
        self.claimant = f"{socket.gethostname()}:{os.getpid()}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── 生效值：每拍重读，所以设置页改完不用重启容器（ADR-020 决策 2）──
    # 做成 property 而不是在 __init__ 里取一次快照：后者会让"改了设置要重启才生效"
    # 这件事悄悄成立，而契约里那些项的 restart_required 明明写的是 false。
    @property
    def max_concurrent(self) -> int:
        return (self._max_concurrent if self._max_concurrent is not None
                else runtime_settings.max_concurrent_jobs())

    @max_concurrent.setter
    def max_concurrent(self, value: int | None) -> None:
        self._max_concurrent = value

    @property
    def interval(self) -> float:
        return (self._interval if self._interval is not None
                else runtime_settings.scheduler_interval())

    @interval.setter
    def interval(self, value: float | None) -> None:
        self._interval = value

    @property
    def timeout_seconds(self) -> int:
        return (self._timeout if self._timeout is not None
                else runtime_settings.job_timeout_seconds())

    @timeout_seconds.setter
    def timeout_seconds(self, value: int | None) -> None:
        self._timeout = value

    # ── 生命周期 ──
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self.run, daemon=True, name="decksmith-scheduler")
        self._thread.start()
        logger.info(
            "调度器已启动：并发上限 %d、节拍 %.1fs、超时预算 %ds（+%ds 宽限）、claimant=%s",
            self.max_concurrent, self.interval, self.timeout_seconds,
            JOB_TIMEOUT_GRACE_SECONDS, self.claimant,
        )

    def stop(self, timeout: float = 10.0) -> None:
        """停止认领。**先不再认领**，在途的那一拍自然跑完（它是同步的，不会半途而废）。
        这样 `docker stop` 不会留下一批 fork 到一半的孤儿子进程。
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("调度器已停止（不再认领新任务）")

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sweep()
            except Exception:  # noqa: BLE001 - 单拍异常绝不能带走整个循环
                logger.exception("调度器单拍异常，已跳过本轮")
            self._stop.wait(self.interval)

    # ── 一拍 ──
    def sweep(self) -> dict:
        """收尸 → 轮询在跑的 → 认领新的。

        顺序刻意如此：先收回崩坏的占位、再拿到在跑任务的最新终态，最后才认领，
        于是闸门看到的是**本拍已知的最大空闲**，不会因为"上一拍其实已经跑完一条"
        而少开一个槽。
        """
        stats = {"reaped": 0, "finalized": 0, "claimed": 0, "lost_claim": 0, "rejected": 0}
        db = self.session_factory()
        try:
            self._reap_stale_claims(db, stats)
            self._poll_running(db, stats)
            self._claim_queued(db, stats)
        finally:
            db.close()
        if any(v for k, v in stats.items()):
            logger.debug("调度一拍：%s", stats)
        return stats

    # ── ① 收尸：崩掉的认领者 ──
    def _reap_stale_claims(self, db, stats: dict) -> None:
        """处理"占了位却没跑起来"的行（超过 `CLAIM_STALE_SECONDS` 仍是 queued + claimed）。

        按证据判定，不猜：job.json 里有活着的 pid 或已有产物 → 说明 fork 真的发生过，
        提升为 running 接管；否则清掉占位，让它重新排队。
        """
        stale = self.repo.list_stale_claims(db)
        for row in stale:
            job_id = row["job_id"]
            data = self._job_json(job_id)
            engine = self._engine_for(row.get("engine"))
            forked = bool(data.get("pid")) and engine is not None and engine.status(job_id) is not None
            has_result = bool(data.get("result_path"))
            if forked or has_result:
                if self.repo.promote_to_running(db, job_id, row["claimed_by"]):
                    stats["reaped"] += 1
                    logger.warning("任务 %s 的认领者已崩，但引擎侧有执行证据 → 提升为 running 接管",
                                   job_id[:8])
                continue
            self.repo.release_claim(db, job_id, row["claimed_by"])
            stats["reaped"] += 1
            logger.warning("任务 %s 的认领者已崩且无执行证据 → 槽位交还队列重新排队", job_id[:8])

    # ── ② 轮询在跑的 ──
    def _poll_running(self, db, stats: dict) -> None:
        for row in self.repo.list_by_status(db, "running"):
            job_id = row["job_id"]
            engine = self._engine_for(row.get("engine"))
            if engine is None:
                # 引擎名已从注册表消失：这条永远等不到人来改，如实判失败，不要挂着
                self.repo.cas_status(
                    db, job_id, from_status="running", status="failed",
                    status_detail="生成失败", error=f"引擎不可用：{row.get('engine')}")
                stats["finalized"] += 1
                continue
            try:
                status = engine.status(job_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("任务 %s 引擎状态查询异常：%s", job_id[:8], exc)
                status = EngineJobStatus.RUNNING  # 查不到就维持原判，绝不凭猜测定生死
            if status is EngineJobStatus.RUNNING:
                if self._overdue(row):
                    self._hit_deadline(db, row, engine, stats)
                else:
                    self.repo.cas_status(db, job_id, from_status="running",
                                         status_detail=f"引擎执行中（{status.value}）")
                continue
            self._finalize(db, row, engine, status, stats)

    def _overdue(self, row: dict) -> bool:
        """超时预算：从**认领成功那一刻**起算，不含排队等待时间。

        起点取 started_at（promote_to_running 时写），老数据没有这列就退回 created_at，
        并且再加 `JOB_TIMEOUT_GRACE_SECONDS` 宽限——让引擎内的 supervisor（guizang）
        先按它更了解子进程的方式收尾，本循环只当**没有 supervisor 的引擎的兜底**。
        """
        started = _epoch_of(row.get("started_at")) or _epoch_of(row.get("created_at"))
        if started is None:
            return False
        return time.time() - started > self.timeout_seconds + JOB_TIMEOUT_GRACE_SECONDS

    def _hit_deadline(self, db, row: dict, engine, stats: dict) -> None:
        """超时：**先杀进程再落终态**。原先 `ppt_master` 只改库不杀子进程，
        于是出现过"库里已 timeout、claude 还在跑并继续写产物"的僵尸（ADR-019 背景 3）。
        """
        job_id = row["job_id"]
        killed = self._terminate(engine, job_id)
        note = ("已终止引擎子进程" if killed
                else "未能终止引擎子进程（该引擎未实现 cancel 钩子）")
        self._finalize(db, row, engine, EngineJobStatus.TIMEOUT, stats,
                       extra_error=f"生成超时（超过 {self.timeout_seconds} 秒），{note}")

    # ── ③ 认领新的 ──
    def _claim_queued(self, db, stats: dict) -> None:
        # 生成闸：max_concurrent 只约束**输出引擎任务**（ADR-024）。running 只数 output，
        # 否则草稿/修改会占用生成额度、把真正的 PPT 生成挤到排队。
        slots = self.max_concurrent - self.repo.count_generation(db, "running")
        for _ in range(max(0, slots)):
            row = self.repo.claim_next_queued(db, self.claimant, generation_lane=True)
            if row is None:
                break
            self._start(db, row, stats)
        # 快通道：草稿/修改任务不等生成槽，每拍直接认领并起跑（上限只是安全阀）；
        # 提交期的 per-identity 未结束粗闸才是防灌水的闸（ADR-024 决策 2）。
        for _ in range(self.fast_lane_max):
            row = self.repo.claim_next_queued(db, self.claimant, generation_lane=False)
            if row is None:
                break
            self._start(db, row, stats)

    def _start(self, db, row: dict, stats: dict) -> None:
        """在调度线程里跑引擎提交（ADR-019 决策 2：从请求线程搬过来的）。

        代价已确认接受：`claude CLI 缺失`这类硬缺失从"提交瞬间红一条"变成
        "提交成功、数秒后转 failed"；容器侧第一道防线仍在（entrypoint 跑同一个
        `prepare_runtime` 入口并按退出码拒绝启动）。
        """
        job_id = row["job_id"]
        engine = self._engine_for(row.get("engine"))
        if engine is None:
            self.repo.cas_status(
                db, job_id, from_status="queued", status="failed",
                status_detail="引擎提交失败", claimed_by=None, claimed_at=None,
                error=f"引擎不可用：{row.get('engine')}")
            stats["rejected"] += 1
            return
        try:
            if row.get("task_type") == "revise":
                # 逐页修改：靠 revise_meta 还原 engine.revise 入参（同 sources 之于 submit）
                meta = row.get("revise_meta") or {}
                engine.revise(
                    job_id,
                    source_task_id=meta.get("source_task_id"),
                    pages=meta.get("pages") or None,
                    deck=meta.get("deck"),
                    instruction=meta.get("instruction"),
                    engine_params=row.get("engine_params") or {},
                )
            else:
                JobService.engine_submit(
                    engine,
                    content=row.get("content"),
                    title=row.get("title"),
                    pages=row.get("pages"),
                    engine_params=row.get("engine_params") or {},
                    sources=row.get("sources") or [],   # 搬家带来的硬前置：不入库就还原不出入参
                    job_id=job_id,
                    engine_type=row.get("task_type"),
                )
        except Exception as exc:  # noqa: BLE001
            # 与搬走之前的行为一致：引擎抛异常就判失败，不退回队列（重试只会再失败一次）
            self.repo.cas_status(
                db, job_id, from_status="queued", status="failed",
                status_detail="引擎提交失败", claimed_by=None, claimed_at=None,
                error=f"引擎提交失败：{exc}")
            stats["rejected"] += 1
            logger.warning("任务 %s 引擎提交失败：%s", job_id[:8], exc)
            return

        if not self.repo.promote_to_running(db, job_id, self.claimant):
            # CAS 输了 = 这一行在 fork 期间被撤销/落走。**必须回收刚起的子进程**，
            # 否则就是"库里已 cancelled、claude 还在跑"的新僵尸（最容易写错的一处）。
            self._terminate(engine, job_id)
            stats["lost_claim"] += 1
            logger.info("任务 %s 在 fork 期间已被落走终态，回收刚启动的引擎进程", job_id[:8])
            return
        stats["claimed"] += 1

    # ── 终态收割 ──
    def _finalize(self, db, row: dict, engine, estatus: EngineJobStatus, stats: dict,
                  *, extra_error: str | None = None) -> None:
        """引擎报了终态 → 落 DB（CAS）→ 只在**自己真的落了**的时候做副作用。

        副作用（演示文件/草稿实体入库）不能重复：判据就是 `cas_status` 的 rowcount，
        抢不到终态写入权的这一方直接放弃，于是"引擎自己判完"和"调度器判完"
        乃至"用户撤销"三方 concurrently 发生时，演示文件只会被登记一次。
        """
        job_id = row["job_id"]
        status = _ENGINE_TO_TASK_STATUS.get(estatus)
        if status is None:  # 引擎态与映射表不对齐（契约被改坏时的兜底）
            logger.error("任务 %s 返回未知引擎态 %r，按 failed 处理", job_id[:8], estatus)
            status = "failed"

        result_path = None
        if status == "completed":
            try:
                result_path = engine.result(job_id)
            except Exception:  # noqa: BLE001 - 产物定位失败不改变"引擎说它完成了"这个事实
                result_path = None

        data = self._job_json(job_id)
        fields: dict = {
            "status": status,
            "status_detail": "生成完成" if status == "completed"
            else ("生成超时" if status == "timeout" else "生成失败"),
            "result_path": result_path,
        }
        if status != "completed":
            fields["error"] = extra_error or data.get("error") or "生成失败，详见引擎 run.log"
        # token 消耗：失败/超时的也已烧掉的部分同样如实回填，未上报仍为 NULL（ADR-016）
        usage = self._engine_reported_usage(job_id)
        if usage:
            fields["token_usage"] = usage
        # 用户没给标题时，回填引擎从素材里拟的那个
        if status == "completed" and not row.get("title"):
            engine_title = self._engine_reported_title(job_id)
            if engine_title:
                fields["title"] = engine_title

        if not self.repo.cas_status(db, job_id, from_status="running", **fields):
            return
        stats["finalized"] += 1

        fresh = self.repo.get(db, job_id) or {**row, **fields}
        try:
            task_type = fresh.get("task_type")
            if status == "completed" and task_type == "output" and result_path:
                self.service.persist_presentation(fresh, result_path)
            elif status == "completed" and task_type == "revise" and result_path:
                # 修改成功 → 在版本树上落一个子演示文件（ADR-023）
                self.service.persist_sub_presentation(fresh, result_path)
            elif status == "completed" and task_type == "draft":
                self.service.persist_draft_entity(fresh)
        except Exception:  # noqa: BLE001
            # 任务终态已经落库了，实体登记的失败要看得见，但不能把任务改判
            logger.exception("任务 %s 终态副作用（演示文件/草稿实体入库）失败", job_id[:8])

    # ── 小工具 ──
    def _engine_for(self, name):
        """按注册名拿引擎实例；名字已从注册表消失时返回 None（调用方按不可用处理）。"""
        try:
            return self.engine_factory(name)
        except ValueError:
            return None

    def _terminate(self, engine, job_id: str) -> bool:
        """调引擎的 cancel 钩子。没实现就是杀不了 —— 返回 False 让调用方如实记录，
        绝不假装"已终止"（契约明令禁止静默假撤销）。
        """
        cancel = getattr(engine, "cancel", None)
        if cancel is None or not callable(cancel):
            return False
        try:
            return bool(cancel(job_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("任务 %s cancel 钩子异常：%s", job_id[:8], exc)
            return False

    @staticmethod
    def _job_json(job_id: str) -> dict:
        """读引擎自己写的 job.json（标题与 token 用量的落点，口径见 ADR-016）。"""
        from ..core.db import JOBS_ROOT
        path = JOBS_ROOT / str(job_id) / "job.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @classmethod
    def _engine_reported_usage(cls, job_id: str) -> dict | None:
        """只认带派生总数的形态；残缺对象按"未记录"处理，不补 0（ADR-016）。"""
        usage = cls._job_json(job_id).get("token_usage")
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            return usage
        return None

    @classmethod
    def _engine_reported_title(cls, job_id: str) -> str | None:
        title = cls._job_json(job_id).get("title")
        if isinstance(title, str) and title.strip() and title != "Untitled":
            return title
        return None
