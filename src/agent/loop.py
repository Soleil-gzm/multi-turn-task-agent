"""Agent ReAct 循环：LLM + 工具 + 状态机 + guardrails + trace。

在线侧数据流转：
  请求 → 会话装载 → guardrails 输入检查 → Agent 循环（LLM + 工具 + 状态机）
  → 工具返回隔离 → guardrails 输出校验 → 持久化 → trace 落盘 → 响应
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.core.config import ScenarioConfig
from src.core.trace import Trace, TraceBuilder, StepType
from src.guardrails.guardrails import check_input, check_output, sanitize_tool_output
from src.state.machine import State, StateMachine
from src.tools.registry import ToolRegistry


@dataclass
class AgentResult:
    reply: str
    stage: str
    tool_calls: list[str]
    trace: Trace
    task_completed: bool
    violations: list[str] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        config: ScenarioConfig,
        registry: ToolRegistry,
        llm_client: Any,
    ) -> None:
        self.config = config
        self.registry = registry
        self.llm = llm_client
        self.sm = StateMachine(config)
        self.policy = config.policy.model_dump()

    def run(self, session_id: str, user_messages: list[str], case_id: str | None = None) -> AgentResult:
        """运行一次多轮对话。user_messages 为模拟用户的逐轮输入。"""
        builder = TraceBuilder(session_id=session_id, case_id=case_id)
        state = self.sm.initial_state()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._build_system_prompt()},
        ]
        violations: list[str] = []
        tool_names_called: list[str] = []

        # 跟踪整个对话过程中的事实状态（供转移条件使用）
        facts = {
            "session_start": True,
            "identity_verified": False,
            "amount_informed": False,
            "counter_offer": False,
            "counter_exceeds_policy": False,
            "counter_within_policy": False,
            "offer_accepted": False,
            "promise_recorded": False,
            "customer_withdraw": False,
            "redline_hit": False,
            "customer_refuse": False,
            "verify_failures": 0,
            "policy_violation_attempt": 0,
        }

        for user_msg in user_messages:
            # 用户意图识别（W1 用关键词，W2 可换 LLM 分类）
            if any(k in user_msg for k in ("分期", "延期", "减免", "少付", "宽限")):
                facts["counter_offer"] = True
            if any(k in user_msg for k in ("同意", "可以", "行", "没问题")):
                facts["offer_accepted"] = True
            if any(k in user_msg for k in ("不同意", "拒绝", "不谈了", "滚")):
                facts["customer_refuse"] = True
            if any(k in user_msg for k in ("算了", "撤回", "刚才不算")):
                facts["customer_withdraw"] = True

            # 输入侧 guardrail
            input_check = check_input(user_msg)
            if input_check["hit"]:
                builder.add_step(
                    type=StepType.GUARDRAIL,
                    rule="input_override_pattern",
                    detail=str(input_check["patterns"]),
                    latency_ms=1,
                )
                facts["policy_violation_attempt"] += 1
                violations.append(f"输入越权指令: {input_check['patterns']}")

            messages.append({"role": "user", "content": user_msg})

            # LLM 调用
            llm_resp = self.llm.chat(messages, tools=self.registry.function_calling_schemas())
            builder.add_step(
                type=StepType.LLM_CALL,
                input_tokens=len(json.dumps(messages)),
                output_tokens=len(json.dumps(llm_resp)),
                output=llm_resp,
                latency_ms=50,
            )

            reply = llm_resp.get("content") or ""

            # 工具调用
            tool_calls = llm_resp.get("tool_calls") or []
            for tc in tool_calls:
                fn = tc["function"]
                name = fn["name"]
                try:
                    args = json.loads(fn["arguments"]) if fn["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}

                tool_names_called.append(name)
                result = self.registry.invoke(name, **args)
                builder.add_step(
                    type=StepType.TOOL_CALL,
                    tool=name,
                    args=args,
                    result=result,
                    latency_ms=result.get("meta", {}).get("latency_ms", 10),
                    degraded=not result.get("ok", False),
                )

                # 更新事实状态
                if name == "query_receivable" and result.get("ok"):
                    facts["amount_informed"] = True
                    facts["identity_verified"] = True
                elif name == "query_policy" and result.get("ok"):
                    # W1 简化：查了政策即认为方案在政策内
                    facts["counter_within_policy"] = True
                elif name == "record_promise" and result.get("ok"):
                    facts["promise_recorded"] = True

                # 工具返回隔离（间接注入防护）
                safe_data = sanitize_tool_output(result.get("data"))
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": safe_data})

                if name == "record_promise" and result.get("ok"):
                    reply = "好的，已为您记录付款承诺。"

            # 状态转移：循环直到无匹配转移（一次工具调用可能触发多级转移）
            context = {**facts, "round": state.round_no}
            while True:
                t = self.sm.find_transition(state, context)
                if t is None:
                    break
                old = state.display()
                state = self.sm.apply_transition(state, t)
                builder.add_step(
                    type=StepType.STATE_TRANSITION,
                    state_before=old,
                    state_after=state.display(),
                    latency_ms=1,
                )
                if self.sm.is_terminal(state):
                    break

            # 输出侧 guardrail
            out_check = check_output(reply, self.policy)
            if not out_check["ok"]:
                builder.add_step(
                    type=StepType.GUARDRAIL,
                    rule="output_forbidden",
                    detail=str(out_check["violations"]),
                    latency_ms=1,
                )
                violations.extend(out_check["violations"])

            messages.append({"role": "assistant", "content": reply})

            if self.sm.is_terminal(state):
                break

        trace = builder.finalize(
            stage=state.stage,
            task_completed=state.stage == "CLOSED",
            violations=violations,
        )

        return AgentResult(
            reply=reply,
            stage=state.stage,
            tool_calls=tool_names_called,
            trace=trace,
            task_completed=state.stage == "CLOSED",
            violations=violations,
        )

    def _build_system_prompt(self) -> str:
        p = self.config.policy
        return (
            f"你是{self.config.scenario.display_name}专员。"
            f"任务：{self.config.goal.success_criteria}。\n"
            f"政策边界：分期≤{p.max_installments}期，延期≤{p.max_extension_days}天，"
            f"减免≤{p.max_waiver_ratio:.0%}。\n"
            "禁止使用威胁性话术，不得编造未提供的单据数据。"
        )


def new_session_id() -> str:
    return f"s_{uuid.uuid4().hex[:12]}"
