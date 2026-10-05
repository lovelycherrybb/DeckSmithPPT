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


#: 结构合法的最小产物写入片段（ADR-021 结构守卫要求：骨架三件套各 1 次 +
#: ≥1 完整闭合页 + 无 SLIDES_HERE 残留 + </html> 收尾 + 过 2048B 下限）。
#: 本文件的用例测的是状态机 / 续跑 / 计量，不是产物结构，所以夹具必须能过守卫——
#: 原先那种 `<div class="slide">` 拼出来的假 HTML 在真实世界里根本翻不动，
#: 守卫拦下它是正确的，不能为了迁就旧夹具而放松判据。
_VALID_PRODUCT_SH = (
    'printf \'<!doctype html><html><body><div id="hint"></div><div id="deck">'
    '<section class="slide light" data-slide-id="cover"><div>cover</div></section>'
    '</div><div id="nav"></div><!-- 演讲备注 -->\' > "$result"\n'
    'i=0; while [ $i -lt 200 ]; do '
    'printf \'<p>aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</p>\' >> "$result"; '
    'i=$((i+1)); done\n'
    'printf \'</body></html>\' >> "$result"\n'
)


def _wait_terminal(eng, job_id: str, budget_s: float = 90.0) -> str:
    """轮询到终态并返回状态值；超时则把 job 记录与 run.log 末尾一起抛出。

    为什么不写死 `for _ in range(N)`：CI runner 上“产物未写出 → 自动 --resume 续跑”这条链
    实测比本机慢一个量级（本机 0.9s，云端 30s 预算内没跑完），固定次数会把环境差异
    伪装成断言失败。这里按墙上时间等，且失败时把原始证据抛出来，日志能自解释。
    """
    deadline = time.monotonic() + budget_s
    status = eng.status(job_id).value
    while status == "running" and time.monotonic() < deadline:
        time.sleep(0.1)
        status = eng.status(job_id).value
    if status == "running":
        data = eng._read_job(job_id)  # noqa: SLF001
        try:
            tail = (eng._job_dir(job_id) / "run.log").read_text(encoding="utf-8")[-800:]  # noqa: SLF001
        except OSError:
            tail = "(无 run.log)"
        raise AssertionError(
            f"{budget_s}s 内未到终态，仍为 running。job 记录={data}；run.log 末尾={tail!r}"
        )
    return status


@pytest.fixture
def engine_factory(tmp_path):
    jobs_root = tmp_path / "jobs"
    made = []

    def _make(**kw):
        eng = GuizangPptEngine(jobs_root=jobs_root, **kw)
        made.append(eng)
        return eng

    return _make


# ── 逐页预览：解析真实页（含注释假页与半截页干扰） ──
def test_逐页预览解析真实页并点亮能力位(engine_factory):
    eng = engine_factory()
    job_id = "pg01"
    job_dir = eng._job_dir(job_id)  # noqa: SLF001
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "result.html").write_text(
        "<!doctype html><html><head><title>t</title></head><body>"
        '<!-- SLIDES_HERE · 在此处粘贴 <section class="slide ..."> 页面块 -->'
        '<section class="slide hero dark" data-slide-id="cover"><div><section class="x"></section></div></section>'
        '<section class="slide light" data-slide-id="body"></section>'
        '<section class="slide" data-slide-id="tail"><div>写到一半'  # 未闭合，不该入墙
        "</body></html>",
        encoding="utf-8",
    )

    pages = eng.get_pages(job_id)
    assert [p["index"] for p in pages] == [1, 2]
    assert [p["name"] for p in pages] == ["01_cover.html", "02_body.html"]
    assert all(p["kind"] == "html" for p in pages)
    assert all(Path(p["path"]).name == "result.html" for p in pages)
    assert eng.get_preview_root(job_id) == job_dir.resolve()

    # 无产物时返回空页而非报错（产物被清理 / 尚未写出的中间态）
    assert eng.get_pages("nonexistent") == []
    assert eng.get_preview_root("nonexistent") is None

    # 能力位由钩子实现自动派生（不按引擎名硬编码）
    import sys

    backend_src = Path(__file__).resolve().parents[2] / "backend" / "src"
    sys.path.insert(0, str(backend_src))
    from app.engines.registry import ENGINE_REGISTRY

    assert "page_preview" in ENGINE_REGISTRY["guizang_ppt"]["capabilities"]


