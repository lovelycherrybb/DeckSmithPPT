"""升级闸口的契约测试：停服前必须能证明"没有在跑的任务"，证明不了就不许停。

为什么放在契约层：`docker compose down` 会当场打断正在生成的任务且不可恢复，
这条判据（哪些状态算"未结束"）与契约里的 JobStatus 是同一个对外承诺，
两处各写一份迟早对不上。核的是四件事，每件都能失败：

1. 脚本里那份终态清单与 docs/contracts/openapi.yaml 的 JobStatus 完全一致
   （将来契约加一个非终态状态，这条必须报红）；
2. 查到有未结束任务 → 退出码 1，且**没有**执行 compose down；
3. 查不到任务状态（服务没跑 / 库读不到）→ 按"没能比"处理：退出码 2，同样不许 down；
4. --force-busy 才允许跳过这道闸，且跳过时打印警告。

另附发版脚本一条：docker 回报 x86_64 时目标平台必须归一化成 linux/amd64
（2026-10-26 出 0.6.3 时在那台 x86 构建机实测撞到，linux/x86_64 会让构建中途 EOF）。

全部跑在临时沙箱 + 假 docker 上，不碰真机、不碰真容器。
"""
import gzip
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UPGRADE = ROOT / "scripts" / "upgrade.sh"
RELEASE = ROOT / "scripts" / "release.sh"
OPENAPI = ROOT / "docs" / "contracts" / "openapi.yaml"

TERMINAL_SQL = re.compile(
    r'where status not in \((?P<list>[^)]*)\)', re.S)


def contract_terminal_statuses() -> set[str]:
    """契约 JobStatus 枚举里，不属于 queued/running 的那些。"""
    text = OPENAPI.read_text(encoding="utf-8")
    m = re.search(r"JobStatus:.*?enum:\n((?:\s*-\s+\S+\n)+)", text, re.S)
    assert m, "openapi.yaml 里找不到 JobStatus 枚举——本测试的前提没了，请同步修订"
    items = re.findall(r"^\s*-\s+(\S+)\s*$", m.group(1), re.M)
    assert items, "JobStatus 枚举为空"
    return {s for s in items if s not in {"queued", "running"}}


def script_terminal_statuses() -> set[str]:
    text = UPGRADE.read_text(encoding="utf-8")
    m = TERMINAL_SQL.search(text)
    assert m, "upgrade.sh 里找不到那份终态清单（SQL 写法变了？请同步修订本测试）"
    return set(re.findall(r'"([^"]+)"', m.group("list")))


FAKE_DOCKER = """#!/usr/bin/env bash
# 假 docker：只记录被调用了什么，并按 FAKE_* 环境变量决定回话
printf '%s\\n' "docker $*" >> "${CALL_LOG}"
case "$1" in
  info) echo "${FAKE_DOCKER_ARCH:-x86_64}"; exit 0 ;;
  version) echo "9.9.9"; exit 0 ;;
  buildx) exit 0 ;;
  image) exit 0 ;;
  system) echo "SIZE"; exit 0 ;;
  compose)
    case "$2" in
      exec)
        case "${FAKE_BUSY:-idle}" in
          idle)    echo '{"ok": true, "busy": ""}'; exit 0 ;;
          busy)    echo '{"ok": true, "busy": "running=1 queued=2"}'; exit 0 ;;
          unknown) echo ''; exit 1 ;;
        esac ;;
      *) exit 0 ;;
    esac ;;
esac
exit 0
"""


def make_sandbox(tmp_path: Path, *, with_ops_tree: bool = False) -> Path:
    """造一棵最小可跑的树：scripts/upgrade.sh + deploy/{docker-compose.yml,.env} + 假 docker。"""
    root = tmp_path / "sandbox"
    (root / "scripts").mkdir(parents=True)
    (root / "deploy").mkdir(parents=True)
    shutil.copy(UPGRADE, root / "scripts" / "upgrade.sh")
    (root / "deploy" / "docker-compose.yml").write_text(
        "services:\n  app:\n    image: decksmith/app:${IMAGE_TAG:?}\n"
        "  web:\n    image: decksmith/web:${IMAGE_TAG:?}\n", encoding="utf-8")
    (root / "deploy" / ".env").write_text("IMAGE_TAG=0.0.1\nWEB_PORT=3000\n", encoding="utf-8")
    (root / "deploy" / ".env.example").write_text("IMAGE_TAG=0.0.1\n", encoding="utf-8")
    # 离线导入这条路要有包可导，否则脚本会转去要源码树（沙箱里刻意不给）
    tar_dir = tmp_path / "tar"
    tar_dir.mkdir(parents=True, exist_ok=True)
    for svc in ("app", "web"):
        with gzip.open(tar_dir / f"decksmith-{svc}-0.0.2-linux-amd64.tar.gz", "wb") as fh:
            fh.write(b"placeholder")
    (root / "packages" / "backend").mkdir(parents=True)
    (root / "packages" / "backend" / "Dockerfile").write_text("FROM x\n", encoding="utf-8")
    (root / "packages" / "frontend").mkdir(parents=True)
    (root / "packages" / "frontend" / "Dockerfile").write_text("FROM x\n", encoding="utf-8")
    if with_ops_tree:
        shutil.copy(RELEASE, root / "scripts" / "release.sh")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "seed"], cwd=root, check=True)
        for probe in ("lib/ppt-master/AGENTS.md",
                      "lib/ppt-master/skills/ppt-master/SKILL.md",
                      "lib/guizang-ppt-skill/SKILL.md"):
            p = root / probe
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# probe\n", encoding="utf-8")
    return root


