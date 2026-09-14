# ADR-011: guizang_ppt 引擎运行时归位为 Claude 技能执行

- 状态: 已接受
- 日期: 2026-09-14
- 决策者: 龚成
- 相关: ADR-001（引擎可插拔）、`docs/vc_design/objects/01-04-HTML输出引擎.md`、
  `docs/research/2026-09-14-guizang引擎运行时口径纠正.md`

## 背景

`guizang_ppt` 引擎此前是**纯 Python 实现**：读 `lib/guizang-ppt-skill/assets/template.html`，
把上游 `references/themes*.md` 的色板逐字抄成 Python 常量，用正则把 Markdown 切成段落，
生成三种硬编码 `<section>` 版式，替换 `:root` CSS 变量后注入 `<!-- SLIDES_HERE -->`。

核实结论（见调研文档）：

1. 上游 `guizang-ppt-skill` 是**写给 AI agent 执行的技能包**，不是可机械填充的模板——
   `SKILL.md` 规定了 10 套杂志版式与锁定的 S01–S22 瑞士版式、light/dark/hero 主题节奏、
   「模板是唯一类名来源」白名单、每页动效 recipe、Lucide 图标、演讲者备注与 node 校验脚本。
2. 本项目自己的设计对象 `01-04-HTML输出引擎.md` 早已写明执行方式：
   「与 ppt-master 引擎一致，通过 Claude Code 子进程执行」。
3. 因此旧实现不是「另一种可选实现」，而是**有损重写并且违背本项目设计口径**：
   它丢掉了版式库、主题节奏、动效、图标、校验，还自创了模板中不存在的类名
   （`content-col` / `visual-col`）。

## 决策

1. **`guizang_ppt` 归位为 Claude 驱动**：`submit` 拉起
   `claude -p --dangerously-skip-permissions --output-format stream-json --include-partial-messages --verbose <prompt>`，
   由 agent 按 `SKILL.md` 完成排版与自检；引擎只负责任务编排、状态机、产物定位与日志透出。
2. **删除纯 Python 实现**（不保留为离线兜底、不另注册引擎）。经产品决策确认：
   宁可失去「无 claude 也能出 HTML」的能力，也不在仓库里留第二套走样的排版事实源。
   连带删除 `impl/guizang_ppt/templates/{magazine,swiss}.html`（该副本从未被代码引用）。
3. **cwd 用任务目录，不用技能目录**。技能目录是 git submodule、上游只读（AGENTS 规则 4），
   不允许 agent 往其中写产物——这是与 `ppt_master`（cwd=`lib/ppt-master`）刻意的差异。
4. **产物路径在 prompt 中钉死**为 `<jobs_root>/<job_id>/result.html`，状态判定直接检查该文件
   （存在 + mtime 晚于 `started_at` + ≥2KB），不再沿用 `ppt_master` 的「mtime 猜最新导出」。
   小文件（截断/占位）判失败，杜绝冒领旧产物。
5. **技能发现用软链**：`lib/guizang-ppt-skill` → `$CLAUDE_CONFIG_DIR|~/.claude/skills/guizang-ppt-skill`，
   幂等、遇到用户自有副本只提示不覆盖；prompt 同时给出技能绝对路径，链接失败仅降级不阻断。
6. ~~**不点亮 `page_preview`**~~ → **已被提交 `f3a953e` 推翻**（本条保留为当时的决策原貌）：
   单文件 HTML 无逐页落盘产物，故有意不实现
   `get_pages` / `get_preview_root`；`get_progress` 亦不报 `pages_generated`，
   避免给出等于编造的数字。
   *现行事实：两钩子均已实现——页是扫出来的 `<section class="slide">`（只收已闭合页），
   PageRef 的 path 一律指向 `result.html`；`get_progress` 直接取 `len(get_pages())`，
   与 pages 接口同口径。当年"不报数=不编造"的顾虑由**真实读盘**满足，而非靠不实现规避。*
7. **`DECKSMITH_FORCE_STUB=1` 下 guizang 一并落 stub**（此前它因是纯 Python 被放行到真引擎，
   归位后会拉起子进程，必须收进离线测试钩子）。

以下两条是**真跑一次失败后**追加的决策（证据见调研文档「真跑复盘」一节）：

8. **产出协议写进 prompt：禁止一次性 Write 整个产物。** 骨架模板实测 103KB（magazine）/
   164KB（swiss），让模型把整份文件重新输出会超出单次回复输出上限、被网关中途掐断连接。
   改为要求 agent：① `cp <骨架> <产物>` 先拿到完整骨架；② 之后**只用 Edit 分批替换**
   `<!-- SLIDES_HERE … -->` 注释块（每批 3–4 页）与 `const SPEAKER_NOTES = [` 数组；
   ③ 收尾用 `wc -c`、`grep -c '<section class="slide'`、`grep -c SLIDES_HERE` 自检并报告。
   这与上游「模板已完整可运行、只待填充」的本意一致。
9. **失败根因必须可诊断，且允许断点续跑。** `claude` 的 `result` 事件在网关断流时**仍报
   `subtype=success`**，只看它会把网络故障误判成“agent 没干活”。因此 pump 记录任意事件里的
   `session_id` 与 `result` 的 `is_error`/原文/`num_turns`；产物缺失时优先用 `result` 原文、
   其次用日志中断流特征词所在的那一行作为失败原因；已知 `session_id` 时用
   `claude -p --resume <sid>` 自动续跑（默认 2 次，续跑共用剩余时间预算、下限 300s）。
   终态决策由 supervisor 线程拥有，并用 `supervisor_pid` 租约防止轮询的 `status()` 抢先判失败
   打断续跑；租约随进程消失而失效，后端重启后 `status()` 仍能自行收敛终态。

## 后果

- 正面：产物质量与上游技能同步演进（升级 submodule 即升级排版能力）；仓库内不再有
  第二套模板事实源；判定逻辑比 `ppt_master` 的启发式更确定；断流等基础设施故障不再被
  「✅ 技能执行完成」掩盖，且能自动续跑。
- 代价：**HTML 引擎从此硬依赖 Claude Code CLI + 可用的模型网关 + Node（技能校验脚本）**，
  离线/内网环境不可用；每次生成的 token 成本与耗时从「本地毫秒级」变为「分钟级长任务」。
- **部署事实（打包 Linux 必读）**：实测开发机的 `claude` CLI 并非直连官方 Anthropic，而是
  经 `ANTHROPIC_BASE_URL` 指向第三方 Anthropic 兼容网关（`ANTHROPIC_MODEL=mimo-v2.5-pro`）。
  这类网关对**超长输出/长连接**稳定性较差（本次失败即其掐断），镜像必须把
  `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` / 凭证作为运行时配置暴露出来，
  并且产出协议（决策 8）不是可选项而是**这类网关下的生存前提**。
- 已验证：无人值守 prompt 足以让 agent 跳过技能内的多轮澄清（实测它自行拍定 12 页 / IKB 主题 /
  每页 data-slide-id 与备注）；`--resume` 续跑与断流诊断由单测覆盖。
- 待观察：产出协议能否让真实网关跑完 12 页不中断；超时上限（3600s）是否适合 25–30 页长稿。
