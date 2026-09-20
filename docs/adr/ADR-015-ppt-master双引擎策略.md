# ADR-015: ppt-master 双引擎策略（ppt_master 锁旧版、新增独立 ppt_master_v6 专供 v6.6.0）

- 状态: 已接受（已实现并验证：ppt_master_v6 端到端真跑通过）
- 日期: 2026-09-20
- 决策者: 龚成
- 相关: ADR-001（引擎可插拔）、ADR-011（软链/prompt 钉路径）、ADR-012（cwd 归位、产物落点）、
  **ADR-014（本决策取代其"就地升级 ppt_master"的路线）**、
  `docs/research/2026-09-19-ppt-master与guizang技能版本升级评估.md`

## 背景

ADR-014 批准把 `ppt_master` 引擎就地升级到 ppt-master v6.6.0。真跑回归发现：
1. v6.6 是 major，能力面从"单一生成为主"扩成"路由式多工作流"（quick-generate / default / image-to-pptx / beautify / create-template / edit-native-pptx），并新增 route profile、`generation_mode`、`pptx_structure.mode`、画布、备注等旋钮；
2. 就地升级下，旧 `ppt_master` 引擎逐页预览回归（`/pages=0`），根因是 v6 的 `project_manager` 会在技能仓库落下 `svg_output` 为空的脚手架、被"目录存在即 pin"钉死；
3. 更重要：v6.6 的新参数若塞进旧引擎，会让 `ppt_master` 的行为随上游 major 变化而漂移，破坏其历史任务的可复现性。

结论：把"旧版行为"与"v6.6 新能力"用**两个引擎**分开，而不是让一个引擎骑墙。

## 决策

1. **`ppt_master` 回退并锁死旧版技能**：`git revert` 正向撤销 ADR-014 的两笔（submodule 升级 + 为 v6 加的 pin 加固），`lib/ppt-master` gitlink 回到 `ebd74d1`（v5.1.x），引擎代码回到升级前版本。旧引擎行为 = 升级前既有行为，不再受上游 major 牵连。
2. **新增第二个 submodule `lib/ppt-master-v6`**：同一上游 `hugohe3/ppt-master.git`、钉在 tag `v6.6.0`（`a50758ac`）。一个 submodule 只能钉一个 commit，故两版本以两条 submodule 并存承载，各自可追溯、都守上游只读。
3. **新增独立引擎 `ppt_master_v6`**（`engine.impl.ppt_master_v6.PptMasterV6Engine`）：**不继承、不复用 `ppt_master`**，两份独立演化。`engine_type=output`；先只做输出，草稿沿用现有 `ppt_master_draft`/`claude_draft`。
4. **v6.6 参数全量接线**：契约 `$defs.ppt_master_v6` 暴露 `style/mode/format/notes/animation/route_profile/generation_mode/pptx_structure_mode`，并在 `_build_prompt` 里逐项写进 prompt（消除旧引擎"契约有、prompt 不喂"的悬空参数）；`route_profile` 默认钉 `quick-generate` 以保证无人值守可复现。
5. **pin 加固归入 v6 引擎**：`resolve_svg_output_dir` 的"非空页 SVG 才算落定"实质校验（`_has_page_svgs`）随新引擎实现（v6 才需要），并保留自愈旧误 pin 与 `Path!=str` 修正。
6. **可插拔接入零调用方改动**：只在 `registry.py` 的 `_ENGINE_CLASSES`/`ENGINE_REGISTRY` + `pyproject` 包列表 + 契约测试 `LANDING_SHAPES` 各登记一项；`factory`/`FORCE_STUB`/能力派生按注册表自动纳入（已被契约测试 `test_factory_seamlessly_admits_a_new_engine` 与本次 25 项契约全绿验证）。

## 后果

- 正面：`ppt_master` 稳定复现旧产物；`ppt_master_v6` 独占 v6.6 新能力并可自由加参数；两者由注册表并列，前端参数表单按 `params_schema` 通用渲染、无需为 v6 改前端；升级风险被隔离在独立引擎里，验证不过可只回退它。
- 代价：仓库并存两条同上游 submodule、两份近似引擎代码（有意冗余换取解耦）；`ppt_master_v6` 的 prompt 与产物形状需随 v6 后续 minor 跟进。
- 已验证（2026-09-20）：`ppt_master_v6` 真跑 4 页任务 `58134be6` 端到端通过——`/pages=4`、下载 200、pptx 4 张 slide、`notes=true` 生成演讲备注（notesSlide 存在）、`animation:auto`/画布 `ppt169` 参数在产物中生效、无宿主绝对路径泄露、且上游 `lib/ppt-master-v6/projects/` 未落本任务脚手架（prompt 约束生效）。交付说明需提示"v6.6 产物风格随上游走"。
- ADR-014 就地升级路线就此**废弃**（在其文件标记 superseded、指向本 ADR），其"submodule 单一事实源/软链发现"等策略性结论仍有效并被本 ADR 沿用。
