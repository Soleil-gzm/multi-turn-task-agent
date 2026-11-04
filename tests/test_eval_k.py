"""k≥3 评测聚合与 Pass^k 口径测试（合成记录，不跑真实模型）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.eval.runner import _handled_correctly, summarize


def test_closed_without_goal_tool_is_not_business_goal():
    """口径回归：到达成功终态但目标工具未成功调用 = 结案，不是目标达成。"""
    case = SimpleNamespace(
        oracle=SimpleNamespace(
            expected_terminal={"stage": "CLOSED", "outcome": "promise_recorded"}
        )
    )
    closed_no_goal = SimpleNamespace(
        stage="CLOSED", closed=True, goal_achieved=False, escalated=False
    )
    assert _handled_correctly(closed_no_goal, case) is False

    closed_with_goal = SimpleNamespace(
        stage="CLOSED", closed=True, goal_achieved=True, escalated=False
    )
    assert _handled_correctly(closed_with_goal, case) is True


def test_correct_escalation_is_handled_but_not_business_goal():
    """正确升级 = 处置正确；它不应进入业务目标达成率的分母或分子。"""
    case = SimpleNamespace(
        oracle=SimpleNamespace(
            expected_terminal={"stage": "ESCALATED", "outcome": "escalated"}
        )
    )
    escalated = SimpleNamespace(
        stage="ESCALATED", closed=False, goal_achieved=False, escalated=True
    )
    assert _handled_correctly(escalated, case) is True


def test_business_goal_rate_excludes_escalation_samples():
    """达成率只统计应关单样本：正确升级的样本既不进分子也不进分母。"""
    cases = [_case("A", "core"), _case("B", "adversarial")]
    goal_trial = _trial(0, "A", "core", success=True)
    esc_trial = _trial(0, "B", "adversarial", success=True)
    goal_trial.update({"closed": True, "business_goal": True})
    esc_trial.update({"closed": False, "business_goal": None})

    report = summarize(cases, [goal_trial, esc_trial], k=1,
                       model_meta={"model": "mock-llm", "provider": "mock"})

    assert report["business_goal_rate"] == 1.0      # 仅 A 计入
    assert report["handled_rate"] == 1.0            # A、B 都算处置正确
    assert report["closed_rate"] == 0.5
    assert report["signal"]["kind"] == "link_check"


def _case(case_id: str, layer: str):
    return SimpleNamespace(case_id=case_id, layer=layer)


def _trial(run: int, case_id: str, layer: str, success: bool, tools=("query_receivable",)):
    return {
        "run": run,
        "case_id": case_id,
        "layer": layer,
        "stage": "CLOSED" if success else "ESCALATED",
        "expected_stage": "CLOSED",
        "stage_match": success,
        "closed": success,
        "handled": success,
        "business_goal": success,
        "tool_score": 0.8 if success else 0.5,
        "tool_calls": list(tools),
        "violations": 0,
        "model_violations": [],
        "contradiction_followed": None,
        "policy_refusal_followed": None,
        "success": success,
    }


def test_pass_power_k_and_pass_at_1():
    cases = [_case("A", "core"), _case("B", "core"), _case("C", "boundary"), _case("D", "adversarial")]
    trials = []
    outcomes = {"A": [True] * 3, "B": [True, True, False], "C": [True] * 3, "D": [False] * 3}
    layers = {"A": "core", "B": "core", "C": "boundary", "D": "adversarial"}
    for run in range(3):
        for case in cases:
            trials.append(_trial(run, case.case_id, layers[case.case_id], outcomes[case.case_id][run]))

    report = summarize(cases, trials, k=3, model_meta={"model": "mock-llm", "provider": "mock"})

    assert report["k"] == 3
    assert report["total_cases"] == 4
    assert report["total_trials"] == 12
    # A/C 全通过，B 有一次失败，D 全失败
    assert report["pass_power_k"] == 0.5
    assert report["pass_at_1"] == pytest.approx(8 / 12, abs=1e-4)
    assert [c["pass_k"] for c in report["results"]] == [True, False, True, False]
    assert report["results"][1]["successes"] == 2


def test_metrics_mean_std_and_layer_aggregation():
    cases = [_case("A", "core"), _case("B", "adversarial")]
    trials = []
    for run in range(3):
        trials.append(_trial(run, "A", "core", success=(run != 1)))
        trials.append(_trial(run, "B", "adversarial", success=False))

    report = summarize(cases, trials, k=3, model_meta={"model": "m"})

    # A: 2 次成功，B: 0 次 → 每次运行的 stage_match_rate 分别为 1/2, 0, 1/2
    assert report["stage_match_rate"] == pytest.approx((0.5 + 0.0 + 0.5) / 3, abs=1e-4)
    assert report["metrics"]["stage_match_rate"]["std"] > 0
    assert report["by_layer"]["core"]["count"] == 1
    assert report["by_layer"]["core"]["stage_match_rate"] == 2 / 3


def test_tool_coverage_counts_cases_and_untouched_tools():
    cases = [_case("A", "core"), _case("B", "core")]
    trials = [
        _trial(0, "A", "core", True, tools=("query_receivable", "record_promise")),
        _trial(1, "A", "core", True, tools=("query_receivable",)),
        _trial(0, "B", "core", False, tools=("query_receivable",)),
        _trial(1, "B", "core", False, tools=("query_receivable",)),
    ]
    report = summarize(
        cases,
        trials,
        k=2,
        model_meta={"model": "m", "_registered_tools": ["query_receivable", "record_promise", "escalate"]},
    )
    assert report["tool_coverage"] == {"query_receivable": 2, "record_promise": 1}
    assert report["untouched_tools"] == ["escalate"]
    assert "_registered_tools" not in report["model_meta"]


def test_contradiction_and_refusal_rates_pool_trials():
    cases = [_case("A", "core")]
    trials = []
    for run in range(3):
        t = _trial(run, "A", "core", True)
        t["contradiction_followed"] = run != 2
        t["policy_refusal_followed"] = True
        trials.append(t)

    report = summarize(cases, trials, k=3, model_meta={"model": "m"})
    assert report["contradiction"]["applicable"] == 3
    assert report["contradiction"]["followed"] == 2
    assert report["policy_refusal"]["rate"] == 1.0
