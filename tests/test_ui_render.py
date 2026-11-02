"""UI 纯函数测试：渲染与会话辅助（不依赖 gradio）。

覆盖：
  1. 转移矩阵统计（12 条规则折叠到 stage、通配符展开）
  2. HTML 渲染（场景摘要 / 信息卡 / 状态条 / Trace 表）与转义
  3. 会话辅助（latest_reply / turn_summary / new_turn_state）
  4. trace 落盘（重定向到 tmp_path，不污染仓库）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.ui.app as ui
from src.agent.llm import MockLLMClient
from src.agent.loop import Agent, new_session_id
from src.core.config import load_config
from src.core.trace import StepType, TraceBuilder
from src.tools.receivables.tools import build_registry

CONFIG_PATH = ROOT / "configs" / "scenarios" / "receivables.yaml"


def _agent():
    return Agent(load_config(str(CONFIG_PATH)), build_registry(), MockLLMClient())


# ---------------------------------------------------------------------------
# 转移矩阵
# ---------------------------------------------------------------------------

class TestTransitionMatrix:
    def setup_method(self):
        self.cfg = load_config(str(CONFIG_PATH))

    def test_counts_collapse_offer_state_and_expand_wildcards(self):
        counts = ui.transition_counts(self.cfg)
        # 12 条规则折叠后：NEGOTIATE→NEGOTIATE 合并 4 条，*→ESCALATED 展开 6 条
        assert sum(counts.values()) == 17
        assert counts[("NEGOTIATE", "NEGOTIATE")] == 4
        assert counts[("NEGOTIATE", "ESCALATED")] == 2  # NEGOTIATE/* 与 * 各 1
        assert counts[("INIT", "IDENTIFY")] == 1
        assert counts.get(("CLOSED", "CLOSED"), 0) == 0

    def test_matrix_html_shape(self):
        html = ui.transition_matrix_html(self.cfg)
        stages = self.cfg.state_machine.stages
        assert all(s in html for s in stages)
        assert html.count("<tr>") == len(stages) + 1  # 表头 + 每个 stage 一行
        assert "·" in html  # 零转移用点号占位
        assert "rgba(46,125,91" in html  # 非零单元格有色阶


# ---------------------------------------------------------------------------
# HTML 渲染
# ---------------------------------------------------------------------------

class TestRenderHtml:
    def setup_method(self):
        self.cfg = load_config(str(CONFIG_PATH))

    def test_scenario_summary_contains_policy_limits(self):
        html = ui.scenario_summary_html(self.cfg)
        assert "3 期" in html
        assert "30 天" in html
        assert "10%" in html
        assert "ESCALATED" in html

    def test_case_card_skips_empty_and_escapes(self):
        html = ui.case_card_html({"contract_no": "HT-Y2026-012", "invoice_no": "", "note": "<b>x</b>"})
        assert "HT-Y2026-012" in html
        assert "invoice_no" not in html  # 空值不展示
        assert "&lt;b&gt;x&lt;/b&gt;" in html  # 已转义

    def test_case_card_none(self):
        assert ui.case_card_html(None) == ""

    def test_status_html_empty_and_filled(self):
        assert "等待输入" in ui.status_html(None)
        html = ui.status_html(
            {"stage": "CLOSED", "round_no": 4, "offer_state": None,
             "tool_count": 3, "violations": 0, "task_completed": True}
        )
        assert "CLOSED" in html and "round" in html and ">4<" in html
        assert "是" in html  # 任务完成

    def test_trace_table_rows(self):
        result = _agent().run(new_session_id(), ["你好", "合同号 HT-Y2026-012"])
        html = ui.trace_table_html(result.trace)
        assert "tool_call" in html and "state_transition" in html
        assert html.count("<tr>") == len(result.trace.steps) + 1

    def test_trace_table_truncates_over_40_steps(self):
        builder = TraceBuilder(session_id="s_test")
        for _ in range(45):
            builder.add_step(type=StepType.LLM_CALL, input_tokens=1, output_tokens=1, latency_ms=1)
        trace = builder.finalize(stage="NEGOTIATE", task_completed=False)
        html = ui.trace_table_html(trace)
        assert "仅展示前 40 步" in html
        assert html.count("<tr>") == 41  # 表头 + 40 行

    def test_trace_table_empty(self):
        assert ui.trace_table_html(None) == ""


# ---------------------------------------------------------------------------
# 会话辅助
# ---------------------------------------------------------------------------

class TestSessionHelpers:
    def test_new_turn_state_defaults(self):
        state = ui.new_turn_state("s_x")
        assert state["session_id"] == "s_x"
        assert state["turns"] == [] and state["messages"] == []
        assert state["ended"] is False
        assert state["trace_json"] is None and state["last"] is None

    def test_latest_reply_skips_empty(self):
        class _R:
            replies = ["", "  ", "最终回复"]

        assert ui.latest_reply(_R()) == "最终回复"

    def test_latest_reply_fallback(self):
        class _R:
            replies = ["", " "]

        assert "已执行工具调用" in ui.latest_reply(_R())

    def test_turn_summary_closed(self):
        result = _agent().run(
            new_session_id(), ["你好", "合同号 HT-Y2026-012", "我想分期 3 期", "好的，我同意"]
        )
        summary = ui.turn_summary(result, round_no=4)
        assert summary["stage"] == "CLOSED"
        assert summary["round_no"] == 4
        assert summary["offer_state"] is None
        assert summary["tool_count"] == 3
        assert summary["violations"] == 0
        assert summary["task_completed"] is True

    def test_turn_summary_negotiate_offer_state(self):
        result = _agent().run(new_session_id(), ["你好", "合同号 HT-Y2026-012"])
        assert result.stage == "NEGOTIATE"
        summary = ui.turn_summary(result, round_no=2)
        allowed = load_config(str(CONFIG_PATH)).state_machine.offer_states["NEGOTIATE"]
        assert summary["offer_state"] in allowed

    def test_dump_trace_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ui, "TRACE_DIR", tmp_path)
        result = _agent().run(new_session_id(), ["你好", "合同号 HT-Y2026-012"])
        path = Path(ui.dump_trace(result))
        assert path.exists() and path.parent == tmp_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["trace_id"] == result.trace.trace_id
        assert payload["session_id"] == result.trace.session_id
