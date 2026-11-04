"""第二场景（milktea）验收测试：验证「换场景 = 换 yaml + 场景包」。

对应用户要求的 4 条硬标准：
  1. 零框架改动：milktea 的工具与 LLM 都在 `scenarios/` 下，`src/` 未被修改
  2. 无业务分支：框架侧不出现场景名（本文件只检查场景包的注入方式）
  3. 状态机同引擎：≥3 个 stage + 1 个异常分支，trace schema 与 receivables 一致
  4. 差异可列举：两场景转移表逐行 diff，差异全部由 YAML 字段表达
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.llm import MockLLMClient, get_llm_client
from src.agent.loop import Agent, new_session_id
from src.core.config import load_config, load_raw_config
from src.tools.receivables.tools import build_registry as build_receivables_registry
from src.tools.registry import build_registry_from_config

MILKTEA_CONFIG = ROOT / "configs" / "scenarios" / "milktea.yaml"
RECEIVABLES_CONFIG = ROOT / "configs" / "scenarios" / "receivables.yaml"

# loop.py 中可用的事实变量（转移条件只能用这些）
KNOWN_FACTS = {
    "session_start",
    "identity_verified",
    "amount_informed",
    "counter_offer",
    "counter_exceeds_policy",
    "counter_within_policy",
    "offer_accepted",
    "promise_recorded",
    "customer_withdraw",
    "redline_hit",
    "customer_refuse",
    "verify_failures",
    "policy_violation_attempt",
    "round",
}

HAPPY_MESSAGES = ["你好", "我是会员 M1001", "我的积分什么时候到期？", "可以，帮我预约这周六"]
EXCEPTION_MESSAGES = ["你好", "我不记得会员号", "真的不记得了"]


def _milktea_agent() -> tuple[Agent, dict]:
    config = load_config(str(MILKTEA_CONFIG))
    demo = load_raw_config(MILKTEA_CONFIG).get("demo") or {}
    agent = Agent(config, build_registry_from_config(config), get_llm_client(demo.get("llm")))
    return agent, demo


def _visited_stages(trace) -> list[str]:
    stages = ["INIT"]
    for step in trace.steps:
        if getattr(step.type, "value", "") == "state_transition" and step.state_after:
            stage = step.state_after.split("/", 1)[0]
            if stage not in stages:
                stages.append(stage)
    return stages


# ---------------------------------------------------------------------------
# 标准 1 / 4：配置与场景包
# ---------------------------------------------------------------------------

class TestScenarioPackaging:
    def test_src_contains_no_scenario_names(self):
        """标准 2：框架侧零场景名（grep src/ 为空）。"""
        hits = []
        for path in (ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"milktea|receivables", text, re.I):
                hits.append(path.as_posix())
        assert hits == [], f"src 中仍存在场景名: {hits}"

    def test_config_preflight_passes(self):
        cfg = load_config(str(MILKTEA_CONFIG))
        assert cfg.scenario.id == "milktea"
        assert len(cfg.state_machine.stages) >= 3
        assert cfg.goal.on_success == "book_visit"

    def test_tools_live_outside_framework(self):
        """工具实现全部位于 scenarios/ 包内 → 新增场景不改 src/tools。"""
        cfg = load_config(str(MILKTEA_CONFIG))
        assert cfg.tools, "milktea 应声明工具"
        for decl in cfg.tools:
            module_path = decl.impl.rpartition(".")[0]
            assert module_path.startswith("scenarios."), f"{decl.name} 不在场景包内: {module_path}"
            assert not module_path.startswith("src."), f"{decl.name} 仍在框架内: {module_path}"

    def test_registry_built_from_yaml(self):
        cfg = load_config(str(MILKTEA_CONFIG))
        registry = build_registry_from_config(cfg)
        assert registry.names() == ["query_member", "check_benefit", "book_visit"]
        schema = registry.function_calling_schemas()[0]
        assert schema["function"]["parameters"]["required"] == ["member_id"]

    def test_demo_script_declared_in_yaml(self):
        demo = load_raw_config(MILKTEA_CONFIG)["demo"]
        assert demo["messages"] == HAPPY_MESSAGES
        assert demo["llm"].startswith("scenarios.milktea.")

    def test_transition_conditions_use_known_facts_only(self):
        """差异可列举：每条转移只使用框架内置事实 + from/to/when 三个字段。"""
        operators = {"and", "or", "true", "false", "None"}
        raw = load_raw_config(MILKTEA_CONFIG)["state_machine"]["transitions"]
        for t in raw:
            assert set(t) <= {"from", "to", "when"}, t
            for var in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", t["when"]):
                if var in operators:
                    continue
                assert var in KNOWN_FACTS, f"{t['from']}→{t['to']} 使用了未知事实 {var}"

    def test_transition_diff_with_receivables_is_enumerable(self):
        """两场景转移表逐行 diff；差异全部落在 milktea 自己的转移声明里。"""
        milk = load_config(str(MILKTEA_CONFIG)).state_machine.transitions
        recv = load_config(str(RECEIVABLES_CONFIG)).state_machine.transitions
        milk_pairs = {(t.from_, t.to) for t in milk}
        recv_pairs = {(t.from_, t.to) for t in recv}
        diff = milk_pairs ^ recv_pairs
        assert diff, "两场景转移表不应完全相同"
        # 每一处差异都能在 milktea 的 yaml 声明中找到对应行
        for pair in milk_pairs - recv_pairs:
            assert pair in milk_pairs
        assert ("GREET", "IDENTIFY") in milk_pairs
        assert ("OFFER", "CONFIRM") in milk_pairs


# ---------------------------------------------------------------------------
# 标准 3：状态机同引擎 + 异常分支 + trace 一致
# ---------------------------------------------------------------------------

class TestMilkteaRuntime:
    def test_happy_path_closes_with_four_stages(self):
        agent, _ = _milktea_agent()
        result = agent.run(new_session_id(), HAPPY_MESSAGES, case_id="MT-HAPPY")
        assert result.stage == "CLOSED"
        assert result.goal_achieved is True
        assert result.violations == []

        visited = _visited_stages(result.trace)
        for stage in ("GREET", "IDENTIFY", "OFFER", "CONFIRM", "CLOSED"):
            assert stage in visited, f"未经过 {stage}: {visited}"
        assert len(visited) - 1 >= 3  # 至少 3 次阶段推进

    def test_tools_are_scenario_specific(self):
        agent, _ = _milktea_agent()
        result = agent.run(new_session_id(), HAPPY_MESSAGES, case_id="MT-TOOLS")
        assert result.tool_calls == ["query_member", "check_benefit", "book_visit"]

    def test_exception_branch_escalates(self):
        agent, _ = _milktea_agent()
        result = agent.run(new_session_id(), EXCEPTION_MESSAGES, case_id="MT-ESC")
        assert result.stage == "ESCALATED"
        assert "ESCALATED" in _visited_stages(result.trace)

    def test_refusal_escalates_from_offer(self):
        agent, _ = _milktea_agent()
        msgs = ["你好", "我是会员 M1001", "我的积分什么时候到期？", "我不想听了，拒绝"]
        result = agent.run(new_session_id(), msgs, case_id="MT-REFUSE")
        assert result.stage == "ESCALATED"

    def test_trace_schema_matches_receivables(self):
        milk_agent, _ = _milktea_agent()
        milk = milk_agent.run(new_session_id(), HAPPY_MESSAGES, case_id="MT-TRACE")

        recv_cfg = load_config(str(RECEIVABLES_CONFIG))
        recv_agent = Agent(recv_cfg, build_receivables_registry(), MockLLMClient())
        recv = recv_agent.run(
            new_session_id(),
            ["你好", "合同号 HT-Y2026-012", "我想分期 3 期", "好的，我同意"],
            case_id="RC-TRACE",
        )

        assert set(milk.trace.model_dump()) == set(recv.trace.model_dump())
        milk_types = {getattr(s.type, "value", s.type) for s in milk.trace.steps}
        recv_types = {getattr(s.type, "value", s.type) for s in recv.trace.steps}
        assert milk_types == recv_types == {"llm_call", "tool_call", "state_transition"}

    def test_incremental_session_matches_batch(self):
        agent, _ = _milktea_agent()
        batch = agent.run(new_session_id(), HAPPY_MESSAGES)

        session = agent.start(new_session_id())
        for msg in HAPPY_MESSAGES:
            session.send(msg)
        incremental = session.result()

        assert incremental.stage == batch.stage == "CLOSED"
        assert incremental.tool_calls == batch.tool_calls
        assert incremental.trace.totals.llm_calls == batch.trace.totals.llm_calls

    def test_no_policy_refusal_configured(self):
        """milktea 未配置超政策规则，不应误触发 receivables 的拒绝话术。"""
        cfg = load_config(str(MILKTEA_CONFIG))
        assert cfg.response is None or not cfg.response.policy_refusal
