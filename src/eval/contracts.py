"""C1 数据契约：case / oracle schema。

评测标签由合成管线程序化产出（oracle），不依赖人工标注。

三个必须定死的判定边界：
  1. 工具重复调用：required=true 只要求「至少一次」，不惩罚重试
  2. 顺序：仅 order != null 校验相对顺序；order=null 表示任意位置
  3. 部分完成：按达成维度给分，不二值化
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ExpectedToolCall(BaseModel):
    tool: str
    required: bool = True
    order: int | None = None  # None = 任意位置


class Oracle(BaseModel):
    expected_tool_sequence: list[ExpectedToolCall] = Field(default_factory=list)
    expected_args: dict[str, dict[str, Any]] = Field(default_factory=dict)
    forbidden_actions: list[str] = Field(default_factory=list)
    expected_terminal: dict[str, Any] | None = None
    max_rounds: int = 15


class UserPersona(BaseModel):
    type: str
    goal: str
    max_rounds: int = 10


class Case(BaseModel):
    case_id: str
    version: str = "v1"
    layer: str  # core / boundary / adversarial
    subtype: str
    difficulty: int = Field(1, ge=1, le=5)
    seed: int = 42
    init_state: dict[str, Any]
    user_persona: UserPersona
    oracle: Oracle
    tags: list[str] = Field(default_factory=list)
    content_hash: str = ""


def load_cases(path: str | Path) -> list[Case]:
    """从 jsonl 加载评测集。"""
    path = Path(path)
    cases: list[Case] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(Case.model_validate_json(line))
    return cases


def load_cases_dir(directory: str | Path) -> list[Case]:
    """加载目录下所有 jsonl。"""
    directory = Path(directory)
    all_cases: list[Case] = []
    for p in sorted(directory.glob("*.jsonl")):
        all_cases.extend(load_cases(p))
    return all_cases


# ---------------------------------------------------------------------------
# 判定边界工具函数（供评测器使用）
# ---------------------------------------------------------------------------

def check_tool_sequence(case: Case, actual_tools: list[str]) -> dict[str, Any]:
    """判定工具调用序列，返回各维度得分。

    边界 1：required=true 只要求至少一次，不惩罚重试。
    边界 2：仅 order != null 校验相对顺序。
    边界 3：按维度给分，不二值化。
    """
    oracle = case.oracle
    expected = oracle.expected_tool_sequence
    if not expected:
        return {"score": 1.0, "details": "no_expected_tools"}

    # 去重后的实际调用集合（保留首次出现顺序）
    seen: list[str] = []
    for t in actual_tools:
        if t not in seen:
            seen.append(t)

    # 维度 1：覆盖率（required 的工具是否都调用了）
    required = [e.tool for e in expected if e.required]
    covered = sum(1 for t in required if t in actual_tools)
    coverage = covered / len(required) if required else 1.0

    # 维度 2：顺序（仅校验 order != null 的相对顺序）
    ordered = [e for e in expected if e.order is not None]
    ordered.sort(key=lambda e: e.order or 0)
    order_ok = True
    positions = {t: i for i, t in enumerate(seen)}
    for i in range(len(ordered) - 1):
        a, b = ordered[i].tool, ordered[i + 1].tool
        if a in positions and b in positions and positions[a] > positions[b]:
            order_ok = False
            break

    # 维度 3：禁用动作
    forbidden_hit = any(t in oracle.forbidden_actions for t in actual_tools)

    # 综合分：覆盖率 0.5 + 顺序 0.3 - 禁用 0.5（命中禁用扣分）
    score = coverage * 0.5 + (1.0 if order_ok else 0.0) * 0.3
    if forbidden_hit:
        score = max(0.0, score - 0.5)

    return {
        "score": round(score, 4),
        "coverage": coverage,
        "order_ok": order_ok,
        "forbidden_hit": forbidden_hit,
    }


def dump_case(case: Case) -> str:
    return json.dumps(case.model_dump(), ensure_ascii=False)
