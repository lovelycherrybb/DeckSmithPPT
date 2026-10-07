"""端到端最小闭环：提交 → 排队 → 调度器认领 → 引擎出片 → 落终态 → 演示文件可下载。

**这条测试为什么值得存在**。此前 `tests/e2e/` 只有一个 `.gitkeep`，`ci-e2e.yml` 里
是一句 `echo "全栈选型后补充 e2e 测试"`——于是整条链上"任务真的能被跑完"这件事
**从来没有被任何一层验证过**。单测层强制 stub 且不碰调度器；契约层只看形状。
中间那段（`submit` 落库 → 调度线程认领 → `engine.submit` → 轮询 → `_finalize`
落终态 → `persist_presentation` 登记演示文件）全靠一次真跑人工看。

**为什么必须走 lifespan 的调度线程，而不是直接调 `Scheduler.sweep()`**。
`ADR-019` 的形态是"一条专职线程 + DB 为状态权威"，认领与终态收割都在里面。
手工调一拍 sweep 只能证明函数能跑，证明不了 lifespan 里那条线程真的起来了、
也证明不了关停顺序是对的。所以这里用 `TestClient(app)` 的上下文管理器进 lifespan，
让**生产同一套装配**跑起来，测试只做HTTP调用。

**隔离靠 env，不靠 monkeypatch**（原理见 tests/conftest.py 顶部）：
`DATABASE_URL` / `DECKSMITH_JOBS_ROOT` 在任何 app 模块导入之前就被钉到临时目录，
于是 `db.py` 那四个模块级单例天生就在沙箱里，调度线程也就无从打到开发库。
`DECKSMITH_FORCE_STUB=1` 让所有注册引擎落到 stub —— 不起 claude 子进程、不烧额度、
无网也能跑完。stub 的输出引擎模拟 3 秒出片，所以整条闭环是秒级。
"""
from __future__ import annotations

import time

TERMINAL = {"completed", "failed", "timeout", "cancelled"}


def _wait_terminal(client, job_id: str, *, timeout: float = 60.0) -> dict:
    """轮询到终态。超时就把最后一次看到的完整任务体抛出来，方便定位卡在哪一拍。"""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, f"查任务 {job_id} 返回 {r.status_code}：{r.text[:200]}"
        last = r.json()
        if last.get("status") in TERMINAL:
            return last
        time.sleep(0.5)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内没到终态，最后状态：{last}")


def _submit(client, payload: dict) -> dict:
    r = client.post("/api/decks", json=payload)
    assert r.status_code == 202, (
        f"提交应被受理为 202，实得 {r.status_code}：{r.text[:300]}")
    body = r.json()
    # 受理即排队，不在请求线程里 fork（ADR-019 决策 2）。这条断言守的就是这个口径：
    # 一旦有人把 fork 搬回 submit，这里立刻红。
    assert body["status"] == "queued", f"提交后应为 queued，实得 {body['status']}"
    return body


