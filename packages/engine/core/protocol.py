"""PPT 生成引擎抽象接口（长任务语义）。

所有引擎实现必须满足本协议，契约见 docs/contracts/engine.schema.json。

引擎类型：
- 草稿引擎（DraftEngine）：将用户文件转化为 Markdown 草稿
- 输出引擎（OutputEngine）：将 Markdown 内容生成最终展示文件
"""

from pathlib import Path
from typing import Protocol

from engine.core.models import Job, JobId, JobStatus, EngineType


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

    def status(self, job_id: JobId) -> JobStatus:
        """查询任务状态。"""

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

    def status(self, job_id: JobId) -> JobStatus:
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

    def status(self, job_id: JobId) -> JobStatus:
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

    def resume(self, job_id: JobId) -> Job:
        """恢复中断的任务。"""
