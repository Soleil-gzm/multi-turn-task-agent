"""milktea 场景的离线演示 LLM（确定性，无外部依赖）。

作用：让第二场景在没有真实模型的条件下也能触发工具调用，
从而验证「工具层也可由场景注入」——而不是只验证状态机。

真实接入时把 yaml 的 `demo.llm` 指向真实客户端工厂即可，本文件可删。
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.agent.llm import LLMClient

_MEMBER_RE = re.compile(r"M\d{4}")


class MilkTeaDemoLLM(LLMClient):
    """按关键词路由到 query_member / check_benefit / book_visit。"""

    def chat(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break

        member_id = self._extract_member(messages)

        if "积分" in last_user or "权益" in last_user or "到期" in last_user:
            return self._call(
                "check_benefit",
                {"member_id": member_id},
                f"我帮您查一下 {member_id} 的积分与权益。",
            )
        if "预约" in last_user or "到店" in last_user:
            return self._call(
                "book_visit",
                {"member_id": member_id, "visit_date": "2026-11-08"},
                "好的，我帮您登记这周六到店。",
            )
        if "会员" in last_user or _MEMBER_RE.search(last_user):
            return self._call(
                "query_member",
                {"member_id": member_id},
                "好的，我先核实一下您的会员信息。",
            )
        return {"content": "您好，我是茗香茶饮会员回访专员。请提供您的会员号，我帮您查询权益。", "tool_calls": None}

    @staticmethod
    def _extract_member(messages: list[dict[str, str]]) -> str:
        for m in reversed(messages):
            if m.get("role") != "user":
                continue
            found = _MEMBER_RE.search(m.get("content", ""))
            if found:
                return found.group(0)
        return "M1001"

    @staticmethod
    def _call(name: str, args: dict, content: str) -> dict[str, Any]:
        return {
            "content": content,
            "tool_calls": [
                {
                    "id": f"call_{name}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        }


def build() -> LLMClient:
    """yaml `demo.llm` 的工厂入口。"""
    return MilkTeaDemoLLM()
