"""ppt-master 引擎实现（第一个真实引擎）。

驱动 lib/ppt-master/（AI Agent 工作流）生成原生可编辑 PPTX。
一段式（新 contracts）：
  submit -> 根据 engine_type 决定行为：
    - draft: 同步生成草稿，直接返回 completed
    - output: 启动 claude 子进程生成 PPT，后台轮询状态

文件布局（jobs_root 来自 engine.core.config，默认 outputs/jobs）：
  <job_id>/
    job.json     # 引擎内部状态（pid/status/result_path/started_at/error）—— 兜底/审计用
    draft.md     # 草稿 Markdown
    prompt.md    # 生成 prompt
    run.log      # claude stdout/stderr 落盘
  lib/ppt-master/projects/<project>_ppt169_<date>/exports/*.pptx  # 最终产物

AGENTS.md 规则：本文件只实现 PptEngine 协议，不修改任何调用方（后端 service 层）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.core.config import get_jobs_root
from engine.core.models import Job, JobId, JobStatus, EngineType
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


# docx/pptx 转换噪音（skill 流程里由 Claude 无视，这里确定性剥除）
_RE_EMPTY_ANCHOR = re.compile(r'<a id="[^"]*"></a>')
_RE_TOC_LINK_LINE = re.compile(r'^\[.*\]\(#_Toc[^)]*\)\s*$')
_RE_TOC_HEADING = re.compile(r'^目\s*录$')


def strip_source_noise(md: str) -> str:
    """确定性清理 docx/pptx 直转 markdown 的噪音：空锚点、页码目录行、目录标题、多余空行。"""
    if not md:
        return md
    lines_out = []
    in_toc = False
    for line in md.splitlines():
        s = line.strip()
        # 1. 空锚点标签（docx 书签残留）
        line = _RE_EMPTY_ANCHOR.sub('', line)
        s = line.strip()
        # 2. 目录标题行：进入目录区块
        if _RE_TOC_HEADING.match(s):
            in_toc = True
            continue
        # 3. 目录条目行：[第1章 xxx  1](#_Toc26157)
        if _RE_TOC_LINK_LINE.match(s):
            continue
        # 目录区块中遇到正文（非目录条目、非空行）则结束目录区间
        if in_toc:
            if s == '':
                continue
            in_toc = False
        if s or (lines_out and lines_out[-1].strip()):
            lines_out.append(line)
    # 收尾：去掉首尾空行、压缩 3+ 连续空行
    out = "\n".join(lines_out)
    out = re.sub(r'\n{3,}', '\n\n', out).strip()
    return out


class PptMasterEngine(PptEngine):
    """通过 ppt-master 工作流生成 PPTX（长任务、异步）。

    jobs_root 来自 config（环境变量 DECKSMITH_JOBS_ROOT > .env > 默认 outputs/jobs），
    部署时可通过环境变量改路径，无需改代码。状态机：running -> completed/failed/timeout。
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

    def _get_svg_progress(self, job_id: Any, since_ts: float = 0) -> dict:
        """扫描 svg_output 目录，返回本任务的真实生成进度。

        目录名由 ppt-master 从标题派生（不一定带 decksmith_ 前缀），故：
        1) 只看项目目录 mtime >= since_ts（任务启动之后被写过的），排除历史遗留目录；
        2) 其中优先取目录名含 job_id 的，取不到再退化为"最新被修改的那个"（与 _find_pptx 同策略）；
        3) 只统计 svg mtime >= since_ts 的文件，避免把旧目录里历史 svg 计进页数。
        """
        fresh = [
            p for p in _PPT_MASTER_DIR.glob("projects/*")
            if p.is_dir() and p.stat().st_mtime >= since_ts
        ]
        if not fresh:
            return {"pages_generated": 0, "current_page": None}
        matched = [p for p in fresh if str(job_id) in p.name]
        pool = matched or fresh
        project_dir = max(pool, key=lambda p: p.stat().st_mtime)

        svg_dir = project_dir / "svg_output"
        if not svg_dir.exists():
            return {"pages_generated": 0, "current_page": None}

        svg_files = sorted(
            (p for p in svg_dir.glob("*.svg") if p.stat().st_mtime >= since_ts),
            key=lambda p: p.stat().st_mtime,
        )
        return {
            "pages_generated": len(svg_files),
            "current_page": svg_files[-1].name if svg_files else None,
        }

    # ── 草稿构建（优先调 source_to_md 确定性转换，失败则模板兜底）──
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

    def _build_draft(
        self,
        content: str | None,
        title: str,
        pages: int,
        sources: list[dict] | None,
        engine_params: dict | None,
    ) -> tuple[str, str]:
        """构建草稿 Markdown。返回 (draft, effective_title)。

        优先级：file_path/url 素材走 source_to_md 确定性转换；text 直接用；
        否则用 content；全空则模板兜底。
        """
        md: str | None = None
        effective_title = title

        if sources:
            for s in sources:
                stype, svalue = s.get("type", ""), s.get("value", "")
                if not svalue:
                    continue
                if stype in ("file_path", "url"):
                    md = self._run_source_to_md(svalue)
                    if md:
                        break
                elif stype == "text":
                    md = svalue
                    break

        if md is None:
            md = content or ""

        # 确定性噪音清理（docx/pptx 直转残留）
        md = strip_source_noise(md)

        # 标题缺失时从内容提取（首个标题行或首行前 50 字符）
        if not effective_title and md:
            first_line = md.strip().splitlines()[0]
            if first_line.startswith("#"):
                effective_title = first_line.lstrip("#").strip()
            else:
                effective_title = first_line[:50]

        effective_title = effective_title or "Untitled"

        if md:
            draft = f"# {effective_title}\n\n{md}\n\n---\n目标页数：{pages}"
        else:
            draft = (
                f"# {effective_title}\n\n"
                f"> 风格：{(engine_params or {}).get('style', 'swiss-minimal')} ｜ "
                f"内容风格：{(engine_params or {}).get('content_style', 'marketing_promo')} ｜ 目标页数：{pages}\n\n"
                "（未提供可用素材，请在草稿基础上编辑补充内容）\n\n---\n目标页数：{pages}"
            )
        return draft, effective_title

    @staticmethod
    def _build_prompt(
        title: str,
        style: str,
        pages: int,
        content: str,
    ) -> str:
        """构建生成 prompt。"""
        return f"""请根据以下内容生成 PPT：

标题：{title}
风格：{style}
页数：{pages}

内容：
{content}
"""

    # ── PptEngine 协议 ──
    def submit(
        self,
        content: str | None = None,
        title: str | None = None,
        pages: int | None = None,
        engine_params: dict | None = None,
        sources: list[dict] | None = None,
        job_id: Any = None,
        engine_type: str | None = None,
    ) -> JobId:
        """提交生成任务，返回任务 ID。

        Args:
            content: Markdown 内容（可选）
            title: PPT 标题（可选，可从源材料提取或由 AI 自动生成）
            pages: 目标页数（可选，可由 AI 决定或使用默认值）
            engine_params: 引擎特定参数（可选）
            sources: 原始素材来源列表（可选）
            job_id: 任务 ID（可选，不提供则自动生成）
            engine_type: 引擎类型（draft/output）
        """
        job_id = job_id or uuid.uuid4().hex
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)

        # 构建草稿（sources 优先走 source_to_md，title 缺失时自动提取）
        draft, effective_title = self._build_draft(
            content, title or "", pages or 10, sources, engine_params,
        )
        (job_dir / "draft.md").write_text(draft, encoding="utf-8")

        effective_pages = pages or 10
        style = (engine_params or {}).get("style", "swiss-minimal")
        
        if engine_type == "draft":
            # 草稿引擎任务：同步完成
            self._write_job(
                job_id, status="completed", title=effective_title, pages=int(effective_pages),
                style=style, engine_params=engine_params or {}, created_at=_now(), updated_at=_now(),
                pid=None, result_path=None, error=None,
            )
        else:
            # 输出引擎任务：启动 claude 子进程生成 PPT
            content_style = (engine_params or {}).get("content_style", "marketing_promo")
            prompt = self._build_prompt(effective_title, style, int(effective_pages), draft)
            prompt += f"\n- 内容风格：{content_style}\n"
            (job_dir / "prompt.md").write_text(prompt, encoding="utf-8")
            
            run_log = open(job_dir / "run.log", "w", encoding="utf-8")
            proc = subprocess.Popen(
                # stream-json：claude -p 默认把输出憋到最后一次写完，运行中日志为空；
                # 流式事件由 _pump_run_stream 边跑边蒸馏写 run.log，前端轮询即可看到实时日志
                [
                    self.claude_bin, "-p", "--dangerously-skip-permissions",
                    "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                    prompt,
                ],
                cwd=str(_PPT_MASTER_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            pump = threading.Thread(
                target=self._pump_run_stream, args=(proc, run_log), daemon=True, name=f"runlog-{job_id[:8]}"
            )
            pump.start()
            self._write_job(
                job_id, status="running", title=effective_title, pages=int(effective_pages),
                style=style, engine_params=engine_params or {}, created_at=_now(), updated_at=_now(),
                pid=proc.pid, started_at=time.time(), result_path=None, error=None,
            )
        
        return JobId(job_id)

    @staticmethod
    def _pump_run_stream(proc: subprocess.Popen, log_file) -> None:
        """把 claude stream-json 事件蒸馏成人类可读日志，即时写入 run.log。

        - text_delta 文本增量：原样续写（claude 的说明性输出，边生成边可见）
        - assistant 消息里的 tool_use：记一行「⚙ 工具名 关键参数」——能看到引擎在写哪个文件
        - result：记完成/失败行
        - 非 JSON 行（stderr 噪音等）：原样落盘，不丢弃
        每次写入都 flush，前端轮询 get_progress 即可近实时展示。
        """
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    log_file.write(line + "\n")
                    log_file.flush()
                    continue
                t = d.get("type")
                if t == "stream_event":
                    delta = (d.get("event") or {}).get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        log_file.write(delta["text"])
                        log_file.flush()
                elif t == "assistant":
                    for c in (d.get("message") or {}).get("content", []):
                        if c.get("type") == "tool_use":
                            hint = ""
                            inp = c.get("input") or {}
                            hint = inp.get("file_path") or inp.get("path") or inp.get("command") or ""
                            log_file.write(f"\n⚙ {c.get('name')} {hint}\n")
                            log_file.flush()
                elif t == "result":
                    if d.get("subtype") == "success":
                        log_file.write("\n✅ 引擎执行完成\n")
                    else:
                        log_file.write(f"\n❌ {d.get('result') or d.get('subtype') or '引擎执行失败'}\n")
                    log_file.flush()
        except Exception as exc:  # noqa: BLE001
            try:
                log_file.write(f"\n[日志流中断] {exc}\n")
                log_file.flush()
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                log_file.close()
            except Exception:  # noqa: BLE001
                pass

    def status(self, job_id: Any) -> JobStatus:
        data = self._read_job(job_id)
        status = data.get("status", "running")
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
        # 对于未完成的任务，重新启动生成
        draft_path = self._job_dir(job_id) / "draft.md"
        content = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
        self.submit(
            content=content,
            title=data.get("title"),
            pages=data.get("pages"),
            engine_params=data.get("engine_params") or {},
            job_id=job_id,
            engine_type="output",
        )
        return self._to_job(job_id, self._read_job(job_id))

    @staticmethod
    def _to_job(job_id: Any, data: dict) -> Job:
        return Job(
            job_id=JobId(str(job_id)),
            status=JobStatus(data.get("status", "running")),
            task_type=EngineType.OUTPUT,
            status_detail=data.get("status_detail"),
            result_path=data.get("result_path"),
            draft_path=str(data.get("draft_path")) if data.get("draft_path") else None,
            error=data.get("error"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            content_style=data.get("content_style"),
        )

    def _tail_run_log(self, job_id: Any, max_chars: int = 200) -> str | None:
        """读引擎子进程 run.log 的最后一行非空输出。

        claude -p 通常只在结束时写 stdout，运行中多为空——此时如实返回 None，
        由调用方显示"暂无输出"，不再用硬编码文案冒充进度。
        """
        try:
            p = self._job_dir(job_id) / "run.log"
            if not p.exists():
                return None
            text = p.read_text(encoding="utf-8", errors="ignore").strip()
            return text.splitlines()[-1][:max_chars] if text else None
        except OSError:
            return None

    def get_progress(self, job_id: Any) -> dict | None:
        """获取任务进度信息，仅在 running 状态时返回。"""
        data = self._read_job(job_id)
        if data.get("status") != "running":
            return None
        
        started_at = data.get("started_at", 0)
        elapsed = int(time.time() - started_at) if started_at else 0
        
        svg_progress = self._get_svg_progress(job_id, since_ts=started_at)
        
        return {
            "stage": "generating",
            "stage_label": "正在生成 PPT（ppt-master）",
            "pages_generated": svg_progress.get("pages_generated", 0),
            "elapsed_seconds": elapsed,
            "current_page": svg_progress.get("current_page"),
            "last_log": self._tail_run_log(job_id) or "引擎运行中，暂无输出日志",
        }
