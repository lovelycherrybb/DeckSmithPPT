"""全站口径：带「操作」列的表格，那一列必须钉在列尾（fixed: 'right'）。

为什么钉成机器检查：这条是明规矩（所有表格的操作列默认固定在列尾），但过去只靠人记得——
用户管理那张就漏了，窄屏横向滚动时按钮会被推出可视区。写进流程却不看守的规矩必然复发。

判据（都能红）：
① 每个 `title: '操作'` 的列定义里必须出现 `fixed: 'right'`；
② 同一列必须给 width（antd 固定列不写宽度会随内容抖，实测操作列被挤到换行）；
③ 用了固定列的 Table 必须配 scroll.x，否则固定列不生效。
退出码三态：0 比过且干净 / 1 有问题 / 2 没能比（前端源码目录不在即算没能比）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO / "packages/frontend/src"

# 一列 = 从 `title: '操作'` 起，到该列对象闭合前的片段；用"下一个列标题"作边界，
# 避免把下一列的 fixed 误算到操作列头上。
COLUMN_BOUNDARY = re.compile(r"title:\s*'")


def frontend_files() -> list[Path]:
    if not FRONTEND_SRC.is_dir():
        return []
    return sorted(p for p in FRONTEND_SRC.rglob("*") if p.suffix in {".tsx", ".jsx"} and p.is_file())


def action_column_snippet(text: str, start: int) -> str:
    """取操作列定义所在片段：从 title: '操作' 到下一个列标题之前。"""
    nxt = COLUMN_BOUNDARY.search(text, start + 10)
    return text[start:nxt.start()] if nxt else text[start:]


def check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems = []
    for m in re.finditer(r"title:\s*'操作'", text):
        snippet = action_column_snippet(text, m.start())
        rel = path.relative_to(REPO)
        if "fixed: 'right'" not in snippet and 'fixed: "right"' not in snippet:
            problems.append(f"{rel}: 操作列没钉在列尾（缺 fixed: 'right'），窄屏会被滚出可视区")
        if not re.search(r"width:\s*\d+", snippet):
            problems.append(f"{rel}: 操作列没写 width，固定列宽度会随内容抖动")
        # 固定列要生效，所在 Table 必须配 scroll
        if not re.search(r"scroll=\{\{\s*x:", text):
            problems.append(f"{rel}: 有固定列但 Table 未配 scroll={{ x }}，固定列不会生效")
    return problems


def run() -> tuple[list[str], bool]:
    files = frontend_files()
    if not files:
        return [f"没能比：找不到前端源码目录 {FRONTEND_SRC}"], True
    problems: list[str] = []
    for f in files:
        problems.extend(check_file(f))
    return problems, False


def test_操作列一律钉在列尾():
    problems, incomparable = run()
    if incomparable:
        import pytest
        pytest.fail(problems[0])
    assert not problems, "\n".join(problems)


if __name__ == "__main__":
    probs, inc = run()
    if inc:
        print(probs[0]); sys.exit(2)
    if probs:
        print("检查未通过："); [print(f"  - {p}") for p in probs]; sys.exit(1)
    print("结论：全站带操作列的表格都已钉在列尾")
    sys.exit(0)