def test_svg_edit_能力位由_revise_钩子派生():
    """ADR-023：只有实现 revise 钩子的引擎才点亮 svg_edit（鸭子类型探测，非按引擎名）。

    当前没有任何引擎实现 revise（引擎侧改稿重导出属步骤 3b，需一次真跑验证），
    所以真实注册表里 guizang 有 page_preview 但没有 svg_edit —— 这条同时守住"不误标"。
    """
    import sys
    from pathlib import Path

    backend_src = Path(__file__).resolve().parents[2] / "backend" / "src"
    sys.path.insert(0, str(backend_src))
    from app.engines.registry import implements_hook, REVISE_HOOK, ENGINE_REGISTRY

    class WithRevise:  # 真实实现了 revise
        def revise(self, job_id, **kw):  # noqa: ANN001, D102
            return job_id

    class WithoutRevise:  # 没实现
        pass

    class ExitsRevise:  # 显式退出（草稿类引擎的形态）
        revise = None

    assert implements_hook(WithRevise, REVISE_HOOK) is True
    assert implements_hook(WithoutRevise, REVISE_HOOK) is False
    assert implements_hook(ExitsRevise, REVISE_HOOK) is False
    assert implements_hook(WithRevise(), REVISE_HOOK) is True  # 实例形态同样探测

    # guizang 未实现 revise → 不该被误标 svg_edit（但仍有 page_preview）
    guiz = ENGINE_REGISTRY["guizang_ppt"]["capabilities"]
    assert "page_preview" in guiz
    assert "svg_edit" not in guiz


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
        + _VALID_PRODUCT_SH +
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

    _wait_terminal(eng, job_id)

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
    _wait_terminal(eng, job_id)
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
    _wait_terminal(eng, job_id)
    assert eng.status(job_id).value == "failed"


# ── 超时：看门狗终止并置 timeout ──
def test_超时置timeout(engine_factory, tmp_path):
    eng = engine_factory(
        claude_bin=_fake_claude(tmp_path, "sleep 30"),
        timeout_seconds=1,
    )
    job_id = eng.submit(content="# x", title="t", pages=5, engine_params={})
    _wait_terminal(eng, job_id)
    assert eng.status(job_id).value == "timeout"
    assert "超时" in eng._read_job(job_id)["error"]  # noqa: SLF001


# ── claude 不存在：明确失败并提示装 Claude Code，不静默降级 ──
def test_缺claude_CLI时明确失败(engine_factory):
    eng = engine_factory(claude_bin="/nonexistent/claude")
    job_id = eng.submit(content="# x", title="t", pages=5, engine_params={})
    assert eng.status(job_id).value == "failed"
    assert "claude" in eng._read_job(job_id)["error"]  # noqa: SLF001


# ── get_progress：页数与页墙同源，且不预测、不编造 ──
def test_进度页数与页墙同源(engine_factory, tmp_path):
    eng = engine_factory(
        claude_bin=_fake_claude(tmp_path, "echo '{\"type\":\"stream_event\",\"event\":{\"delta\":{\"type\":\"text_delta\",\"text\":\"hi\"}}}'; sleep 5"),
    )
    job_id = eng.submit(content="# x", title="t", pages=5, engine_params={"style": "swiss"})
    prog = eng.get_progress(job_id)
    assert prog and prog["stage"] == "generating"
    assert "swiss" in prog["stage_label"]
    assert prog["last_log"]
    # 产物还没落盘：如实 0 页，不预测目标页数、不塞 pending 占位
    assert prog["pages_generated"] == 0 and prog["current_page"] is None

    # 写出 3 个已闭合页后，进度数字必须等于页墙长度（同一读盘事实源）
    body = "".join(f'<section class="slide" data-slide-id="s{i}">x</section>' for i in range(1, 4))
    (eng._job_dir(job_id) / "result.html").write_text(  # noqa: SLF001
        f"<html><head></head><body>{body}</body></html>", encoding="utf-8"
    )
    prog = eng.get_progress(job_id)
    assert prog["pages_generated"] == len(eng.get_pages(job_id)) == 3
    assert prog["current_page"] == "03_s3.html"

    eng._write_job(job_id, status="completed")  # noqa: SLF001
    assert eng.get_progress(job_id) is None


