#!/bin/sh
# DeckSmith app 容器启动脚本（ADR-012 决策 5）
#
# 职责只有一件事：把"运行前置条件"在**每次启动**时按容器内真实路径幂等备好。
# 为什么不能烤进镜像层：技能发现用的软链存的是**绝对路径**，镜像里建好的链，
# 换挂载点或换宿主目录就当场断链。
#
# 注：guizang 引擎在 submit 时也会自己调 ensure_skill_installed()（engine.py:384），
# 所以本脚本不是唯一通路，只是把失败提前到启动期暴露、并在日志里留痕，
# 避免出现"任务跑到一半才发现技能没链上"的静默降级。
# 待 ADR-012 决策 2 的 prepare_runtime 钩子落地后，这里的硬编码应由钩子遍历取代。
set -eu

SKILLS_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"
GUIZANG_SRC="/app/lib/guizang-ppt-skill"

mkdir -p "$SKILLS_DIR" /data/jobs /data/db /data/decks

if [ -f "$GUIZANG_SRC/SKILL.md" ]; then
  if ln -sfn "$GUIZANG_SRC" "$SKILLS_DIR/guizang-ppt-skill"; then
    echo "[entrypoint] 技能软链就绪：$SKILLS_DIR/guizang-ppt-skill -> $GUIZANG_SRC"
  else
    # 不退出：引擎侧还有按绝对路径读 SKILL.md 的降级路径（ADR-011 决策 5）
    echo "[entrypoint][warn] 技能软链建立失败，将退回按绝对路径读 SKILL.md" >&2
  fi
else
  echo "[entrypoint][error] 镜像内缺少 $GUIZANG_SRC/SKILL.md —— 构建上下文未包含 lib/ 子模块内容" >&2
fi

# 解释器唯一性自检（决策 8）：claude 调技能脚本用的是 PATH 上的 python3，
# 必须与引擎 sys.executable 同一个，否则上游 C 扩展会时灵时不灵
PY3="$(command -v python3)"
case "$PY3" in
  /opt/venv/*) : ;;
  *) echo "[entrypoint][error] PATH 上的 python3 不是 venv 解释器：$PY3" >&2; exit 1 ;;
esac
if ! python3 -c "import pathops" >/dev/null 2>&1; then
  echo "[entrypoint][error] python3 ($PY3) 缺 skia-pathops，技能脚本会在布尔运算页失败" >&2
  exit 1
fi
echo "[entrypoint] 解释器自检通过：$PY3"

exec "$@"
