"""出包取包口径的契约测试：镜像站选择与构建缓存挂载。

为什么放在契约层：出包机到国内镜像站会中途断流（2026-10-26 实测：清华源约 135 KB/s，
一个 79KB 的包下了 137 秒仍断流，四十分钟的构建毁在这一步）。对策有两条，都必须能被
机器守住，否则下次改 Dockerfile 时会不知不觉退回去：

1. 两个 Dockerfile 里 apt / pip / npm 三处取包步骤都挂着 BuildKit 持久缓存
   （失败重试不必从头重下约 1.5GB 依赖）；
2. 后端那份不得再出现 PIP_NO_CACHE_DIR=1 或 `npm cache clean`——这两句的作用正是
   把刚下好的缓存清掉，与挂载互相抵消（本次就是先加了挂载、后复核才发现它们同存）；
3. `--mirror cn` 的 pip 首选地址是实测最快的阿里，清华降为备用索引。

判据全是可机械核对的硬事实。退出码三态：0 比过且干净 / 1 比过有问题 / 2 没能比。
--selftest 用故意改坏的文件证明每条判据都能报红（从没红过的检查不算检查）。
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "packages" / "backend" / "Dockerfile"
FRONTEND = ROOT / "packages" / "frontend" / "Dockerfile"
RELEASE = ROOT / "scripts" / "release.sh"

# 三处取包步骤 → 必须挂持久缓存
CACHE_TARGETS = {
    "pip": "/root/.cache/pip",
    "npm": "/root/.npm",
    "apt": "/var/cache/apt/archives",
}

# 与缓存挂载互相抵消的写法（出现即判红）
CACHE_DEFEATERS = (
    (r"PIP_NO_CACHE_DIR\s*=\s*1", "PIP_NO_CACHE_DIR=1 会让 pip 下完就扔，缓存挂载白挂"),
    (r"npm\s+cache\s+clean", "npm cache clean 正好清掉刚挂上的缓存"),
    (r"--no-cache-dir", "pip --no-cache-dir 同上"),
)


def _read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"找不到文件：{path}")
    return path.read_text(encoding="utf-8")


def _run_step_has_cache(text: str, step: str, target: str) -> bool:
    """该取包命令所在的那一条 RUN（含跨行续行）是否挂着指向 target 的缓存。"""
    pat = re.compile(
        r"RUN\s+((?:--mount[^\n]*\n?\s*)*)" + re.escape(step) + r"\b([^\n]*(?:\n[ \t]+[^\n]*)*)",
        re.M)
    for m in pat.finditer(text):
        blob = m.group(0)
        if f"target={target}" in blob and "type=cache" in blob:
            return True
    return False


def _code_lines(text: str) -> str:
    """剥掉注释行。判据查的是"有没有真的写下这句命令"，说明文字里提到不该算红
    （第一版就被自己的注释红过两次）。"""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def check_backend(text: str) -> list[str]:
    problems = []
    code = _code_lines(text)
    for step, target in CACHE_TARGETS.items():
        if not _run_step_has_cache(code, step, target):
            problems.append(f"后端 Dockerfile：`{step}` 这一步没挂持久缓存（应指向 {target}）")
    for pat, why in CACHE_DEFEATERS:
        if re.search(pat, code):
            problems.append(f"后端 Dockerfile：出现「{pat}」——{why}")
    return problems


def check_frontend(text: str) -> list[str]:
    problems = []
    code = _code_lines(text)
    for step, target in (("npm ci", CACHE_TARGETS["npm"]),
                         ("apt-get", CACHE_TARGETS["apt"])):
        if not _run_step_has_cache(code, step, target):
            problems.append(f"前端 Dockerfile：`{step}` 这一步没挂持久缓存（应指向 {target}）")
    return problems


def check_release(text: str) -> list[str]:
    problems = []
    m = re.search(r'^\s*PIP_INDEX_URL="([^"]*)"', text, re.M)
    if not m:
        return ["release.sh：读不到 cn 模式下的 PIP_INDEX_URL（写法变了？请同步修订本测试）"]
    if "aliyun.com" not in m.group(1):
        problems.append(f"release.sh：cn 模式 pip 首选源应为实测最快的阿里，实得 {m.group(1)}")
    extra = re.search(r'^\s*PIP_EXTRA_INDEX_URL="([^"]*)"', text, re.M)
    if not extra:
        problems.append("release.sh：读不到 PIP_EXTRA_INDEX_URL（备用索引缺失）")
    elif " " in extra.group(1).strip():
        # Dockerfile 里备用索引是 `--extra-index-url "$PIP_EXTRA_INDEX_URL"` 整体引用，
        # 塞两个地址会被 shell 拆开传错，必须只留一个
        problems.append(f"release.sh：备用索引只能一个地址，实得 {extra.group(1)}")
    return problems


def run_checks() -> tuple[list[str], bool]:
    """返回（问题列表，是否没能比）。缺文件一律算没能比，绝不判通过。"""
    problems: list[str] = []
    incomparable = False
    for path, fn in ((BACKEND, check_backend), (FRONTEND, check_frontend), (RELEASE, check_release)):
        try:
            text = _read(path)
        except FileNotFoundError as exc:
            print(f"没能比：{exc}")
            incomparable = True
            continue
        problems.extend(fn(text))
    return problems, incomparable


# ── 破坏用例：证明每条判据都能红 ─────────────────────────────────
SABOTAGE = {
    "pip 缓存被摘掉": (BACKEND, ("--mount=type=cache,id=decksmith-pip,target=/root/.cache/pip \\\n    pip install", "RUN pip install")),
    "重新加回 PIP_NO_CACHE_DIR": (BACKEND, ("ENV PYTHONUNBUFFERED=1 \\", "ENV PYTHONUNBUFFERED=1 \\\n    PIP_NO_CACHE_DIR=1 \\")
                                  ),
    "npm 缓存被摘掉": (FRONTEND, ('RUN --mount=type=cache,id=decksmith-npm,target=/root/.npm \\\n    npm ci', 'RUN npm ci')),
    "首选源退回清华": (RELEASE, ('PIP_INDEX_URL="https://mirrors.aliyun.com/pypi/simple"',
                                'PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"')),
    "备用索引塞两个地址": (RELEASE, ('PIP_EXTRA_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"',
                                    'PIP_EXTRA_INDEX_URL="a/simple b/simple"')),
}


def _check_text(path: Path, text: str) -> list[str]:
    return {BACKEND: check_backend, FRONTEND: check_frontend, RELEASE: check_release}[path](text)


@pytest.mark.parametrize("label", list(SABOTAGE))
def test_每条判据都能报红(label):
    target, (old, new) = SABOTAGE[label]
    text = _read(target)
    assert old in text, f"破坏用例失效：{target.name} 里找不到待替换的原文，请先同步本测试"
    problems = _check_text(target, text.replace(old, new))
    assert problems, f"把「{label}」改坏后检查竟然全绿——这台检查是摆设"


def test_现状必须全绿():
    problems, incomparable = run_checks()
    if incomparable:
        pytest.fail("有文件读不到，按「没能比」处理，不算通过")
    assert not problems, "\n".join(problems)


def main() -> int:
    problems, incomparable = run_checks()
    if incomparable:
        print("没能比：有文件缺失，不判通过")
        return 2
    if problems:
        print("检查未通过：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK：三处取包都挂着持久缓存，且没有互相抵消的写法；cn 模式首选阿里源")
    return 0


if __name__ == "__main__":
    sys.exit(main())