FAKE_CURL = """#!/usr/bin/env bash
printf '%s\\n' "curl $*" >> "${CALL_LOG}"
exit 0
"""

FAKE_UNAME = """#!/usr/bin/env bash
echo "${FAKE_UNAME_M:-x86_64}"
"""


def make_bin(tmp_path: Path, *, uname_m: str | None = None) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
        f = bindir / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)
    if uname_m is not None:
        f = bindir / "uname"
        f.write_text(FAKE_UNAME, encoding="utf-8")
        f.chmod(0o755)
    return bindir


def run_upgrade(root: Path, tmp_path: Path, busy: str, *args: str) -> subprocess.CompletedProcess:
    bindir = make_bin(tmp_path)
    call_log = tmp_path / "calls.log"
    call_log.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "CALL_LOG": str(call_log),
        "FAKE_BUSY": busy,
    }
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "upgrade.sh"), *args],
        cwd=root, capture_output=True, text=True, timeout=120, env=env,
    )
    proc.calls = call_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    return proc


def tar_dir_arg(tmp_path: Path) -> str:
    return str(tmp_path / "tar")


def test_脚本终态清单与契约一致():
    assert script_terminal_statuses() == contract_terminal_statuses(), (
        "upgrade.sh 认的终态与契约 JobStatus 对不上——判错方向就是把在跑的任务当空闲")


def test_有任务在跑时拒绝停服(tmp_path):
    root = make_sandbox(tmp_path)
    proc = run_upgrade(root, tmp_path, "busy", "0.0.2", tar_dir_arg(tmp_path))
    assert proc.returncode == 1, f"应判红退出码 1，实得 {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert "running=1" in proc.stderr, "报红时必须说清是哪几个状态在跑"
    assert "compose down" not in proc.calls, "拒绝升级却仍然停了服务——闸口形同虚设"


def test_查不到状态按没能比处理(tmp_path):
    root = make_sandbox(tmp_path)
    proc = run_upgrade(root, tmp_path, "unknown", "0.0.2", tar_dir_arg(tmp_path))
    assert proc.returncode == 2, f"拿不到输入必须判'没能比'(2)，实得 {proc.returncode}\n{proc.stderr}"
    assert "compose down" not in proc.calls, "没能比还往下走，就是假绿灯"


def test_空闲时放行且闸口在停服之前(tmp_path):
    root = make_sandbox(tmp_path)
    proc = run_upgrade(root, tmp_path, "idle", "0.0.2", tar_dir_arg(tmp_path))
    assert proc.returncode == 0, f"空闲应放行，实得 {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    lines = [l for l in proc.calls.splitlines() if l.startswith("docker compose")]
    gate = [i for i, l in enumerate(lines) if l.startswith("docker compose exec")]
    down = [i for i, l in enumerate(lines) if l.startswith("docker compose down")]
    assert gate and down, f"闸口或停服没被执行：\n{proc.calls}"
    assert gate[0] < down[0], "闸口必须排在停服之前，顺序反了等于没闸"


def test_force_busy_明确跳过并留警告(tmp_path):
    root = make_sandbox(tmp_path)
    proc = run_upgrade(root, tmp_path, "busy", "0.0.2", tar_dir_arg(tmp_path), "--force-busy")
    assert proc.returncode == 0, f"--force-busy 应放行，实得 {proc.returncode}\n{proc.stderr}"
    assert "force-busy" in proc.stderr, "跳过闸口必须留下可见警告"
    assert "docker compose down" in proc.calls


@pytest.mark.parametrize("uname_m,expect", [("x86_64", "linux/amd64"), ("aarch64", "linux/arm64")])
def test_发版脚本的目标平台永远是规范名(tmp_path, uname_m, expect):
    """无论 docker 回报 x86_64 还是 aarch64，交给构建器的必须是 OCI 规范名。

    反例是实测撞出来的：linux/x86_64 不报错，只在构建中途甩一句 EOF，根因看不出来。
    """
    root = make_sandbox(tmp_path, with_ops_tree=True)
    bindir = make_bin(tmp_path / "rel", uname_m=uname_m)
    (tmp_path / "calls.log").write_text("", encoding="utf-8")
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "CALL_LOG": str(tmp_path / "calls.log"),
        "RELEASE_DRY_RUN": "1",
        "FAKE_DOCKER_ARCH": "x86_64",
        "FAKE_UNAME_M": uname_m,
    }
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "release.sh"), "0.0.0-test"],
        cwd=root, capture_output=True, text=True, timeout=180, env=env,
    )
    assert "linux/x86_64" not in proc.stdout and "linux/aarch64" not in proc.stdout, (
        "不得把非规范名交给构建器（实测会中途 EOF）")
    assert f"--platform {expect}" in proc.stdout, (
        f"目标平台应为 {expect}\nstdout={proc.stdout[-1200:]}\nstderr={proc.stderr[-800:]}")
