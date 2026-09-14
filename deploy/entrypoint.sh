#!/bin/sh
# DeckSmith app 容器启动脚本（ADR-012 决策 2、5）
#
# 这里**不再有任何针对具体引擎的硬编码**。运行前置条件（技能软链、解释器与技能文件
# 自检、claude 可解析性）由各引擎实现 prepare_runtime 钩子、app.engines.runtime 遍历
# 注册表统一调用。新增引擎不必回来加一段 if/else——这正是决策 2 要消除的
# "每加一个引擎就补一处 entrypoint"。
#
# 为什么软链仍须启动期建、而不是烤进镜像层：软链记的是**绝对路径**，换挂载点或换
# 宿主目录就当场断链（决策 5）。
#
# 退出码：任一引擎报"硬缺失"即非零，容器不启动——把"镜像装好了、跑起来才发现
# 某引擎不可用"提前到启动期。
set -eu

# HOME 已由镜像 ENV 指向 /data/home（卷），技能软链落在那里
mkdir -p /data/jobs /data/db /data/decks "${HOME:?HOME 未设置，无法定位技能发现目录}/.claude/skills"

# 与后端服务同一套导入路径：app 在 backend/src，engine 在 packages/
export PYTHONPATH="/app/packages/backend/src:/app/packages:/app"
cd /app/packages/backend/src

if ! python3 -m app.engines.runtime; then
  echo "[entrypoint][error] 引擎运行前置条件未满足，拒绝启动（见上方 fail 项）" >&2
  exit 1
fi

echo "[entrypoint] 引擎前置条件预检通过，启动服务"
exec "$@"