# ── resume：沿用原 draft.md 与参数，job_id 不变 ──
def test_resume复用草稿与job_id(engine_factory, tmp_path):
    eng = engine_factory(
        claude_bin=_fake_claude(
            tmp_path,
            "prompt=\"$PROMPT\"\n"
            "result=$(printf '%s' \"$prompt\" | grep -o '/[^ ]*result\\.html' | head -1)\n"
            + _VALID_PRODUCT_SH,
        )
    )
    job_id = eng.submit(content="# 原始草稿\n内容", title="恢复测试", pages=6,
                        engine_params={"style": "magazine"})
    eng._write_job(job_id, status="failed", result_path=None)  # noqa: SLF001
    job = eng.resume(job_id)
    assert str(job.job_id) == str(job_id)
    _wait_terminal(eng, job_id)
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
    _wait_terminal(eng, job_id)

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
        + _VALID_PRODUCT_SH +
        'printf \'{"type":"result","subtype":"success","is_error":false}\\n\'\n'
    )
    eng = engine_factory(claude_bin=_fake_claude(tmp_path, script), timeout_seconds=120)
    job_id = eng.submit(content="# x\n正文", title="续跑测试", pages=12,
                        engine_params={"style": "swiss"})
    _wait_terminal(eng, job_id)

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


# ── 断点续跑的 token 消耗：两个 result 分项累加、不覆盖（ADR-016 唯一的算错风险位）──
def test_resume续跑的token消耗按分项累加(engine_factory, tmp_path):
    script = (
        'resume=0\n'
        'for a in "$@"; do [ "$a" = "--resume" ] && resume=1; done\n'
        'printf \'{"type":"system","subtype":"init","session_id":"sess-u1"}\\n\'\n'
        'if [ "$resume" -eq 0 ]; then\n'
        '  printf \'{"type":"result","subtype":"success","is_error":false,'
        '"usage":{"input_tokens":100,"cache_creation_input_tokens":20,'
        '"cache_read_input_tokens":300,"output_tokens":40}}\\n\'\n'
        '  exit 0\n'
        'fi\n'
        'result=$(printf \'%s\' "$PROMPT" | grep -o \'/[^ ]*result\\.html\' | head -1)\n'
        + _VALID_PRODUCT_SH +
        'printf \'{"type":"result","subtype":"success","is_error":false,'
        '"usage":{"input_tokens":5,"cache_creation_input_tokens":null,'
        '"cache_read_input_tokens":800,"output_tokens":10}}\\n\'\n'
    )
    eng = engine_factory(claude_bin=_fake_claude(tmp_path, script), timeout_seconds=120)
    job_id = eng.submit(content="# x\n正文", title="用量累加", pages=12,
                        engine_params={"style": "swiss"})
    _wait_terminal(eng, job_id)

    assert eng.status(job_id).value == "completed"
    u = eng._read_job(job_id)["token_usage"]  # noqa: SLF001
    # 首跑 100/20/300/40 + 续跑 5/null/800/10：null 分项不覆盖已知值，已知分项相加
    assert u["input_tokens"] == 105
    assert u["cache_creation_input_tokens"] == 20
    assert u["cache_read_input_tokens"] == 1100
    assert u["output_tokens"] == 50
    assert u["total_tokens"] == 105 + 20 + 1100 + 50
    # 引擎对外 Job 也带出同一份账（后端收割走的就是它）
    assert eng._to_job(job_id, eng._read_job(job_id)).token_usage == u  # noqa: SLF001


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
