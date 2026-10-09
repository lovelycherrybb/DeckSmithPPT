"""PPT 生成引擎抽象接口（长任务语义）。

所有引擎实现必须满足本协议，契约见 docs/contracts/engine.schema.json。

引擎类型：
- 草稿引擎（DraftEngine）：将用户文件转化为 Markdown 草稿
- 输出引擎（OutputEngine）：将 Markdown 内容生成最终展示文件
"""

from pathlib import Path
from typing import Protocol

from engine.core.models import Job, JobId, EngineJobStatus, EngineType


class PptEngine(Protocol):
    """PPT 生成引擎协议。"""

    def submit(
        self,
        content: str | None,
        title: str | None = None,
        pages: int | None = None,
        engine_params: dict | None = None,
        source: dict | None = None,
        job_id: JobId | None = None,
    ) -> JobId:
        """提交生成任务，返回任务 ID。

        Args:
            content: Markdown 内容（可选）
            title: PPT 标题（可选，可从源材料提取或由 AI 自动生成）
            pages: 目标页数（可选，可由 AI 决定或使用默认值）
            engine_params: 引擎特定参数（可选）
            source: 原始素材来源（可选）
            job_id: 任务 ID（可选，不提供则自动生成）
        """

    def status(self, job_id: JobId) -> EngineJobStatus:
        """查询任务状态。

        值域是引擎态（4 值，见 engine.core.models.EngineJobStatus 与 ADR-018 分层枚举）：
        引擎只报告自己那个子进程的真相，**永远不产出** queued / cancelled——
        排队与撤销是编排层（app.service.scheduler）的决定。
        """

    def cancel(self, job_id: JobId) -> bool:
        """【可选钩子】终止该任务的引擎执行（杀掉子进程，不再产生新产物）。

        实现它即点亮 `EngineInfo.capabilities` 的 `cancel` 能力位——由
        `registry.implements_hook()` 鸭子类型派生，不按引擎名门控（ADR-001/ADR-019 决策 5）。

        两处在调它：
        1. 用户对 running 任务点撤销（不具备本钩子的引擎，该请求返回 409，
           **绝不静默"假撤销"而让子进程继续跑继续烧额度**）；
        2. 调度器发现"已 fork 但落库 CAS 输给了撤销/终态"时回收刚起的子进程（ADR-019 决策 3）。

        Returns:
            True  : 已终止，或进程本就不存在（幂等，重复调用安全）。
            False : 无法终止（引擎内部错误）。调用方不得据此把任务判成 cancelled。
        """

    def result(self, job_id: JobId) -> str:
        """取回生成的 PPTX 文件路径。"""

    def resume(self, job_id: JobId) -> Job:
        """恢复中断的任务。"""


class DraftEngine(Protocol):
    """草稿引擎协议：将用户文件转化为 Markdown 草稿。"""

    def submit(
        self,
        content: str | None,
        title: str | None = None,
        pages: int | None = None,
        engine_params: dict | None = None,
        source: dict | None = None,
        job_id: JobId | None = None,
    ) -> JobId:
        """提交草稿生成任务，返回任务 ID。

        Args:
            content: 文本内容（可选）
            title: 标题（可选）
            pages: 目标页数（可选）
            engine_params: 引擎特定参数（可选）
            source: 原始素材来源（可选）
            job_id: 任务 ID（可选，不提供则自动生成）
        """

    def status(self, job_id: JobId) -> EngineJobStatus:
        """查询任务状态。"""

    def result(self, job_id: JobId) -> str:
        """取回生成的草稿文件路径。"""


