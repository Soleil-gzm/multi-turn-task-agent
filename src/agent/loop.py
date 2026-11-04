"""Agent ReAct 循环：LLM + 工具 + 状态机 + guardrails + trace。

在线侧数据流转：
  请求 → 会话装载 → guardrails 输入检查 → Agent 循环（LLM + 工具 + 状态机）
  → 工具返回隔离 → guardrails 输出校验 → 持久化 → trace 落盘 → 响应

两种调用方式（共享同一套事实状态、转移与 trace 语义）：
  - 会话级增量（UI / 服务）：`Agent.start()` → `session.send(msg)` → `TurnResult`
  - 批量（demo / eval）：`Agent.run(session_id, user_messages)` → `AgentResult`
    内部同样是「start + 连续 send」，到终态即停止。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.core.config import ScenarioConfig
from src.core.trace import Trace, TraceBuilder, StepType, TraceOutcome
from src.guardrails.guardrails import check_input, check_output, sanitize_tool_output
from src.state.machine import State, StateMachine
from src.tools.registry import ToolRegistry


@dataclass
class AgentResult:
    """批量运行结果（demo / eval 使用）。

    closed / goal_achieved 是两个不同口径：
      - closed：到达成功终态（状态机层面结束）
      - goal_achieved：成功终态 + 场景声明的目标工具（goal.on_success）已成功调用
    """

    reply: str
    stage: str
    tool_calls: list[str]
    trace: Trace
    closed: bool
    goal_achieved: bool
    escalated: bool
    violations: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)


@dataclass
class TurnResult:
    """单轮结果（会话级增量 / UI 使用）。"""

    reply: str
    stage: str
    round_no: int
    offer_state: str | None
    tool_calls: list[str]          # 本轮调用的工具
    trace: Trace                   # 截至本轮的累计快照（含 outcome）
    closed: bool
    goal_achieved: bool
    escalated: bool
    violations: list[str]          # 累计违规
    ended: bool                    # 是否已到终态（成功 / 升级）
    new_steps: int                 # 本轮新增 trace 步数


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

    def start(self, session_id: str, case_id: str | None = None) -> "AgentSession":
        """创建一个会话级增量会话：每轮只跑当前输入，不重放历史。"""
        return AgentSession(self, session_id, case_id)

    def run(self, session_id: str, user_messages: list[str], case_id: str | None = None) -> AgentResult:
        """批量接口（demo / eval 兼容）：逐条 send，到达终态即停止。"""
        session = self.start(session_id, case_id)
        for user_msg in user_messages:
            if session.send(user_msg).ended:
                break
        return session.result()

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
            f"政策边界：分期≤{p.max_installments}期（仅限制期数，不限制末期日期），"
            f"延期≤{p.max_extension_days}天（最晚付款日为到期日后 {p.max_extension_days} 天），"
            f"减免≤{p.max_waiver_ratio:.0%}。\n"
            "禁止使用威胁性话术，不得编造未提供的单据数据。"
        )


class AgentSession:
    """会话级增量执行器：持有跨轮上下文，每次 `send` 只跑一轮。

    与批量接口的关系：`Agent.run` 就是「start + 连续 send」的薄封装，
    因此增量与批量两条路径不会出现语义漂移。
    """

    def __init__(self, agent: Agent, session_id: str, case_id: str | None = None) -> None:
        self.agent = agent
        self.session_id = session_id
        self.case_id = case_id
        self.builder = TraceBuilder(session_id=session_id, case_id=case_id)
        self.state: State = agent.sm.initial_state()
        self.messages: list[dict[str, str]] = [
            {"role": "system", "content": agent._build_system_prompt()},
        ]

        # 跨轮事实状态（供转移条件使用）
        self.facts: dict[str, Any] = {
            "session_start": True,
            "identity_verified": False,
            "amount_informed": False,
            "counter_offer": False,
            "counter_exceeds_policy": False,
            "counter_within_policy": False,
            "offer_accepted": False,
            "goal_tool_called": False,
            "customer_withdraw": False,
            "redline_hit": False,
            "customer_refuse": False,
            "verify_failures": 0,
            "policy_violation_attempt": 0,
        }
        self.violations: list[str] = []
        self.tool_names_called: list[str] = []
        self.replies: list[str] = []
        self.last_contract_no: str | None = None
        self.escalation_reason: str | None = None
        self.last_reply = ""
        self.ended = False

    # -- 只读视图 ---------------------------------------------------------
    @property
    def stage(self) -> str:
        return self.state.stage

    @property
    def round_no(self) -> int:
        return self.state.round_no

    @property
    def offer_state(self) -> str | None:
        return self.state.offer_state

    # -- 对外接口 ---------------------------------------------------------
    def send(self, user_msg: str) -> TurnResult:
        """处理一轮用户输入，返回本轮结果（trace 为累计快照）。"""
        if self.ended:
            return self._snapshot(reply="当前会话已结束，请重置后重新开始。", tool_calls=[], new_steps=0)
        before = len(self.builder.trace.steps)
        self._run_turn(user_msg)
        self.ended = self.agent.sm.is_terminal(self.state)
        new_steps = len(self.builder.trace.steps) - before
        turn_tools = [
            s.tool
            for s in self.builder.trace.steps[before:]
            if s.type == StepType.TOOL_CALL and s.tool
        ]
        return self._snapshot(reply=self.last_reply, tool_calls=turn_tools, new_steps=new_steps)

    def _signals(self) -> tuple[bool, bool, bool]:
        """三个独立口径：closed / goal_achieved / escalated。

        goal_achieved 不等于 closed：到达成功终态但没有调用目标工具（如只在口头确认后
        直接走到 CLOSED）算结案、不算达成。目标工具由 `goal.on_success` 声明，
        框架不内含业务工具名。
        """
        cfg = self.agent.config
        stage = self.state.stage
        closed = stage == cfg.state_machine.success_stage
        escalated = bool(cfg.state_machine.escalate_to) and stage == cfg.state_machine.escalate_to
        goal_tool = cfg.goal.on_success
        goal_achieved = closed and (goal_tool is None or goal_tool in self.tool_names_called)
        return closed, goal_achieved, escalated

    def result(self) -> AgentResult:
        """收尾：把累计 trace 定稿，返回与批量接口一致的 AgentResult。"""
        stage = self.state.stage
        closed, goal_achieved, escalated = self._signals()
        trace = self.builder.finalize(
            stage=stage,
            closed=closed,
            goal_achieved=goal_achieved,
            violations=self.violations,
        )
        return AgentResult(
            reply=self.last_reply,
            stage=stage,
            tool_calls=self.tool_names_called,
            trace=trace,
            closed=closed,
            goal_achieved=goal_achieved,
            escalated=escalated,
            violations=self.violations,
            replies=self.replies,
        )

    # -- 内部 -------------------------------------------------------------
    def _snapshot(self, reply: str, tool_calls: list[str], new_steps: int) -> TurnResult:
        stage = self.state.stage
        closed, goal_achieved, escalated = self._signals()
        trace = self.builder.trace.model_copy(deep=True)
        trace.outcome = TraceOutcome(
            stage=stage,
            closed=closed,
            goal_achieved=goal_achieved,
            violations=list(self.violations),
        )
        return TurnResult(
            reply=reply,
            stage=stage,
            round_no=self.state.round_no,
            offer_state=self.state.offer_state,
            tool_calls=tool_calls,
            trace=trace,
            closed=closed,
            goal_achieved=goal_achieved,
            escalated=escalated,
            violations=list(self.violations),
            ended=self.ended,
            new_steps=new_steps,
        )

    def _run_turn(self, user_msg: str) -> None:
        """单轮：识别意图 → 输入护栏 → LLM/工具 → 转移 → 升级落单 → 输出护栏。"""
        agent = self.agent

        # 进入新一轮：轮次 +1（转移条件 round > 5 依赖此计数）
        self.state = agent.sm.next_turn(self.state)

        # 用户意图识别（W1 用关键词，W2 可换 LLM 分类）
        if any(k in user_msg for k in ("分期", "延期", "减免", "少付", "宽限")):
            self.facts["counter_offer"] = True
        if any(k in user_msg for k in ("同意", "可以", "行", "没问题")):
            self.facts["offer_accepted"] = True
        if any(k in user_msg for k in ("不同意", "拒绝", "不谈了", "滚")):
            self.facts["customer_refuse"] = True
        if any(k in user_msg for k in ("算了", "撤回", "刚才不算")):
            self.facts["customer_withdraw"] = True
        # 身份 / 单据核对失败：客户无法提供合同号等核验信息
        if any(k in user_msg for k in ("不记得", "不知道", "忘了", "记不清", "没记住")):
            self.facts["verify_failures"] += 1

        # 输入侧 guardrail
        input_check = check_input(user_msg)
        if input_check["hit"]:
            self.builder.add_step(
                type=StepType.GUARDRAIL,
                rule="input_override_pattern",
                detail=str(input_check["patterns"]),
                latency_ms=1,
            )
            self.facts["policy_violation_attempt"] += 1
            self.violations.append(f"输入越权指令: {input_check['patterns']}")

        # 超政策请求：显式拒绝并给出政策内替代（规则由场景配置声明）
        refusal = agent._policy_refusal(user_msg)
        if refusal:
            self.builder.add_step(
                type=StepType.GUARDRAIL,
                rule="policy_refusal",
                detail=refusal,
                latency_ms=1,
            )
            self.facts["policy_violation_attempt"] += 1

        self.messages.append({"role": "user", "content": user_msg})

        # LLM 调用
        llm_resp = agent.llm.chat(self.messages, tools=agent.registry.function_calling_schemas())
        self.builder.add_step(
            type=StepType.LLM_CALL,
            input_tokens=len(json.dumps(self.messages)),
            output_tokens=len(json.dumps(llm_resp)),
            output=llm_resp,
            latency_ms=50,
        )

        reply = llm_resp.get("content") or ""
        tool_calls = llm_resp.get("tool_calls") or []

        # 真实模型协议：工具结果之前必须有一条带 tool_calls 的 assistant 消息，
        # 否则 OpenAI 兼容接口会拒绝后续 role=tool 的消息。
        if tool_calls:
            self.messages.append(
                {
                    "role": "assistant",
                    "content": llm_resp.get("content"),
                    "tool_calls": tool_calls,
                }
            )

        # 工具调用
        for index, tc in enumerate(tool_calls):
            fn = tc.get("function") or {}
            name = fn.get("name") or "unknown_tool"
            call_id = tc.get("id") or f"call_{index}"
            try:
                args = json.loads(fn["arguments"]) if fn.get("arguments") else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            if not isinstance(args, dict):
                args = {}

            self.tool_names_called.append(name)
            if args.get("contract_no"):
                self.last_contract_no = args["contract_no"]
            result = agent.registry.invoke(name, **args)
            self.builder.add_step(
                type=StepType.TOOL_CALL,
                tool=name,
                args=args,
                result=result,
                latency_ms=result.get("meta", {}).get("latency_ms", 10),
                degraded=not result.get("ok", False),
            )

            # 矛盾数据必须主动告知（C4）：字段与话术由场景配置声明
            contradiction = agent._contradiction_reply(name, result)
            if contradiction:
                reply = contradiction

            # 更新事实状态
            if name == "query_receivable":
                if result.get("ok"):
                    self.facts["amount_informed"] = True
                    self.facts["identity_verified"] = True
                else:
                    # 合同号查不到 = 核对失败（IDENTIFY 阶段累计 2 次升级人工）
                    self.facts["verify_failures"] += 1
            elif name == "query_policy" and result.get("ok"):
                # W1 简化：查了政策即认为方案在政策内
                self.facts["counter_within_policy"] = True
            elif agent.config.goal.on_success and name == agent.config.goal.on_success and result.get("ok"):
                self.facts["goal_tool_called"] = True

            # 工具返回隔离（间接注入防护）
            safe_data = sanitize_tool_output(result.get("data"))
            self.messages.append({"role": "tool", "tool_call_id": call_id, "content": safe_data})

            if name == "record_promise" and result.get("ok"):
                reply = "好的，已为您记录付款承诺。"

        # 超政策请求的拒绝话术优先于工具产出话术
        if refusal:
            reply = refusal

        # 状态转移：循环直到无匹配转移（一次工具调用可能触发多级转移）
        context = {**self.facts, "round": self.state.round_no}
        while True:
            t = agent.sm.find_transition(self.state, context)
            if t is None:
                break
            old = self.state.display()
            self.state = agent.sm.apply_transition(self.state, t)
            self.builder.add_step(
                type=StepType.STATE_TRANSITION,
                state_before=old,
                state_after=self.state.display(),
                latency_ms=1,
            )
            if self.state.stage == (agent.config.state_machine.escalate_to or ""):
                self.escalation_reason = f"{old} -> {self.state.display()}：{t.when}"
            if agent.sm.is_terminal(self.state):
                break

        # 升级分支必须落工单：进入升级状态时调用一次 escalate（工具名由配置声明）
        escalate_to = agent.config.state_machine.escalate_to
        escalate_tool = agent.config.state_machine.escalate_tool
        if (
            escalate_to
            and escalate_tool
            and self.state.stage == escalate_to
            and escalate_tool not in self.tool_names_called
            and escalate_tool in agent.registry.names()
        ):
            if self.escalation_reason is None:
                self.escalation_reason = f"进入 {escalate_to}（round {self.state.round_no}）"
            esc_args = {
                "contract_no": self.last_contract_no or "UNKNOWN",
                "reason": self.escalation_reason,
            }
            result = agent.registry.invoke(escalate_tool, **esc_args)
            self.tool_names_called.append(escalate_tool)
            self.builder.add_step(
                type=StepType.TOOL_CALL,
                tool=escalate_tool,
                args=esc_args,
                result=result,
                latency_ms=result.get("meta", {}).get("latency_ms", 10),
                degraded=not result.get("ok", False),
            )

        # 输出侧 guardrail
        out_check = check_output(reply, agent.policy)
        if not out_check["ok"]:
            self.builder.add_step(
                type=StepType.GUARDRAIL,
                rule="output_forbidden",
                detail=str(out_check["violations"]),
                latency_ms=1,
            )
            self.violations.extend(out_check["violations"])

        # 自然语言回复入历史：无工具调用时回复即本轮 assistant 消息；
        # 有工具调用时，若最终话术与模型原始 content 不同（矛盾告知 / 拒绝 / 记录成功），
        # 追加一条 assistant 消息，保证下一轮上下文与用户实际看到的一致。
        if not tool_calls:
            self.messages.append({"role": "assistant", "content": reply})
        elif reply and reply != (llm_resp.get("content") or ""):
            self.messages.append({"role": "assistant", "content": reply})

        self.replies.append(reply)
        self.last_reply = reply


def new_session_id() -> str:
    return f"s_{uuid.uuid4().hex[:12]}"
