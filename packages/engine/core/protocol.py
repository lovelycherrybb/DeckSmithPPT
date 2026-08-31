"""PPT 生成引擎抽象接口（长任务语义）。

所有引擎实现必须满足本协议，契约见 docs/contracts/engine.schema.json。
"""

from typing import Protocol

from engine.core.models import Job, JobId, JobStatus


class PptEngine(Protocol):
    """PPT 生成引擎协议。"""

    def submit(
        self,
        content: str | None,
        title: str,
        pages: int,
        engine_params: dict | None = None,
        source: dict | None = None,
        job_id: JobId | None = None,
    ) -> JobId:
        """提交生成任务，返回任务 ID。"""

    def status(self, job_id: JobId) -> JobStatus:
        """查询任务状态。"""

    def result(self, job_id: JobId) -> str:
        """取回生成的 PPTX 文件路径。"""

    def confirm(self, job_id: JobId, content: str, engine_params: dict) -> None:
        """草稿阶段用户确认：携带修改后的内容 + 引擎参数，进入正式生成。"""

    def resume(self, job_id: JobId) -> Job:
        """恢复中断的任务。"""
