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

import pytest

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
             "tool_count": 3, "violations": 0, "closed": True, "goal_achieved": True}
        )
        assert "CLOSED" in html and "round" in html and ">4<" in html
        assert "是" in html  # 结案 / 目标达成

    def test_trace_table_rows(self):
        result = _agent().run(new_session_id(), ["你好", "合同号 HT-Y2026-012"])
        html = ui.trace_table_html(result.trace)
        assert "tool_call" in html and "state_transition" in html
        assert html.count("<tr>") == len(result.trace.steps) + 1

    def test_trace_table_truncates_over_40_steps(self):
        builder = TraceBuilder(session_id="s_test")
        for _ in range(45):
            builder.add_step(type=StepType.LLM_CALL, input_tokens=1, output_tokens=1, latency_ms=1)
        trace = builder.finalize(stage="NEGOTIATE", closed=False, goal_achieved=False)
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
        assert state["session"] is None
        assert state["messages"] == []
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
        assert summary["goal_achieved"] is True

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


# ---------------------------------------------------------------------------
# 评测页渲染
# ---------------------------------------------------------------------------

_REPORT = {
    "model": "mock-llm",
    "k": 3,
    "total_cases": 80,
    "pass_power_k": 1.0,
    "stage_match_rate": 1.0,
    "closed_rate": 0.35,
    "handled_rate": 1.0,
    "business_goal_rate": 1.0,
    "avg_tool_score": 0.8,
    "signal": {"kind": "link_check", "oracle_same_source": True, "note": "mock"},
    "tool_coverage": {f"t{i}": 1 for i in range(6)},
    "untouched_tools": [],
    "contradiction": {"applicable": 6, "followed": 6, "rate": 1.0},
    "policy_refusal": {"applicable": 10, "followed": 10, "rate": 1.0},
    "by_layer": {
        "core": {"count": 24, "stage_match_rate": 1.0, "business_goal_rate": 1.0, "avg_tool_score": 0.8},
        "boundary": {"count": 16, "stage_match_rate": 1.0, "business_goal_rate": 1.0, "avg_tool_score": 0.8},
        "adversarial": {"count": 40, "stage_match_rate": 1.0, "business_goal_rate": None, "avg_tool_score": 0.8},
    },
}


class TestEvalPage:
    def test_metrics_shows_three_different_verdicts(self):
        html = ui.metrics_html(_REPORT)
        for label in ("业务目标达成率", "处置正确率", "正常结案率", "终态标签匹配率", "工具序列均分", "工具覆盖", "矛盾告知率", "超政策拒绝率"):
            assert label in html
        assert "100.0%" in html
        assert "35.0%" in html
        assert "0.80" in html
        assert "6/6" in html
        assert "mock-llm" in html and "80" in html
        assert "k=3" in html and "Pass^3" in html

    def test_metrics_missing_report_shows_hint(self):
        html = ui.metrics_html(None)
        assert "v0_baseline.json" in html
        assert "运行 80 条评测" in html

    def test_metrics_handles_none_rate(self):
        report = {
            "stage_match_rate": 1.0,
            "closed_rate": 1.0,
            "handled_rate": 1.0,
            "business_goal_rate": 1.0,
            "avg_tool_score": 0.8,
            "tool_coverage": {"a": 1},
            "untouched_tools": ["b"],
            "contradiction": {"applicable": 0, "followed": 0, "rate": None},
            "policy_refusal": {"applicable": 0, "followed": 0, "rate": None},
        }
        html = ui.metrics_html(report)
        assert "—" in html
        assert "1/2" in html
        assert "未触达工具" in html

    def test_layer_table_rows(self):
        html = ui.layer_table_html(_REPORT)
        for layer in ("core", "boundary", "adversarial"):
            assert layer in html
        assert html.count("<tr>") == 4  # 表头 + 3 层

    def test_layer_table_empty(self):
        assert ui.layer_table_html(None) == ""
        assert ui.layer_table_html({"by_layer": {}}) == ""

    def test_eval_page_has_disclaimer(self):
        html = ui.eval_page_html(_REPORT)
        assert "链路自洽性上限" in html

    def test_load_report_real_file(self):
        report = ui.load_report()
        assert report is not None
        assert report["total_cases"] == 80

    def test_load_report_missing(self, tmp_path):
        assert ui.load_report(tmp_path / "nope.json") is None

    def test_load_report_bad_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert ui.load_report(p) is None


