"""ppt-master 引擎实现（第一个真实引擎）。

驱动 lib/ppt-master/（AI Agent 工作流）生成原生可编辑 PPTX。
一段式（新 contracts）：
  submit -> 根据 engine_type 决定行为：
    - draft: 同步生成草稿，直接返回 completed
    - output: 启动 claude 子进程生成 PPT，后台轮询状态

文件布局（jobs_root 来自 engine.core.config，默认 outputs/jobs）：
  <job_id>/
    job.json     # 引擎内部状态（pid/status/result_path/started_at/error/svg_output_dir）
                 # —— 兜底/审计用；svg_output_dir 为首次解析到的产物目录，落定一次防串页
    draft.md     # 草稿 Markdown
    prompt.md    # 生成 prompt
    run.log      # claude stdout/stderr 落盘
  lib/ppt-master/projects/<project>_ppt169_<date>/exports/*.pptx  # 最终产物
                 # 同目录 svg_output/NN_slug.svg 为逐页产物（逐页预览数据源）

AGENTS.md 规则：本文件只实现 PptEngine 协议，不修改任何调用方（后端 service 层）。
"""

from __future__ import annotations

import json
import re
import shutil
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
from engine.core.usage import add_usage, usage_from_result

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

# 逐页产物命名规则：NN_slug.svg（数字前缀 = 页序）。不匹配者视为临时/非页 SVG。
_RE_PAGE_SVG = re.compile(r'^\d+_.*\.svg$')


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

    # ── 运行前置条件（协议可选钩子，见 engine.core.protocol.OutputEngine）──
    def prepare_runtime(self, job_dir: Path | None = None) -> str | None:
        """检查本引擎在当前环境可运行的前置条件。只读探测，不写任何状态（天然幂等）。

        分级依据（都来自 ADR-012 决策 1 之后的实测）：

        * **技能文件不可读 = 硬缺失。** `cwd` 已归位到任务目录，技能发现完全依赖
          `_runtime_anchor()` 注入给 agent 的绝对路径。若这两个文件读不到，任务不会报错，
          而是**静默降级**成"agent 凭感觉做 PPT"、状态照样 completed——这种失败从接口上
          看不出来，必须在启动期拦住。
        * **claude 不可解析 = 硬缺失。** 本引擎无离线退路（ADR-011 决策 2）。
        * **agent 侧 python3 缺上游 C 扩展 = 可降级告警。** 只影响用到 shape 布尔运算的
          页面（`svg_to_pptx/shape_boolean.py`），其余页仍可正常导出；但镜像若做到
          解释器唯一（ADR-012 决策 8），此项自然通过。
        """
        agents_md = _PPT_MASTER_DIR / "AGENTS.md"
        skill_md = _PPT_MASTER_DIR / "skills" / "ppt-master" / "SKILL.md"
        missing = [str(p) for p in (agents_md, skill_md) if not p.is_file()]
        if missing:
            raise RuntimeError(
                f"ppt_master 技能说明书缺失：{missing}。"
                "请先执行 git submodule update --init --recursive。"
                "cwd 归位任务目录后技能发现依赖这些绝对路径，缺失会静默降级为无流程生成。"
            )
        if shutil.which(self.claude_bin) is None and not Path(self.claude_bin).is_file():
            raise RuntimeError(
                f"未找到可执行的 claude CLI（解析名 {self.claude_bin!r}）；本引擎无离线退路。"
            )

        notices: list[str] = []
        py3 = shutil.which("python3")
        if py3 is None:
            notices.append("PATH 上没有 python3，agent 无法调用技能脚本（project_manager 等）。")
        else:
            probe = subprocess.run(
                [py3, "-c", "import pathops"], capture_output=True, text=True, timeout=30,
            )
            if probe.returncode != 0:
                notices.append(
                    f"PATH 上的 python3（{py3}）缺 skia-pathops，"
                    "涉及 shape 布尔运算的页面会失败；同一镜像应只保留一个 Python 环境"
                    "并把其 bin 置于 PATH 首位（ADR-012 决策 8）。"
                )
            if Path(py3).resolve() != Path(sys.executable).resolve():
                notices.append(
                    f"解释器分裂：引擎用 {sys.executable}，agent 用 {py3}，"
                    "两者包集合可能不一致（ADR-012 决策 8）。"
                )
        return " | ".join(notices) or None

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

    def _project_roots(self, job_id: Any) -> list[Path]:
        """产物项目目录的搜索根，按优先级排列（ADR-012 决策 1）。

        1) `<jobs_root>/<job_id>/projects/`：新口径，claude 的 cwd 即任务目录，
           `project_manager` 的 `Path.cwd() / "projects"` 落在此处，天然按 job 隔离；
        2) `lib/ppt-master/projects/`：归位前的历史口径，仅供旧任务继续可读。

        命中即止（调用方按 root 顺序取第一个有结果的），**不与历史目录混合比较**，
        否则「最新 mtime」会在两处之间乱选，重新引入串页风险。
        """
        return [self._job_dir(job_id), _PPT_MASTER_DIR]

    def _newest_export(self, job_id: Any, since_ts: float) -> Path | None:
        """返回自 since_ts 起、属于本 job 的最新 .pptx；无则 None。

        优先匹配项目目录名含 job_id 的产物，避免并发 job 互相串味；
        匹配不到时退化为「since 之后的最新产物」。
        """
        candidates: list[Path] = []
        for root in self._project_roots(job_id):
            for p in root.glob("projects/*/exports/*.pptx"):
                if p.stat().st_mtime < since_ts:
                    continue
                candidates.append(p)
            if candidates:
                break  # 新口径命中即止，不回退历史目录
        if not candidates:
            return None
        matched = [p for p in candidates if str(job_id) in p.parent.parent.name]
        pool = matched or candidates
        return max(pool, key=lambda p: p.stat().st_mtime)

    def resolve_svg_output_dir(self, job_id: Any, data: dict | None = None) -> Path | None:
        """定位本任务的逐页产物目录 svg_output/，返回 Path；无法定位返回 None。

        单一事实来源 + 落定一次：
        1) 若 job.json 已回写 svg_output_dir 且目录仍在 → 固定读它
           （根治并发 job / 历史目录的「最新目录」退化匹配串页问题）；
        2) 否则启发式定位：只看项目目录 mtime >= started_at（任务启动后被写过的），
           其中优先目录名含 job_id，取不到退化为「最新被修改的那个」（与 _newest_export 同策略）；
        3) 首次解析到**已存在**的 svg_output 目录后回写进 job.json 的 svg_output_dir，
           映射就此落定；目录尚未生成时不回写，下次再试。
        """
        if data is None:
            data = self._read_job(job_id)

        pinned = data.get("svg_output_dir")
        if pinned:
            p = Path(pinned)
            if p.is_dir():
                return p
            # 钉住的目录已被清理 → 退回启发式重新定位

        since_ts = data.get("started_at", 0) or 0
        if not since_ts:
            # 输出任务的 job.json 必有 started_at（submit 时写入）；缺失说明是
            # 草稿类任务（engine_type=draft 分支不产逐页），不做启发式猜测，
            # 否则 since_ts=0 会把历史最新目录误钉为本任务产物（冒领旧任务）。
            return None
        project_dir: Path | None = None
        for root in self._project_roots(job_id):
            fresh = [
                p for p in root.glob("projects/*")
                if p.is_dir() and p.stat().st_mtime >= since_ts
            ]
            if not fresh:
                continue  # 该根下没有本任务时间窗内的目录，再看下一个根
            matched = [p for p in fresh if str(job_id) in p.name]
            pool = matched or fresh
            project_dir = max(pool, key=lambda p: p.stat().st_mtime)
            break  # 高优先级根一旦有结果即定，不回退历史目录
        if project_dir is None:
            return None

        svg_dir = project_dir / "svg_output"
        if not svg_dir.is_dir():
            return None
        if not pinned or Path(pinned) != svg_dir:
            self._write_job(job_id, svg_output_dir=str(svg_dir))
        return svg_dir

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

