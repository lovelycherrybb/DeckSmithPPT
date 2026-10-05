"""数据存取层（SQLAlchemy）。返回普通 dict，避免 ORM 对象跨会话失效。

包含项目、任务、草稿、草稿版本、演示文件的 CRUD 操作。
"""
# 须为文件首条语句。本模块的类里定义了名为 `list` 的方法（ProjectRepo.list 等），
# 同名之后、同类中方法的注解 `list[str]` 会在类作用域解析到该函数对象而非内建类型：
# Python 3.11/3.12 在 import 期即抛 `TypeError: 'function' object is not subscriptable`，
# 而 3.14 起注解默认惰性求值（PEP 649）恰好把它掩盖了——开发机因此从未暴露。
# 本行让各版本行为一致，也是 pyproject 里 requires-python ">=3.11" 能成立的前提。
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select, update

from ..models import Project, Job, Draft, DraftVersion, Presentation, SubPresentation
from ..core.db import SessionLocal


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_json_columns(fields: dict) -> dict:
    """把「以 JSON 文本存列」的容器值就地序列化（engine_params / token_usage / sources）。

    create 与 update 两条写路径必须同规则，否则一边存对象一边存字符串。
    None 保持 None（落库为 NULL）——token_usage 的 NULL 就是「未记录」这个语义
    （ADR-016），序列化成字符串 "null" 就读不回来了。
    """
    for key in ("engine_params", "token_usage", "revise_meta"):
        if isinstance(fields.get(key), dict):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    if isinstance(fields.get("sources"), (list, tuple)):
        fields["sources"] = json.dumps(list(fields["sources"]), ensure_ascii=False)
    return fields


class ProjectRepo:
    """项目表 CRUD。"""

    @staticmethod
    def _row_to_dict(obj: Project) -> dict:
        return {
            "project_id": obj.project_id,
            "project_name": obj.project_name,  # 与 contracts 对齐
            "current_draft_id": obj.current_draft_id,  # 与 contracts 对齐
            "current_draft_version": obj.current_draft_version,  # 与 contracts 对齐
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
            # 归属主体（v0.8）：owner_user_id=NULL 且 visitor_id 有值 = 未认领访客项目。
            # owner_label 不入库，由 core/ownership.attach_owner_label 出参时现算。
            "owner_user_id": obj.owner_user_id,
            "visitor_id": obj.visitor_id,
        }

    def create(self, db, **fields) -> dict:
        obj = Project(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, project_id: str) -> dict | None:
        obj = db.execute(select(Project).where(Project.project_id == project_id)).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list(self, db, limit: int = 20, offset: int = 0, *,
             owner_user_id: str | None = None, visitor_only: str | None = None) -> list[dict]:
        query = select(Project).order_by(Project.created_at.desc(), Project.id.desc())
        # 归属过滤（v0.8）：owner_user_id 精确匹配；visitor_only 只看该访客未认领的项目。
        # 二者都不传 = 不加过滤（管理员或无鉴权上下文的旧调用）。
        if owner_user_id is not None:
            query = query.where(Project.owner_user_id == owner_user_id)
        if visitor_only is not None:
            query = query.where(Project.owner_user_id.is_(None), Project.visitor_id == visitor_only)
        rows = db.execute(query.limit(limit).offset(offset)).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]

    def update(self, db, project_id: str, **fields) -> dict | None:
        obj = db.execute(select(Project).where(Project.project_id == project_id)).scalar_one_or_none()
        if obj is None:
            return None
        for key, value in fields.items():
            setattr(obj, key, value)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def stats(self, db, project_ids: list[str]) -> dict[str, dict]:
        """批量统计各项目的草稿数 / 演示文件数 / 未结束任务数（三条 group-by，避免 N+1）。

        `running_task_count` 的口径是**未结束**（queued + running），不是"status 等于
        running"：引入排队态之后再只数 running，项目卡上就会出现"看着 0 个在跑、
        其实有 3 个在排队"的假象（ADR-018 消费侧口径）。字段名沿用契约不改。
        """
        from sqlalchemy import func
        if not project_ids:
            return {}

        def group_count(model, column, *extra_filters):
            query = select(column, func.count()).where(column.in_(project_ids))
            for f in extra_filters:
                query = query.where(f)
            return dict(db.execute(query.group_by(column)).all())

        drafts = group_count(Draft, Draft.project_id)
        presentations = group_count(Presentation, Presentation.project_id)
        unfinished = group_count(Job, Job.project_id, Job.status.in_(("queued", "running")))
        return {
            pid: {
                "draft_count": drafts.get(pid, 0),
                "presentation_count": presentations.get(pid, 0),
                "running_task_count": unfinished.get(pid, 0),
            }
            for pid in project_ids
        }

    def claim_visitor_projects(self, db, visitor_id: str | None, user_id: str) -> int:
        """把该访客名下、尚未被认领的项目并入真实用户；返回并入数量。

        只动 owner_user_id IS NULL 且 visitor_id 命中的行；已归属真实用户的不碰。
        """
        if not visitor_id:
            return 0
        rows = db.execute(
            select(Project).where(Project.visitor_id == visitor_id, Project.owner_user_id.is_(None))
        ).scalars().all()
        now = _now()
        for p in rows:
            p.owner_user_id = user_id
            p.updated_at = now
        db.commit()
        return len(rows)

    def delete(self, db, project_id: str) -> str:
        """删除项目。本期只允许删未认领（owner_user_id IS NULL）的访客项目。

        返回 'deleted' / 'owned'（已归属真实用户，拒删→上层转 409）/ 'not_found'。
        级联删关联的 job/draft/draft_version/presentation 行，避免孤儿；
        磁盘产物（outputs/jobs 下）不删——文件保护，且重跑/审计可能还要用。
        """
        obj = db.execute(select(Project).where(Project.project_id == project_id)).scalar_one_or_none()
        if obj is None:
            return "not_found"
        if obj.owner_user_id is not None:
            return "owned"
        draft_ids = list(db.execute(
            select(Draft.draft_id).where(Draft.project_id == project_id)
        ).scalars().all())
        if draft_ids:
            db.execute(delete(DraftVersion).where(DraftVersion.draft_id.in_(draft_ids)))
        db.execute(delete(Draft).where(Draft.project_id == project_id))
        db.execute(delete(Job).where(Job.project_id == project_id))
        # 先删子演示文件再删根演示文件（子表 FK 指向 presentations，反过来留孤儿）
        db.execute(delete(SubPresentation).where(SubPresentation.project_id == project_id))
        db.execute(delete(Presentation).where(Presentation.project_id == project_id))
        db.delete(obj)
        db.commit()
        return "deleted"

    def set_owner(self, db, project_id: str, user_id: str) -> dict | None:
        """管理员代为认领：把项目归属改为指定用户。项目不存在返回 None。"""
        obj = db.execute(select(Project).where(Project.project_id == project_id)).scalar_one_or_none()
        if obj is None:
            return None
        obj.owner_user_id = user_id
        obj.updated_at = _now()
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)


