# ADR-029: 接入萝卜 PPT（luobo-ppt）为第三个输出引擎——生成路线 + 逐页预览，产物为原生 .pptx

- 状态: 提议（待评审，未实现；本轮只落方案与契约草案，不动 `packages/` 任何实现代码）
- 日期: 2026-10-09
- 决策者: 龚成
- 相关: ADR-001（引擎可插拔）、ADR-011（guizang 运行时归位 Claude 技能执行）、
  ADR-012（交付形态与引擎运行时数据布局：cwd 归位、产物落点钉死、`prepare_runtime` 钩子）、
  ADR-014（上游技能版本 submodule 唯一源）、ADR-015（双引擎策略）、ADR-016（token 消耗口径）、
  ADR-018（任务状态分层枚举）、ADR-019（调度循环与取消/进程组）、ADR-021（产物结构守卫：存在≠能用）、
  ADR-023 / ADR-027 / ADR-028（逐页修改工作台、整稿物化、改稿补丁协议——本 ADR 明确**不接**这条）；
  外部事实源：`https://github.com/zhouwei713/luobo-ppt`（MIT，v2.5.0）

## 背景

萝卜 PPT（`luobo-ppt`）是一个"做 PPT / 美化 PPT"的 Agent Skill：仓库根即技能本体（`SKILL.md` 在最外层），
由 `scripts/` 下一批 Python 脚本 + 一份驱动它的 `SKILL.md` 组成，被任何认 `SKILL.md` 开放标准的
Agent（Claude Code / Codex / Gemini CLI / Cursor）加载执行。它与本项目现有 `guizang_ppt` 引擎
**是同一物种**——"引擎不自己排版，把整件活交给 `claude` 子进程执行一个只读技能，引擎只做编排、
状态机、产物定位与日志透出"（见 ADR-011）。所以接入路径基本是把 `packages/engine/impl/guizang_ppt/engine.py`
那套骨架复刻一份。

两条路线共用一套设计引擎，中间靠三份 JSON 契约串起：`deck.json`（每页内容）、`style.json`（风格）、
`plan.json`（版式规划）。分工原则与本项目一致——脚本干确定性的活，模型干理解与审美。

**关键不匹配（本 ADR 的决策核心）**：`guizang` 产的是**单文件 HTML、内部逐页 `<section>`**，所以
`get_pages`（逐页预览）靠扫 HTML 零额外成本、`revise`（对话式改稿）靠编辑逐页 SVG/HTML 源；
萝卜产的是**一个原生二进制 `.pptx`**（`python-pptx` 直出，非渲染图）。这一条决定了
本引擎哪些能力白拿、哪些要转换、哪些本版直接放弃。

## 决策

### 1. 归类与范围：第三个"输出引擎"，本版只做生成路线 + 逐页预览

- `engine_type: "output"`，与 `ppt_master` / `ppt_master_v6` / `guizang_ppt` 并列。上游分工不变：
  草稿引擎产 Markdown → 萝卜输出引擎吃 Markdown 产 `.pptx`。
- **本版范围**：`submit` / `status` / `result` / `cancel` / `prepare_runtime` 五个必选与取消钩子
  + `get_pages` / `get_preview_root` 逐页预览钩子。
- **本版明确不做**：① `revise`（修改工作台/对话式改稿）——萝卜的改稿模型是"改 `deck.json`/`plan.json`
  重跑 pipeline"，与本项目 ADR-023/027/028 建立在"逐页 SVG/HTML 源 + 补丁协议"上的 `revise`
  语义不兼容，需另立 ADR；② 美化路线（上传 `.pptx` + 参考图换装），后置。
  不实现 `revise` 即不点亮 `svg_edit` 能力位，按协议它就不进修改工作台——这是协议允许的降级形态
  （`registry.py: implements_hook` 鸭子类型探测），无需任何特判。

### 2. 契约先行（唯一事实源在 `docs/contracts/engine.schema.json`）

1. 在 `DeckRequest.engine` 的 name enum 里加 `"luobo_ppt"`（该枚举须与
   `app/engines/registry.py: ENGINE_REGISTRY` 现登记的 5 个引擎名一致）。
2. 新增 `$defs.luobo_ppt` 作为本引擎 `params_schema`（草案见附录 A，结构照 `$defs.guizang_ppt`）。
3. `docs/contracts/style-guide.md` 补中文参数说明。
4. 跑 `scripts/generate-types.sh` 重生成共享类型（AGENTS 硬规则 1，禁止手写共享类型）。

### 3. 代码接线只三处登记，调用方零改动

