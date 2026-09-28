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

    def resume(self, job_id: JobId) -> Job:
        """恢复中断的任务。"""