class JobRepo:
    """任务表 CRUD。方法第一个参数是 db 会话（由调用方提供）。"""

    @staticmethod
    def _row_to_dict(obj: Job) -> dict:
        return {
            "job_id": obj.job_id,
            "project_id": obj.project_id,
            "task_type": obj.task_type,
            "status": obj.status,
            "status_detail": obj.status_detail,
            # 调度器要用这三个值做认领判定与超时预算（ADR-019）。它们**不进契约**：
            # JobOut 是 extra="ignore"，多出来的键不会出现在任何响应体里。
            "claimed_by": obj.claimed_by,
            "claimed_at": obj.claimed_at,
            "started_at": obj.started_at,
            "engine_job_id": obj.engine_job_id,  # 与 contracts 对齐
            "title": obj.title,
            "pages": obj.pages,
            "engine": obj.engine,
            "agent": obj.agent,
            "content_style": obj.content_style,
            "engine_params": json.loads(obj.engine_params) if obj.engine_params else {},
            "content": obj.content,
            # 调度器认领时要靠它还原 engine.submit 的 sources 入参（ADR-019 决策 2）。
            # JobOut 是 extra="ignore"，所以多出来这个键不会污染契约响应。
            "sources": json.loads(obj.sources) if obj.sources else [],
            # 修改任务输入（revise 才有）：JSON 文本 → dict；非 revise 恒为 {}
            "revise_meta": json.loads(obj.revise_meta) if obj.revise_meta else {},
            "draft_content": obj.draft_content,
            "draft_path": obj.draft_path,
            "draft_id": obj.draft_id,
            "draft_version": obj.draft_version,
            "result_path": obj.result_path,
            "error": obj.error,
            # JSON 文本 → dict；NULL/空串一律 None（= 未记录，不是 0）
            "token_usage": json.loads(obj.token_usage) if obj.token_usage else None,
            "created_by": obj.created_by,
            "updated_by": obj.updated_by,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }

    def create(self, db, **fields) -> dict:
        fields.setdefault("content_style", "marketing_promo")
        fields.setdefault("task_type", "draft")
        _encode_json_columns(fields)
        obj = Job(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, job_id: str) -> dict | None:
        obj = db.execute(select(Job).where(Job.job_id == job_id)).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list(self, db, project_id: str | None = None, status: str | None = None, 
             task_type: str | None = None, limit: int = 20, offset: int = 0,
             project_ids: list[str] | None = None) -> list[dict]:
        query = select(Job)
        if project_id:
            query = query.where(Job.project_id == project_id)
        # 归属过滤（v0.8）：跨项目列表时把可见项目 id 集传进来；None=不受限，[]=看不到
        if project_ids is not None:
            query = query.where(Job.project_id.in_(project_ids))
        if status:
            query = query.where(Job.status == status)
        if task_type:
            query = query.where(Job.task_type == task_type)
        query = query.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit).offset(offset)
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]

    def update(self, db, job_id: str, **fields) -> dict | None:
        """无条件更新（仅供调用方持有该行写权限时使用）。

        并发敏感的终态写入**不要**用本方法，走 `cas_status`——本方法不带前置条件，
        会把别人刚落好的终态抹掉（ADR-019 决策 4）。
        """
        obj = db.execute(select(Job).where(Job.job_id == job_id)).scalar_one_or_none()
        if obj is None:
            return None
        if "content_style" in fields and fields["content_style"] is None:
            fields["content_style"] = "marketing_promo"
        _encode_json_columns(fields)
        for key, value in fields.items():
            setattr(obj, key, value)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    # ── 调度器专用写入（ADR-019 决策 3/4）──────────────────────────────
    # 共同口径：**一律带前置条件**，以 rowcount 判定自己是否抢到；抢不到就放弃覆写，
    # 绝不"先 SELECT 再 UPDATE"（那是 TOCTOU），也绝不无条件 UPDATE（那会把别人已落的
    # 终态抹掉）。于是"用户撤队 vs 调度器 fork 成功 vs 引擎判超时"三方竞态都有确定答案。

    #: 认领占位超过这个秒数仍未提升 = 上一个认领者大概已经崩了，允许被抢回。
    #: 判定证据由调度器的收尸阶段负责（job.json 里有活 pid 或有产物 → 提升为 running）。
    CLAIM_STALE_SECONDS = 120

    @staticmethod
    def _claim_stale_before() -> str:
        """陈旧占位的判定界。

        刻意与 `_now()` 同用 UTC：`claimed_at` 存的是 ISO 字符串，比较走字典序，
        混用本地时区（带 +08:00 偏移）会让"界"和"值"不同格式、判定当场失真。
        """
        return (datetime.now(timezone.utc)
                - timedelta(seconds=JobRepo.CLAIM_STALE_SECONDS)).isoformat()

    def list_stale_claims(self, db) -> list[dict]:
        """被占了位却迟迟没提升为 running 的 queued 行（认领者大概已经崩了）。

        时间比较只留在这一个地方（见 `_claim_stale_before` 的注释），调度器不再自己算界。
        """
        stale_before = self._claim_stale_before()
        rows = db.execute(
            select(Job).where(Job.status == "queued", Job.claimed_at.is_not(None),
                              Job.claimed_at < stale_before)
            .order_by(Job.created_at.asc(), Job.id.asc())
        ).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]

    def claim_next_queued(self, db, claimant: str, *, generation_lane: bool = True) -> dict | None:
        """预留队头一个可认领的 queued 任务（严格 FIFO），返回抢到的行；无则 None。

        两条通道（ADR-024）：`generation_lane=True` 只认领**输出引擎任务**（受 max_concurrent
        生成闸约束）；`False` 只认领**草稿/修改**等快通道任务（不等生成槽）。
        只打 `claimed_by/claimed_at`，**status 仍是 queued**——fork 成功后才由
        `promote_to_running` 翻态（契约把 queued 定义为"子进程尚未 fork 成功"，
        先置 running 再 fork 会让 fork 失败的行冒充 running）。
        """
        stale_before = self._claim_stale_before()
        lane_filter = (Job.task_type == "output") if generation_lane else (Job.task_type != "output")
        for _ in range(10):  # 有界重试：这一行被别人抢先就换下一行，不空转
            cand = db.execute(
                select(Job.job_id).where(
                    Job.status == "queued",
                    lane_filter,
                    (Job.claimed_by.is_(None)) | (Job.claimed_at < stale_before),
                ).order_by(Job.created_at.asc(), Job.id.asc()).limit(1)
            ).scalar_one_or_none()
            if cand is None:
                return None
            res = db.execute(
                update(Job)
                .where(Job.job_id == cand, Job.status == "queued")
                .values(claimed_by=claimant, claimed_at=_now())
            )
            db.commit()
            if res.rowcount:
                return self.get(db, cand)
        return None

    def promote_to_running(self, db, job_id: str, claimant: str) -> bool:
        """CAS：queued（且仍是我占的位）→ running，并记 `started_at` 作超时预算起点。

        返回 False **不是异常**，而是"这一行已被别人落走"（多半是用户在这空档撤销了）。
        调用方必须回收刚 fork 出来的子进程，否则就是"库里已 cancelled、claude 还在跑"
        的新僵尸——本设计最容易写错的一处（ADR-019 决策 3）。
        """
        res = db.execute(
            update(Job)
            .where(Job.job_id == job_id, Job.status == "queued", Job.claimed_by == claimant)
            .values(status="running", started_at=_now(), status_detail="引擎执行中",
                    updated_at=_now())
        )
        db.commit()
        return res.rowcount > 0

    def release_claim(self, db, job_id: str, claimant: str) -> bool:
        """交还预留槽位（fork 失败或提升 CAS 输掉时调用），让任务重新排队。"""
        res = db.execute(
            update(Job)
            .where(Job.job_id == job_id, Job.status == "queued", Job.claimed_by == claimant)
            .values(claimed_by=None, claimed_at=None, updated_at=_now())
        )
        db.commit()
        return res.rowcount > 0

    def cas_status(self, db, job_id: str, *, from_status: str, **fields) -> bool:
        """带前置条件的状态写入：只有当前仍是 from_status 才改，改不到就 False。"""
        fields.setdefault("updated_at", _now())
        _encode_json_columns(fields)
        res = db.execute(
            update(Job).where(Job.job_id == job_id, Job.status == from_status).values(**fields)
        )
        db.commit()
        return res.rowcount > 0

    def count_by_status(self, db, *statuses: str) -> int:
        """按状态集合计数（跨所有任务类型；快通道/粗闸等总量口径用它）。"""
        from sqlalchemy import func
        return db.execute(
            select(func.count()).select_from(Job).where(Job.status.in_(statuses))
        ).scalar_one()

    def count_generation(self, db, *statuses: str) -> int:
        """只数**输出引擎任务**（generation lane）——并发闸 max_concurrent 的唯一分母。

        引入快通道后（ADR-024），"还有几个生成槽"必须只按 output 计，否则草稿/修改会占用
        生成额度、把真正的 PPT 生成挤到排队。
        """
        from sqlalchemy import func
        return db.execute(
            select(func.count()).select_from(Job).where(
                Job.status.in_(statuses), Job.task_type == "output")
        ).scalar_one()

    def list_by_status(self, db, *statuses: str) -> list[dict]:
        rows = db.execute(
            select(Job).where(Job.status.in_(statuses)).order_by(Job.created_at.asc(), Job.id.asc())
        ).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]

    def count_unfinished_for_identity(self, db, *, user_id: str | None = None,
                                      visitor_id: str | None = None) -> int:
        """该身份未完成任务数（queued + running），用于 per-identity 粗闸（ADR-019 决策 7）。

        归属沿 projects 走：登录用户看 owner_user_id，访客看未认领的 visitor_id。
        两个都传就取并集（同一个人可能既有登录项目又有登录前的访客项目）。
        """
        from sqlalchemy import func, or_
        conds = []
        if user_id:
            conds.append(Project.owner_user_id == user_id)
        if visitor_id:
            conds.append(
                (Project.owner_user_id.is_(None)) & (Project.visitor_id == visitor_id)
            )
        if not conds:
            return 0
        query = (select(func.count()).select_from(Job)
                 .join(Project, Project.project_id == Job.project_id)
                 .where(Job.status.in_(("queued", "running")), or_(*conds)))
        return db.execute(query).scalar_one()

    def queue_position(self, db, job_id: str) -> int | None:
        """排队位序：本任务前面还有多少个未认领的 queued **输出**任务；非 queued 或非输出返回 None。

        只有输出引擎任务进生成排队（ADR-024），所以：非 output 任务恒为 None（它不在这条队里）；
        位序也只数 output 的未认领 queued。口径与契约一致（openapi Job.queue_position）：
        **读取时现算的派生值**，按 created_at FIFO，只数 claimed_by IS NULL 的（已被占位的等于轮到它了）。
        不入库、不构成承诺。
        """
        from sqlalchemy import func
        mine = db.execute(
            select(Job.created_at, Job.id, Job.status, Job.task_type).where(Job.job_id == job_id)
        ).one_or_none()
        if mine is None or mine[2] != "queued" or mine[3] != "output":
            return None
        return db.execute(
            select(func.count()).select_from(Job).where(
                Job.status == "queued", Job.task_type == "output", Job.claimed_by.is_(None),
                (Job.created_at < mine[0])
                | ((Job.created_at == mine[0]) & (Job.id < mine[1])),
            )
        ).scalar_one()


