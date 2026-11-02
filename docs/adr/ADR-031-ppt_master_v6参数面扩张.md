# ADR-031: ppt_master_v6 参数面扩张——六项进参数区、三项进高级输入、发散度给预设句

- 状态: 已接受（清单经龚成 2026-10-31 批准："参数清单就按你说的做"）；预设句措辞仍**待定稿**
- 日期: 2026-10-31
- 决策者: 龚成
- 相关: ADR-015（双引擎策略）、ADR-012（交付形态与产物落点）、ADR-026（假绿灯改真检查）；
  项目规则 8/9/10（`AGENTS.md`：引擎参数门控、禁止假控件与幽灵参数、所有参数支持不选）

## 背景

2026-10-31 排查"PPT Master 生成时爱加戏"时发现：真正控制加戏的字段是技能自己的
`content_divergence`（材料发散度），而我们的产品**根本没把它做成参数**。顺着查下去，
`ppt_master` 引擎暴露的 5 个参数里有 4 个是假控件（契约声明了、引擎从不读），
`content_style` 则是反向的幽灵参数（引擎读了、没人能喂）。两条已按 ADR-026 的路子
装上机器检查并修好（`tests/contract/engine_param_wiring_test.py`）。

本 ADR 解决剩下那半件事：**参数面不全**。技能 v6.6 自己声明的可确认字段有二十来个
（权威清单见技能 `references/confirm-surface.md` §2–§3），我们只暴露了 6 个。

## 决策

### 1. 只扩 `ppt_master_v6`，不动 `ppt_master`

v5 引擎走的是完整多阶段流程，参数语义挂在 Stage 1/2 的确认页上；v6 默认
`route_profile=quick-generate`，无交互、只认 prompt 里写明的指令。ADR-015 的真实运行
已验证"参数写进 prompt 在 quick 路线下确实生效"（notes/animation/canvas 三项）。
把新参数加在 v6 上通路最短，v5 保持"稳定复现旧产物"的定位不变。

### 2. 六项进「引擎参数」区（弹窗直接可见）

| 参数 | 技能字段 | 控件 | 留空含义 |
|---|---|---|---|
| 材料发散度 | `content_divergence` | 预设下拉 + 可自写 | 按技能的「平衡」默认档 |
| 阅读模式 | `delivery_purpose` | 枚举（字多自读 / 平衡 / 字少靠讲） | 由技能判断；**仅 PPT 类画布有效**，非 PPT 画布下该控件不出现 |
| 是否允许上网补素材 | `topic-research` 触发口径 | 布尔 | 由技能判断素材是否够 |
| 主语言 | `primary_language` | 枚举（BCP-47） | 由技能从素材推断 |
| 配图来源 | `image_usage` | 多选（AI 生成 / 网络搜图 / 只用我上传的 / 允许占位符 / 不用图） | 由技能判断 |
| 旁白音频 | `proactive_narration_audio` | 布尔 | 按技能默认（关） |

### 3. 三项进「高级」折叠区（自由文本）

受众 `audience`、沟通意图 `communication_intent`、核心信息 `core_message`。

理由：技能明写这三项是开放散文、**禁止做成枚举或单选**（`communication_intent` 那九个
说法只是提示语，不得变成 `primary_job` 字段）。做成文本框最忠实，但摆在普通用户面前像
填空题；引擎参数区本就只对 vip/管理员开放（规则 8），放折叠区既不吓人也不减能力。

### 4. 材料发散度必须支持"不知道写什么"

界面给三句**预设**，用户点哪句就把那句话原样提交（仍是"用户自己的话"，不违反技能
"不许 AI 替用户推荐该字段"的口径——我们提供的是输入便利，不是替他决定）：

- 严格贴着素材来
- 结构可以重排但内容不增不减
- 在素材范围内自由重组并展开

> 这三句照技能确认页自身的示例措辞改写，**尚未逐字定稿**，实施前须龚成过目。
> 留空 = 不下发该项 = 技能按「平衡」默认档处理。

### 5. 全部遵守已定的两条口径

- 规则 10：schema 里这些参数一律**不写 `default`**；引擎侧未选即不写该行；布尔项 `false` 照常下发。
- 规则 9：新增每个参数必须同时落契约与引擎读取点，由既有机器检查双向看守。

## 明确不做（本轮及可预见的后续都不做）

- **模板选择**：不是"一个参数值"。要走"列候选 → 用户挑 → 安装握手 → 再确认怎么用"整条链，
  做成下拉框只会假成功。属功能，将来单独立项。
- **配色与字体**（`color` 六角色 / `typography` 字体对与字号）：需要取色器与实时预览，弹窗装不下。
- **配图渲染风格**（`image_strategy.rendering`，20 预设 + 自定义行为）：依附于"配图来源含 AI"，
  等第 2 项跑通再谈。
- **动效细节**：能给的开关已接通；再细是逐对象配置文件，属功能。
- **`refine_spec`**（规格复核）：会在中途停下等人工聊天改规格，无人值守场景必然卡死，**永久不做**。

## 契约落点清单（本 ADR 被接受后据此改）

1. `docs/contracts/engine.schema.json` → `$defs.ppt_master_v6.properties` 增加 9 个键
   （不带 `default`；阅读模式带「仅 canvas=ppt* 时有效」条件说明，复用前端既有依赖显隐机制）。
2. `scripts/generate-types.sh` 重新生成 `packages/shared/types/*`（不入库）。
3. `packages/engine/impl/ppt_master_v6/engine.py::_build_prompt` 按"未选不下发"写这 9 项。
4. `packages/frontend/src/lib/engineParams.ts` 补中文名与枚举中文值（`content_divergence`
   例外——它是自由文本，走"预设 + 自写"）。
5. `packages/frontend/src/components/workspace/DraftsPanel.tsx` 参数区分两组：常规 + 「高级」折叠。
6. `docs/contracts/style-guide.md` 增对应中文说明表（取值集合须与 openapi/引擎契约逐项相等，
   由 `engine_param_wiring_test.py` 看守）。
7. `tests/contract/engine_param_wiring_test.py` 自动纳入新参数（按注册表枚举，无需登记）；
   另补"未选不下发"对新参数的对照组用例。

## 后果

- 正面：加戏程度、信息密度、素材边界（能不能上网补料）第一次变成用户可控项；参数面从 6 项到 15 项；
  新增参数受既有双向机器检查约束，不会再长出假控件。
- 代价：弹窗变长（靠折叠区消化）；v6 与 v5 的参数面进一步拉开差距，用户换引擎会看到不同控件集，
  这是双引擎策略（ADR-015）本来就接受的后果。
- 待验证：quick 路线是否**逐条**认这些 prompt 说法，尤其 `content_divergence` 与 `delivery_purpose`。
  契约与代码可以机器验，"技能照不照做"只能靠真实生成比对产物，实施后需一次授权真跑验收。

## 顺带发现（未处理，待裁定）

`ppt_master` / `ppt_master_v6` 的 `job.json` 读写非原子：`_write_job` 用 `write_text` 直接覆盖，
`_read_job` 把"读到半截 JSON"吞成 `{}`，于是 `status()` 会把它当成"running 的输出引擎任务且进程已死"
判失败，**把后台线程刚写的真实失败原因覆盖掉**。实测 `packages/engine/tests/test_draft_async.py`
在隔离运行下约四次里红一次（错误文案变成"claude 子进程结束但未找到产物"）。
与本 ADR 无关，属任务生命周期缺陷，修法（原子写 + `status()` 读不到记录时不得判失败）另案处理。
