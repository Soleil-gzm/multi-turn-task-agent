"""契约单元测试：C1 数据 / C2 工具 / C3 配置 / C6 Trace / 状态机。"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import ConfigError, load_config
from src.core.trace import TraceBuilder, StepType
from src.eval.contracts import Case, check_tool_sequence, load_cases
from src.state.machine import State, StateMachine
from src.tools.registry import ToolRegistry, ToolSchema, ok, fail, ErrorCode
from src.tools.receivables.tools import (
    SCHEMAS,
    build_registry,
    check_invoice,
    diff_statement,
    escalate,
)


CONFIG_PATH = ROOT / "configs" / "scenarios" / "receivables.yaml"
CASES_PATH = ROOT / "eval" / "datasets" / "receivables" / "cases.jsonl"


# ---------------------------------------------------------------------------
# C1 数据契约
# ---------------------------------------------------------------------------

class TestC1DataContract:
    def test_load_cases(self):
        cases = load_cases(CASES_PATH)
        assert len(cases) == 10
        assert all(isinstance(c, Case) for c in cases)

    def test_case_layers_distribution(self):
        cases = load_cases(CASES_PATH)
        layers = {}
        for c in cases:
            layers[c.layer] = layers.get(c.layer, 0) + 1
        assert layers["core"] == 4
        assert layers["boundary"] == 4
        assert layers["adversarial"] == 2

    def test_boundary_repeated_call_not_penalized(self):
        """边界 1：required=true 只要求至少一次，不惩罚重试。"""
        case = Case(
            case_id="t1", layer="core", subtype="x", difficulty=1,
            init_state={}, user_persona={"type": "x", "goal": "x"},
            oracle={"expected_tool_sequence": [{"tool": "query_receivable", "required": True}]},
        )
        result = check_tool_sequence(case, ["query_receivable", "query_receivable", "query_receivable"])
        assert result["coverage"] == 1.0  # 重复调用不惩罚

    def test_boundary_order_only_checks_non_null(self):
        """边界 2：仅 order != null 校验相对顺序。"""
        case = Case(
            case_id="t2", layer="core", subtype="x", difficulty=1,
            init_state={}, user_persona={"type": "x", "goal": "x"},
            oracle={"expected_tool_sequence": [
                {"tool": "query_receivable", "required": True, "order": 1},
                {"tool": "query_policy", "required": True, "order": None},
                {"tool": "record_promise", "required": True, "order": 2},
            ]},
        )
        # query_policy 在 record_promise 之后，但 order=None 不校验
        result = check_tool_sequence(case, ["query_receivable", "record_promise", "query_policy"])
        assert result["order_ok"] is True

    def test_boundary_partial_completion_scores(self):
        """边界 3：部分完成按维度给分，不二值化。"""
        case = Case(
            case_id="t3", layer="core", subtype="x", difficulty=1,
            init_state={}, user_persona={"type": "x", "goal": "x"},
            oracle={"expected_tool_sequence": [
                {"tool": "query_receivable", "required": True},
                {"tool": "record_promise", "required": True},
            ]},
        )
        # 只调用了 query_receivable，未 record_promise
        result = check_tool_sequence(case, ["query_receivable"])
        assert 0 < result["score"] < 1  # 部分得分，不是 0


# ---------------------------------------------------------------------------
# C2 工具契约
# ---------------------------------------------------------------------------

class TestC2ToolContract:
    def test_unified_return_ok(self):
        r = ok({"a": 1}, source="test")
        assert r["ok"] is True
        assert r["data"] == {"a": 1}
        assert r["error"] is None
        assert r["meta"]["source"] == "test"

    def test_unified_return_fail(self):
        r = fail(ErrorCode.NOT_FOUND, "not found", retryable=False)
        assert r["ok"] is False
        assert r["error"]["code"] == "NOT_FOUND"
        assert r["error"]["retryable"] is False

    def test_registry_register_and_invoke(self):
        reg = ToolRegistry()
        schema = ToolSchema(name="echo", description="echo", parameters={"type": "object", "properties": {}})
        reg.register("echo", lambda **kw: ok(kw), schema)
        r = reg.invoke("echo", x=1)
        assert r["ok"] is True
        assert r["data"] == {"x": 1}

    def test_registry_unknown_tool(self):
        reg = ToolRegistry()
        r = reg.invoke("nope")
        assert r["ok"] is False
        assert r["error"]["code"] == ErrorCode.NOT_FOUND


class TestReceivablesTools:
    def test_build_registry_registers_all_six(self):
        reg = build_registry()
        assert set(reg.names()) == {
            "query_receivable",
            "check_invoice",
            "diff_statement",
            "query_policy",
            "record_promise",
            "escalate",
        }

    def test_schema_registry_consistency(self):
        """C2：注册表与 SCHEMAS 一一对应。"""
        reg = build_registry()
        assert set(reg.names()) == set(SCHEMAS.keys())

    def test_check_invoice_ok(self):
        r = check_invoice("INV-20260123")
        assert r["ok"] is True
        assert r["data"]["status"] == "已开"

    def test_check_invoice_not_found(self):
        r = check_invoice("INV-99999999")
        assert r["ok"] is False
        assert r["error"]["code"] == ErrorCode.NOT_FOUND

    def test_diff_statement_detects_mismatch(self):
        r = diff_statement("HT-Y2026-012")
        assert r["ok"] is True
        assert r["data"]["book_amount"] == 380000
        assert r["data"]["customer_claimed_amount"] == 200000
        assert r["data"]["diff"] == 180000
        assert r["data"]["has_diff"] is True

    def test_diff_statement_no_mismatch(self):
        r = diff_statement("HT-Y2026-007")
        assert r["ok"] is True
        assert r["data"]["has_diff"] is False

    def test_escalate_creates_ticket(self):
        r = escalate("HT-Y2026-012", "客户拒绝沟通")
        assert r["ok"] is True
        assert r["data"]["ticket_id"].startswith("ESC-")
        assert r["data"]["status"] == "created"

    def test_escalate_requires_reason(self):
        r = escalate("HT-Y2026-012", "")
        assert r["ok"] is False
        assert r["error"]["code"] == ErrorCode.INVALID_ARG


# ---------------------------------------------------------------------------
# C3 配置契约
# ---------------------------------------------------------------------------

class TestC3Config:
    def test_load_valid_config(self):
        cfg = load_config(str(CONFIG_PATH))
        assert cfg.scenario.id == "receivables"
        assert len(cfg.state_machine.transitions) == 12
        assert len(cfg.tools) == 6

    def test_preflight_blocks_bad_transition(self, tmp_path):
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("""
scenario:
  id: bad
  display_name: bad
  version: 1