class DraftRepo:
    """草稿表 CRUD。"""

    @staticmethod
    def _row_to_dict(obj: Draft) -> dict:
        return {
            "draft_id": obj.draft_id,
            "project_id": obj.project_id,
            "task_id": obj.task_id,
            "current_version": obj.current_version,
            "draft_path": obj.draft_path,  # 与 contracts 对齐
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }

    def create(self, db, **fields) -> dict:
        obj = Draft(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, draft_id: str) -> dict | None:
        obj = db.execute(select(Draft).where(Draft.draft_id == draft_id)).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list_by_project(self, db, project_id: str) -> list[dict]:
        query = select(Draft).where(Draft.project_id == project_id).order_by(Draft.created_at.desc())
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]

    def update(self, db, draft_id: str, **fields) -> dict | None:
        obj = db.execute(select(Draft).where(Draft.draft_id == draft_id)).scalar_one_or_none()
        if obj is None:
            return None
        for key, value in fields.items():
            setattr(obj, key, value)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)


class DraftVersionRepo:
    """草稿版本表 CRUD。"""

    @staticmethod
    def _row_to_dict(obj: DraftVersion) -> dict:
        return {
            "draft_id": obj.draft_id,
            "version": obj.version,
            "content": obj.content,
            "created_at": obj.created_at,
        }

    def create(self, db, **fields) -> dict:
        obj = DraftVersion(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, draft_id: str, version: str) -> dict | None:
        obj = db.execute(
            select(DraftVersion).where(
                DraftVersion.draft_id == draft_id,
                DraftVersion.version == version
            )
        ).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list_by_draft(self, db, draft_id: str) -> list[dict]:
        query = select(DraftVersion).where(DraftVersion.draft_id == draft_id).order_by(DraftVersion.created_at.desc())
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]


