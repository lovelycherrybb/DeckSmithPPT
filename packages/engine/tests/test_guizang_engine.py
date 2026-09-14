"""guizang_ppt 引擎单测：验证「驱动 claude 执行技能」的运行时契约。

全部用假 claude 可执行脚本驱动，不发真实网络请求、不依赖 Anthropic 凭证，
因此可在 DECKSMITH_FORCE_STUB 环境下安全运行。
"""

from __future__ import annotations

import stat
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/ 供 import engine

from engine.impl.guizang_ppt.engine import (  # noqa: E402
    GuizangPptEngine,
    _SKILL_DIR,
    ensure_skill_installed,
)


def _fake_claude(tmp_path: Path, body: str, name: str = "claude") -> str:
    """生成一个假 claude CLI。

    引擎真实 argv 形态：claude -p --dangerously-skip-permissions --output-format
    stream-json --include-partial-messages --verbose <prompt>，即 prompt 是**最后一个**参数。
    body 里可直接用 "$PROMPT"（POSIX sh 取末位参数）。
    """
    script = tmp_path / name
    script.write_text(
        "#!/bin/sh\n"
        'PROMPT=""\n'
        'for a in "$@"; do PROMPT="$a"; done\n'
        'export PROMPT\n'
        f"{body}\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


@pytest.fixture(autouse=True)
def _isolate_claude_config(tmp_path, monkeypatch):
    """submit() 会 best-effort 建技能软链：把它指向用例专属沙箱，绝不写真实 ~/.claude。"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


@pytest.fixture
def engine_factory(tmp_path):
    jobs_root = tmp_path / "jobs"
    made = []

    def _make(**kw):
        eng = GuizangPptEngine(jobs_root=jobs_root, **kw)
        made.append(eng)
        return eng

    return _make


# ── 运行时形态：必须是子进程驱动，且不得复活逐页预览能力 ──
def test_不实现_get_pages_能力位不点亮():
    from engine.core.protocol import OutputEngine

    assert not hasattr(GuizangPptEngine, "get_pages") or "get_pages" not in vars(GuizangPptEngine)
    assert "get_preview_root" not in vars(GuizangPptEngine)
    # Protocol 上的占位定义存在，但不算实现（registry.implements_hook 语义）
    assert "get_pages" in vars(OutputEngine)


def test_引擎不再自带模板副本():
    assert not (Path(__file__).resolve().parents[1] / "impl" / "guizang_ppt" / "templates").exists()
    # 唯一模板事实源是上游技能目录
    assert (_SKILL_DIR / "assets" / "template.html").is_file()


# ── prompt 构建：技能路径、产物路径、禁反问，三者必须在文 ──
def test_prompt_钉死技能与产物路径并禁止反问(engine_factory, tmp_path):
    eng = engine_factory()
    template_file = eng._template_file("swiss")  # noqa: SLF001
    prompt = eng._build_prompt(  # noqa: SLF001
        title="测试标题", style="swiss", theme_desc="ikb（克莱因蓝）", pages=12,
        draft="# 草稿\n正文", job_dir=tmp_path / "job", result_file=tmp_path / "job" / "result.html",
        template_file=template_file,
    )
    assert str(_SKILL_DIR) in prompt
    assert "result.html" in prompt
    assert "无人值守" in prompt and "跳过技能里的澄清提问" in prompt
    assert "禁止修改其中任何文件" in prompt
    assert "瑞士国际主义" in prompt and "ikb" in prompt
    # 产出协议：先复制骨架、再分批 Edit，禁止一次性 Write 整个产物（防网关掐断长输出）
    assert f'cp "{template_file}"' in prompt
    assert "SLIDES_HERE" in prompt and "SPEAKER_NOTES" in prompt
    assert "禁止" in prompt and "一次性 Write 整个产物" in prompt
    assert "我现在开始创建" in prompt  # 防“只说要做就结束回复”
    # 骨架按风格选对文件
    assert eng._template_file("magazine").name == "template.html"  # noqa: SLF001
    assert template_file.name == "template-swiss.html"


# ── 提交 → 完成：产物存在即 completed，路径为钉死的 result.html ──
def test_成功产出单文件HTML(engine_factory, tmp_path):
    # wrapper 从 prompt 里解析引擎钉死的产物绝对路径，模拟 agent 真的写出单文件 HTML
    wrapper = tmp_path / "claude"
    wrapper.write_text(
        "#!/bin/sh\n"
        'PROMPT=""\n'
        'for a in "$@"; do PROMPT="$a"; done\n'
        "result=$(printf '%s' \"$PROMPT\" | grep -o '/[^ ]*result\\.html' | head -1)\n"
        "printf '{\"type\":\"stream_event\",\"event\":{\"delta\":{\"type\":\"text_delta\",\"text\":\"正在排版\"}}}\\n'\n"
        "printf '{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"tool_use\",\"name\":\"Write\",\"input\":{\"file_path\":\"%s\"}}]}}\\n' \"$result\"\n"
        "printf '<!doctype html><html><body>' > \"$result\"\n"
        "i=0; while [ $i -lt 300 ]; do printf '<div>slide-%d</div>' \"$i\" >> \"$result\"; i=$((i+1)); done\n"
        "printf '</body></html>' >> \"$result\"\n"
        "printf '{\"type\":\"result\",\"subtype\":\"success\"}\\n'\n",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    eng = engine_factory(claude_bin=str(wrapper))

    job_id = eng.submit(
        content="# 标题\n\n第一段\n\n## 第二节\n内容",
        title="季度复盘",
        pages=8,
        engine_params={"style": "magazine", "magazine_theme": "indigo"},
    )
    data = eng._read_job(job_id)  # noqa: SLF001
    assert data["status"] == "running"
    assert data["started_at"]

    for _ in range(100):
        if eng.status(job_id).value != "running":
            break
        time.sleep(0.1)

    assert eng.status(job_id).value == "completed"
    result = Path(eng.result(job_id))
    assert result.name == "result.html"
    assert result.parent == eng._job_dir(job_id)  # noqa: SLF001
    assert result.read_text(encoding="utf-8").startswith("<!doctype html>")

    log = (eng._job_dir(job_id) / "run.log").read_text(encoding="utf-8")  # noqa: SLF001
    assert "正在排版" in log and "⚙ Write" in log and "✅ 技能执行完成" in log
    # 审计文件齐备
    assert (eng._job_dir(job_id) / "prompt.md").exists()  # noqa: SLF001
    assert (eng._job_dir(job_id) / "draft.md").read_text(encoding="utf-8").startswith("# 标题")  # noqa: SLF001
    # 正常完成：未触发续跑、租约已释放
    done = eng._read_job(job_id)  # noqa: SLF001
    assert done["resume_attempt"] == 0 and done["supervisor_pid"] is None


# ── 失败：子进程退出但没写产物 → failed，且错误里带真实日志尾行 ──
def test_无产物则判失败(engine_factory, tmp_path):
    eng = engine_factory(
        claude_bin=_fake_claude(tmp_path, 'echo \'{"type":"result","subtype":"error_during_execution","result":"模型限流"}\'')
    )
    job_id = eng.submit(content="# x\n正文", title="t", pages=5, engine_params={})
    for _ in range(100):
        if eng.status(job_id).value != "running":
            break
        time.sleep(0.1)
    assert eng.status(job_id).value == "failed"
    data = eng._read_job(job_id)  # noqa: SLF001
    assert "error_during_execution" in data["error"] and "模型限流" in data["error"]
    with pytest.raises(FileNotFoundError):
        eng.result(job_id)


# ── 小文件（截断/占位）不算成功 ──
def test_过小产物不判成功(engine_factory, tmp_path):
    eng = engine_factory(
        claude_bin=_fake_claude(
            tmp_path,
            "prompt=\"$PROMPT\"\n"
            "result=$(printf '%s' \"$prompt\" | grep -o '/[^ ]*result\\.html' | head -1)\n"
            "printf '<html>' > \"$result\"",
        )
    )
    job_id = eng.submit(content="# x", title="t", pages=5, engine_params={})
    for _ in range(100):
        if eng.status(job_id).value != "running":
            break
        time.sleep(0.1)
    assert eng.status(job_id).value == "failed"


# ── 超时：看门狗终止并置 timeout ──
def test_超时置timeout(engine_factory, tmp_path):
    eng = engine_factory(
        claude_bin=_fake_claude(tmp_path, "sleep 30"),
        timeout_seconds=1,
    )
    job_id = eng.submit(content="# x", title="t", pages=5, engine_params={})
    for _ in range(200):
        if eng.status(job_id).value != "running":
            break
        time.sleep(0.1)
    assert eng.status(job_id).value == "timeout"
    assert "超时" in eng._read_job(job_id)["error"]  # noqa: SLF001


# ── claude 不存在：明确失败并提示装 Claude Code，不静默降级 ──
def test_缺claude_CLI时明确失败(engine_factory):
    eng = engine_factory(claude_bin="/nonexistent/claude")
    job_id = eng.submit(content="# x", title="t", pages=5, engine_params={})
    assert eng.status(job_id).value == "failed"
    assert "claude" in eng._read_job(job_id)["error"]  # noqa: SLF001


# ── get_progress：只报真实信息，不编造页数 ──
def test_进度不编造页数(engine_factory, tmp_path):
    eng = engine_factory(
        claude_bin=_fake_claude(tmp_path, "echo '{\"type\":\"stream_event\",\"event\":{\"delta\":{\"type\":\"text_delta\",\"text\":\"hi\"}}}'; sleep 5"),
    )
    job_id = eng.submit(content="# x", title="t", pages=5, engine_params={"style": "swiss"})
    prog = eng.get_progress(job_id)
    assert prog and prog["stage"] == "generating"
    assert "swiss" in prog["stage_label"]
    assert "pages_generated" not in prog and "current_page" not in prog
    assert prog["last_log"]
    eng._write_job(job_id, status="completed")  # noqa: SLF001
    assert eng.get_progress(job_id) is None


# ── resume：沿用原 draft.md 与参数，job_id 不变 ──
def test_resume复用草稿与job_id(engine_factory, tmp_path):
    eng = engine_factory(
        claude_bin=_fake_claude(
            tmp_path,
            "prompt=\"$PROMPT\"\n"
            "result=$(printf '%s' \"$prompt\" | grep -o '/[^ ]*result\\.html' | head -1)\n"
            "i=0; while [ $i -lt 300 ]; do printf '<div>aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</div>' >> \"$result\"; i=$((i+1)); done\n",
        )
    )
    job_id = eng.submit(content="# 原始草稿\n内容", title="恢复测试", pages=6,
                        engine_params={"style": "magazine"})
    eng._write_job(job_id, status="failed", result_path=None)  # noqa: SLF001
    job = eng.resume(job_id)
    assert str(job.job_id) == str(job_id)
    for _ in range(200):
        if eng.status(job_id).value != "running":
            break
        time.sleep(0.1)
    assert eng.status(job_id).value == "completed"
    data = eng._read_job(job_id)  # noqa: SLF001
    assert data["title"] == "恢复测试" and data["pages"] == 6
    assert data["engine_params"]["style"] == "magazine"


# ── 断流诊断：result 报 success 也不得掩盖网关掐断（实测失败形态） ──
def test_网关断流不被成功假象掩盖(engine_factory, tmp_path):
    script = (
        'printf \'{"type":"system","subtype":"init","session_id":"sess-abc123"}\\n\'\n'
        'printf \'{"type":"stream_event","event":{"delta":{"type":"text_delta",'
        '"text":"API Error: terminated"}}}\\n\'\n'
        'printf \'{"type":"result","subtype":"success","is_error":false,'
        '"num_turns":31,"result":"Now I will create the presentation"}\\n\'\n'
    )
    eng = engine_factory(claude_bin=_fake_claude(tmp_path, script), max_resume_attempts=0)
    job_id = eng.submit(content="# x\n正文", title="t", pages=12, engine_params={"style": "swiss"})
    for _ in range(200):
        if eng.status(job_id).value != "running":
            break
        time.sleep(0.1)

    assert eng.status(job_id).value == "failed"
    data = eng._read_job(job_id)  # noqa: SLF001
    assert "上游模型连接被中断" in data["error"] and "terminated" in data["error"]
    # 会话号与 result 原文都被留档，可供续跑与复盘
    assert data["claude_session_id"] == "sess-abc123"
    assert data["claude_result"]["num_turns"] == 31
    assert data["supervisor_pid"] is None  # 终态时释放租约


# ── 断点续跑：产物未写出时用 --resume 续一轮，成功即 completed ──
def test_无产物时自动resume续跑并成功(engine_factory, tmp_path):
    script = (
        'resume=0\n'
        'for a in "$@"; do [ "$a" = "--resume" ] && resume=1; done\n'
        'printf \'{"type":"system","subtype":"init","session_id":"sess-keep1"}\\n\'\n'
        'if [ "$resume" -eq 0 ]; then\n'
        '  printf \'{"type":"stream_event","event":{"delta":{"type":"text_delta",'
        '"text":"API Error: terminated"}}}\\n\'\n'
        '  printf \'{"type":"result","subtype":"success","is_error":false}\\n\'\n'
        '  exit 0\n'
        'fi\n'
        'result=$(printf \'%s\' "$PROMPT" | grep -o \'/[^ ]*result\\.html\' | head -1)\n'
        'i=0; while [ $i -lt 300 ]; do '
        'printf \'<div class="slide">aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</div>\' >> "$result"; '
        'i=$((i+1)); done\n'
        'printf \'{"type":"result","subtype":"success","is_error":false}\\n\'\n'
    )
    eng = engine_factory(claude_bin=_fake_claude(tmp_path, script), timeout_seconds=120)
    job_id = eng.submit(content="# x\n正文", title="续跑测试", pages=12,
                        engine_params={"style": "swiss"})
    for _ in range(300):
        if eng.status(job_id).value != "running":
            break
        time.sleep(0.1)

    assert eng.status(job_id).value == "completed"
    data = eng._read_job(job_id)  # noqa: SLF001
    assert data["resume_attempt"] == 1
    assert data["claude_session_id"] == "sess-keep1"
    assert data["result_path"].endswith("result.html")
    log = (eng._job_dir(job_id) / "run.log").read_text(encoding="utf-8")  # noqa: SLF001
    assert "↻ 产物未写出" in log
    resume_prompt = eng._job_dir(job_id) / "prompt-resume-1.md"  # noqa: SLF001
    assert resume_prompt.is_file()
    rp = resume_prompt.read_text(encoding="utf-8")  # noqa: SLF001
    assert "result.html" in rp and "不要重新规划" in rp and "禁止一次性 Write 整个产物" in rp


# ── 租约失效（后端重启、supervisor 线程已死）：status 自行派生终态，不永远 running ──
def test_租约失效时status自行判失败(engine_factory, tmp_path):
    eng = engine_factory(claude_bin=_fake_claude(tmp_path, "echo 'no session here'"))
    job_id = eng.submit(content="# x", title="t", pages=5, engine_params={})
    eng._write_job(job_id, pid=999999, supervisor_pid=999999, claude_session_id=None)  # noqa: SLF001
    assert eng.status(job_id).value == "failed"
    assert "未写出有效产物" in eng._read_job(job_id)["error"]  # noqa: SLF001


# ── 技能发现：软链到 CLAUDE_CONFIG_DIR/skills，且不写上游 ──
def test_技能软链幂等且不污染上游(tmp_path, monkeypatch):
    cfg = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    link = cfg / "skills" / _SKILL_DIR.name

    assert ensure_skill_installed() is None
    assert link.is_symlink() and link.resolve() == _SKILL_DIR.resolve()
    assert ensure_skill_installed() is None  # 幂等
    assert link.is_symlink()

    # 已存在独立副本时不覆盖，只提示版本可能漂移
    link.unlink()
    link.mkdir(parents=True)
    (link / "SKILL.md").write_text("mine", encoding="utf-8")
    notice = ensure_skill_installed()
    assert notice and "独立副本" in notice
    assert (link / "SKILL.md").read_text(encoding="utf-8") == "mine"

    # 上游目录本身未被写入（AGENTS 规则 4：上游只读）
    assert not (_SKILL_DIR / "result.html").exists()


# ── 后端工厂：FORCE_STUB 时 guizang 不再拉起真引擎 ──
def test_FORCE_STUB下guizang不落真引擎(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKSMITH_FORCE_STUB", "1")
    backend_src = Path(__file__).resolve().parents[2] / "backend" / "src"
    sys.path.insert(0, str(backend_src))
    from app.engines.factory import get_engine  # noqa: E402
    from app.engines.stub import StubPptMasterEngine  # noqa: E402

    assert isinstance(get_engine("guizang_ppt"), StubPptMasterEngine)
    assert isinstance(get_engine("ppt_master"), StubPptMasterEngine)
    with pytest.raises(ValueError):
        get_engine("no_such_engine")
