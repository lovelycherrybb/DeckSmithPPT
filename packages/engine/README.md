# packages/engine — PPT 生成引擎

- `core/` — 抽象层：PptEngine 协议 + 注册表 + 数据模型
- `impl/ppt_master/` — 第一个实现（驱动 `lib/ppt-master/`）

## 新增一个引擎

1. 在 `impl/<name>/` 下实现 `core/protocol.py` 的 `PptEngine`。
2. 满足 `docs/contracts/engine.schema.json`。
3. 在 `core/registry.py` 注册。
4. 通过 `tests/contract/engine_contract_test.py`。

## 说明

- 引擎是"长任务"语义：submit/status/result/resume，禁止同步阻塞调用。
- 引擎抽象与契约对齐，契约变更后运行 `bash scripts/generate-types.sh` 重新生成模型。