class PresentationRepo:
    """演示文件表 CRUD。"""

    @staticmethod
    def _row_to_dict(obj: Presentation) -> dict:
        return {
            "presentation_id": obj.presentation_id,
            "project_id": obj.project_id,
            "task_id": obj.task_id,
            "file_name": obj.file_name,  # 与 contracts 对齐
            "file_format": obj.file_format,  # 与 contracts 对齐
            "file_path": obj.file_path,  # 与 contracts 对齐
            "created_at": obj.created_at,
        }

    def create(self, db, **fields) -> dict:
        obj = Presentation(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, presentation_id: str) -> dict | None:
        obj = db.execute(select(Presentation).where(Presentation.presentation_id == presentation_id)).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list_by_project(self, db, project_id: str) -> list[dict]:
        query = select(Presentation).where(Presentation.project_id == project_id).order_by(Presentation.created_at.desc())
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]


class SubPresentationRepo:
    """子演示文件表 CRUD（演示文件逐页修改后另存的版本树）。"""

    @staticmethod
    def _row_to_dict(obj: SubPresentation) -> dict:
        return {
            "sub_presentation_id": obj.sub_presentation_id,
            "presentation_id": obj.presentation_id,
            "parent_sub_presentation_id": obj.parent_sub_presentation_id,  # NULL=改自根演示文件
            "project_id": obj.project_id,
            "task_id": obj.task_id,
            "file_name": obj.file_name,  # 与 contracts 对齐
            "file_format": obj.file_format,
            "file_path": obj.file_path,
            "created_at": obj.created_at,
        }

    def create(self, db, **fields) -> dict:
        obj = SubPresentation(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, sub_presentation_id: str) -> dict | None:
        obj = db.execute(select(SubPresentation).where(
            SubPresentation.sub_presentation_id == sub_presentation_id)).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list_by_presentation(self, db, presentation_id: str) -> list[dict]:
        """某根演示文件下的全部子文件（扁平，前端按 parent_sub_presentation_id 拼缩进树）。"""
        query = select(SubPresentation).where(
            SubPresentation.presentation_id == presentation_id).order_by(SubPresentation.created_at)
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]

    def count_by_presentation(self, db, presentation_id: str) -> int:
        return len(db.execute(select(SubPresentation.sub_presentation_id).where(
            SubPresentation.presentation_id == presentation_id)).scalars().all())
