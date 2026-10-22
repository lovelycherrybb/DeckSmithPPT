"""交付包构建口径的契约测试：发版脚本的国内源开关。

为什么放在契约层：这张包"从哪儿取的包"是要写给客户和事后审计看的，属于对外承诺，
不是脚本内部实现细节。核的是三件事，每件都能失败：

1. 不给 --mirror 时，一切取包地址参数为空——构建行为与加这个开关之前完全一致；
2. 给 --mirror cn 时，apt / pip / npm 三个地址全部非空且指向国内镜像，
   且镜像标签 decksmith.build-mirror 被传成 cn（走没走过国内源必须可核对）；
3. 试跑模式绝不能碰真 dist/ 下的出厂清单——脚本会整体重写 SHA256SUMS 与 MANIFEST.txt，
   这一条丢过两次（2026-10-16、10-21），所以钉成断言。

用 RELEASE_DRY_RUN=1 跑，只打印命令不构建，所以本文件几秒内出结论，也不烧额度。
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "scripts" / "release.sh"
DIST = ROOT / "dist"


def run_release(*extra: str, dry_run: bool = True) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    if dry_run:
        env["RELEASE_DRY_RUN"] = "1"
    return subprocess.run(
        ["bash", str(RELEASE), "0.0.0-test", *extra],
        cwd=ROOT, capture_output=True, text=True, timeout=300, env=env,
    )


def dry_run_lines(stdout: str) -> list[str]:
    lines = [l for l in stdout.splitlines() if l.startswith("DRYRUN | docker ")]
    assert lines, f"没有打印任何构建命令：\n{stdout[-800:]}"
    return lines


@pytest.fixture(autouse=True)
def _need_docker():
    proc = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail("这台机器上 docker 不可用，构建脚本的前置自检会直接停——"
                    "本文件因此不能算通过，请修好环境再跑（不许静默跳过）")


def arg_of(lines: list[str], name: str) -> list[str]:
    got = []
    pat = re.compile(r"--build-arg " + re.escape(name) + r"=(\S*)")
    for line in lines:
        got.extend(m.group(1) for m in pat.finditer(line))
    return got


def test_default_keeps_official_sources():
    """不给开关时，四个取包地址必须全为空（空＝Dockerfile 里的默认值＝不动任何源）"""
    proc = run_release()
    assert proc.returncode == 0, proc.stderr[-800:]
    lines = dry_run_lines(proc.stdout)
    # 默认只构建本机一个架构，app 与 web 各一条；显式传双平台时是两组
    assert len(lines) % 2 == 0 and lines, f"每个平台都该有 app 与 web 两条，实得 {len(lines)}"
    for name in ("APT_MIRROR", "APT_SECURITY_MIRROR", "PIP_INDEX_URL", "NPM_REGISTRY"):
        vals = arg_of(lines, name)
        assert vals and all(v == "" for v in vals), f"默认模式下 {name} 应为空，实得 {vals}"
    assert all(v == "official" for v in arg_of(lines, "BUILD_MIRROR"))
    assert "取包源    : official" in proc.stdout


def test_cn_mirror_sets_every_channel():
    proc = run_release("--mirror", "cn")
    assert proc.returncode == 0, proc.stderr[-800:]
    lines = dry_run_lines(proc.stdout)
    for name in ("APT_MIRROR", "APT_SECURITY_MIRROR", "PIP_INDEX_URL",
                 "PIP_EXTRA_INDEX_URL", "NPM_REGISTRY", "PIP_TIMEOUT", "PIP_RETRIES"):
        vals = arg_of(lines, name)
        assert vals and all(v for v in vals), f"cn 模式下 {name} 不该留空，实得 {vals}"
    # 走没走过国内源必须留在镜像标签里，事后能核对
    assert all(v == "cn" for v in arg_of(lines, "BUILD_MIRROR"))


def test_unknown_mirror_value_is_rejected():
    proc = run_release("--mirror", "jp")
    assert proc.returncode != 0, "未知镜像名必须报错退出，不能悄悄当官方源跑"
    assert "--mirror" in proc.stderr


def test_dry_run_does_not_touch_real_manifest(tmp_path):
    """试跑不得改写 dist/ 下的出厂清单——这条是拿两次真实丢档换来的"""
    sums, manifest = DIST / "SHA256SUMS", DIST / "MANIFEST.txt"
    before = {p: (p.read_bytes() if p.exists() else None) for p in (sums, manifest)}
    run_release("--mirror", "cn")
    after = {p: (p.read_bytes() if p.exists() else None) for p in (sums, manifest)}
    assert before == after, "RELEASE_DRY_RUN=1 竟然改动了真 dist/ 的出厂清单"
