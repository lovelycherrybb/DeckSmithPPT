"""API 契约测试：后端响应必须符合 docs/contracts/openapi.yaml。

TODO: 选型后接入 schema 校验（如 schemathesis / pact），当前为占位。
"""

from pathlib import Path


def test_openapi_contract_exists() -> None:
    contract = Path(__file__).parents[2] / "docs" / "contracts" / "openapi.yaml"
    assert contract.exists(), "缺少 openapi.yaml 契约"