def test_草稿任务从排队到落库跑完整条闭环(client, visitor, sandbox) -> None:
    """草稿引擎任务：queued → running → completed，然后草稿实体必须出现在项目里。

    `persist_draft_entity` 是终态副作用，只有真的拿到终态写入权的那一方会做一次
    （CAS 判据）。所以"项目详情里草稿数为 1"这条断言同时覆盖了"副作用不重复"。
    """
    job = _submit(client, {
        "engine": "ppt_master_draft",
        "engine_type": "draft",
        "title": "e2e 草稿闭环",
        "pages": 2,
        "content": "# 素材\n\n- 要点一\n- 要点二",
    })
    done = _wait_terminal(client, job["job_id"])
    assert done["status"] == "completed", (
        f"草稿任务没跑成：status={done['status']} "
        f"detail={done.get('status_detail')} error={done.get('error')}")

    project_id = done["project_id"]
    assert project_id, "任务没带 project_id，自动创建项目这条链路断了"

    detail = client.get(f"/api/projects/{project_id}").json()
    drafts = [d for d in detail["drafts"] if d.get("task_id") == job["job_id"]]
    assert len(drafts) == 1, (
        f"期望该任务恰好落 1 份草稿实体，实得 {len(drafts)}（0=副作用没跑，"
        f">1=终态写入权没守住）")

    # 草稿正文的事实源是 draft_versions（`drafts.draft_path` 在这条路径上就是 null，
    # 内容按版本存）。所以这里验的是"前端真能取回那份草稿"，不是某个可能为空的列。
    draft = drafts[0]
    version = draft["current_version"]
    assert version and len(version) == 14 and version.isdigit(), (
        f"current_version 不是 14 位时间戳形态：{version!r}（与契约 pattern 同一口径）")
    v = client.get(f"/api/drafts/{draft['draft_id']}/versions/{version}")
    assert v.status_code == 200, f"取回草稿版本失败：{v.status_code} {v.text[:200]}"
    body = v.json()["content"]
    assert body.strip(), "草稿版本内容为空——任务判了 completed，内容却不在"
    assert "要点一" in body or "素材" in body, (
        f"草稿内容里找不到提交时的素材痕迹，内容链路可疑：{body[:120]!r}")

    # 产物必须落在沙箱里，不许写到仓库树（上游只读 + 镜像无状态两条纪律的共同要求）
    jobs_dir = sandbox / "jobs" / job["job_id"]
    assert jobs_dir.is_dir(), f"任务目录没落在沙箱内：{jobs_dir}"
    assert (jobs_dir / "draft.md").exists(), f"引擎产物没写进任务目录：{jobs_dir}"


def test_输出任务出片后演示文件可下载(client, visitor, sandbox) -> None:
    """输出引擎任务：完整跑到 .pptx 落盘、登记演示文件、再经下载接口取回。

    这是整条链上唯一一次"从一次 HTTP 提交到拿到可交付文件"的真实演练。
    """
    job = _submit(client, {
        "engine": "ppt_master",
        "engine_type": "output",
        "title": "e2e 出片闭环",
        "pages": 2,
        "content": "# 封面\n\n正文要点",
    })
    done = _wait_terminal(client, job["job_id"])
    assert done["status"] == "completed", (
        f"输出任务没跑成：status={done['status']} "
        f"detail={done.get('status_detail')} error={done.get('error')}")
    result_path = done.get("result_path")
    assert result_path, "completed 却没有 result_path，产物定位环节有问题"
    assert str(sandbox) in str(result_path), f"产物不在沙箱内：{result_path}"

    detail = client.get(f"/api/projects/{done['project_id']}").json()
    pres = [p for p in detail["presentations"] if p["task_id"] == job["job_id"]]
    assert len(pres) == 1, f"期望登记 1 个演示文件，实得 {len(pres)}"
    assert pres[0]["file_format"] == "pptx"

    r = client.get(f"/api/presentations/{pres[0]['presentation_id']}/download")
    assert r.status_code == 200, f"下载失败：{r.status_code} {r.text[:200]}"
    assert r.content[:2] == b"PK", "下载到的不是 zip 容器，.pptx 是坏的"
    assert len(r.content) > 1024, f"下载内容过小（{len(r.content)} 字节），像是空文件"


def test_未登录访客看不到别人的任务(client, visitor) -> None:
    """归属隔离的端到端一瞥：换一个访客身份，前面那些任务应当不可见。

    单独一个 TestClient 会话 = 另一份 `ds_visitor` Cookie。用同一 client 做不到，
    因为 Cookie 会被沿用。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as other:
        other.get("/api/auth/me")                      # 领一份新的访客令牌
        me = other.get("/api/auth/me").json()
        assert me["authenticated"] is False, "新会话不该是已登录态"
        mine = other.get("/api/jobs").json()
        assert mine == [], (
            f"新访客看到了 {len(mine)} 条不属于自己的任务，归属过滤失效："
            f"{[j['job_id'] for j in mine][:5]}")