class OutputEngine(Protocol):
    """输出引擎协议：将 Markdown 内容生成最终展示文件。

    逐页预览为全局可选能力：实现 get_pages / get_preview_root 即自动接入
    （框架以 hasattr 鸭子类型探测，不按引擎名门控）；不实现则界面维持
    原下载形态。
    """

    def submit(
        self,
        content: str | None,
        title: str | None = None,
        pages: int | None = None,
        engine_params: dict | None = None,
        source: dict | None = None,
        job_id: JobId | None = None,
    ) -> JobId:
        """提交生成任务，返回任务 ID。

        Args:
            content: Markdown 内容（可选）
            title: PPT 标题（可选，可从源材料提取或由 AI 自动生成）
            pages: 目标页数（可选，可由 AI 决定或使用默认值）
            engine_params: 引擎特定参数（可选）
            source: 原始素材来源（可选）
            job_id: 任务 ID（可选，不提供则自动生成）
        """

    def status(self, job_id: JobId) -> EngineJobStatus:
        """查询任务状态。"""

    def result(self, job_id: JobId) -> str:
        """取回生成的 PPTX 文件路径。"""

    def get_pages(self, job_id: JobId) -> list[dict] | None:
        """【可选钩子】枚举该任务「一页一页可预览的已完成产物」。

        不强制实现：框架（后端路由 / 前端）通过 hasattr(engine, "get_pages")
        鸭子类型探测，实现即自动接入逐页预览（EngineInfo.capabilities
        含 page_preview），未实现的引擎返回空、界面维持原下载形态。
        与 get_progress 的既有鸭子类型用法同风格，不按引擎名门控。

        Returns:
            PageRef 列表，每项为 dict：
            {index: int, name: str, path: Path, kind: "image"|"html"}；
            仅描述已确定落盘的完成页，不预测总数、无 pending 占位。
            无逐页概念或暂无法定位时返回 None / 空列表。
        """

    def get_preview_root(self, job_id: JobId) -> Path | None:
        """【可选钩子】返回该任务可预览产物的根目录（与 get_pages 配套实现）。

        preview 静态路由挂载此目录（而非单个文件），使页内相对引用
        （如 ../images/xxx）能同源解析。调用方须以
        target.resolve().is_relative_to(root.resolve()) 防路径穿越。

        Returns:
            产物根目录 Path；无法定位时返回 None。
        """

    def prepare_runtime(self, job_dir: Path | None = None) -> str | None:
        """【可选钩子】备齐本引擎跑一个任务所需的**运行前置条件**。

        与 `get_pages` 同风格：框架以 `implements_hook()` 鸭子类型探测，**不按引擎名
        分支**，因此新增引擎只需在自己类里实现本方法，entrypoint、后端启动与业务调用方
        一律无需改动（ADR-012 决策 2）。注：本钩子虽只在 OutputEngine 上文档化，
        继承 `PptEngine` 的实现类同样可以定义它，探测机制不依赖继承关系。

        典型职责：把仓库内锁定的技能目录软链到 Claude 的技能发现目录（幂等完成）、
        校验技能脚本所需的解释器与依赖是否齐备。不做业务分支、不产出内容。

        Args:
            job_dir: 本次任务目录。传 None 表示**启动期预检**（不属于任何具体任务），
                由 `app.engines.runtime` 在进程启动与容器 entrypoint 中调用。
                该参数同时是"产物落点契约"的前向接入点（ADR-012 决策 3）：
                将来若把落点升级为强制契约，无需改签名。

        Returns:
            None: 前置条件齐备。
            str : 人读的**可降级告警**（任务仍可尝试），调用方记入日志与 `run.log`。
        Raises:
            任何异常: **硬缺失**，即该引擎在当前环境不可运行。调用方须计为失败而非告警；
                容器 entrypoint 据此拒绝启动。
        """

    def revise(
        self,
        job_id: JobId,
        *,
        source_task_id: JobId,
        pages: list[dict] | None = None,
        instruction: str | None = None,
        deck: list[dict] | None = None,
        engine_params: dict | None = None,
    ) -> JobId:
        """【可选钩子】对已有版本的逐页设计稿做修改并在背后重导出，产出一个新版本成品。

        与 `submit` 分列：submit 从 Markdown 内容**从零生成**整副 deck；revise 以"改自哪个
        版本"（`source_task_id` 那次任务的逐页稿）为源，按 `pages`（改好的页）、
        `deck`（有序整页清单，增删页，ADR-027）或 `instruction`（对话指令，由 AI 改稿）
        改稿后重导出。实现本钩子即点亮
        `EngineInfo.capabilities` 的 `svg_edit` 能力位（由 `implements_hook` 沿 MRO 探测，
        **不按引擎名分支**，与 `get_pages`/`cancel`/`prepare_runtime` 同风格）；不实现的引擎不进修改工作台。

        与 `submit` 一样是"启动后台任务、立即返回 job_id"的形态：真正的改稿与重导出由引擎在
        子进程里完成，调用方随后照常走 `status(job_id)` / `result(job_id)` 轮询与收割
        （复用同一套调度器认领、取消、超时机制，见 ADR-019 / ADR-024 快通道）。

        Args:
            job_id: 本次修改任务的 ID（调用方已建好任务行与 job_dir）。
            source_task_id: "改自哪个版本"的那次任务 ID；引擎自行定位它的逐页稿目录
                （布局是引擎私有的，调用方不必知道）。
            pages: 改好的逐页内容（直接编辑通道），每项含 index/name/content；与 instruction 至少一个。
            instruction: 对话式修改指令（由 AI 在工作副本上改稿）。
            deck: 有序整页清单（整稿通道，ADR-027），每项含 ref/name/content；数组顺序即新页序，
                未引用源页删除、无 ref 项为新增页。与 pages 同给时以 deck 为准。
            engine_params: 重导出所需的引擎参数。

        产物隔离（ADR-023）：实现须先把源版本那份逐页稿**复制**进本任务 job_dir 当工作稿，
        所有改动只落副本，**绝不回写源版本**。

        Returns:
            job_id（与 submit 一致）。
        Raises:
            任何异常: 引擎不可运行或改稿/导出失败——调用方按失败处理，不产出子文件。
        """

    def resume(self, job_id: JobId) -> Job:
        """恢复中断的任务。"""
