"""C6 Trace 契约。

一条轨迹记录多轮对话的完整执行过程，同时支撑：
  - E3 轨迹比对（与 oracle 期望工具序列比对）
  - 成本 / 延迟指标采集
  - bad case 归因

type 枚举：llm_call / tool_call / guardrail / state_transition / degradation
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StepType(str, Enum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    GUARDRAIL = "guardrail"
    STATE_TRANSITION = "state_transition"
    DEGRADATION = "degradation"


class TraceStep(BaseModel):
    """单步轨迹。"""

    step: int = Field(..., ge=1, description="步骤序号，从 1 开始")
    type: StepType
    latency_ms: int = Field(0, ge=0)
    # llm_call 专用
    input_tokens: int | None = None
    output_tokens: int | None = None
    output: dict[str, Any] | None = None
    # tool_call 专用
    tool: str | None = None
    args: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    degraded: bool = False
    # state_transition 专用
    state_before: str | None = None
    state_after: str | None = None
    # guardrail / degradation 专用
    rule: str | None = None
    detail: str | None = None


class TraceTotals(BaseModel):
    llm_calls: int = 0
    tool_calls: int = 0
    tokens: int = 0
    latency_ms: int = 0


class TraceOutcome(BaseModel):
    stage: str
    task_completed: bool
    violations: list[str] = Field(default_factory=list)


class Trace(BaseModel):
    """完整轨迹。"""

    trace_id: str = Field(default_factory=lambda: f"tr_{uuid.uuid4().hex[:12]}")
    session_id: str
    case_id: str | None = None
    model: dict[str, Any] = Field(default_factory=dict)
    steps: list[TraceStep] = Field(default_factory=list)
    totals: TraceTotals = Field(default_factory=TraceTotals)
    outcome: TraceOutcome | None = None
    started_at: float = Field(default_factory=time.time)
    ended_at: float | None = None


class TraceBuilder:
    """增量构造 Trace 的辅助类，供 agent 循环使用。"""

    def __init__(self, session_id: str, case_id: str | None = None, model: dict[str, Any] | None = None) -> None:
        self._trace = Trace(session_id=session_id, case_id=case_id, model=model or {})
        self._step_no = 0

    def add_step(self, **kwargs: Any) -> TraceStep:
        self._step_no += 1
        step = TraceStep(step=self._step_no, **kwargs)
        self._trace.steps.append(step)
        # 累加统计
        if step.type == StepType.LLM_CALL:
            self._trace.totals.llm_calls += 1
            if step.input_tokens:
                self._trace.totals.tokens += step.input_tokens
            if step.output_tokens:
                self._trace.totals.tokens += step.output_tokens
        elif step.type == StepType.TOOL_CALL:
            self._trace.totals.tool_calls += 1
        self._trace.totals.latency_ms += step.latency_ms
        return step

    def finalize(self, stage: str, task_completed: bool, violations: list[str] | None = None) -> Trace:
        self._trace.ended_at = time.time()
        self._trace.outcome = TraceOutcome(
            stage=stage,
            task_completed=task_completed,
            violations=violations or [],
        )
        return self._trace

    @property
    def trace(self) -> Trace:
        return self._trace
