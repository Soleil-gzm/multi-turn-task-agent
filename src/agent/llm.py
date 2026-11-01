"""LLM 客户端抽象层。

W1 使用 MockLLM 跑通端到端，不依赖真实 API。
真实接入时实现 LLMClient 接口即可（function calling 格式）。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any


_CONTRACT_RE = re.compile(r"HT-Y2026-\d{3}")
_AMOUNT_RE = re.compile(r"'amount':\s*(\d+)")


class LLMClient(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """返回 {"content": str | None, "tool_calls": [...] | None}。"""


class MockLLMClient(LLMClient):
    """用于演示与测试的 mock LLM，按预设脚本响应。

    参数单一真源：
      - contract_no 取自用户输入（最近一条含合同号的用户消息）
      - amount 取自已查询的账面数据（最近一条 query_receivable 工具返回）
    任何分支都不再硬编码合同号/金额，避免跨合同样本查到同一份数据。
    """

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

        # 参数真源：合同号来自用户输入，金额来自已查询的账面数据
        contract_no = self._extract_contract_no(messages)
        amount = self._extract_amount(messages)

        # 关键词路由
        if "合同" in last_user and "HT-" in last_user:
            return self._tool_call("query_receivable", {"contract_no": contract_no}, "call_1")
        if "金额不对" in last_user or "对账" in last_user or "不符" in last_user or "差异" in last_user:
            return self._tool_call("diff_statement", {"contract_no": contract_no}, "call_4")
        if (
            "政策" in last_user
            or "分期" in last_user
            or "减免" in last_user
            or "延期" in last_user
            or "宽限" in last_user
        ):
            return self._tool_call("query_policy", {}, "call_2")
        if "承诺" in last_user or "付款" in last_user or "同意" in last_user:
            return self._tool_call(
                "record_promise",
                {"contract_no": contract_no, "pay_date": "2026-11-15", "amount": amount},
                "call_3",
            )
        # 默认：给出协商回复（不假设已查询，避免编造）
        return {
            "content": f"好的，关于您提到的「{last_user[:20]}」，我帮您核实一下。",
            "tool_calls": None,
        }

    @staticmethod
    def _extract_contract_no(messages: list[dict[str, str]]) -> str:
        """从最近一条含合同号的用户消息提取合同号（真源：用户输入）。"""
        for m in reversed(messages):
            if m.get("role") != "user":
                continue
            found = _CONTRACT_RE.search(m.get("content", ""))
            if found:
                return found.group(0)
        return "HT-Y2026-012"

    @staticmethod
    def _extract_amount(messages: list[dict[str, str]]) -> int:
        """从最近一条工具返回中提取 amount（真源：query_receivable 账面数据）。"""
        for m in reversed(messages):
            if m.get("role") != "tool":
                continue
            found = _AMOUNT_RE.search(m.get("content", ""))
            if found:
                return int(found.group(1))
        return 380000

    @staticmethod
    def _tool_call(name: str, args: dict, call_id: str) -> dict[str, Any]:
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        }


def get_llm_client() -> LLMClient:
    """获取 LLM 客户端。W1 默认用 mock。"""
    return MockLLMClient()
