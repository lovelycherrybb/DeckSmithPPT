"""进程树终止助手：供实现了 `cancel` 钩子的引擎共用，避免三处各写一份 kill。

为什么要按进程组杀，而不是只杀直接子进程：`claude` 自己还会 fork `python3`/`node` 去跑
技能脚本（project_manager、svg_to_pptx、校验脚本…）。只杀它一个，底下会留下一整棵孤儿树
继续吃 CPU、继续写产物——ADR-019 要消灭的"超时不杀进程 = 僵尸"就是这个形态。

因此 spawn 侧必须带 `start_new_session=True`：子进程自成一组，`killpg` 才只打到本任务的
子树，不会误伤本服务自己所在的进程组（没有这一条，killpg 等于自杀）。

平台口径：POSIX（macOS 开发机 + Linux 容器）。`getpgid` 不可用或进程已不在，一律按
"已终止"返回 True——cancel 要幂等，重复调用与对不存在的进程调用都不该报错。
"""
from __future__ import annotations

import os
import signal
import subprocess
import time


def is_running(pid: int | None) -> bool:
    """该 pid 是否还在**真正执行**。

    与引擎里既有的 `_process_alive` 同一判据（`ps -p`），但多排掉一种假阳性：
    已退出、只等父进程回收的**僵尸态（Z）**不算在跑。cancel 之后子进程必然先变僵尸
    （回收由 pump 线程结束时完成），不排掉它就会白等一轮 grace。
    """
    if not pid:
        return False
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True,
        )
    except Exception:  # noqa: BLE001 - ps 不可用时宁可说"不在跑"，让上层走保守分支
        return False
    stat = proc.stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def terminate_tree(pid: int | None, grace: float = 5.0) -> bool:
    """终止 pid 所在的整个进程组：先 SIGTERM 给它收尾的机会，超时限再 SIGKILL。

    Returns:
        True  : 该 pid 及其进程组已不再执行（含"本来就不存在"，故幂等安全）。
        False : 发得出信号但没能确认它停下来的终止失败。调用方**不得**据此把任务
                判成 cancelled——撤销没成就是没成，撒谎比卡住更糟。
    """
    if not pid:
        return True  # 从未 fork 成功 = 没有需要终止的东西
    try:
        target = int(pid)
        pgid = os.getpgid(target)
    except (ProcessLookupError, ValueError):
        return True  # 已经没了
    except (AttributeError, OSError):
        return False  # 平台不支持 getpgid：不假装能杀干净

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        if sig is signal.SIGTERM:
            deadline = time.time() + grace
            while time.time() < deadline:
                if not is_running(target):
                    return True
                time.sleep(0.2)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not is_running(target):
            return True
        time.sleep(0.2)
    return False
