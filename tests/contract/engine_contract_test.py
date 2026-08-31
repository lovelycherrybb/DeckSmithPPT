"""引擎契约测试：所有注册的引擎必须满足同一契约。

TODO: 接入真实引擎后，用同一输入验证所有实现输出一致的结构。
"""

from pathlib import Path


def test_engine_contract_exists() -> None:
    schema = Path(__file__).parents[2] / "docs" / "contracts" / "engine.schema.json"
    assert schema.exists(), "缺少 engine.schema.json 契约"
