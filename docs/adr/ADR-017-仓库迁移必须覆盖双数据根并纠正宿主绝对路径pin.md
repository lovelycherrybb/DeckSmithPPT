# ADR-017: 仓库迁移必须覆盖双数据根并纠正宿主绝对路径 pin

- 状态: 已接受，**已实现并验证**（决策 1-6 全部落地，见「验证结果」）
- 日期: 2026-09-24
- 决策者: 龚成
- 相关: ADR-012（交付形态与引擎运行时数据布局）、ADR-013（升级期数据安全）、
  ADR-015（ppt-master 双引擎策略）、
  `docs/research/2026-09-24-迁移后产物与pin路径失配.md`（本 ADR 全部证据的出处所在）

## 背景

仓库从 iCloud 路径迁至本地路径后，任务详情页的逐页预览（页墙）全部为空，且接口不报错、日志无异常。
排查确认这是两层相互独立的失配叠加，任何一层单独修复都不足以让新副本真正自持：

其一，**数据根被漏搬**。本地开发模式与容器模式的数据根是两套互不相干的目录：
`config.py:15` 写死 `_DEFAULT_JOBS_ROOT = REPO_ROOT / "outputs" / "jobs"`，同文件 `:18` 的默认库是
`REPO_ROOT/outputs/decksmith.db`；容器则走 `DATABASE_URL=sqlite:////data/db/decksmith.db` 与
`deploy/data/jobs` 的 bind 卷。既有迁移清单只覆盖 `deploy/data/`，`outputs/` 整目录不在其中，
新副本的 `outputs/jobs` 是后端启动时新建的空壳。

其二，**pin 掩盖了残留依赖**。`job.json` 里钉的是未解析的宿主绝对路径。把产物文件搬回新副本后实测，
10 个有页任务中仍有 9 个解析到旧路径——因为旧副本还在、其中 SVG 还在，
`resolve_svg_output_dir`（`engine.py:246`）第一步“pin 存在且确有页 SVG”即命中，
自愈回写永不触发。表面“修好了”，实则偷读旧路径，旧副本一旦处置就会当场再空一次。

同时明确一条判据教训：`list_pages`（`service/jobs.py:267`）在产物解析不到时把异常吞成空页列表，
因此**不能以“有没有报错”判断预览健康**；而 `ownership.py:65` 使匿名请求一律返回空列表或 404，
因此**不能以 `curl` 结果判断数据可见性**。本轮两次误判（把匿名空响应读成“数据丢了”、
把匿名 404 读成“修坏了”）都源于此。

## 决策

1. **迁移清单必须同时覆盖两个数据根**：`outputs/`（dev 默认）与 `deploy/data/`（容器 bind 卷）。
   只搬其一即视为迁移未完成。
2. **判活库用结构判据，不用文件名或路径猜测**：比对 `users` 表有无与 `projects`/`jobs` 条数。
   本次即靠此识别出 `deploy/data/db/decksmith.db` 是停在 09-18 的 3 项目 / 7 任务旧 schema，
   而真正在用的是 `outputs/decksmith.db` 的 22 项目 / 53 任务 / 2 用户。
3. **逐页产物必须保 mtime 搬迁**：用 `tar -cf - jobs | tar -xpf -` 而非不带 `-p` 的 `cp -R`。
   启发式定位要求项目目录 `mtime >= started_at`，mtime 丢失会让产物“在但认不出”。
4. **搬完必须改写 `job.json` 的宿主绝对路径前缀**（实测涉及 `result_path` 46 处、
   `svg_output_dir` 16 处、`value` 1 处，共 47 个文件），且改写前先拍备份。
   **不得把“自愈回写”当作迁移手段**——自愈只在 pin 失效时触发，而旧副本存活期间它不会失效。
5. **复验走三段，缺一不可**：引擎层直接调 `resolve_svg_output_dir` 确认全部落新路径；
   接口层借真实浏览器会话打 `/api/jobs/<id>/pages` 看 `pages_generated`；
   文件层实际取一枚 `preview/svg_output/*.svg` 确认可渲染（覆盖预览端点的类型白名单与防穿越守卫）。
   命令行 `curl` 只用于证明服务存活，不得作为数据可见性的结论依据。
6. **核对页墙前先按引擎类型排除草稿类任务**：`claude_draft`、`ppt_master_draft`、`guizang_ppt`
   本就不产逐页，未出页的 `ppt_master` 亦属正常，不得计为丢失。

## 后果

正面：迁移从“搬完看着能用”变为“可验证自持”——残留旧路径归零后，新副本对旧副本不再有任何运行依赖，
旧副本的处置时机由本 ADR 的验证结论决定，而不是靠运气发现。

代价与约束：多一次备份与改写；并且由此确立一条顺序纪律——**先改写、再三段复验、最后才允许处置旧副本**。
顺序颠倒会把隐性依赖留到旧副本消失那天才暴露。日志与 md 中另有 77 个文件含旧绝对路径，
属历史运行记录，**有意不改写**，改了等于伪造现场。

本次未覆盖：`dist/` 下 v0.2.5 双架构交付包（约 1.6GB）与旧副本 `lib/ppt-master/projects/`
的 374 个历史污染产物仍留在原处，后者对应当前 2 个任务的“原始产出”，如需回溯只能回旧副本取。

待议（本 ADR 未采纳，因超出迁移口径）：根因是 pin 存了宿主绝对路径、且数据根随 `REPO_ROOT` 派生。
根治方案是二者之一——`job.json` 的 `svg_output_dir`/`result_path` 改存相对 `jobs_root` 的路径，
或把 `jobs_root`/`DATABASE_URL` 统一提升为显式配置项。此项另立 ADR，不在本 ADR 范围内。

## 验证结果

产物搬迁后两侧计数一致：1266 文件 / 138 枚 SVG，抽样 mtime 一致。
改写前备份为 `outputs/backup/jobjson-pre-repath-20260924T053614Z.tar.gz`（47 个文件），
改写后旧路径残留 0。引擎层 10/10 解析到新路径、仍指旧路径 0，
其中 2 个原钉在 `lib/ppt-master/projects/` 的任务由自愈重新定位到各自 `<jobs_root>/<job_id>/projects/`。
接口层带会话实测 `b6966761…` 返回 10 页、`ab2c66f9…` 返回 5 页，`supports_page_preview` 均为 true。
文件层取 `01_cover.svg` 在浏览器真实渲染出封面文字。`git status` 全程保持 0 改动。
