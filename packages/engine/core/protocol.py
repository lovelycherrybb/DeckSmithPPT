"""PPT 生成引擎抽象接口（长任务语义）。

所有引擎实现必须满足本协议，契约见 docs/contracts/engine.schema.json。

引擎类型：
- 草稿引擎（DraftEngine）：将用户文件转化为 Markdown 草稿
- 输出引擎（OutputEngine）：将 Markdown 内容生成最终展示文件
"""

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
    """输出引擎协议：将 Markdown 内容生成最终展示文件。"""

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