# ---------------------------------------------------------------------------
# UI 回调（端到端：不启动界面）
# ---------------------------------------------------------------------------

class TestSubmitTurn:
    MSGS = ["你好", "合同号 HT-Y2026-012", "我想分期 3 期", "好的，我同意"]

    def setup_method(self):
        self.agent = _agent()

    def test_full_conversation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ui, "TRACE_DIR", tmp_path)
        state = ui.new_turn_state(new_session_id())
        history: list = []
        for msg in self.MSGS:
            history, state, status, trace, path = ui.submit_turn(self.agent, state, msg)

        assert len(history) == 8  # 4 轮 user + assistant
        assert state["ended"] is True
        assert state["last"]["stage"] == "CLOSED"
        assert state["last"]["round_no"] == 4
        assert "CLOSED" in status
        assert "state_transition" in trace
        assert Path(state["trace_json"]).exists()
        assert path == state["trace_json"]

    def test_empty_input_keeps_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ui, "TRACE_DIR", tmp_path)
        state = ui.new_turn_state(new_session_id())
        history, new_state, _, trace, path = ui.submit_turn(self.agent, state, "   ")
        assert history == []
        assert new_state is state
        assert trace == "" and path is None
        assert not any(tmp_path.iterdir())

    def test_send_after_terminal_is_guarded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ui, "TRACE_DIR", tmp_path)
        state = ui.new_turn_state(new_session_id())
        for msg in self.MSGS:
            _, state, _, _, _ = ui.submit_turn(self.agent, state, msg)

        session = state["session"]
        before = session.builder.trace.totals.llm_calls
        history, state, status, _, _ = ui.submit_turn(self.agent, state, "再说一句")

        assert history[-1]["content"].find("已结束") >= 0
        assert session.builder.trace.totals.llm_calls == before
        assert state["ended"] is True

    def test_reset_clears_session(self):
        history, state, status, trace, path = ui.reset_state()
        assert history == [] and trace == "" and path is None
        assert state["session"] is None and state["messages"] == []
        assert "等待输入" in status

    def test_output_arity_matches_gradio_outputs(self, tmp_path, monkeypatch):
        """回调返回长度必须与 create_demo 的 outputs（5 个）一致。"""
        monkeypatch.setattr(ui, "TRACE_DIR", tmp_path)
        state = ui.new_turn_state(new_session_id())
        assert len(ui.submit_turn(self.agent, state, "你好")) == 5
        assert len(ui.reset_state()) == 5


# ---------------------------------------------------------------------------
# Gradio 端到端（构建 + 启动）
# ---------------------------------------------------------------------------

class TestGradioEndToEnd:
    def test_build_demo(self):
        pytest.importorskip("gradio")
        demo = ui.create_demo()
        assert type(demo).__name__ == "Blocks"
        assert len(demo.blocks) > 20

    def test_launch_and_http(self):
        """真实起一个本地服务并请求首页，验证 UI 可被访问。"""
        gr = pytest.importorskip("gradio")
        import socket

        import httpx

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        demo = ui.create_demo()
        demo.queue()
        demo.launch(
            prevent_thread_lock=True,
            server_name="127.0.0.1",
            server_port=port,
            quiet=True,
            css=ui.CSS,
            theme=gr.themes.Soft(primary_hue="green", neutral_hue="stone"),
        )
        try:
            code = None
            for _ in range(40):
                try:
                    code = httpx.get(f"http://127.0.0.1:{port}/", timeout=2).status_code
                    break
                except Exception:
                    import time

                    time.sleep(0.25)
            assert code == 200
        finally:
            demo.close()