goal:
  success_criteria: x
  max_rounds: 5
state_machine:
  stages: [INIT, IDENTIFY]
  transitions:
    - from: INIT
      to: NONEXISTENT
      when: "true"
tools:
  - name: t
    impl: src.tools.registry.ok
policy:
  max_installments: 3
  max_extension_days: 30
  max_waiver_ratio: 0.10
  forbidden_patterns: []
prompts:
  system: x
""", encoding="utf-8")
        with pytest.raises((ConfigError, ValidationError)):
            load_config(str(bad_yaml))

    def test_preflight_blocks_bad_policy(self, tmp_path):
        bad_yaml = tmp_path / "bad2.yaml"
        bad_yaml.write_text("""
scenario:
  id: bad
  display_name: bad
  version: 1
goal:
  success_criteria: x
  max_rounds: 5
state_machine:
  stages: [INIT, CLOSED]
  transitions:
    - from: INIT
      to: CLOSED
      when: "true"
tools:
  - name: t
    impl: src.tools.registry.ok
policy:
  max_installments: 0
  max_extension_days: 30
  max_waiver_ratio: 0.10
  forbidden_patterns: []
prompts:
  system: x
""", encoding="utf-8")
        with pytest.raises((ConfigError, ValidationError)):
            load_config(str(bad_yaml))


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------

class TestStateMachine:
    def setup_method(self):
        self.cfg = load_config(str(CONFIG_PATH))
        self.sm = StateMachine(self.cfg)

    def test_initial_state(self):
        s = self.sm.initial_state()
        assert s.stage == "INIT"

    def test_transition_init_to_identify(self):
        s = self.sm.initial_state()
        t = self.sm.find_transition(s, {"session_start": True})
        assert t is not None
        assert t.to == "IDENTIFY"

    def test_wildcard_stage_match(self):
        """NEGOTIATE/* 应匹配任意子状态。"""
        s = State(stage="NEGOTIATE", round_no=3, offer_state="probe")
        assert self.sm._state_matches(s, "NEGOTIATE/*") is True
        s2 = State(stage="NEGOTIATE", round_no=3, offer_state="final")
        assert self.sm._state_matches(s2, "NEGOTIATE/*") is True

    def test_full_happy_path(self):
        """完整 happy path：INIT → ... → CLOSED。"""
        s = self.sm.initial_state()
        ctx = {
            "session_start": True, "identity_verified": True,
            "amount_informed": True, "counter_offer": True,
            "counter_within_policy": True, "offer_accepted": True,
            "promise_recorded": True, "round": 0,
        }
        # 逐步推进
        for _ in range(20):
            t = self.sm.find_transition(s, ctx)
            if t is None:
                break
            s = self.sm.apply_transition(s, t)
            if self.sm.is_terminal(s):
                break
        assert s.stage == "CLOSED"


# ---------------------------------------------------------------------------
# C6 Trace
# ---------------------------------------------------------------------------

class TestC6Trace:
    def test_trace_builder(self):
        b = TraceBuilder(session_id="s_test", case_id="c_test")
        b.add_step(type=StepType.LLM_CALL, input_tokens=100, output_tokens=50, latency_ms=10)
        b.add_step(type=StepType.TOOL_CALL, tool="query_receivable", latency_ms=5)
        trace = b.finalize(stage="CLOSED", task_completed=True)
        assert len(trace.steps) == 2
        assert trace.totals.llm_calls == 1
        assert trace.totals.tool_calls == 1
        assert trace.outcome.task_completed is True
