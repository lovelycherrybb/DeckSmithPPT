"""guizang_ppt 引擎实现：将 Markdown 内容生成 HTML 演示文稿。

基于 guizang-ppt-skill 的模板和布局系统，生成高质量 HTML演示文稿。
支持两种风格：
- magazine：电子杂志风格（衬线字体、暖色调）
- swiss：瑞士国际主义风格（无衬线、网格、高对比度）
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 确保 engine 包可被 import
_PACKAGES_DIR = Path(__file__).resolve().parents[3]
if str(_PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_DIR))

from engine.core.config import get_jobs_root
from engine.core.models import Job, JobId, JobStatus, EngineType
from engine.core.protocol import OutputEngine

# guizang-ppt-skill 根目录
SKILL_ROOT = Path(__file__).resolve().parents[4] / "lib" / "guizang-ppt-skill"
TEMPLATES_DIR = SKILL_ROOT / "assets"
REFERENCES_DIR = SKILL_ROOT / "references"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 主题色板：逐字对齐上游 references/themes.md（magazine，6 个 CSS 变量）
# 与 references/themes-swiss.md（swiss，10 个 CSS 变量）。用于改写模板 :root 里的
# 同名 CSS 自定义属性——真模板所有散落颜色都走 var(--...)，只改 :root 一处即可换肤。
MAGAZINE_THEMES = {
    "monocle": {"ink": "#0a0a0b", "ink-rgb": "10,10,11", "paper": "#f1efea", "paper-rgb": "241,239,234", "paper-tint": "#e8e5de", "ink-tint": "#18181a"},
    "indigo": {"ink": "#0a1f3d", "ink-rgb": "10,31,61", "paper": "#f1f3f5", "paper-rgb": "241,243,245", "paper-tint": "#e4e8ec", "ink-tint": "#152a4a"},
    "forest": {"ink": "#1a2e1f", "ink-rgb": "26,46,31", "paper": "#f5f1e8", "paper-rgb": "245,241,232", "paper-tint": "#ece7da", "ink-tint": "#253d2c"},
    "kraft": {"ink": "#2a1e13", "ink-rgb": "42,30,19", "paper": "#eedfc7", "paper-rgb": "238,223,199", "paper-tint": "#e0d0b6", "ink-tint": "#3a2a1d"},
    "dune": {"ink": "#1f1a14", "ink-rgb": "31,26,20", "paper": "#f0e6d2", "paper-rgb": "240,230,210", "paper-tint": "#e3d7bf", "ink-tint": "#2d2620"},
}

SWISS_THEMES = {
    "ikb": {"paper": "#fafaf8", "paper-rgb": "250,250,248", "ink": "#0a0a0a", "ink-rgb": "10,10,10", "grey-1": "#f0f0ee", "grey-2": "#d4d4d2", "grey-3": "#737373", "accent": "#002FA7", "accent-rgb": "0,47,167", "accent-on": "#ffffff"},
    "lemon": {"paper": "#fafaf8", "paper-rgb": "250,250,248", "ink": "#0a0a0a", "ink-rgb": "10,10,10", "grey-1": "#f0f0ee", "grey-2": "#d4d4d2", "grey-3": "#737373", "accent": "#FFD500", "accent-rgb": "255,213,0", "accent-on": "#0a0a0a"},
    "green": {"paper": "#fafaf8", "paper-rgb": "250,250,248", "ink": "#0a0a0a", "ink-rgb": "10,10,10", "grey-1": "#f0f0ee", "grey-2": "#d4d4d2", "grey-3": "#737373", "accent": "#C5E803", "accent-rgb": "197,232,3", "accent-on": "#0a0a0a"},
    "orange": {"paper": "#fafaf8", "paper-rgb": "250,250,248", "ink": "#0a0a0a", "ink-rgb": "10,10,10", "grey-1": "#f0f0ee", "grey-2": "#d4d4d2", "grey-3": "#737373", "accent": "#FF6B35", "accent-rgb": "255,107,53", "accent-on": "#ffffff"},
}


class GuizangPptEngine(OutputEngine):
    """guizang_ppt 引擎：生成 HTML 演示文稿。"""

    def __init__(self, jobs_root=None):
        self.jobs_root: Path = (jobs_root or get_jobs_root()).resolve()
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def _job_dir(self, job_id) -> Path:
        return self.jobs_root / str(job_id)

    def _read_job(self, job_id) -> dict:
        p = self._job_dir(job_id) / "job.json"
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_job(self, job_id, **fields) -> dict:
        p = self._job_dir(job_id) / "job.json"
        data = self._read_job(job_id)
        data.update(fields)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def _parse_markdown_to_sections(self, content: str) -> list[dict]:
        """将 Markdown 解析为章节结构。"""
        if not content:
            return [{"type": "content", "title": "内容", "content": "暂无内容"}]
        
        sections = []
        lines = content.split('\n')
        current_section = None
        current_content = []
        is_first_h1 = True
        
        for line in lines:
            if line.startswith('# ') and not line.startswith('## '):
                # 主标题
                if current_section:
                    current_section['content'] = '\n'.join(current_content).strip()
                    sections.append(current_section)
                # 第一个 # 标题是封面，其他是章节
                section_type = "hero" if is_first_h1 else "section"
                current_section = {"type": section_type, "title": line[2:].strip(), "content": ""}
                current_content = []
                is_first_h1 = False
            elif line.startswith('## '):
                # 章节标题
                if current_section:
                    current_section['content'] = '\n'.join(current_content).strip()
                    sections.append(current_section)
                current_section = {"type": "content", "title": line[3:].strip(), "content": ""}
                current_content = []
            elif line.startswith('### '):
                # 子章节
                if current_section:
                    current_section['content'] = '\n'.join(current_content).strip()
                    sections.append(current_section)
                current_section = {"type": "content", "title": line[4:].strip(), "content": ""}
                current_content = []
            elif line.strip():
                current_content.append(line)
        
        if current_section:
            current_section['content'] = '\n'.join(current_content).strip()
            sections.append(current_section)
        
        if not sections:
            sections = [{"type": "content", "title": "内容", "content": content}]
        
        return sections

    def _markdown_to_html(self, md: str) -> str:
        """将 Markdown 转换为 HTML。"""
        if not md:
            return ""
        
        html = md
        
        # 转换列表
        html = re.sub(r'^\- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        html = re.sub(r'^\* (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        html = re.sub(r'^\d+\. (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        
        # 包装列表项
        if '<li>' in html:
            html = re.sub(r'(<li>.*</li>\n?)+', r'<ul>\g<0></ul>', html)
        
        # 转换段落（避免转换已经是HTML标签的内容）
        lines = html.split('\n')
        processed_lines = []
        for line in lines:
            if line.strip() and not line.strip().startswith('<'):
                processed_lines.append(f'<p>{line}</p>')
            else:
                processed_lines.append(line)
        html = '\n'.join(processed_lines)
        
        # 转换粗体和斜体
        html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
        html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)
        
        return html

    def _generate_slide_html(self, section: dict, style: str, index: int, total: int) -> str:
        """生成单张幻灯片 HTML。"""
        section_type = section.get('type', 'content')
        title = section.get('title', '')
        content = section.get('content', '')
        content_html = self._markdown_to_html(content)
        
        # 根据类型选择布局
        if section_type == 'hero':
            # 封面页
            return f'''
    <section class="slide hero light" data-animate="hero">
      <div class="chrome">
        <div></div>
        <div>封面</div>
      </div>
      <div class="frame center">
        <h1 class="h-hero">{title}</h1>
        <p class="lead">{content_html[:200] if content_html else ''}</p>
      </div>
      <div class="foot">
        <div></div>
        <div>{index + 1} / {total}</div>
      </div>
    </section>'''
        
        elif section_type == 'section':
            # 章节封面
            return f'''
    <section class="slide dark" data-animate="hero">
      <div class="chrome">
        <div>章节 {index + 1}</div>
        <div>{index + 1} / {total}</div>
      </div>
      <div class="frame center">
        <h2 class="h-xl">{title}</h2>
      </div>
      <div class="foot">
        <div></div>
        <div></div>
      </div>
    </section>'''
        
        else:
            # 内容页 - 使用左文右图布局
            return f'''
    <section class="slide light">
      <div class="chrome">
        <div>{title}</div>
        <div>{index + 1} / {total}</div>
      </div>
      <div class="frame grid-2-7-5">
        <div class="content-col">
          <h2 class="h-sub">{title}</h2>
          {content_html}
        </div>
        <div class="visual-col">
          <!-- 图片槽位 -->
        </div>
      </div>
      <div class="foot">
        <div></div>
        <div></div>
      </div>
    </section>'''

    @staticmethod
    def _apply_css_vars(html: str, vars_map: dict) -> str:
        """把模板 :root 里的 CSS 自定义属性改写为选定主题的值。

        真模板所有散落颜色都走 var(--xxx)，只需替换 :root 中每个 `--xxx: 值;`
        的首次定义即可整体换肤（含 rgb 变体里的逗号，用函数替换避免转义问题）。
        """
        for name, value in vars_map.items():
            pattern = re.compile(r'(--' + re.escape(name) + r'\s*:\s*)[^;]*(;)')
            html = pattern.sub(lambda m, v=value: m.group(1) + v + m.group(2), html, count=1)
        return html

    @staticmethod
    def _inline_motion(html: str) -> str:
        """把上游 motion.min.js 内联为 data: URL，消除 file:// 双击打开时的 CORS 拦截。

        模板用 `import('./assets/motion.min.js')` 加载动效引擎；但 result.html 被
        复制到 jobs 目录后，该相对路径不存在，且浏览器禁止 file:// 加载外部 ES 模块
        （CORS: origin 'null'）。motion.min.js 是自包含 ESM，base64 成 data: URL
        （file:// 允许 data:）即可让成品离线自包含、动效正常。
        """
        marker = "'./assets/motion.min.js'"
        if marker not in html:
            return html
        motion_file = SKILL_ROOT / "assets" / "motion.min.js"
        if not motion_file.exists():
            return html
        import base64
        b64 = base64.b64encode(motion_file.read_bytes()).decode("ascii")
        return html.replace(marker, f"'data:text/javascript;base64,{b64}'")

    def _generate_html(self, title: str, content: str, engine_params: dict) -> str:
        """生成 HTML 演示文稿内容（套用上游真模板 + 选定主题色）。"""
        style = (engine_params or {}).get("style", "magazine")

        # 选择模板与主题色板（逐字对齐上游 references/themes*.md）
        if style == "swiss":
            theme = (engine_params or {}).get("swiss_theme", "ikb")
            theme_vars = SWISS_THEMES.get(theme, SWISS_THEMES["ikb"])
            template_file = TEMPLATES_DIR / "template-swiss.html"
        else:
            theme = (engine_params or {}).get("magazine_theme", "monocle")
            theme_vars = MAGAZINE_THEMES.get(theme, MAGAZINE_THEMES["monocle"])
            template_file = TEMPLATES_DIR / "template.html"

        # 模板缺失时退回内置简化模板
        if not template_file.exists():
            return self._generate_fallback_html(title, content, engine_params)

        template = template_file.read_text(encoding="utf-8")

        # 解析 Markdown 为章节并生成幻灯片 HTML
        sections = self._parse_markdown_to_sections(content)
        slides_html = ""
        for i, section in enumerate(sections):
            slides_html += self._generate_slide_html(section, style, i, len(sections))

        # 注入幻灯片：匹配任意 <!-- SLIDES_HERE ... --> 注释（magazine 是干净标记，
        # swiss 的标记带说明文字且跨行，精确字符串替换会静默失败）
        html = re.sub(r'<!--\s*SLIDES_HERE.*?-->', lambda m: slides_html, template, count=1, flags=re.S)

        # 套用主题色：改写 :root 里的 CSS 自定义属性
        html = self._apply_css_vars(html, theme_vars)

        # 填充 <title>（模板里是占位文案"[必填] 替换为 PPT 标题"）
        html = re.sub(r'<title>.*?</title>', lambda m: '<title>' + title + '</title>', html, count=1, flags=re.S)
        # 兼容其它可能的标题占位写法
        html = html.replace('{{title}}', title).replace('${title}', title)

        # 内联 motion.min.js 为 data: URL，使成品离线自包含（消除 file:// CORS）
        html = self._inline_motion(html)

        # 加固 lucide 图标初始化：CDN 加载失败（离线/被墙）时不再抛 lucide is not defined
        html = html.replace('lucide.createIcons();', 'window.lucide && lucide.createIcons();')

        return html

    def _generate_fallback_html(self, title: str, content: str, engine_params: dict) -> str:
        """当模板文件不存在时，生成简化版 HTML。"""
        style = (engine_params or {}).get("style", "magazine")
        sections = self._parse_markdown_to_sections(content)
        
        slides_html = ""
        for i, section in enumerate(sections):
            slides_html += self._generate_slide_html(section, style, i, len(sections))
        
        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: sans-serif; overflow: hidden; }}
        .slide {{ width: 100vw; height: 100vh; display: flex; flex-direction: column; justify-content: center; align-items: center; padding: 5vh 10vw; }}
        .hero {{ background: #1a1a1a; color: #f5f0e8; }}
        .dark {{ background: #2c2c2c; color: #f5f0e8; }}
        .light {{ background: #f5f0e8; color: #1a1a1a; }}
        h1 {{ font-size: 3rem; margin-bottom: 1rem; }}
        h2 {{ font-size: 2rem; margin-bottom: 1rem; }}
        p {{ font-size: 1.2rem; line-height: 1.6; }}
        .presentation {{ display: flex; transition: transform 0.5s; }}
    </style>
</head>
<body>
    <div class="presentation">
        {slides_html}
    </div>
    <script>
        let current = 0;
        const slides = document.querySelectorAll('.slide');
        const pres = document.querySelector('.presentation');
        document.addEventListener('keydown', e => {{
            if (e.key === 'ArrowRight' && current < slides.length - 1) current++;
            if (e.key === 'ArrowLeft' && current > 0) current--;
            pres.style.transform = `translateX(-${{current * 100}}vw)`;
        }});
    </script>
</body>
</html>'''

    def _run_generation(self, job_id, content, title, engine_params) -> None:
        """后台线程：生成 HTML 演示文稿。"""
        try:
            time.sleep(0.5)
            
            html_content = self._generate_html(title, content, engine_params)
            
            # 保存 HTML 文件
            result_path = self._job_dir(job_id) / "result.html"
            result_path.write_text(html_content, encoding="utf-8")
            
            self._write_job(
                job_id,
                status="completed",
                result_path=str(result_path),
                updated_at=_now()
            )
        except Exception as e:
            self._write_job(
                job_id,
                status="failed",
                error=str(e),
                updated_at=_now()
            )

    def submit(self, content, title=None, pages=None, engine_params=None, source=None, job_id=None) -> JobId:
        """提交生成任务。"""
        job_id = job_id or uuid.uuid4().hex
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        
        effective_title = title or "Untitled HTML Presentation"
        
        (job_dir / "draft.md").write_text(content or "", encoding="utf-8")
        
        self._write_job(
            job_id,
            status="running",
            title=effective_title,
            pages=pages,
            engine_params=engine_params or {},
            created_at=_now(),
            updated_at=_now(),
            started_at=time.time()
        )
        
        threading.Thread(
            target=self._run_generation,
            args=(job_id, content, effective_title, engine_params),
            daemon=True
        ).start()
        
        return JobId(job_id)

    def status(self, job_id) -> JobStatus:
        """查询任务状态。"""
        data = self._read_job(job_id)
        status_str = data.get("status", "running")
        try:
            return JobStatus(status_str)
        except ValueError:
            return JobStatus.RUNNING

    def result(self, job_id) -> str:
        """取回生成的文件路径。"""
        data = self._read_job(job_id)
        result_path = data.get("result_path")
        if result_path and Path(result_path).exists():
            return result_path
        raise FileNotFoundError(f"任务 {job_id} 尚未生成产物")

    def resume(self, job_id) -> Job:
        """恢复中断的任务。"""
        data = self._read_job(job_id)
        if data.get("status") == "completed":
            return self._to_job(job_id, data)
        
        draft_path = self._job_dir(job_id) / "draft.md"
        content = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
        
        self._write_job(
            job_id,
            status="running",
            started_at=time.time(),
            updated_at=_now(),
            error=None
        )
        
        threading.Thread(
            target=self._run_generation,
            args=(job_id, content, data.get("title", ""), data.get("engine_params", {})),
            daemon=True
        ).start()
        
        return self._to_job(job_id, self._read_job(job_id))

    @staticmethod
    def _to_job(job_id, data) -> Job:
        """将 job.json 数据转换为 Job 对象。"""
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

    def get_progress(self, job_id) -> dict | None:
        """获取任务进度信息。"""
        data = self._read_job(job_id)
        if data.get("status") != "running":
            return None
        
        started_at = data.get("started_at", 0)
        elapsed = int(time.time() - started_at) if started_at else 0
        
        return {
            "stage": "generating",
            "stage_label": "正在生成 HTML 演示文稿",
            "elapsed_seconds": elapsed,
            "current_page": "result.html",
            "last_log": "guizang_ppt 引擎生成中...",
        }
