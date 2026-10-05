"""业务逻辑层：提交/查询，串联引擎（PptEngine 协议）与数据存取。

- 只依赖 PptEngine 协议，不关心具体引擎（stub 或真实 PptMasterEngine 皆可替换）。
- **提交即入库排队**（status=queued），由 `app.service.scheduler` 的调度循环认领、
  轮询并落终态（ADR-019）。本层不再挂 BackgroundTasks 轮询，也不再在请求线程里 fork
  引擎子进程 —— 那正是"约 36 条并发就把整个后端冻住"的根因。
- 支持项目自动创建、草稿版本管理、状态明细（status_detail）。
"""
# 与 repository/jobs.py 同源：本类有名为 list 的方法，注解里再用 `list[...]` 在
# Python 3.11/3.12 下会解析到该方法对象。此行使注解惰性求值，跨版本行为一致。
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..core.db import SessionLocal, JOBS_ROOT
from ..core import ownership, runtime_settings
from ..repository.jobs import ProjectRepo, JobRepo, DraftRepo, DraftVersionRepo, PresentationRepo
from ..core.schemas import DeckRequest, JobOut, ProgressInfo, PageItem, JobPages
from ..engines.registry import implements_hook

# ── 状态集合（ADR-018 消费侧口径）──────────────────────────────────
# 全仓库判"任务是否还在推进"一律走这两个集合，**不要写 status != 'running'**：
# 那是引入 queued 之前的等价写法，之后就错了（排队中的任务会被当成已结束）。
PENDING_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "timeout", "cancelled")
#: 可重试 = 未成功的终态。刻意**不含 completed**：那等于给一条已成功出片的历史
#: 任务开二次烧额度的口子（ADR-018 决策 4）。
RETRYABLE_STATUSES = ("failed", "timeout", "cancelled")


