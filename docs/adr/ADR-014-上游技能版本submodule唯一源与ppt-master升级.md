# ADR-014: 上游技能版本以 lib/ submodule 为唯一事实源，并批准 ppt-master 升级到 v6.6.0

- 状态: 已接受（版本管理策略生效；`lib/ppt-master` 指针推进到 v6.6.0 **待真跑回归后合入**，本 ADR 不代表已升级）
- 日期: 2026-09-19
- 决策者: 龚成
- 相关: ADR-001（引擎可插拔）、ADR-011（技能发现用软链 / prompt 钉绝对路径）、ADR-012（子进程 cwd 归位任务目录）、
  AGENTS 规则 2「引擎可插拔」·规则 4「上游只读」·规则 5「选型必记 ADR」、
  `docs/research/2026-09-19-ppt-master与guizang技能版本升级评估.md`

## 背景

2026-09-19 核查两个 claude 驱动引擎所依赖的上游技能版本（详见同日调研文档），确认：

1. `ppt_master`、`guizang_ppt` 驱动 Claude 时，实际加载的都是**本仓库 `lib/` 下的 git submodule 副本**；`~/.claude/skills` 里的 `guizang-ppt-skill`、`ppt-master` 只是指向 `lib/` 的**软链**（`ensure_skill_installed()` 维护，见 ADR-011 决策 5），不承载第二份内容。
2. `guizang-ppt-skill` 当前锁 `c91369c`，恰等于上游 `origin/main` 顶点，且比其唯一 tag `v1.1.0` 还超前 20 提交——**无新版可升**。
3. `ppt-master` 当前锁 `ebd74d1`（2026-08-25），落后上游 `v6.6.0`（`a50758ac`，2026-09-19）**363 个提交、跨 v6.0.0 major**，但 `超前 0`（现锁在 main 历史线上，可干净快进）。
4. v5.1→v6.6 的 breaking change **全部落在 ppt-master 技能自身的 prompt/workflow 内部**（AGENTS.md 删两大段、`native_enhance_pptx*` 等脚本被删、`template_fill_pptx/*`→`pptx_ooxml/*` 重命名、术语重命名）。**DeckSmith 引擎所依赖的集成面逐项核对均未破坏**：`AGENTS.md`/`SKILL.md`/`source_to_md.py` 三路径仍在、`source_to_md.py` 的位置参数与 `OUTPUT:` 标记契约不变、产物回收 glob `projects/*/exports/*.pptx` 与 `svg_output/` 命名约定未挪、`requirements.txt` 无引擎相关包增删。

本 ADR 固化"版本从哪来、怎么升"的架构决策，并记录升级到 v6.6.0 的批准与合入前置门。

## 决策

1. **版本唯一事实源 = `lib/` submodule 的钉定 commit**。上游技能的"版本"只由父仓库记录的 gitlink 决定，不依赖任何机器上 `~/.claude/skills` 的独立副本。换人/换机部署时，技能版本由 `git submodule update --init --recursive` 还原，与本机是否手动 clone 无关。
2. **`~/.claude/skills` 仅允许软链，禁止实体 clone**。沿用 ADR-011 的 `ensure_skill_installed()` 幂等软链；遇用户手滑 `git clone` 成真实目录时只告警"版本可能漂移"、不覆盖。判据：`readlink ~/.claude/skills/<name>` 应指向仓库 `lib/…`，否则视为漂移源须纠正为 `ln -sfn`。
3. **`guizang-ppt-skill` 维持现状不升**：已在 main 顶点，无可升目标；仅纳入发布核对清单，随镜像标签一并核。
4. **批准 `ppt-master` 升级到 `v6.6.0`，但设合入前置门**：因跨 major，指针推进前必须跑一次**真实 ppt_master 任务回归**，覆盖「草稿→出片→产物回收（`projects/<x>/exports/*.pptx` 与逐页 `svg_output/`）→在线预览」全链，确认产物落点与版式质量不回退，方可 `git add lib/ppt-master` 并提交。回归关注点由 `ebd74d1..v6.6.0` 中触及工作流/门禁/脚本重命名的提交单独列清单。
5. **升级手法只动 gitlink，不改 `lib/` 内文件**：`cd lib/ppt-master && git fetch --tags && git checkout v6.6.0` 后在父仓库 `git add lib/ppt-master && git commit`；符合 AGENTS 规则 4「上游只读」。因是软链，**技能侧无需重装/重链**。
6. **升级后必重启后端**（`uvicorn` 无 `--reload`、import 有缓存），再真跑验证；`DECKSMITH_FORCE_STUB` 的 CI stub 不受技能版本影响。

## 后果

- 正面：技能版本可复现、可回退（回退 = 指回旧 commit）、与镜像交付版本一一对应；升级到 v6.6.0 让产物质量随上游排版能力演进（新 `edit-native-pptx` 路由等），且经逐项核对**不改引擎代码**即可生效。
- 代价 / 风险：
  1. v6 为 major，**生成行为会变**（版式、门禁、导出细节按新 SKILL 走），历史任务复跑结果可能与当年不一致——属预期内，但需在交付说明中提示"产物随技能版本走"。
  2. 集成面虽经静态 diff 核对为兼容，但 ppt_master 产物定位**零单测、真跑是唯一裁判**，故合入前那次真实回归是硬门，不可用静态结论替代。
  3. 上游 `requirements.txt` 新增 PyMuPDF(AGPL-3.0) 的许可说明（当前非强制包行）；若后续版本将其列为硬依赖且随镜像再分发，需按 AGPL 复核交付合规。
- 本 ADR 状态与执行解耦：策略（决策 1–3、5–6）即接受生效；决策 4 的指针推进在回归通过前**保持"待合入"**，届时更新本文件状态与实际 submodule 记录，二者必须一致（严禁把未执行的升级写成现状）。