`app/engines/factory.py` 顶部注释已承诺"新增引擎只需在注册表加一项，factory 与 `runtime.py`、
容器 entrypoint 都不动"。兑现到文件：

1. 新建 `packages/engine/impl/luobo_ppt/{__init__.py, engine.py}`，类 `LuoboPptEngine`，
   以 `impl/guizang_ppt/engine.py` 为蓝本。
2. `packages/backend/src/app/engines/registry.py` 加两处：
   `_ENGINE_CLASSES["luobo_ppt"] = ("engine.impl.luobo_ppt", "LuoboPptEngine")`
   与 `ENGINE_REGISTRY["luobo_ppt"] = {…}`（含新增 `_load_luobo_ppt_params()`，从 `$defs.luobo_ppt` 读）。
   能力位由 `_derive_capabilities()` 按钩子实现自动派生，**不写死引擎名**。
3. 上游落地：`luobo-ppt` 按 ADR-014 做成**只读 git submodule 钉版本放 `lib/luobo-ppt`**
   （对齐 `lib/ppt-master`、`lib/guizang-ppt-skill`），禁止直接修改（AGENTS 硬规则 4）。

> 注意：`registry.py: output_engine_names()` 驱动的"产物落点契约测试"是注册表驱动的——
> 一旦 `luobo_ppt` 进 `ENGINE_REGISTRY` 且 `engine_type=output`，该测试当场要求登记它的产物形状，
> 漏登即红。这是 ADR-026"假绿灯改真检查"的机制化延续，属预期。

### 4. 依赖清单单一入口

萝卜必需 `python-pptx`、`Pillow`、`numpy`、`lxml` + 一款中文字体；可选 `LibreOffice`（逐页预览更准）、
`pdftoppm`/`PyMuPDF`、`requests`（调生图）。一律并到 `packages/engine/requirements.txt`
（backend 经 `-r ../engine` 引，见依赖单一入口约定），**两 `pyproject` 仍不写 deps**（防双口径）。
LibreOffice/中文字体进镜像。镜像内解释器唯一：`PATH` 首位的 `python3` 必须是应用 venv 解释器
（ADR-012 决策 8）——萝卜脚本靠它跑，否则重演"`sys.executable` ≠ `PATH python3` 分裂"。

### 5. 运行期钩子与产物落点（逐方法对齐 guizang）

- **`prepare_runtime(job_dir)`**：`lib/luobo-ppt/SKILL.md` 缺失 = 硬缺失抛异常（entrypoint 拒绝启动）；
  把 `lib/luobo-ppt` **软链到 `~/.claude/skills/luobo-ppt`**（幂等、遇用户自有副本只提示不覆盖，
  ADR-011 决策 5）；校验萝卜 Python 依赖齐备、探测 LibreOffice，缺则返回**可降级告警**字符串
  （任务仍可跑，预览退回 `render_lite.py` 近似图）。
- **`submit`**：把 Markdown 落成 `job_dir/draft.md` → 组装 `prompt.md` → 起
  `claude -p --dangerously-skip-permissions --output-format stream-json …`，
  `cwd=job_dir`（**不是技能目录**，ADR-011 决策 3）、`start_new_session=True`（自成进程组，供 cancel 连坐）、
  后台 pump 线程 + 超时看门狗。prompt 必含：技能绝对路径 + "先完整读 `SKILL.md` 再按其指引读
  `references/`/`assets/`"（只改 cwd 会静默降级成无流程凭感觉做 PPT，ADR-011 教训）、走"快速模式"
  （见决策 6）、参数直喂、产物落点钉死（见下）。
- **产物落点钉死（ADR-011 决策 4 同款，杜绝冒领旧产物）**：萝卜 pipeline 默认吐
  `out/beautified.pptx`（文件名"主题.pptx/原名_美化版.pptx"只是交付步的改名动作）。prompt 要求
  agent 在 build 完成后把定稿 **`cp` 一份到固定 `<job_dir>/result.pptx`** 作为引擎权威产物；
  `result()` 返回该路径；`status()` 判 `result.pptx` 存在 + mtime ≥ `started_at` + ≥尺寸阈值，
  小文件/截断判失败。逐页 PNG 钉到 `<job_dir>/out/png/slide-NN.png`（见决策 7）。
- **`status` / `result`**：读 `job_dir/job.json`（pid/status/result_path/started_at/error）+ 上面的
  `result.pptx` 判据；引擎态严格 4 值（ADR-018），永不产出 queued/cancelled。
