"""guizang_ppt 引擎实现：驱动 Claude Code 执行 guizang-ppt-skill 生成单文件 HTML 演示文稿。

运行时口径（与 docs/vc_design/objects/01-04-HTML输出引擎.md 一致）：
本引擎**不自己排版**。上游 guizang-ppt-skill 是写给 AI agent 执行的技能包
（SKILL.md 规定布局库、主题节奏、类名白名单、动效 recipe、校验脚本），
所以这里把整件生成工作交给 `claude` 子进程去执行技能，引擎只做
「任务编排 + 状态机 + 产物定位 + 日志透出」。

与 ppt_master 引擎的三点差异（刻意为之）：
1. cwd 设为本任务的 job 目录，**不是技能目录**——技能目录是 git submodule，
   属上游只读区（AGENTS 规则 4），绝不让 agent 往里面写产物；
2. 产物路径**在 prompt 里显式钉死**为 `<job_dir>/result.html`，状态判定直接
   查这个文件（存在 + mtime 晚于任务启动 + 非空），不再像 ppt_master 那样靠
   「mtime 猜最新导出」，从根上避免并发/历史任务冒领旧产物；
3. 产物是**单个 HTML**，没有逐页落盘文件；逐页预览靠**解析该文件读出真实页
   序列**（见 get_pages），页 url 由预览路由注入的引导脚本驱动 `__playSlide` 定位。

文件布局（jobs_root 来自 engine.core.config）：
  <job_id>/
    job.json     # 引擎内部状态（pid/status/result_path/started_at/error）
    draft.md     # 输入草稿 Markdown
    prompt.md    # 交给 claude 的完整 prompt（审计用）
    run.log      # claude stream-json 蒸馏后的可读日志
    result.html  # 最终产物（单文件网页 PPT）

AGENTS.md 规则：本文件只实现 OutputEngine 协议，不修改任何调用方（后端 service 层）。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from engine.core.config import get_jobs_root
from engine.core.models import Job, JobId, JobStatus, EngineType
from engine.core.protocol import OutputEngine
from engine.core.usage import add_usage, usage_from_result

# 仓库根：packages/engine/impl/guizang_ppt/engine.py
# parents[0]=guizang_ppt [1]=impl [2]=engine [3]=packages [4]=仓库根
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SKILL_DIR = _REPO_ROOT / "lib" / "guizang-ppt-skill"
_SKILL_NAME = _SKILL_DIR.name
_CLAUDE_BIN = "claude"
_DEFAULT_TIMEOUT_SECONDS = 3600

# 产物文件名（与 prompt 中钉死的路径一致，改一处要同步改另一处）
_RESULT_FILENAME = "result.html"
# 产物有效性下限：小于此字节数视为 agent 没真写出东西（截断/占位）
_MIN_RESULT_BYTES = 2048

# 产物未写出时的自动续跑上限（每次都是一条新的 claude 长任务，克制烧额度）
_DEFAULT_MAX_RESUME_ATTEMPTS = 2

# 网关/CLI 断流特征词：实测第三方 Anthropic 兼容网关会在超长输出中途
# 直接掐连接，会话里留下 "API Error: terminated"，而 stream 的 result 仍报
# subtype=success —— 只看 result 会把网络故障误判成“agent 没干活”。
_API_ERROR_MARKERS = (
    "API Error", "terminated", "ECONNRESET", "socket hang up",
    "overloaded", "rate limit", "timed out", "timeout",
)

_STYLE_LABEL = {"magazine": "电子杂志 × 电子墨水（magazine）", "swiss": "瑞士国际主义（swiss）"}
_THEME_LABEL = {
    "magazine_theme": {
        "monocle": "monocle（墨水经典）",
        "indigo": "indigo（靛蓝瓷）",
        "forest": "forest（森林墨）",
        "kraft": "kraft（牛皮纸）",
        "dune": "dune（沙丘）",
    },
    "swiss_theme": {
        "ikb": "ikb（克莱因蓝）",
        "lemon": "lemon（柠檬黄）",
        "green": "green（柠檬绿）",
        "orange": "orange（安全橙）",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _SlideScanner(HTMLParser):
    """扫描 HTML 里真实存在的 `<section class="slide ...">` 页序列。

    为什么必须用解析器而不是字符串计数（真跑取证）：
    - magazine 骨架的 `<!-- SLIDES_HERE … -->` 注释里写着示例文字
      `<section class="slide ...">`，grep 计数会多出一页（产物 grep=6、真实=5）；
    - swiss 骨架自带 2 个**真实**示例页（data-slide-id=cover/closing，不在注释内），
      agent 通常就地改写它们而非删除。
    解析器天然跳过注释，并按标签嵌套配对闭合，因此数出来就是浏览器会渲染的页。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.slides: list[list[Any]] = []   # [[data-slide-id, 是否已闭合], ...]
        self._in_slide = False
        self._nested = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "section":
            return
        a = dict(attrs)
        if not self._in_slide and "slide" in (a.get("class") or "").split():
            self._in_slide = True
            self._nested = 0
            self.slides.append(
                [a.get("data-slide-id") or f"slide-{len(self.slides) + 1}", False]
            )
        elif self._in_slide:
            self._nested += 1

    def handle_endtag(self, tag: str) -> None:
        if tag != "section" or not self._in_slide:
            return
        if self._nested == 0:
            self.slides[-1][1] = True     # 本页闭合
            self._in_slide = False
        else:
            self._nested -= 1


