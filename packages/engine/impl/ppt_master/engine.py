"""ppt-master 引擎实现（第一个真实引擎）。

驱动 lib/ppt-master/（AI Agent 工作流）生成原生可编辑 PPTX。
两段式（见 docs/research/2026-08-26-engine-调用设计.md §11）：
  submit  -> 调 source_to_md.py 把 source/content 转成 Markdown 草稿（draft，免费、秒级、确定性）
  confirm -> 启动 `claude -p` 子进程跑完整 ppt-master 生成（running -> completed）

文件布局（jobs_root 来自 engine.core.config，默认 outputs/jobs）：
  <job_id>/
    job.json     # 引擎内部状态（pid/status/result_path/started_at/error）—— 兜底/审计用
    draft.md     # 草稿 Markdown（= draft_content 落盘副本）
    prompt.md    # confirm 后的生成 prompt
    run.log      # claude stdout/stderr 落盘
  lib/ppt-master/projects/<project>_ppt169_<date>/exports/*.pptx  # 最终产物

AGENTS.md 规则：本文件只实现 PptEngine 协议，不修改任何调用方（后端 service 层）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.core.config import get_jobs_root
from engine.core.models import Job, JobId, JobStatus
from engine.core.protocol import PptEngine

# 仓库根：packages/engine/impl/ppt_master/engine.py
# parents[0]=ppt_master [1]=impl [2]=engine [3]=packages [4]=仓库根
_REPO_ROOT = Path(__file__).resolve().parents[4]
_PPT_MASTER_DIR = _REPO_ROOT / "lib" / "ppt-master"
_SOURCE_TO_MD = _PPT_MASTER_DIR / "skills" / "ppt-master" / "scripts" / "source_to_md.py"
_CLAUDE_BIN = "claude"
_DEFAULT_TIMEOUT_SECONDS = 3600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PptMasterEngine(PptEngine):
    """通过 ppt-master 工作流生成 PPTX（长任务、异步）。

    jobs_root 来自 config（环境变量 DECKSMITH_JOBS_ROOT > .env > 默认 outputs/jobs），
    部署时可通过环境变量改路径，无需改代码。状态机：draft -> running -> completed/failed/timeout。
    """

    def __init__(
        self,
        jobs_root: Path | None = None,
        agent: str = "claude",
        claude_bin: str = _CLAUDE_BIN,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.jobs_root: Path = Path(jobs_root or get_jobs_root()).resolve()
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.agent = agent
        self.claude_bin = claude_bin
        self.timeout_seconds = timeout_seconds

    # ── 内部工具 ──
    def _job_dir(self, job_id: Any) -> Path:
        return self.jobs_root / str(job_id)

    def _read_job(self, job_id: Any) -> dict:
        p = self._job_dir(job_id) / "job.json"
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_job(self, job_id: Any, **fields: Any) -> dict:
        p = self._job_dir(job_id) / "job.json"
        data = self._read_job(job_id)
        data.update(fields)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    @staticmethod
    def _process_alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            proc = subprocess.run(
                ["ps", "-p", str(pid), "-o", "pid="],
                capture_output=True, text=True,
            )
            return bool(proc.stdout.strip())
        except Exception:
            return False

    def _newest_export(self, job_id: Any, since_ts: float) -> Path | None:
        """返回自 since_ts 起、属于本 job 的最新 .pptx；无则 None。

        优先匹配项目目录名含 job_id 的产物，避免并发 job 互相串味；
        匹配不到时退化为「since 之后的最新产物」。
        """
        candidates: list[Path] = []
        for p in _PPT_MASTER_DIR.glob("projects/*/exports/*.pptx"):
            if p.stat().st_mtime < since_ts:
                continue
            candidates.append(p)
        if not candidates:
            return None
        matched = [p for p in candidates if str(job_id) in p.parent.parent.name]
        pool = matched or candidates
        return max(pool, key=lambda p: p.stat().st_mtime)

    def _get_svg_progress(self, job_id: Any) -> dict:
        """扫描 svg_output 目录，返回当前生成进度。"""
        # 查找最近修改的项目目录
        project_dirs = sorted(
            _PPT_MASTER_DIR.glob("projects/decksmith_*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        
        if not project_dirs:
            return {"pages_generated": 0, "current_page": None}
        
        svg_dir = project_dirs[0] / "svg_output"
        if not svg_dir.exists():
            return {"pages_generated": 0, "current_page": None}
        
        svg_files = sorted(svg_dir.glob("*.svg"))
        pages_generated = len(svg_files)
        current_page = svg_files[-1].name if svg_files else None
        
        return {
            "pages_generated": pages_generated,
            "current_page": current_page,
        }

    def _get_last_log_lines(self, job_id: Any, lines: int = 3) -> str:
        """读取 run.log 的最后几行。"""
        log_path = self._job_dir(job_id) / "run.log"
        if not log_path.exists():
            return ""
        
        try:
            content = log_path.read_text(encoding="utf-8")
            log_lines = content.strip().split("\n")
            return "\n".join(log_lines[-lines:]) if log_lines else ""
        except Exception:
            return ""

    def get_progress(self, job_id: Any) -> dict | None:
        """获取任务进度信息，仅在 running 状态时返回。"""
        data = self._read_job(job_id)
        if data.get("status") != "running":
            return None
        
        started_at = data.get("started_at", 0)
        elapsed = int(time.time() - started_at) if started_at else 0
        
        # 判断阶段：有 SVG 文件说明在生成页面，否则在初始化
        svg_progress = self._get_svg_progress(job_id)
        pages_generated = svg_progress.get("pages_generated", 0)
        
        if pages_generated > 0:
            stage = "generating_svg"
            stage_label = "正在生成 SVG 页面"
        else:
            stage = "initializing"
            stage_label = "正在初始化生成任务"
        
        # 检查是否有导出文件（正在导出阶段）
        project_dirs = sorted(
            _PPT_MASTER_DIR.glob("projects/decksmith_*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if project_dirs and (project_dirs[0] / "exports").exists():
            exports = list((project_dirs[0] / "exports").glob("*.pptx"))
            if exports:
                stage = "exporting"
                stage_label = "正在导出 PPTX 文件"
        
        return {
            "stage": stage,
            "stage_label": stage_label,
            "pages_generated": pages_generated,
            "elapsed_seconds": elapsed,
            "current_page": svg_progress.get("current_page"),
            "last_log": self._get_last_log_lines(job_id),
        }

    # ── 草稿生成 ──
    def _run_source_to_md(self, path_or_url: str) -> str | None:
        """调 ppt-master 内置 source_to_md 脚本，确定性地把文件/URL 转成 Markdown。"""
        try:
            proc = subprocess.run(
                [sys.executable, str(_SOURCE_TO_MD), str(path_or_url)],
                capture_output=True, text=True, cwd=str(_PPT_MASTER_DIR), timeout=300,
            )
            if proc.returncode != 0:
                return None
            # source_to_md 把 Markdown 落到文件，stdout 只回一行 `OUTPUT: <path>`（其余状态走 stderr）
            for line in proc.stdout.splitlines():
                if line.startswith("OUTPUT:"):
                    md_path = Path(line[len("OUTPUT:"):].strip())
                    if md_path.exists():
                        return md_path.read_text(encoding="utf-8")
            return None
        except Exception:
            return None

    @staticmethod
    def _outline_sections(content_style: str) -> list[str]:
        return {
            "product_story": ["## 用户痛点", "## 产品方案", "## 使用场景", "## 价值收益"],
            "briefing": ["## 背景", "## 现状", "## 方案", "## 计划"],
            "event_promo": ["## 活动亮点", "## 时间地点", "## 参与收益", "## 行动号召"],
            "investment_pitch": ["## 市场机会", "## 商业模式", "## 合作价值", "## 下一步"],
        }.get(content_style, ["## 目标受众", "## 核心卖点", "## 价值收益", "## 行动号召"])

    @classmethod
    def _templated_draft(cls, title: str, pages: int, content: str, engine_params: dict | None = None) -> str:
        style = (engine_params or {}).get("style", "swiss-minimal")
        content_style = (engine_params or {}).get("content_style", "marketing_promo")
        lines = [
            f"# {title}",
            "",
            f"> 风格：{style} ｜ 内容风格：{content_style} ｜ 目标页数：{pages}",
            "",
            "## 摘要",
            content or "（请补充正文内容）",
            "",
        ]
        outline = cls._outline_sections(content_style)
        for i in range(1, max(int(pages), 1) + 1):
            section = outline[(i - 1) % len(outline)]
            lines += [f"## 第 {i} 页", "", section, "", "- 要点", ""]
        return "\n".join(lines)

    def _build_draft(self, content: str | None, title: str, pages: int, source: dict | None, engine_params: dict | None) -> str:
        """双来源收敛为一份 Markdown 草稿（见设计文档 §11.2）。"""
        if source:
            stype = source.get("type")
            svalue = source.get("value")
            if stype in ("file_path", "url") and svalue:
                md = self._run_source_to_md(svalue)
                if md:
                    return md
            if stype == "text" and svalue:
                content = svalue
        
        # 如果没有提供标题，尝试从内容中提取
        if not title and content:
            # 简单提取：取第一行作为标题
            first_line = content.strip().split('\\n')[0]
            if first_line.startswith('#'):
                title = first_line.lstrip('#').strip()
            else:
                title = first_line[:50]  # 取前 50 个字符作为标题
        
        return self._templated_draft(title or "Untitled", pages, content or "", engine_params)

    @staticmethod
    def _build_prompt(title: str, style: str, pages: int, content: str) -> str:
        return (
            "请使用 ppt-master 技能（先读 AGENTS.md 和 skills/ppt-master/SKILL.md），"
            "以 Quick Generate 流程生成 PPTX：\n"
            f"- 主题：{title}\n"
            f"- 视觉风格：{style}\n"
            "- 内容处理：先把输入 Markdown 重写成更适合宣传材料/汇报场景的结构，再生成 PPT\n"
            "- 画布：ppt169（16:9）\n"
            "- 要求：不使用 AI 生图/外部图片，全部用原生形状、图标和文本；不加旁白和动画\n"
            f"- 项目名：decksmith_{title}\n"
            "- 完成后告诉我最终 PPTX 文件的绝对路径和页数\n\n"
            "内容来源：\n"
            f"{content[:15000]}"
        )

    # ── PptEngine 协议 ──
    def submit(
        self,
        content: str | None,
        title: str | None = None,
        pages: int | None = None,
        engine_params: dict | None = None,
        source: dict | None = None,
        job_id: Any = None,
    ) -> JobId:
        job_id = job_id or uuid.uuid4().hex
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        # 使用默认值处理可选的 title 和 pages
        effective_title = title or ""
        effective_pages = pages or 10  # 默认 10 页
        
        draft = self._build_draft(content, effective_title, effective_pages, source, engine_params)
        (job_dir / "draft.md").write_text(draft, encoding="utf-8")
        style = (engine_params or {}).get("style", "swiss-minimal")
        self._write_job(
            job_id, status="draft", title=effective_title, pages=int(effective_pages),
            style=style, engine_params=engine_params or {}, created_at=_now(), updated_at=_now(),
            pid=None, result_path=None, error=None,
        )
        return JobId(job_id)

    def confirm(self, job_id: Any, content: str, engine_params: dict) -> None:
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        # 用户改好的内容落盘为草稿
        (job_dir / "draft.md").write_text(content or "", encoding="utf-8")
        prev = self._read_job(job_id)
        style = (engine_params or {}).get("style") or prev.get("style") or "swiss-minimal"
        pages = (engine_params or {}).get("pages") or prev.get("pages") or 1
        title = (engine_params or {}).get("title") or prev.get("title") or ""
        content_style = (engine_params or {}).get("content_style") or prev.get("engine_params", {}).get("content_style") or "marketing_promo"
        prompt = self._build_prompt(title, style, int(pages), content or "")
        prompt += f"\n- 内容风格：{content_style}\n"
        (job_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        run_log = open(job_dir / "run.log", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [self.claude_bin, "-p", "--dangerously-skip-permissions", prompt],
            cwd=str(_PPT_MASTER_DIR),
            stdout=run_log, stderr=subprocess.STDOUT,
            text=True,
        )
        self._write_job(
            job_id, status="running", pid=proc.pid, started_at=time.time(),
            updated_at=_now(), error=None,
        )

    def status(self, job_id: Any) -> JobStatus:
        data = self._read_job(job_id)
        status = data.get("status", "draft")
        if status != "running":
            return JobStatus(status)
        # running：先看进程是否还活着
        if self._process_alive(data.get("pid")):
            return JobStatus.RUNNING
        # 进程已结束 -> 看产物
        since = data.get("started_at", 0.0)
        result = self._newest_export(job_id, since)
        if result is not None:
            self._write_job(job_id, status="completed", result_path=str(result), updated_at=_now())
            return JobStatus.COMPLETED
        self._write_job(
            job_id, status="failed",
            error="claude 子进程结束但未找到产物（详见 run.log）", updated_at=_now(),
        )
        return JobStatus.FAILED

    def result(self, job_id: Any) -> str:
        data = self._read_job(job_id)
        rp = data.get("result_path")
        if rp and Path(rp).exists():
            return rp
        since = data.get("started_at", 0.0)
        r = self._newest_export(job_id, since)
        if r:
            return str(r)
        raise FileNotFoundError(f"任务 {job_id} 尚未生成产物")

    def resume(self, job_id: Any) -> Job:
        data = self._read_job(job_id)
        if data.get("status") == "completed":
            return self._to_job(job_id, data)
        draft_path = self._job_dir(job_id) / "draft.md"
        content = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
        self.confirm(job_id, content, data.get("engine_params") or {})
        return self._to_job(job_id, self._read_job(job_id))

    @staticmethod
    def _to_job(job_id: Any, data: dict) -> Job:
        return Job(
            job_id=JobId(str(job_id)),
            status=JobStatus(data.get("status", "draft")),
            result_path=data.get("result_path"),
            draft_path=str(data.get("draft_path")) if data.get("draft_path") else None,
            error=data.get("error"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            content_style=data.get("content_style"),
        )