- **`cancel`**：复用 `engine/core/proc.py: terminate_tree`，杀 claude fork 出的
  `python3`（build_deck/render/qa_check）子树。无此钩子的引擎点撤销返 409，绝不假撤销继续烧额度（ADR-019）。
- **`resume`**：沿用 guizang 的断点续跑思路（记 `session_id`，产物缺失时 `claude -p --resume <sid>`，
  共用剩余预算），待真跑验证后按 ADR-011 决策 9 收敛终态归属。

### 6. 无头运行的前置约束（萝卜特有，不处理就必炸）

- **强制"快速模式"**：萝卜 `SKILL.md` 默认有"开工前一次提问"+ 风格卡/大纲/设计规格三轮确认。
  headless 子进程没人应答会卡死、被看门狗误杀。prompt 须显式令其走快速模式或把参数喂全，
  跳过全部中间确认（guizang 实测无人值守 prompt 可自行拍定，同源处置）。
- **禁止运行时联网装依赖**：`setup_check.py --install` 会 `pip install`——离线/内网容器禁用。
  改为镜像预烘焙（决策 4），prompt 声明"依赖已就绪、跳过 setup_check --install"。
- **生图默认走程序化插画兜底**：不配生图模型也能出稿（萝卜 README 全部样张即无生图跑出），
  恰好适配无出网容器。交付说明须如实标注"程序化插画"，不得说成 AI 生成（萝卜硬规则 4 +
  本项目"拒冒领/假删除"纪律）。
- **鉴权沿用网关三坑**（ADR-011 部署事实）：`ANTHROPIC_AUTH_TOKEN`（Bearer）而非 `ANTHROPIC_API_KEY`；
  一并设 `ANTHROPIC_DEFAULT_{SONNET,OPUS,HAIKU}_MODEL` 指到网关模型名。

### 7. 逐页预览的取值来源（本版新增能力，非白拿）

- 萝卜 `pipeline.py` 的 render 步：`render.py out/beautified.pptx out/png --sheet out/sheet.png`，
  LibreOffice 转 PDF 再 `pdftoppm`/PyMuPDF 逐页出 `out/png/slide-NN.png`（前缀 `slide`、两位序号）。
- `get_pages` 枚举 `out/png/slide-*.png`（按序号排）返回 PageRef `{index,name,path,kind:"image"}`，
  `get_preview_root` 返回 `out/png`，preview 静态路由挂此目录、两侧 `resolve()` 防穿越（ADR-012 决策 6）。
- **代价与降级**：与 guizang"扫 HTML 零渲染"不同，逐页预览**依赖渲染步真的跑过**；无 LibreOffice 时
  退回 `render_lite.py` 的近似 PNG（须在预览/交付里标注"近似图"）；连近似都没有时 `get_pages`
  返回 `None`/空，界面回落到原下载形态（协议允许）。`render_lite` 是否与 LibreOffice 路径同用
  `out/png/slide-NN.png` 落点，**落地时核对 `render_lite.py` 实际产出**，不凭 README 措辞当事实。

### 8. 产物守卫（对齐 ADR-021，但换一个抓手）

- guizang 的 `guard.py` 查的是 HTML 骨架（`#deck/#nav/#hint`、`SLIDES_HERE` 残留），萝卜产 pptx，
  **无 HTML 骨架可查**——该守卫不可照搬。
- 引擎侧最低守卫（存在≠能用）：解包 `.pptx`（zip）校验可打开、`python-pptx` 读页数 ≥1、
  与 `deck.json` 页数一致、`result.pptx` 非空且 mtime 合规。
- **可选增强（倾向采纳）**：收割后调用萝卜自带 `qa_check.py`——它的 fidelity（丢字逐页核对，少一行
  点名页/句=ERROR）、overflow（CJK 字体度量）、contrast（WCAG）、rail/accent 等本就是"内容保真"
  的确定性检查，比 HTML 守卫更贴本项目痛点。定档：引擎在 `result.pptx` 就绪后跑一次 `qa_check.py`，
  有 ERROR 则判 failed 并落根因，WARN 记入交付说明。此条待评审确认是否纳入本版。

### 9. 离线/测试钩子收编

`DECKSMITH_FORCE_STUB=1` 时凡注册过的真实引擎一律落 stub（`factory.py` 据注册表判，非白名单）。
`luobo_ppt` 一进 `ENGINE_REGISTRY` 就自动被该钩子覆盖，**不得**在离线 CI 里绕过、真拉 claude 烧额度。

## 后果