def is_terminal(status: str | None) -> bool:
    """是否终态。未知值按"不是终态"处理更安全？不——未知值说明有东西写歪了，
    按终态处理会让调度器再也接管不到它，所以这里如实返回 False 并让上层去报警。
    """
    return status in TERMINAL_STATUSES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _version_stamp() -> str:
    """版本号：UTC 年月日时分秒（14 位）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _queued_status_detail(position: int | None, *, retry: bool = False,
                          running: int | None = None, capacity: int | None = None) -> str:
    """queued 行的状态明细：把排队位序写进文案（出参时现算，**不落库**）。

    静态的"排队中"没有信息量——排队时该知道的就是"还要等几个"。口径与契约
    Job.queue_position 完全一致：只数前面尚未被认领的 queued 任务，不含正在生成
    的占槽任务；位序拿不到时退回原静态文案，绝不编数字。
    落库行仍写静态"排队中"：位序是读取时快照，落库那一刻就已过期。

    **队首要说两句不同的话**：`queue_position == 0` 只代表"前面没有排队的人"，
    不代表"马上开始"——并发闸占满时它还得等某个 running 让出槽位。实测踩过一次：
    闸门 2、2 个在跑、它排第一，界面却显示"即将开始"，用户据此以为马上出片。
    所以拿到 running/capacity 时按占位情况分说；拿不到（调用方没查）才沿用"即将开始"。
    """
    base = "排队中（重试）" if retry else "排队中"
    if position is None:
        return base
    if position > 0:
        return f"{base} · 前方还有 {position} 个任务"
    if running is not None and capacity is not None and running >= capacity:
        return f"{base} · 等待空位（{running} 个正在生成）"
    return f"{base} · 即将开始"


_VERSION_RE = re.compile(r"^[0-9]{14}$")

# 上传文件名消毒：只留安全字符，防路径穿越
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]")

# 轮询节拍与超时预算已随调度循环搬到 core.config（DECKSMITH_SCHEDULER_INTERVAL /
# DECKSMITH_JOB_TIMEOUT，见 ADR-019）。这里不再留第二份口径。


def sanitize_filename(name: str | None) -> str:
    """消毒上传文件名：去路径部分、替换危险字符、限制长度。"""
    if not name:
        return "upload.bin"
    # 只取 basename（防 ../../x 和绝对路径）
    name = Path(name).name
    name = _SAFE_FILENAME_RE.sub("_", name).strip("._") or "upload.bin"
    return name[:120]


class JobService:
    def __init__(self, repo: JobRepo, engine_factory):
        self.repo = repo
        self.project_repo = ProjectRepo()
        self.draft_repo = DraftRepo()
        self.draft_version_repo = DraftVersionRepo()
        self.presentation_repo = PresentationRepo()
        self._engine_factory = engine_factory  # 按引擎名创建实例（引擎可插拔）

    # ── 引擎选择：按请求里的 engine 名拿实例，而非全局单例 ──
    def _get_engine(self, engine_name: str | None):
        return self._engine_factory(engine_name)

    def _get_engine_safe(self, engine_name: str | None):
        """容错版引擎获取：无效引擎名返回 None（用于查询路径，不抛错）。"""
        try:
            return self._get_engine(engine_name)
        except ValueError:
            return None

    @staticmethod
    def _resolve_style(engine_params: dict | None) -> str:
        return (engine_params or {}).get("style") or "swiss-minimal"

    def _enrich_with_progress(self, job: JobOut, engine) -> JobOut:
        """如果任务正在运行，附加进度信息。engine 为 None（无效引擎名）时跳过。"""
        if engine is None:
            return job
        if job.status == "running" and hasattr(engine, 'get_progress'):
            try:
                progress_data = engine.get_progress(job.job_id)
            except Exception:
                progress_data = None
            if progress_data:
                job.progress = ProgressInfo(**progress_data)
        return job

    def _default_project_name(self, req: DeckRequest) -> str:
        """项目默认名：优先 title，其次第一个上传文件名（去扩展名），最后 Untitled。"""
        if req.title:
            return req.title
        for s in req.sources:
            if s.type == "file_path":
                stem = Path(s.value).stem
                if stem:
                    return stem[:100]
            elif s.type == "text" and s.value:
                first = s.value.strip().splitlines()[0].lstrip("# ").strip()
                if first:
                    return first[:50]
        return "Untitled"

    def _ensure_project(self, db: Session, project_id: str | None, default_name: str,
                        identity=None) -> str:
        """确保项目存在并归属正确。返回 project_id。

        传入已存在但当前身份无权访问的 project_id → 404（不泄露存在性）。
        新建项目按 identity 打归属戳：已登录→owner，未登录试用→visitor。
        """
        if project_id:
            project = self.project_repo.get(db, project_id)
            if project:
                if not ownership.can_access(project, identity):
                    raise HTTPException(status_code=404, detail="项目不存在")
                return project_id
            now = _now()
            self.project_repo.create(
                db,
                project_id=project_id,
                project_name=default_name,
                created_at=now,
                updated_at=now,
                **ownership.create_stamp(identity),
            )
            return project_id

        new_project_id = uuid.uuid4().hex
        now = _now()
        self.project_repo.create(
            db,
            project_id=new_project_id,
            project_name=default_name,
            created_at=now,
            updated_at=now,
            **ownership.create_stamp(identity),
        )
        return new_project_id

    # ── submit：提交任务 = 校验 + 入库排队，然后立刻返回 ──
    def submit(self, db: Session, req: DeckRequest, identity=None) -> JobOut:
        """受理任务：只负责校验与入库（status=queued），**不启动引擎**。

        认领、fork 引擎子进程与轮询全部由调度循环负责（ADR-019 决策 2）。这是并发闸
        能成立的前提：不在请求线程里 fork，用户瞬间提交 50 个也只会有
        `MAX_CONCURRENT_JOBS` 条真在跑。
        """
        # 校验版本号格式（与契约 pattern 一致）
        if req.version and not _VERSION_RE.match(req.version):
            raise HTTPException(status_code=400, detail="version 必须是 14 位数字（年月日时分秒）")

        # per-identity 粗闸（ADR-019 决策 7）：取代加权公平调度。
        # 本期真正的风险只是"一个人把队排到 50 个"，所以只数该身份的未完成任务。
        # 上限走系统设置的**生效值**（管理员在设置页改了立即生效，不用重启容器）。
        # identity 为 None（内部调用/旧单测）不设闸，与引擎参数门控同一口径。
        if identity is not None:
            limit = runtime_settings.max_unfinished_per_identity()
            user = getattr(identity, "user", None)   # Identity.user 是 ORM 对象或 None
            unfinished = self.repo.count_unfinished_for_identity(
                db, user_id=getattr(user, "user_id", None),
                visitor_id=getattr(identity, "visitor_id", None))
            if unfinished >= limit:
                raise HTTPException(
                    status_code=409,
                    detail=f"你已有 {unfinished} 个任务未结束，达到上限 {limit}；"
                           "请等待其中一个完成或先撤销")

        # 如果提供了 draft_id，从草稿版本中获取 content
        content = req.content
        used_version: str | None = None  # 本任务实际使用的草稿版本号（演示文件命名用）
        effective_title = req.title
        if req.draft_id and req.engine_type == "output":
            draft = self.draft_repo.get(db, req.draft_id)
            if draft is None:
                raise HTTPException(status_code=404, detail="草稿不存在")

            version = req.version or draft.get("current_version")
            if not version:
                raise HTTPException(status_code=400, detail="草稿没有版本，请指定 version")

            draft_version = self.draft_version_repo.get(db, req.draft_id, version)
            if draft_version is None:
                raise HTTPException(status_code=404, detail="草稿版本不存在")

            content = draft_version.get("content")
            used_version = version

            # 标题继承：草稿→输出交接原本只带 content 不带 title，于是 running 期
            # 列表只能显示「未命名」（产物出来后靠文件名掩盖）。标题挂在生成该草稿
            # 的任务上（drafts.task_id → jobs.title），这里顺链取回。
            if not effective_title and draft.get("task_id"):
                src_job = self.repo.get(db, draft.get("task_id"))
                if src_job:
                    effective_title = src_job.get("title") or None

        job_id = uuid.uuid4().hex
        sources = [s.model_dump() for s in req.sources] if req.sources else []
        engine_params = dict(req.engine_params or {})
        # 引擎参数角色门控（后端独立复核，前端禁用只是体验）：普通用户/访客忽略自定义参数，
        # 一律走引擎默认值。identity 为 None（内部调用/旧单测）不门控。
        if not ownership.can_edit_engine_params(identity):
            engine_params = {}

        # 确保项目存在
        project_id = self._ensure_project(db, req.project_id, self._default_project_name(req), identity)

        # 按请求选择引擎（引擎可插拔：选什么跑什么，不再静默降级到全局单例）。
        # 这里只校验名字可用，**不 fork 任何东西**；无效名字仍然当场 400。
        try:
            self._get_engine(req.engine)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # 落库即排队。sources 必须一起入库：引擎提交已搬到调度器里执行，
        # 认领时要靠它还原入参（ADR-019 决策 2 的硬前置）。
        now = _now()
        row = self.repo.create(
            db,
            job_id=job_id,
            project_id=project_id,
            task_type=req.engine_type,
            status="queued",
            status_detail="排队中",
            title=effective_title,
            pages=req.pages,
            engine=req.engine,
            agent=req.agent,
            content_style=req.content_style,
            engine_params=engine_params,
            content=content,
            sources=sources,
            draft_id=req.draft_id if req.engine_type == "output" else None,
            draft_version=used_version,
            created_at=now,
            updated_at=now,
        )

        out = JobOut(**row)
        return self._with_queue_detail(db, out)

    def _with_queue_detail(self, db: Session, job: JobOut) -> JobOut:
        """queued 行出参时补两件套：位序 + 把位序写进状态明细。

        所有 JobOut 出口（submit / list / get / retry）都走这里，判据只有
        `status == 'queued'` —— **不要写 status != 'running'**（ADR-018 口径）。
        重试行靠落库文案里的"（重试）"标记保留语义，其余整句重写为带位序的现算案。
        """
        if job.status == "queued":
            job.queue_position = self.repo.queue_position(db, job.job_id)
            running = capacity = None
            if job.queue_position == 0:
                # 只有队首才需要"有没有空位"这个事实；排在后面时用不上，别白查两次。
                # 生成闸只数 output（ADR-024），这里的 running 口径必须与 _claim_queued 一致。
                running = self.repo.count_generation(db, "running")
                capacity = runtime_settings.max_concurrent_jobs()
            job.status_detail = _queued_status_detail(
                job.queue_position,
                retry=(job.status_detail or "").endswith("（重试）"),
                running=running, capacity=capacity,
            )
        return job

    @staticmethod
    def engine_submit(engine, **kwargs):
        """调引擎 submit（对旧签名引擎做兼容适配）。"""
        import inspect
        sig = inspect.signature(engine.submit)
        params = sig.parameters
        # 旧签名引擎（单数 source、无 engine_type）
        if "sources" not in params:
            # 重试路径不传 sources，必须 pop 容错（硬 del 会 KeyError 让任务秒失败）
            source = kwargs.pop("sources", None) or None
            kwargs["source"] = source[0] if source else None
        if "engine_type" not in params:
            kwargs.pop("engine_type", None)
        engine.submit(**kwargs)

    def list(self, db: Session, project_id: str | None = None, status: str | None = None,
             task_type: str | None = None, limit: int = 20, offset: int = 0,
             project_ids: list[str] | None = None) -> list[JobOut]:
        rows = self.repo.list(db, project_id=project_id, status=status, task_type=task_type,
                              limit=limit, offset=offset, project_ids=project_ids)
        # 只为**排队中**的行补位序与状态明细（一次派生查询），为**运行中**的行补进度
        # （读引擎产物）。queued 刻意不读盘：它连子进程都还没有，扫不到任何东西，读了纯属浪费请求线程。
        jobs = [JobOut(**row) for row in rows]
        return [self._enrich_with_progress(self._with_queue_detail(db, job),
                                           self._get_engine_safe(job.engine))
                for job in jobs]

    # ── get：查任务 ──
    def get(self, db: Session, job_id: str) -> JobOut:
        row = self.repo.get(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        job = JobOut(**row)
        return self._enrich_with_progress(self._with_queue_detail(db, job),
                                          self._get_engine_safe(job.engine))

    # ── 逐页预览（引擎无关：能力由钩子实现派生，无引擎名分支）──
    def list_pages(self, db: Session, job_id: str) -> JobPages:
        """组装任务逐页预览快照。只承载已确定落盘的页：无预测总数、无 pending。

        末尾「生成中/导出中」转圈格不在数据里，由前端按 status+stage 渲染。
        """
        row = self.repo.get(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        status = row.get("status") or "running"
        engine_name = row.get("engine")
        engine = self._get_engine_safe(engine_name)
        supports = engine is not None and implements_hook(engine, "get_pages")
        if not supports:
            return JobPages(job_id=job_id, engine=engine_name, supports_page_preview=False,
                            status=status, stage=None, pages_generated=0, pages=[])

        pages: list[PageItem] = []
        stage: str | None = None
        try:
            if status == "running" and implements_hook(engine, "get_progress"):
                stage = (engine.get_progress(job_id) or {}).get("stage")

            root = engine.get_preview_root(job_id) if implements_hook(engine, "get_preview_root") else None
            refs = engine.get_pages(job_id) or []
            if root is not None:
                root_resolved = root.resolve()
                for ref in refs:
                    rel = Path(str(ref["path"])).resolve().relative_to(root_resolved).as_posix()
                    # url 用 quote 保留 / 分隔符（文件名可能含中文/空格）
                    pages.append(PageItem(
                        index=int(ref["index"]),
                        name=str(ref["name"]),
                        url=f"/api/jobs/{job_id}/preview/{quote(rel)}",
                        kind=str(ref.get("kind") or "image"),
                    ))
        except Exception:
            # 产物目录被清理等读盘异常：如实返回已确定的状态 + 空页，不整墙报错
            pages = []
        return JobPages(job_id=job_id, engine=engine_name, supports_page_preview=True,
                        status=status, stage=stage, pages_generated=len(pages), pages=pages)

    # preview 允许的扩展名 -> MIME（页内 ../images 相对引用的位图同源可解析）
    _PREVIEW_MEDIA_TYPES = {
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        # 单文件 HTML 引擎（guizang_ppt）的逐页预览：页 = 同一文件里的第 N 个
        # <section class="slide">，靠下面 build_html_preview 注入的引导脚本定位。
        # 注意：job 目录里还有 prompt.md / run.log / job.json 等审计文件，
        # 这张白名单就是它们不被同源读走的唯一屏障——加类型前先想清楚。
        ".html": "text/html",
        ".htm": "text/html",
    }

    # 预览引导脚本：必须早于 deck 自身脚本执行（注入在 <head> 开标签之后）。
    # 1) 存储垫片：预览 iframe 用严格沙箱（allow-scripts，不给 allow-same-origin），
    #    此时访问 localStorage 抛 SecurityError；实测模板 18 处 localStorage 用法里
    #    9 处无 try/catch 且位于顶层 IIFE（如 low-power key、noteSize），会让整页崩。
    #    故先探测，不可用就换成内存实现，既保住 deck 能跑，也绝不放开同源权限。
    # 2) 页定位：?slide=N 轮询等待 window.__playSlide 就绪后跳到第 N 页；
    #    ?static=1 调 __setLowPowerMode(true) 关 WebGL 背景，供页墙缩略图省 GPU。
    _PREVIEW_BOOTSTRAP = """<script id="decksmith-preview-boot">
