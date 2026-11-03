"""测试隔离：开发者本地 `.env` 可能配置了真实模型。

测试一律默认 Mock，避免跑单测时产生真实 API 调用；
需要验证 `.env`/真实模型行为的用例（tests/test_env.py）会显式删除 `MTA_LLM` 后自行断言。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _force_mock_llm(monkeypatch):
    monkeypatch.setenv("MTA_LLM", "mock")
