"""C7 安全契约：注入防护（四层，W1 实现输入侧 + 输出侧 + 决策侧）。

四层：
  1. 输入侧：越权指令模式检测（命中仅标记，不阻断对话）
  2. 决策侧：减免/延期数值必须来自 query_policy 工具返回，模型无权自定
  3. 工具侧（间接注入）：工具返回值进入上下文前定界标记 + 截断 + 剥离指令
  4. 输出侧：校验不含超政策承诺、不泄露 system prompt、不输出未授权金额
"""

from __future__ import annotations

import re
from typing import Any

from src.tools.registry import ok


# 越权指令模式（输入侧检测）
_OVERRIDE_PATTERNS = [
    r"忽略(以上|之前|所有).{0,10}(指令|规则|提示)",
    r"你现在是",
    r"我是(老板|领导|负责人|管理员)",
    r"(直接|马上|立刻)(批准|减免|同意)",
    r"打印?(系统)?提示词",
    r"念出?(你的)?(系统)?提示",
]

_OVERRIDE_RE = [re.compile(p, re.IGNORECASE) for p in _OVERRIDE_PATTERNS]

# 禁止话术（输出侧检测）
_FORBIDDEN_PATTERNS = [
    r"威胁",
    r"起诉你",
    r"冻结.{0,4}(账户|账号)",
    r"保证一定",
]

_FORBIDDEN_RE = [re.compile(p) for p in _FORBIDDEN_PATTERNS]


def check_input(text: str) -> dict[str, Any]:
    """输入侧检测：返回是否命中越权指令（仅标记，不阻断）。"""
    hits = []
    for pat in _OVERRIDE_RE:
        m = pat.search(text)
        if m:
            hits.append(m.group(0))
    return {"hit": bool(hits), "patterns": hits, "action": "mark_only"}


def check_output(text: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """输出侧校验：禁止话术 + 超政策承诺。"""
    violations = []

    for pat in _FORBIDDEN_RE:
        if pat.search(text):
            violations.append(f"禁止话术: {pat.pattern}")

    # 超政策减免检测：如果文本提到减免比例，检查是否超过上限
    if policy:
        max_ratio = policy.get("max_waiver_ratio", 0.10)
        # 匹配 "减免 X%" 或 "X% 减免"
        for m in re.finditer(r"减免\s*(\d+(?:\.\d+)?)\s*%", text):
            ratio = float(m.group(1)) / 100
            if ratio > max_ratio:
                violations.append(f"减免比例 {ratio:.1%} 超过上限 {max_ratio:.1%}")

    return {"ok": not violations, "violations": violations}


def sanitize_tool_output(data: Any, max_len: int = 2048) -> str:
    """工具侧（间接注入）防护：定界标记 + 截断 + 剥离疑似指令片段。"""
    text = str(data)
    # 剥离疑似指令片段
    text = re.sub(r"忽略(以上|之前|所有).{0,20}(指令|规则|提示)", "[已剥离]", text)
    text = re.sub(r"你现在是.{0,30}", "[已剥离]", text)
    text = re.sub(r"请执行.{0,30}", "[已剥离]", text)
    # 截断
    if len(text) > max_len:
        text = text[:max_len] + "...[截断]"
    return f"[tool_data]\n{text}\n[/tool_data]"
