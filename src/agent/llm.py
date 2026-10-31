"""LLM 客户端抽象层。

W1 使用 MockLLM 跑通端到端，不依赖真实 API。
真实接入时实现 LLMClient 接口即可（function calling 格式）。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """返回 {"content": str | None, "tool_calls": [...] | None}。"""


class MockLLMClient(LLMClient):
    """用于演示与测试的 mock LLM，按预设脚本响应。"""

    def __init__(self) -> None:
        # 简单的关键词 → 响应脚本
        self._script = [
            {"role": "assistant", "content": "您好，我是财务专员小账。请提供您的合同号以便核实。"},
        ]
        self._call_count = 0

    def chat(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._call_count += 1
        last_user = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user = m["content"]
                break

        # 关键词路由
        if "合同" in last_user and "HT-" in last_user:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "query_receivable",
                            "arguments": json.dumps({"contract_no": "HT-Y2026-012"}),
                        },
                    }
                ],
            }
        if "政策" in last_user or "分期" in last_user or "减免" in last_user:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "query_policy",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        if "承诺" in last_user or "付款" in last_user or "同意" in last_user:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_3",
                        "type": "function",
                        "function": {
                            "name": "record_promise",
                            "arguments": json.dumps({
                                "contract_no": "HT-Y2026-012",
                                "pay_date": "2026-11-15",
                                "amount": 380000,
                            }),
                        },
                    }
                ],
            }
        # 默认：给出协商回复
        return {
            "content": f"好的，关于您提到的「{last_user[:20]}」，我帮您核实一下。根据合同记录，您目前有一笔逾期账款需要协商付款安排。",
            "tool_calls": None,
        }


def get_llm_client() -> LLMClient:
    """获取 LLM 客户端。W1 默认用 mock。"""
    return MockLLMClient()