def scan_slides(html_text: str) -> list[str]:
    """返回产物中**已完整闭合**的页 data-slide-id 列表（顺序即页序）。

    未闭合的尾页被剔除：agent 分批写入时最后页可能只写了一半，塞进页墙会渲染成
    破版假页。（html.parser 不强制闭合标签，所以必须在结尾按 closed 过滤。）
    """
    scanner = _SlideScanner()
    try:
        scanner.feed(html_text)
        scanner.close()
    except Exception:  # noqa: BLE001 畸形 HTML 不应让预览接口整墙报错
        return []
    return [sid for sid, closed in scanner.slides if closed]


def _claude_skills_dir() -> Path:
    """Claude Code 的技能发现目录（尊重 CLAUDE_CONFIG_DIR 重定位）。"""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "skills"


def ensure_skill_installed() -> str | None:
    """把仓库内锁定的上游技能软链到 Claude Code 技能目录。

    上游 README 的标准安装方式是 `git clone ... ~/.claude/skills/<name>`；本仓库
    已经用 git submodule 把同一份内容锁在 lib/ 下并钉了版本，所以这里用
    **符号链接**复用它，而不是再 clone 一份（避免第二份漂移）。软链不写入上游，
    满足 AGENTS 规则 4「上游只读」。

    返回一句人类可读的安装状态（进 run.log 与告警用）；一切正常返回 None。
    该函数是 best-effort：技能能否生效最终以 prompt 里给出的绝对路径为准，
    链接失败只降级为「agent 需按路径自行读取 SKILL.md」，不阻断任务。
    """
    if not (_SKILL_DIR / "SKILL.md").is_file():
        return (
            f"未找到上游技能 {_SKILL_DIR}（SKILL.md 缺失）。"
            "请先执行 git submodule update --init --recursive，否则本引擎无法生成。"
        )

    link = _claude_skills_dir() / _SKILL_NAME
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if link.resolve() == _SKILL_DIR.resolve():
                return None  # 已正确链好
            link.unlink()
        elif link.exists():
            # 用户自己 clone 过一份真实目录：不覆盖、不清空，只提示版本可能漂移
            return (
                f"{link} 已存在为独立副本（非本仓库 submodule）。技能版本可能与项目锁定的 "
                f"{_SKILL_NAME} 不一致，建议改用软链：ln -sfn {_SKILL_DIR} {link}"
            )
        link.symlink_to(_SKILL_DIR, target_is_directory=True)
        return None
    except OSError as exc:
        return f"技能软链到 {link} 失败（{exc}）；将退回按绝对路径读取 SKILL.md。"