【重要·无人值守自动运行】本次为后台自动化任务，没有任何人会回复你：
- 不要在任何阶段停下来向我确认或提问（包括 Stage 1 的传播合约 / 模板 / 设计方向）。
- Stage 1 你给出的传播合约与设计选择，直接采用你自己推荐的最优值，视为已确认。
- 请一路连续执行到最终导出 PPTX 文件为止，中途绝不等待人工输入，也不要只输出计划就结束。
"""

    @staticmethod
    def _runtime_anchor(job_dir: Path) -> str:
        """技能仓库与任务目录的运行锚点说明，追加到 prompt 末尾。

        必需性：claude 的 `cwd` 已是任务目录（ADR-012 决策 1），不再位于技能仓库内，
        「靠 cwd 自动读 AGENTS.md」这条发现链路断开，因此技能位置必须由本段显式告知。
        """
        root = _PPT_MASTER_DIR
        return f"""

【运行锚点·必读】
- 技能仓库绝对根：{root}
- 先完整阅读 {root / 'AGENTS.md'}，再按其指向阅读 {root / 'skills' / 'ppt-master' / 'SKILL.md'}，
  然后才开始任何生成动作。该说明书规定的流程、阶段与门禁优先于本提示中的其他表述。
- 说明书内的所有仓库相对路径记号一律按上面的技能仓库绝对根解析；调用脚本时使用形如
  `python3 {root / 'skills' / 'ppt-master' / 'scripts' / '<脚本>.py'} ...` 的绝对路径，
  不要用相对路径，也不要 `cd` 进 skills/ 或 projects/ 子目录。