- **正面**：兑现 ADR-001 引擎可插拔——加引擎在调用方零改动，只动契约 + 实现 + 三处登记；
  产物质量随 `lib/luobo-ppt` submodule 版本演进；补上一条**原生可编辑 `.pptx`** 交付形态
  （符合"用户拿回去能自己改字"的产品取向）；若纳入决策 8 的 `qa_check.py`，把萝卜的
  丢字/溢出/对比度保真校验引进来，直击本项目在 guizang 骨架上反复踩的内容保真坑。
- **代价**：引入 `python-pptx`/LibreOffice/中文字体，镜像更重、多一条分钟级 claude 长任务烧额度；
  逐页预览依赖渲染步、非白拿（不像 guizang 扫 HTML 那样零成本）；**改稿能力缺口**（不支持
  修改工作台），与 guizang/ppt_master 的既有体验不对齐，需在界面/文案上对 `luobo_ppt` 说明。
- **不可当已证（真跑是唯一裁判，同 ADR-011/012）**：产物落点、页数一致、逐页 PNG 是否真按
  `out/png/slide-NN.png` 落、`render_lite` 近似图质量、快速模式能否真的免确认跑完、单次 token 量级
  ——全部待真跑。**真跑前按用户铁律先给量化证据（预估 tokens/耗时）再征求同意，不擅自起**。

## 落地门禁（顺序即依赖，未过上一项不进下一项）

1. 契约：改 `engine.schema.json`（name enum + `$defs.luobo_ppt`）→ `generate-types.sh` →
   `check-alignment`/假绿灯检查真读该目录（吸取 ADR-026：别用硬编码清单蒙混）。
2. submodule `lib/luobo-ppt` 落地、钉版本、只读，产物里 0 宿主绝对路径。
3. 引擎实现 + registry 三处登记；单测覆盖 submit/cancel/prepare_runtime，`FORCE_STUB` 下不真拉子进程。
4. 产物落点契约测试转绿（`output_engine_names()` 驱动枚举，登记 `luobo_ppt` 的 `.pptx` 形状）。
5. 真跑一个小样例（如 5 页 mint-fresh）验证落点 + 逐页预览 + 守卫——**须先给量化证据并获同意**。

---

## 附录 A：`$defs.luobo_ppt` params_schema 草案（仅草案，本轮不改契约文件）

```jsonc
"luobo_ppt": {
  "type": "object",
  "description": "luobo-ppt 引擎在 engine_params 中支持的参数。中文说明见 docs/contracts/style-guide.md。此定义即 GET /api/engines 返回的 params_schema 内容。",
  "properties": {
    "route": {
      "type": "string",
      "enum": ["generate"],            // 本版只开放生成路线；beautify 待后续 ADR
      "default": "generate",
      "description": "路线：generate=由 Markdown 草稿生成原生 PPT（本版仅此一项）"
    },
    "style": {
      "type": "string",
      "enum": [
        "mint-fresh", "business-blue", "warm-orange", "dark-tech",
        "lavender-soft", "paper-notes", "black-gold", "geo-blocks",
        "cobalt-type", "ink-lime", "pastel-mist", "wave-launch",
        "ink-magazine", "swiss-grid"
      ],
      "default": "business-blue",
      "description": "萝卜 14 套内置风格之一（无参考图时直接报名）"
    },
    "pages": {
      "type": "integer",
      "description": "目标页数（不传由内容决定；参考：15 分钟≈10 页）"
    },
    "notes": {
      "type": "boolean",
      "default": true,
      "description": "是否为每页生成口播备注"
    },
    "projection": {
      "type": "boolean",
      "default": false,
      "description": "投影模式：抬高字号下限，适配会议室投影"
    },
    "preview": {
      "type": "boolean",
      "default": true,
      "description": "是否渲染逐页 PNG 供界面逐页预览；关闭则回落到下载形态"
    }
  }
}
```

> 参数面与上游一致性：`style` 枚举取自萝卜 `references/styles/`（index.json + 每套 json）；
> `route`/力度 light·standard·full/是否精修文字等属美化路线参数，本版不暴露，留待美化路线 ADR。

## 附录 B：为什么不用"改一份 guizang 引擎带过"而要新增独立引擎

沿用 ADR-015 的判断：产物形态与改稿语义不同（HTML 逐页 vs 二进制 `.pptx`；扫 `<section>` 预览 vs
渲染 PNG 预览；补丁改稿 vs 重跑 pipeline），继承 `GuizangPptEngine` 会让钩子语义混叠、
能力位误标。独立实现、按钩子探测能力位，才守得住 ADR-001"调用方只依赖协议与注册表"。
