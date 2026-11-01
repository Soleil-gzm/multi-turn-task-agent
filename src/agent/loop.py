"""Agent ReAct 循环：LLM + 工具 + 状态机 + guardrails + trace。

在线侧数据流转：
  请求 → 会话装载 → guardrails 输入检查 → Agent 循环（LLM + 工具 + 状态机）
  → 工具返回隔离 → guardrails 输出校验 → 持久化 → trace 落盘 → 响应
"""

from __future__ import annotations

import json
import re
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
    replies: list[str] = field(default_factory=list)


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
        replies: list[str] = []
        last_contract_no: str | None = None
        escalation_reason: str | None = None

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
            # 进入新一轮：轮次 +1（转移条件 round > 5 依赖此计数）
            state = self.sm.next_turn(state)

            # 用户意图识别（W1 用关键词，W2 可换 LLM 分类）
            if any(k in user_msg for k in ("分期", "延期", "减免", "少付", "宽限")):
                facts["counter_offer"] = True
            if any(k in user_msg for k in ("同意", "可以", "行", "没问题")):
                facts["offer_accepted"] = True
            if any(k in user_msg for k in ("不同意", "拒绝", "不谈了", "滚")):
                facts["customer_refuse"] = True
            if any(k in user_msg for k in ("算了", "撤回", "刚才不算")):
                facts["customer_withdraw"] = True
            # 身份 / 单据核对失败：客户无法提供合同号等核验信息
            if any(k in user_msg for k in ("不记得", "不知道", "忘了", "记不清", "没记住")):
                facts["verify_failures"] += 1

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

            # 超政策请求：显式拒绝并给出政策内替代（规则由场景配置声明）
            refusal = self._policy_refusal(user_msg)
            if refusal:
                builder.add_step(
                    type=StepType.GUARDRAIL,
                    rule="policy_refusal",
                    detail=refusal,
                    latency_ms=1,
                )
                facts["policy_violation_attempt"] += 1

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
                if args.get("contract_no"):
                    last_contract_no = args["contract_no"]
                result = self.registry.invoke(name, **args)
                builder.add_step(
                    type=StepType.TOOL_CALL,
                    tool=name,
                    args=args,
                    result=result,
                    latency_ms=result.get("meta", {}).get("latency_ms", 10),
                    degraded=not result.get("ok", False),
                )

                # 矛盾数据必须主动告知（C4）：字段与话术由场景配置声明
                contradiction = self._contradiction_reply(name, result)
                if contradiction:
                    reply = contradiction

                # 更新事实状态
                if name == "query_receivable":
                    if result.get("ok"):
                        facts["amount_informed"] = True
                        facts["identity_verified"] = True
                    else:
                        # 合同号查不到 = 核对失败（IDENTIFY 阶段累计 2 次升级人工）
                        facts["verify_failures"] += 1
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

            # 超政策请求的拒绝话术优先于工具产出话术
            if refusal:
                reply = refusal

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
                if state.stage == (self.config.state_machine.escalate_to or ""):
                    escalation_reason = f"{old} -> {state.display()}：{t.when}"
                if self.sm.is_terminal(state):
                    break

            # 升级分支必须落工单：进入升级状态时调用一次 escalate（工具名由配置声明）
            escalate_to = self.config.state_machine.escalate_to
            escalate_tool = self.config.state_machine.escalate_tool
            if (
                escalate_to
                and escalate_tool
                and state.stage == escalate_to
                and escalate_tool not in tool_names_called
                and escalate_tool in self.registry.names()
            ):
                if escalation_reason is None:
                    escalation_reason = f"进入 {escalate_to}（round {state.round_no}）"
                esc_args = {
                    "contract_no": last_contract_no or "UNKNOWN",
                    "reason": escalation_reason,
                }
                result = self.registry.invoke(escalate_tool, **esc_args)
                tool_names_called.append(escalate_tool)
                builder.add_step(
                    type=StepType.TOOL_CALL,
                    tool=escalate_tool,
                    args=esc_args,
                    result=result,
                    latency_ms=result.get("meta", {}).get("latency_ms", 10),
                    degraded=not result.get("ok", False),
                )

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
            replies.append(reply)

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
            replies=replies,
        )

    def _contradiction_reply(self, tool_name: str, result: dict[str, Any]) -> str | None:
        """C4：工具返回两个字段不一致时，按配置话术主动告知客户。"""
        rule = self.config.response.contradiction if self.config.response else None
        if rule is None or tool_name != rule.tool or not result.get("ok"):
            return None
        data = result.get("data")
        if not isinstance(data, dict) or not all(f in data for f in rule.fields):
            return None
        left, right = rule.fields[0], rule.fields[1]
        if data[left] == data[right]:
            return None
        return rule.template.format(**{f: data[f] for f in rule.fields})

    def _policy_refusal(self, text: str) -> str | None:
        """超政策请求：抽取值与 policy 上限比较，超出则按配置模板显式拒绝并给替代方案。"""
        rules = self.config.response.policy_refusal if self.config.response else []
        for rule in rules:
            m = re.search(rule.pattern, text)
            if not m:
                continue
            raw = m.group(1)
            try:
                value = float(raw)
            except ValueError:
                continue
            limit = self.policy.get(rule.policy_field)
            if limit is None:
                continue
            actual = value / 100 if rule.scale == "percent" else value
            if actual <= limit:
                continue
            limit_display = f"{limit:.0%}" if rule.scale == "percent" else f"{limit:g}"
            return rule.template.format(value=raw, limit=limit_display)
        return None

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