- **产物落点（硬约束）**：本次任务的工作目录是 {job_dir}。项目初始化与全部中间产物、
  导出文件都必须落在该目录下的 `projects/` 内；**严禁**向技能仓库 {root} 内写入或创建任何文件。
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
            # ADR-012 决策 1：cwd 改为任务目录后，claude 不再位于技能仓库内，
            # 原先「cwd 落在 lib/ppt-master → 自动读到该目录 AGENTS.md」这条技能发现
            # 链路就此断开。故把运行锚点显式写进 prompt（与 guizang 引擎同构）：
            # 技能绝对路径 + 只写 cwd 下的 projects/。
            prompt += self._runtime_anchor(job_dir)
            (job_dir / "prompt.md").write_text(prompt, encoding="utf-8")

            # 运行前置条件走 prepare_runtime 钩子（与 guizang 同形状）：硬缺失直接抛出，
            # 让任务在拉起子进程之前就失败——宁可红一条记录，也不要 spawn 一个会
            # "静默做成无流程 PPT" 的子进程；可降级告警写进 run.log 留痕。
            runtime_notice = self.prepare_runtime(job_dir)
            run_log = open(job_dir / "run.log", "w", encoding="utf-8")
            if runtime_notice:
                run_log.write(f"[warn] {runtime_notice}\n")
                run_log.flush()
            proc = subprocess.Popen(
                # stream-json：claude -p 默认把输出憋到最后一次写完，运行中日志为空；
                # 流式事件由 _pump_run_stream 边跑边蒸馏写 run.log，前端轮询即可看到实时日志
                [
                    self.claude_bin, "-p", "--dangerously-skip-permissions",
                    "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                    prompt,
                ],
                cwd=str(job_dir),  # ADR-012 决策 1：产物落 <jobs_root>/<job_id>/projects/
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            pump = threading.Thread(
                target=self._pump_run_stream, args=(job_id, proc, run_log), daemon=True, name=f"runlog-{job_id[:8]}"
            )
            pump.start()
            self._write_job(
                job_id, status="running", title=effective_title, pages=int(effective_pages),
                style=style, engine_params=engine_params or {}, created_at=_now(), updated_at=_now(),
                pid=proc.pid, started_at=time.time(), result_path=None, error=None,
            )
        
        return JobId(job_id)

    def _pump_run_stream(self, job_id: Any, proc: subprocess.Popen, log_file) -> None:
        """把 claude stream-json 事件蒸馏成人类可读日志，即时写入 run.log。

        - text_delta 文本增量：原样续写（claude 的说明性输出，边生成边可见）
        - assistant 消息里的 tool_use：记一行「⚙ 工具名 关键参数」——能看到引擎在写哪个文件
        - result：记完成/失败行，并接住本任务的 token 消耗写进 job.json（见 ADR-016）
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
                    # 接住本任务的 token 消耗（ADR-016）：result.usage 是整段会话累计值，
                    # 同一 job 出现多个 result（断点续跑/重跑）时按分项累加、不覆盖。
                    # 用量只是附加信息，写不进 job.json 也不能把整条日志流打断。
                    usage = usage_from_result(d)
                    if usage is not None:
                        try:
                            self._write_job(
                                job_id,
                                token_usage=add_usage(
                                    self._read_job(job_id).get("token_usage"), usage
                                ),
                            )
                        except OSError:
                            pass
                    if d.get("subtype") == "success":
                        log_file.write("\n✅ 引擎执行完成\n")
                    else:
                        log_file.write(f"\n❌ {d.get('result') or d.get('subtype') or '引擎执行失败'}\n")
                    if usage is not None:
                        log_file.write(f"📊 token 消耗 {usage['total_tokens']:,}\n")
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
            token_usage=data.get("token_usage"),
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

    def get_pages(self, job_id: Any) -> list[dict] | None:
        r"""逐页预览钩子：枚举本任务已确定落盘的完成页（单一读盘事实源）。

        确定性规则（不依赖 mtime 排序、不预测总数、无 pending）：
        1) 命名规则筛选：^\d+_.*\.svg，排除临时/非页 SVG；
        2) 按 int(数字前缀) 升序（防 1_ 排到 10_ 前）；同一前缀多个文件时
           取 mtime 最新的一份（视作该页的重写版本），天然去重；
        3) index 为 1 起的连续页序；path 为绝对路径（路由换算成相对
           preview_root 的 url）。
        目录尚未生成时返回 []（还没写出任何页，属正常中间态）。
        """
        svg_dir = self.resolve_svg_output_dir(job_id)
        if svg_dir is None:
            return []
        by_prefix: dict[int, Path] = {}
        for p in svg_dir.glob("*.svg"):
            if not _RE_PAGE_SVG.match(p.name):
                continue
            prefix = int(p.name.split("_", 1)[0])
            prev = by_prefix.get(prefix)
            if prev is None or p.stat().st_mtime > prev.stat().st_mtime:
                by_prefix[prefix] = p
        pages = [
            {"index": i, "name": p.name, "path": p, "kind": "image"}
            for i, p in enumerate((by_prefix[k] for k in sorted(by_prefix)), start=1)
        ]
        return pages

    def get_preview_root(self, job_id: Any) -> Path | None:
        """逐页预览钩子：返回产物根目录（svg_output 的项目父目录）。

        挂整个项目目录而非 svg_output，使页内 ../images/xxx 相对引用
        能经 preview 路由同源解析。无法定位时返回 None。
        """
        svg_dir = self.resolve_svg_output_dir(job_id)
        return svg_dir.parent if svg_dir else None

    def get_progress(self, job_id: Any) -> dict | None:
        """获取任务进度信息，仅在 running 状态时返回。

        计数与逐页预览共用同一读盘事实源 get_pages()（「已生成 N 页」与
        页墙天然对齐，杜绝两套 mtime/命名口径边界差 1）。
        """
        data = self._read_job(job_id)
        if data.get("status") != "running":
            return None

        started_at = data.get("started_at", 0)
        elapsed = int(time.time() - started_at) if started_at else 0

        pages = self.get_pages(job_id) or []

        return {
            "stage": "generating",
            "stage_label": "正在生成 PPT（ppt-master）",
            "pages_generated": len(pages),
            "elapsed_seconds": elapsed,
            "current_page": pages[-1]["name"] if pages else None,
            "last_log": self._tail_run_log(job_id) or "引擎运行中，暂无输出日志",
        }


class PptMasterDraftEngine(PptMasterEngine):
    """注册名 `ppt_master_draft` 专用类：实现全继承，仅声明**退出逐页钩子**。

    为什么要多这层薄子类而不是直接复用 `PptMasterEngine`：能力位是按**实现类**做鸭子类型
    探测派生的（`implements_hook`），草稿与输出两个注册名此前指向同一个类，于是
    `GET /api/engines` 会把草稿引擎也标成具备 `page_preview`——一个与实际能力不符的标签。
    按注册名分支被 ADR-001 第 4 条禁止，所以正确的分离手段就是这个子类，
    与 `ClaudeDraftEngine` 同一手法。

    `prepare_runtime` 有意沿用父类：草稿路径只跑确定性的 `source_to_md.py`，严格说不需要
    claude CLI，故该检查对草稿偏严。保留的理由是本产品交付目标就是出片，一个装不出
    claude 的环境里草稿也没有实际意义；放宽只会让启动预检少一道真实信号。
    """

    get_pages = None  # type: ignore[assignment]
    get_preview_root = None  # type: ignore[assignment]
