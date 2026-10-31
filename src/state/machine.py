"""状态机引擎：元组状态 (stage, round_no, offer_state) + 转移表。

转移条件使用受限表达式白名单（比较运算 + 明确的槽位名），不使用 eval。
支持 from/to 三种形式：stage、stage/offer_state、*（通配）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.core.config import ScenarioConfig, Transition, _split_state


# ---------------------------------------------------------------------------
# 受限条件求值
# ---------------------------------------------------------------------------

# 允许的比较运算符
_ALLOWED_OPS = {">", ">=", "<", "<=", "==", "!=", "and", "or"}
# 变量名白名单（由上下文提供）
_VAR_PATTERN = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")


def _safe_eval(expression: str, context: dict[str, Any]) -> bool:
    """受限求值：只允许比较运算 + and/or，变量从 context 取。

    不使用 eval/exec，避免配置层成为注入面。
    """
    expr = expression.strip()
    if not expr:
        return True

    # 拆分 and/or
    parts_or = re.split(r"\bor\b", expr)
    results_or = []
    for part_or in parts_or:
        parts_and = re.split(r"\band\b", part_or.strip())
        results_and = []
        for part_and in parts_and:
            results_and.append(_eval_comparison(part_and.strip(), context))
        results_or.append(all(results_and))
    return any(results_or)


def _eval_comparison(expr: str, context: dict[str, Any]) -> bool:
    """求值单个比较表达式，如 'round > 5'、'verify_failures >= 2'。

    若变量未定义（不在 context 中），视为条件不满足（返回 False），
    避免 None 与数值比较抛出 TypeError。
    """
    expr = expr.strip()
    # 支持的运算符（顺序重要：先匹配长的）
    for op in (">=", "<=", "==", "!=", ">", "<"):
        if op in expr:
            left, _, right = expr.partition(op)
            left_val = _resolve(left.strip(), context)
            right_val = _resolve(right.strip(), context)
            if left_val is None or right_val is None:
                return False
            try:
                if op == ">=":
                    return left_val >= right_val
                if op == "<=":
                    return left_val <= right_val
                if op == "==":
                    return left_val == right_val
                if op == "!=":
                    return left_val != right_val
                if op == ">":
                    return left_val > right_val
                if op == "<":
                    return left_val < right_val
            except TypeError:
                return False
    # 无运算符，当作布尔变量
    val = _resolve(expr, context)
    return bool(val) if val is not None else False


def _resolve(token: str, context: dict[str, Any]) -> Any:
    """解析 token：数字直接转，字符串去引号，变量从 context 取。"""
    token = token.strip()
    if not token:
        return None
    # 数字
    try:
        if "." in token:
            return float(token)
        return int(token)
    except ValueError:
        pass
    # 字符串字面量
    if (token.startswith('"') and token.endswith('"')) or (
        token.startswith("'") and token.endswith("'")
    ):
        return token[1:-1]
    # 布尔
    if token.lower() == "true":
        return True
    if token.lower() == "false":
        return False
    # 变量
    if token in context:
        return context[token]
    # 未知变量，返回 None（比较时会失败）
    return None


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------

@dataclass
class State:
    stage: str
    round_no: int = 0
    offer_state: str | None = None

    def to_tuple(self) -> tuple[str, int, str | None]:
        return (self.stage, self.round_no, self.offer_state)

    def display(self) -> str:
        if self.offer_state:
            return f"{self.stage}/{self.offer_state}"
        return self.stage


class StateMachine:
    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config
        self.sm = config.state_machine

    def _state_matches(self, current: State, pattern: str) -> bool:
        """判断当前状态是否匹配转移的 from 模式。

        支持："*"（所有 stage）、"stage"、"stage/offer"、"stage/*"（任意子状态）。
        """
        if pattern == "*":
            return True
        stage, offer = _split_state(pattern)
        if current.stage != stage:
            return False
        if offer is not None:
            if offer == "*":
                return True
            if current.offer_state != offer:
                return False
        return True

    def find_transition(self, current: State, context: dict[str, Any]) -> Transition | None:
        """找到满足条件的下一条转移。"""
        for t in self.sm.transitions:
            if not self._state_matches(current, t.from_):
                continue
            if _safe_eval(t.when, context):
                return t
        return None

    def apply_transition(self, current: State, t: Transition) -> State:
        """应用转移，返回新状态。

        round_no 由对话轮次驱动（见 next_turn），转移本身不改变轮次——
        同一轮内可能连续发生多条转移，若在此处递增会重复计数。
        """
        stage, offer = _split_state(t.to)
        return State(stage=stage, round_no=current.round_no, offer_state=offer)

    def next_turn(self, current: State) -> State:
        """进入下一轮对话：轮次 +1，stage 与子状态保持不变。"""
        return State(
            stage=current.stage,
            round_no=current.round_no + 1,
            offer_state=current.offer_state,
        )

    def is_terminal(self, state: State) -> bool:
        return state.stage in {"CLOSED", "ESCALATED"}

    def initial_state(self) -> State:
        return State(stage="INIT", round_no=0, offer_state=None)