(function () {
  function memStorage() {
    var mem = {};
    return {
      getItem: function (k) { k = String(k); return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null; },
      setItem: function (k, v) { mem[String(k)] = String(v); },
      removeItem: function (k) { delete mem[String(k)]; },
      clear: function () { mem = {}; },
      key: function (i) { return Object.keys(mem)[i] || null; }
    };
  }
  ['localStorage', 'sessionStorage'].forEach(function (name) {
    try { window[name].getItem('__decksmith_probe__'); } catch (e) {
      var shim = memStorage();
      Object.defineProperty(shim, 'length', { get: function () { return 0; } });
      try { Object.defineProperty(window, name, { value: shim, configurable: true }); } catch (e2) {}
    }
  });
  try {
    var q = new URLSearchParams(location.search);
    var slide = parseInt(q.get('slide') || '0', 10) || 0;
    var lowPower = q.get('static') === '1';
    if (slide > 0 || lowPower) {
      var tries = 0;
      var locate = function () {
        if (lowPower && typeof window.__setLowPowerMode === 'function') {
          try { window.__setLowPowerMode(true); } catch (e) {}
        }
        if (slide > 0 && typeof window.__playSlide === 'function') {
          try { window.__playSlide(slide - 1); } catch (e) {}
          return;
        }
        if (slide > 0 && tries++ < 50) setTimeout(locate, 100);
      };
      if (document.readyState === 'complete') setTimeout(locate, 0);
      else window.addEventListener('load', function () { setTimeout(locate, 0); });
    }
  } catch (e) {}
})();
</script>"""

    @classmethod
    def build_html_preview(cls, target: Path) -> str:
        """读产物 HTML 并注入预览引导脚本（只改响应，**不改磁盘上的产物**）。"""
        src = target.read_text(encoding="utf-8", errors="ignore")
        if 'id="decksmith-preview-boot"' in src:
            return src
        head = re.search(r"<head[^>]*>", src, re.IGNORECASE)
        if head:
            pos = head.end()
            return src[:pos] + "\n" + cls._PREVIEW_BOOTSTRAP + src[pos:]
        html = re.search(r"<html[^>]*>", src, re.IGNORECASE)
        if html:
            pos = html.end()
            return src[:pos] + "\n" + cls._PREVIEW_BOOTSTRAP + src[pos:]
        # 找不到锚点：追加在末尾（此时 slide 定位可能失效，但不会破坏文档结构）
        return src + "\n" + cls._PREVIEW_BOOTSTRAP

    def resolve_preview_file(self, db: Session, job_id: str, rel_path: str) -> tuple[Path, str]:
        """把 /preview/{path} 解析到引擎声明的产物根目录内并校验安全。

        返回 (绝对路径, media_type)；错误按契约：越界 400、不支持类型 415、
        任务/引擎无产物根/文件不存在 404。挂整个根目录（而非单文件）正是为了
        让页内相对引用（../images/xxx）同源解析——路径换算必须走 resolve()+
        is_relative_to 双保险。
        """
        row = self.repo.get(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        engine = self._get_engine_safe(row.get("engine"))
        root = None
        if engine is not None and implements_hook(engine, "get_preview_root"):
            try:
                root = engine.get_preview_root(job_id)
            except Exception:
                root = None
        if root is None:
            raise HTTPException(status_code=404, detail="该任务无可预览产物（引擎不支持或已被清理）")

        root_resolved = root.resolve()
        try:
            target = (root_resolved / rel_path).resolve()
        except (OSError, ValueError):
            raise HTTPException(status_code=400, detail="非法预览路径")
        if not target.is_relative_to(root_resolved):
            raise HTTPException(status_code=400, detail="非法预览路径")

        media_type = self._PREVIEW_MEDIA_TYPES.get(target.suffix.lower())
        if media_type is None:
            raise HTTPException(status_code=415, detail="不支持的预览文件类型")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="预览文件不存在")
        return target, media_type

    # ── retry：重试未成功结束的任务（创建新任务，从排队开始）──
    def retry(self, db: Session, job_id: str, identity=None) -> JobOut:
        row = self.repo.get(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        # 可重试 = 未成功的终态（failed/timeout/cancelled）。completed 拒绝——已成功
        # 的条目不该有二次烧额度的入口；queued/running 拒绝——它还没结束（ADR-018 决策 4）
        if row["status"] not in RETRYABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"任务状态不允许重试（仅 {'/'.join(RETRYABLE_STATUSES)} 可重试）")

        # 创建新任务，复用原任务的参数（含 sources：契约的"参数复用"口径）
        new_job_id = uuid.uuid4().hex
        now = _now()

        content = row.get("content") or ""

        new_row = self.repo.create(
            db,
            job_id=new_job_id,
            project_id=row.get("project_id"),
            task_type=row.get("task_type"),
            status="queued",
            status_detail="排队中（重试）",
            title=row.get("title"),
            pages=row.get("pages"),
            engine=row.get("engine"),
            agent=row.get("agent"),
            content_style=row.get("content_style"),
            engine_params=row.get("engine_params") or {},
            content=content,
            sources=row.get("sources") or [],
            draft_id=row.get("draft_id"),
            draft_version=row.get("draft_version"),
            created_at=now,
            updated_at=now,
        )

        # 只校验引擎名可用（无效名字仍然当场 400）；真正的提交交给调度循环
        try:
            self._get_engine(row.get("engine"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        out = JobOut(**new_row)
        return self._with_queue_detail(db, out)

    # ── cancel：撤销未到终态的任务（ADR-019 决策 4/5）──
    def cancel(self, db: Session, job_id: str) -> JobOut:
        """撤销 queued 或 running 的任务。

        与调度器的竞态靠**带前置条件的写入**解决，不靠锁：
        - queued → cancelled 抢到了：直接从队列摘掉，引擎进程根本还没起；
          没抢到（调度器刚好在同一瞬把它提升成 running）：重读后走 running 分支。
        - running → 先问引擎能不能杀（`cancel` 钩子），**不能杀就 409**，
          绝不改库假装已撤销——那正是契约里明令禁止的"静默假撤销"。

        已烧掉的 token 一律保留（ADR-016），撤销不抹账。
        """
        row = self.repo.get(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        status = row["status"]
        if is_terminal(status):
            raise HTTPException(status_code=409, detail="任务已结束，无法撤销")

        if status == "queued":
            if self.repo.cas_status(
                    db, job_id, from_status="queued", status="cancelled",
                    status_detail="已取消", error="任务在排队期间被取消",
                    claimed_by=None, claimed_at=None):
                return self.get(db, job_id)
            row = self.repo.get(db, job_id) or row     # 抢输了：调度器刚接手，重读再判
            status = row["status"]
            if is_terminal(status):
                raise HTTPException(status_code=409, detail="任务已结束，无法撤销")

        # 到这里一定是 running：必须引擎真的能停，才允许落 cancelled
        engine = self._get_engine_safe(row.get("engine"))
        if engine is None or not implements_hook(engine, "cancel"):
            raise HTTPException(
                status_code=409,
                detail="该引擎不支持中断运行中的任务，只能撤销还在排队的任务")
        if not self._terminate(engine, job_id):
            raise HTTPException(status_code=409, detail="引擎进程终止失败，任务未撤销，请重试")
        if not self.repo.cas_status(
                db, job_id, from_status="running", status="cancelled",
                status_detail="已取消", error="用户取消，引擎子进程已终止"):
            # 我们杀进程的这段时间里调度器已把真实终态落了（比如它刚好跑完）。
            # 谁先落谁赢，这里如实返回当前状态，不覆盖别人的结论。
            pass
        return self.get(db, job_id)

    @staticmethod
    def _terminate(engine, job_id: str) -> bool:
        """调引擎的 cancel 钩子；没实现或抛异常都算"杀不掉"（返回 False）。"""
        cancel = getattr(engine, "cancel", None)
        if cancel is None or not callable(cancel):
            return False
        try:
            return bool(cancel(job_id))
        except Exception:  # noqa: BLE001
            return False

    # ── restore_draft_version：还原草稿到历史版本 ──
    def restore_draft_version(self, db: Session, draft_id: str, version: str) -> dict:
        draft = self.draft_repo.get(db, draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="草稿不存在")

        # 检查版本是否存在
        draft_version = self.draft_version_repo.get(db, draft_id, version)
        if draft_version is None:
            raise HTTPException(status_code=404, detail="草稿版本不存在")

        # 更新当前版本
        now = _now()
        self.draft_repo.update(db, draft_id, current_version=version, updated_at=now)

        # 同步项目的当前草稿版本（Project.current_draft_version 指向同一草稿时）
        if draft.get("project_id"):
            project = self.project_repo.get(db, draft["project_id"])
            if project and project.get("current_draft_id") == draft_id:
                self.project_repo.update(db, draft["project_id"],
                                         current_draft_version=version, updated_at=now)

        return self.draft_repo.get(db, draft_id)

    # ── 草稿实体落库：draft 任务 completed 后写 Draft + DraftVersion ──
    # 公开给调度器调用（终态收割的一侧），本层自身不再触发它
    def persist_draft_entity(self, job_row: dict) -> None:
        db = SessionLocal()
        try:
            draft_id = uuid.uuid4().hex
            version = _version_stamp()
            # 同秒冲突：等待下一秒
            while self.draft_version_repo.get(db, draft_id, version) is not None:
                time.sleep(1.0)
                version = _version_stamp()
            now = _now()
            # 草稿真实内容：读引擎写的 draft.md（DB 的 draft_content 列已废弃、不再写入）
            job_dir = JOBS_ROOT / job_row["job_id"]
            draft_path = job_dir / "draft.md"
            content = ""
            if draft_path.exists():
                try:
                    content = draft_path.read_text(encoding="utf-8")
                except OSError:
                    content = ""
            else:
                # 引擎未落盘时兜底用请求内容
                content = job_row.get("content") or ""

            self.draft_repo.create(
                db,
                draft_id=draft_id,
                project_id=job_row.get("project_id"),
                task_id=job_row["job_id"],
                current_version=version,
                created_at=now,
                updated_at=now,
            )
            self.draft_version_repo.create(
                db,
                draft_id=draft_id,
                version=version,
                content=content,
                draft_path=str(draft_path),
                created_at=now,
            )
            # 更新项目的当前草稿
            self.project_repo.update(db, job_row.get("project_id"),
                                     current_draft_id=draft_id,
                                     current_draft_version=version,
                                     updated_at=now)
        finally:
            db.close()

    # ── 演示文件落库：输出任务 completed 后写 Presentation（由调度器在终态收割时调用）──
    def persist_presentation(self, job_row: dict, result_path: str) -> None:
        db = SessionLocal()
        try:
            presentation_id = uuid.uuid4().hex
            now = _now()
            project = self.project_repo.get(db, job_row.get("project_id"))
            project_name = ((project or {}).get("project_name") or "Untitled").replace("/", "_")
            # 按引擎真实产物后缀判定格式（契约 Presentation.file_format: pptx | html）
            # guizang_ppt 引擎产出 result.html，不能一律伪装成 pptx
            suffix = Path(result_path).suffix.lower()
            file_format = "html" if suffix in (".html", ".htm") else "pptx"
            # 命名规则（docs/contracts/openapi.yaml Presentation.file_name + style-guide.md）：
            # 项目名称_文件生成时间戳（UTC 年月日时分秒）。刻意用生成时刻而非草稿版本号——
            # 同一草稿在不同时间节点重复出片时草稿版本号不变，会让文件名撞车；生成时间戳保证
            # 每个节点生成的文件名称唯一。草稿版本仍记在任务行 job_row.draft_version 上，可追溯。
            file_name = f"{project_name}_{_version_stamp()}.{file_format}"
            self.presentation_repo.create(
                db,
                presentation_id=presentation_id,
                project_id=job_row.get("project_id"),
                task_id=job_row["job_id"],
                file_name=file_name,
                file_format=file_format,
                file_path=result_path,
                created_at=now,
            )
        finally:
            db.close()

    # 终态收割（读引擎上报的标题与 token、落演示文件/草稿实体）原先在本文件的
    # `_poll_until_done` 里做。那段已整体搬到 `app.service.scheduler` —— 它才是唯一
    # 推动状态机的角色，一个任务只会被调度器判一次终态（CAS 先到先得，ADR-019 决策 4）。