class GuizangPptEngine(OutputEngine):
    """通过 guizang-ppt-skill（Claude Code 执行）生成单文件 HTML 演示文稿（长任务、异步）。

    jobs_root 来自 config（DECKSMITH_JOBS_ROOT > .env > outputs/jobs）。
    状态机：running -> completed / failed / timeout。
    """

    def __init__(
        self,
        jobs_root: Path | None = None,
        claude_bin: str = _CLAUDE_BIN,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_resume_attempts: int = _DEFAULT_MAX_RESUME_ATTEMPTS,
    ) -> None:
        self.jobs_root: Path = Path(jobs_root or get_jobs_root()).resolve()
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.claude_bin = claude_bin
        self.timeout_seconds = timeout_seconds
        self.max_resume_attempts = max_resume_attempts
        # pump 线程 / supervisor 线程 / 轮询 status() 都会读改写 job.json，
        # 无锁会互相覆盖（例如 pump 写 session_id 时把 supervisor 写的状态抹掉）。
        self._lock = threading.Lock()

    # ── 运行前置条件（协议可选钩子，见 engine.core.protocol.OutputEngine）──
    def prepare_runtime(self, job_dir: Path | None = None) -> str | None:
        """把仓库内锁定的上游技能软链到 Claude 的技能发现目录。

        分级依据：技能**文件缺失**是硬缺失（抛异常）——没有 SKILL.md 本引擎根本无法
        出片，绝不能只留一行 warn；而**软链建不上**只是可降级告警，因为 prompt 里
        同时给了技能绝对路径，agent 可按路径自行读取（ADR-011 决策 5 的降级设计）。

        幂等：`ensure_skill_installed()` 自身已保证（已链好则直接返回 None）。
        """
        if not (_SKILL_DIR / "SKILL.md").is_file():
            raise RuntimeError(
                f"未找到上游技能 {_SKILL_DIR}（SKILL.md 缺失）。"
                "请先执行 git submodule update --init --recursive，否则本引擎无法生成。"
            )
        return ensure_skill_installed()

    # ── 内部工具 ──
    def _job_dir(self, job_id: Any) -> Path:
        return self.jobs_root / str(job_id)

    def _result_path(self, job_id: Any) -> Path:
        return self._job_dir(job_id) / _RESULT_FILENAME

    @staticmethod
    def _template_file(style: str) -> Path:
        """按风格选骨架模板（prompt 里要给出绝对路径供 cp）。"""
        return _SKILL_DIR / "assets" / ("template-swiss.html" if style == "swiss" else "template.html")

    def _read_job(self, job_id: Any) -> dict:
        p = self._job_dir(job_id) / "job.json"
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_job(self, job_id: Any, **fields: Any) -> dict:
        with self._lock:
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

    def _artifact_ready(self, job_id: Any, since_ts: float) -> bool:
        """产物判定：显式路径 + 启动时间下界 + 非空，三条同时成立才算完成。"""
        p = self._result_path(job_id)
        try:
            st = p.stat()
        except OSError:
            return False
        return st.st_size >= _MIN_RESULT_BYTES and (not since_ts or st.st_mtime >= since_ts)

    # ── Prompt 构建 ──
    @staticmethod
    def _effective_title(content: str, title: str | None) -> str:
        """标题缺失时从草稿首个标题行/首行提取（与 ppt_master 同口径）。"""
        if title:
            return title
        md = (content or "").strip()
        if md:
            first = md.splitlines()[0]
            return first.lstrip("#").strip() if first.startswith("#") else first[:50]
        return "Untitled HTML Presentation"

    @staticmethod
    def _build_prompt(
        *,
        title: str,
        style: str,
        theme_desc: str | None,
        pages: int | None,
        draft: str,
        job_dir: Path,
        result_file: Path,
        template_file: Path,
    ) -> str:
        """构建交给 Claude Code 的 prompt：技能按路径显式指定、产物路径钉死、
        产出走「复制骨架 + 分批 Edit」、并禁止任何反问。

        产出协议是这里最关键的一段：模板骨架 100KB+，让模型一次性 Write 整个产物
        会超出单次回复输出上限、被 Anthropic 兼容网关直接掐断连接
        （实测会话记录末条为 `API Error: terminated`，而 stream 的 result 仍报
        subtype=success）。改为复制骨架 + 局部 Edit 后，单次输出只剩几页 HTML。
        """
        pages_line = f"{pages} 页" if pages else "页数由你按内容节奏判断"
        style_line = _STYLE_LABEL.get(style, style)
        theme_line = f"\n- 主题色：{theme_desc}" if theme_desc else ""
        return f"""请使用 guizang-ppt-skill 技能，把下面的 Markdown 草稿制作成一份 HTML 演示文稿。

技能位置（**只读**，禁止修改其中任何文件）：{_SKILL_DIR}
必须先完整阅读 `{_SKILL_DIR}/SKILL.md`，并按它的指引读取所需的 `references/`、`assets/`，
严格按技能规定的版式库、主题节奏、类名白名单与动效规范产出，不要自创模板里不存在的类名。

- 标题：{title}
- 视觉风格：{style_line}{theme_line}
- 篇幅：{pages_line}
- 工作目录：{job_dir}
- **唯一产物文件（必须落盘）**：{result_file}
  要求是单文件 HTML（CSS/JS 内联、含横向翻页与演讲者模式），可直接用浏览器离线打开。
  产物只能写到上面这个路径；除必要临时文件外不要往别处写，尤其**不要写进技能目录**。

【产出协议·必须严格遵守，违反会导致任务失败】
骨架模板 `{template_file}` 已含全部 CSS / WebGL / 翻页 / 演讲者模式，体积 100KB 以上。
**禁止**把整份文件内容重新输出（一次性 Write 整个产物会超出单次回复上限、被网关掐断连接）。
按下面顺序做：
1. 第一步复制骨架：`cp "{template_file}" "{result_file}"`
2. 之后**只用 Edit 做局部替换**：把骨架里的 `<!-- SLIDES_HERE … -->` 注释块
   （注意它可能跨多行、带中文说明文字，必须整段替换掉）换成你的
   `<section class="slide …">` 页面块。
3. 分批注入：每次 Edit 只写 3–4 页，逐批往后追加，直到全部页面写完。
4. 演讲备注：把骨架里 `const SPEAKER_NOTES = [` 数组按同样顺序逐条 Edit 填充，
   每条以页面对应的 `data-slide-id` 为键（见 `references/presenter-mode.md`），
   不要用数组下标或页码当持久化键。
5. 收尾必须自检并在回复里报告结果：
   `wc -c "{result_file}"`（应为几十 KB 以上）、
   `grep -c '<section class="slide' "{result_file}"`（应等于页数）、
   `grep -c 'SLIDES_HERE' "{result_file}"`（应为 0）。

草稿内容（Markdown）：
---
{draft}
---

【重要·无人值守自动运行】本次是后台自动化任务，没有任何人会回复你：
- 跳过技能里的澄清提问环节（SKILL.md 的「动手前必做 / 多轮确认」）：需要决策的参数
  一律直接采用你自己推荐的最优值，并视同已经用户确认。
- 不要停下来征求确认，**更不要只说“我现在开始创建”就结束回复**——必须在本次任务里
  实际执行复制骨架与分批 Edit，直到产物文件写完为止。
- 若技能提供校验脚本（`{_SKILL_DIR}/scripts/*.mjs`），完成后用 node 自检一次；
  不通过就地修正到通过，再收尾。
"""

    # ── OutputEngine 协议 ──
    def submit(
        self,
        content: str | None = None,
        title: str | None = None,
        pages: int | None = None,
        engine_params: dict | None = None,
        source: dict | None = None,
        job_id: Any = None,
    ) -> JobId:
        """提交生成任务：写草稿 → 拉 claude 执行技能 → 后台线程泵日志 + 超时看门狗。

        兼容说明：调用方（service/jobs.py engine_submit）按引擎签名自动适配
        sources/engine_type，本引擎按 OutputEngine 协议的 `source` 单数形态接收。
        """
        job_id = job_id or uuid.uuid4().hex
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        result_file = self._result_path(job_id)

        params = engine_params or {}
        style = params.get("style") or "magazine"
        theme_key = "magazine_theme" if style == "magazine" else "swiss_theme"
        theme_val = params.get(theme_key)
        theme_desc = _THEME_LABEL[theme_key].get(theme_val) if theme_val else None

        draft = content or ""
        effective_title = self._effective_title(draft, title)
        if not draft.strip() and source:
            # 输出任务正常都带草稿；万一没有，把素材位置透传给 agent 自己去读
            draft = f"（未提供 Markdown 草稿，请读取素材后自行成稿：{source.get('value') or source}）"
        (job_dir / "draft.md").write_text(draft, encoding="utf-8")

        prompt = self._build_prompt(
            title=effective_title, style=style, theme_desc=theme_desc,
            pages=pages, draft=draft, job_dir=job_dir, result_file=result_file,
            template_file=self._template_file(style),
        )
        (job_dir / "prompt.md").write_text(prompt, encoding="utf-8")

        # 运行前置条件统一走 prepare_runtime 钩子：启动期预检与任务前检查共用同一实现，
        # 避免出现"entrypoint 链好了、submit 里又是另一套"的第二路径
        skill_notice = self.prepare_runtime(job_dir)
        run_log = open(job_dir / "run.log", "w", encoding="utf-8")
        if skill_notice:
            run_log.write(f"[warn] {skill_notice}\n")
            run_log.flush()

        try:
            proc = subprocess.Popen(
                # stream-json：claude -p 默认把输出憋到最后一次写完，运行中日志为空；
                # 流式事件由 _pump_run_stream 边跑边蒸馏写 run.log，前端轮询即可看到实时日志
                [
                    self.claude_bin, "-p", "--dangerously-skip-permissions",
                    "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                    prompt,
                ],
                # cwd 用 job 目录而非技能目录：技能是上游只读区，且产物必须落在本任务目录
                cwd=str(job_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            run_log.write("[error] 未找到 claude CLI（Claude Code）。本引擎依赖 claude code 执行技能。\n")
            run_log.close()
            self._write_job(
                job_id, status="failed", title=effective_title, pages=pages,
                engine_params=params, created_at=_now(), updated_at=_now(),
                pid=None, result_path=None,
                error="未找到 claude CLI（Claude Code 未安装或不在 PATH 中）",
            )
            return JobId(job_id)

        # 先落状态再起线程：pump 线程会写 claude_session_id，晚写初始状态会把它覆盖掉
        self._write_job(
            job_id, status="running", title=effective_title, pages=pages,
            engine_params=params, created_at=_now(), updated_at=_now(),
            started_at=time.time(), pid=proc.pid, result_path=None, error=None,
            skill_notice=skill_notice,
            resume_attempt=0, claude_session_id=None, claude_result=None,
            supervisor_pid=os.getpid(),
        )

        threading.Thread(
            target=self._pump_run_stream, args=(job_id, proc, run_log),
            daemon=True, name=f"guizang-runlog-{str(job_id)[:8]}",
        ).start()
        threading.Thread(
            target=self._supervise, args=(job_id, proc, self.timeout_seconds, 0),
            daemon=True, name=f"guizang-supervise-{str(job_id)[:8]}",
        ).start()

        return JobId(job_id)

    def _pump_run_stream(self, job_id: Any, proc: subprocess.Popen, log_file) -> None:
        """把 claude stream-json 事件蒸馏成人类可读日志，并落盘可诊断的元信息。

        - 任何带 session_id 的事件（system/init、assistant、result）：记下会话号，
          供 supervisor 用 `--resume` 断点续跑；
        - text_delta：原样续写（agent 的说明性输出，边生成边可见）；
        - assistant.tool_use：记一行「⚙ 工具名 关键参数」——能看到它在写哪个文件；
        - result：连 is_error / result 原文 / num_turns 一起写进 job.json，并把本任务的
          token 消耗按分项累加进去（续跑不覆盖，见 ADR-016）。
          **只看 subtype 会骗人**：实测网关中途掐连接时 result 仍报 subtype=success，
          所以只有 success 且非 is_error 才记 ✅，否则记 ❌ 并带出原文；
        - 非 JSON 行（stderr 噪音等）：原样落盘，不丢弃。
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
                sid = d.get("session_id")
                if sid:
                    self._write_job(job_id, claude_session_id=sid)
                t = d.get("type")
                if t == "stream_event":
                    delta = (d.get("event") or {}).get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        log_file.write(delta["text"])
                        log_file.flush()
                elif t == "assistant":
                    for c in (d.get("message") or {}).get("content", []):
                        if c.get("type") == "tool_use":
                            inp = c.get("input") or {}
                            hint = inp.get("file_path") or inp.get("path") or inp.get("command") or ""
                            log_file.write(f"\n⚙ {c.get('name')} {hint}\n")
                            log_file.flush()
                elif t == "result":
                    payload = {
                        "subtype": d.get("subtype"),
                        "is_error": bool(d.get("is_error")),
                        "num_turns": d.get("num_turns"),
                        "result": str(d.get("result") or "")[:400],
                    }
                    # token 消耗按分项**累加**：本引擎有 --resume 断点续跑，每个 result 的
                    # usage 只是那一段进程的累计值（CLI 内为 totalUsage），覆盖写会少算（ADR-016）。
                    # 每个 result 事件在此只被处理一次，故不会重复计数。
                    usage = add_usage(
                        self._read_job(job_id).get("token_usage"), usage_from_result(d)
                    )
                    self._write_job(
                        job_id, claude_result=payload, token_usage=usage, updated_at=_now()
                    )
                    if payload["subtype"] == "success" and not payload["is_error"]:
                        log_file.write("\n✅ 技能执行完成\n")
                    else:
                        log_file.write(
                            f"\n❌ 技能执行未成功（subtype={payload['subtype']},"
                            f" is_error={payload['is_error']}）："
                            f"{payload['result'] or '无返回文本'}\n"
                        )
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

    @staticmethod
    def _build_resume_prompt(result_file: Path, template_file: Path, done_hint: str) -> str:
        """续跑 prompt：只补断点，不重新规划、不重复已完成的页面。"""
        return f"""上一轮执行在写出产物之前被打断了（模型连接被中断），任务尚未完成。

- 产物文件：{result_file}
- 骨架模板：{template_file}
- 当前状态：{done_hint}

请**从断点继续**，不要重新规划、不要重读你已经读过的参考文件：
1. 若产物文件不存在，先执行 `cp "{template_file}" "{result_file}"`；若已存在，直接接着它往下补。
2. 仍然**禁止一次性 Write 整个产物**（会再次被中断）；只用 Edit 分批替换，每批 3–4 页。
3. 已写好的页面保持原样，只补剩余页面与 `const SPEAKER_NOTES = [` 里缺的备注。
4. 收尾自检并在回复里报告：`wc -c`、`grep -c '<section class="slide'`、`grep -c SLIDES_HERE`（应为 0）。

本次是无人值守后台任务，不要提问、不要只回复计划，直接动手直到产物写完。
"""

    def _resume_run(self, job_id: Any, session_id: str, attempt: int, template_file: Path) -> subprocess.Popen:
        """用 `--resume <session_id>` 续跑同一会话（cwd 不变，故会话可被定位）。"""
        job_dir = self._job_dir(job_id)
        result_file = self._result_path(job_id)
        if result_file.exists():
            done_hint = f"文件已存在，{result_file.stat().st_size} 字节，可能写到一半"
        else:
            done_hint = "文件尚不存在（骨架都还没复制）"
        prompt = self._build_resume_prompt(result_file, template_file, done_hint)
        (job_dir / f"prompt-resume-{attempt}.md").write_text(prompt, encoding="utf-8")

        proc = subprocess.Popen(
            [
                self.claude_bin, "-p", "--resume", session_id, "--dangerously-skip-permissions",
                "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                prompt,
            ],
            cwd=str(job_dir), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self._write_job(job_id, pid=proc.pid, resume_attempt=attempt, updated_at=_now())
        return proc

    def _append_log(self, job_id: Any, text: str) -> None:
        """往 run.log 追加一行引擎自己的状态说明（续跑/终态判定等）。"""
        try:
            with open(self._job_dir(job_id) / "run.log", "a", encoding="utf-8") as f:
                f.write(text)
                f.flush()
        except OSError:
            pass

    def _diagnose_failure(self, job_id: Any, data: dict) -> str:
        """失败根因：优先 claude 的 result 原文，其次日志里的断流特征词，最后兜底。"""
        res = data.get("claude_result") or {}
        if res.get("is_error") or (res.get("subtype") not in (None, "success")):
            return (
                f"claude 执行返回失败（subtype={res.get('subtype')}）："
                f"{res.get('result') or '无返回文本'}"
            )
        try:
            log = (self._job_dir(job_id) / "run.log").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            log = ""
        hit = next((m for m in _API_ERROR_MARKERS if m.lower() in log.lower()), None)
        if hit:
            # 回显命中行的原文（而非光秃秃一个特征词），让用户在 UI 上直接看到断流文本
            idx = log.lower().find(hit.lower())
            start = log.rfind("\n", 0, idx) + 1
            end = log.find("\n", idx)
            snippet = (log[start:end if end != -1 else len(log)]).strip()[:200] or hit
            return (
                f"上游模型连接被中断（日志：{snippet}），产物未写出；"
                f"已自动续跑 {data.get('resume_attempt', 0)} 次仍未成功"
            )
        tail = self._tail_run_log(job_id) or "（无输出）"
        return (
            f"claude 子进程已结束但未写出有效产物（{_RESULT_FILENAME}）；"
            f"已自动续跑 {data.get('resume_attempt', 0)} 次；末行日志：{tail}"
        )

    def _supervise(self, job_id: Any, proc: subprocess.Popen, timeout: int, attempt: int) -> None:
        """任务监督：超时终止 + 进程结束但无产物时按会话续跑，额度用尽才判 failed。"""
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if self._read_job(job_id).get("status") != "running":
                return
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self._write_job(
                job_id, status="timeout", updated_at=_now(), supervisor_pid=None,
                error=f"生成超时（超过 {timeout} 秒），已终止 claude 子进程",
            )
            return

        data = self._read_job(job_id)
        if data.get("status") != "running":
            return  # 其它路径已落终态（如 timeout），不覆盖
        since = data.get("started_at", 0.0) or 0.0
        if self._artifact_ready(job_id, since):
            # 产物已落盘：释放租约，终态交给 status() 判 completed
            self._write_job(job_id, supervisor_pid=None)
            return

        sid = data.get("claude_session_id")
        if attempt < self.max_resume_attempts and sid:
            elapsed = int(time.time() - since) if since else 0
            remaining = max(300, timeout - elapsed)  # 续跑共用总预算，避免 3×3600s
            style = (data.get("engine_params") or {}).get("style", "magazine")
            self._append_log(
                job_id,
                f"\n↻ 产物未写出，续跑会话 {str(sid)[:8]}…（第 {attempt + 1}/{self.max_resume_attempts} 次，"
                f"剩余预算 {remaining}s）\n",
            )
            try:
                rproc = self._resume_run(job_id, sid, attempt + 1, self._template_file(style))
            except FileNotFoundError:
                self._write_job(
                    job_id, status="failed", updated_at=_now(), supervisor_pid=None,
                    error="续跑失败：未找到 claude CLI（Claude Code 未安装或不在 PATH 中）",
                )
                return
            rlog = open(self._job_dir(job_id) / "run.log", "a", encoding="utf-8")
            threading.Thread(
                target=self._pump_run_stream, args=(job_id, rproc, rlog),
                daemon=True, name=f"guizang-runlog-{str(job_id)[:8]}-r{attempt + 1}",
            ).start()
            threading.Thread(
                target=self._supervise, args=(job_id, rproc, remaining, attempt + 1),
                daemon=True, name=f"guizang-supervise-{str(job_id)[:8]}-r{attempt + 1}",
            ).start()
            return

        self._write_job(
            job_id, status="failed", updated_at=_now(), supervisor_pid=None,
            error=self._diagnose_failure(job_id, data),
        )

    def status(self, job_id: Any) -> JobStatus:
        """状态推进：已终态直接返回；running 时看进程，进程已结束时按钉死的产物判定。

        进程已退出且产物缺失时，若本进程内的 supervisor 仍在决策（可能正要 --resume 续跑），
        则维持 running 不抢判 failed；租约失效（后端重启、线程已死）才自行派生终态。
        """
        data = self._read_job(job_id)
        status = data.get("status", "running")
        if status != "running":
            return JobStatus(status)
        if self._process_alive(data.get("pid")):
            return JobStatus.RUNNING

        since = data.get("started_at", 0.0) or 0.0
        result_file = self._result_path(job_id)
        if self._artifact_ready(job_id, since):
            self._write_job(job_id, status="completed", result_path=str(result_file), updated_at=_now())
            return JobStatus.COMPLETED

        lease = data.get("supervisor_pid")
        if lease and self._process_alive(lease):
            return JobStatus.RUNNING  # 交给 supervisor 决策（续跑或判失败）

        self._write_job(
            job_id, status="failed",
            error=self._diagnose_failure(job_id, data),
            updated_at=_now(), supervisor_pid=None,
        )
        return JobStatus.FAILED

    def result(self, job_id: Any) -> str:
        data = self._read_job(job_id)
        rp = data.get("result_path")
        if rp and Path(rp).exists():
            return rp
        p = self._result_path(job_id)
        if p.is_file():
            return str(p)
        raise FileNotFoundError(f"任务 {job_id} 尚未生成产物")

    def resume(self, job_id: Any) -> Job:
        """重启中断任务：沿用已落盘的 draft.md 与参数，重新提交同一 job_id。"""
        data = self._read_job(job_id)
        if data.get("status") == "completed":
            return self._to_job(job_id, data)
        draft_path = self._job_dir(job_id) / "draft.md"
        content = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
        self.submit(
            content=content,
            title=data.get("title"),
            pages=data.get("pages"),
            engine_params=data.get("engine_params") or {},
            job_id=job_id,
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
        """读 run.log 最后一行非空输出；运行中无输出时如实返回 None，不冒充进度。"""
        try:
            p = self._job_dir(job_id) / "run.log"
            if not p.exists():
                return None
            text = p.read_text(encoding="utf-8", errors="ignore").strip()
            return text.splitlines()[-1][:max_chars] if text else None
        except OSError:
            return None

    def get_pages(self, job_id: Any) -> list[dict]:
        r"""逐页预览钩子：解析单文件产物，读出**浏览器实际会渲染的页序列**。

        与 ppt_master 的 `svg_output/NN_*.svg` 不同，这里没有逐页文件可枚举，
        页是同一文件里的第 N 个 `<section class="slide">`。因此：
        - 只收**已完整闭合**的页（半截页不入墙，见 _SlideScanner）；
        - 所有 PageRef 的 path 都指向 result.html，由预览路由注入的引导脚本按
          `?slide=N` 驱动 `window.__playSlide(N-1)` 定位到该页；
        - 口径已知：swiss 骨架自带 cover/closing 两个真实页位，agent 就地改写，
          所以 running 初期页数可能从 2 起算——这是文件真实内容，不做基线扣减，
          避免出现第二套计数逻辑。
        进度与页墙共用本方法（get_progress 直接取 len），天然对齐。
        """
        p = self._result_path(job_id)
        try:
            if not p.is_file():
                return []
            ids = scan_slides(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            return []
        return [
            {"index": i, "name": f"{i:02d}_{sid}.html", "path": p, "kind": "html"}
            for i, sid in enumerate(ids, start=1)
        ]

    def get_preview_root(self, job_id: Any) -> Path | None:
        """逐页预览钩子：产物根目录即 job 目录（预览路由据此限制可读类型）。

        注意：job 目录里还有 prompt.md / run.log / job.json 等审计文件，
        靠 service._PREVIEW_MEDIA_TYPES 白名单挡住（只放行图片与 html），
        不靠目录隔离——新增可预览类型时必须复核这张表。
        """
        p = self._result_path(job_id)
        return p.parent if p.is_file() else None

    def get_progress(self, job_id: Any) -> dict | None:
        """运行中进度：页数直接取自 get_pages（单一读盘事实源）。

        与 pages 接口的 pages_generated=len(pages) 同口径，不会出现「进度说 4 页、
        页墙只有 3 格」这种两套计数逻辑的边界差。
        """
        data = self._read_job(job_id)
        if data.get("status") != "running":
            return None
        started_at = data.get("started_at", 0)
        elapsed = int(time.time() - started_at) if started_at else 0
        style = (data.get("engine_params") or {}).get("style", "magazine")
        pages = self.get_pages(job_id)
        return {
            "stage": "generating",
            "stage_label": f"正在生成 HTML 演示文稿（guizang-ppt-skill · {style}）",
            "pages_generated": len(pages),
            "current_page": pages[-1]["name"] if pages else None,
            "elapsed_seconds": elapsed,
            "last_log": self._tail_run_log(job_id) or "引擎运行中，暂无输出日志",
        }
