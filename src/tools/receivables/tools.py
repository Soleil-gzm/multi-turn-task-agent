"""receivables 场景的业务工具实现（W1：mock 数据版）。

W1 只实现 3 个工具：query_receivable / query_policy / record_promise。
其余 3 个（check_invoice / diff_statement / escalate）在 W2 补齐。
"""

from __future__ import annotations

from src.tools.registry import ToolSchema, ok, fail, ErrorCode


# ---------------------------------------------------------------------------
# mock 数据
# ---------------------------------------------------------------------------

_MOCK_RECEIVABLES: dict[str, dict] = {
    "HT-Y2026-012": {
        "contract_no": "HT-Y2026-012",
        "invoice_no": "INV-20260123",
        "amount": 380000,
        "due_date": "2026-08-15",
        "overdue_days": 45,
        "status": "unpaid",
    },
    "HT-Y2026-007": {
        "contract_no": "HT-Y2026-007",
        "invoice_no": "INV-20260078",
        "amount": 120000,
        "due_date": "2026-09-01",
        "overdue_days": 30,
        "status": "unpaid",
    },
}

_MOCK_POLICY = {
    "max_installments": 3,
    "max_extension_days": 30,
    "max_waiver_ratio": 0.10,
    "forbidden_patterns": ["威胁", "起诉你", "冻结账户", "保证一定"],
}

# 内存承诺记录（W1 用，W3 换 SQLite）
_PROMISES: list[dict] = []


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

def query_receivable(contract_no: str) -> dict:
    """查询指定合同号的应收账款信息。"""
    if not contract_no:
        return fail(ErrorCode.INVALID_ARG, "contract_no 不能为空")
    record = _MOCK_RECEIVABLES.get(contract_no)
    if record is None:
        return fail(ErrorCode.NOT_FOUND, f"合同 {contract_no} 不存在")
    return ok(record, source="mock_db")


def query_policy() -> dict:
    """查询政策边界（分期/延期/减免上限）。"""
    return ok(_MOCK_POLICY, source="policy_doc")


def record_promise(contract_no: str, pay_date: str, amount: int) -> dict:
    """写入付款承诺（日期 + 金额）。"""
    if not contract_no or not pay_date or not amount:
        return fail(ErrorCode.INVALID_ARG, "contract_no / pay_date / amount 均必填")
    promise = {
        "contract_no": contract_no,
        "pay_date": pay_date,
        "amount": amount,
        "recorded_at": "2026-10-31T00:00:00",
    }
    _PROMISES.append(promise)
    return ok(promise, source="promise_store")


# ---------------------------------------------------------------------------
# 工具 Schema（用于 function calling / 评测 / 文档）
# ---------------------------------------------------------------------------

SCHEMAS: dict[str, ToolSchema] = {
    "query_receivable": ToolSchema(
        name="query_receivable",
        description="查询指定合同号的应收账款信息（金额、账期、逾期天数、发票状态）",
        parameters={
            "type": "object",
            "properties": {
                "contract_no": {
                    "type": "string",
                    "pattern": "^HT-Y2026-\\d{3}$",
                    "description": "合同号",
                }
            },
            "required": ["contract_no"],
        },
        error_codes=[ErrorCode.NOT_FOUND, ErrorCode.INVALID_ARG],
    ),
    "query_policy": ToolSchema(
        name="query_policy",
        description="查询协商政策边界：分期期数上限、延期天数上限、减免比例上限",
        parameters={"type": "object", "properties": {}, "required": []},
        error_codes=[],
    ),
    "record_promise": ToolSchema(
        name="record_promise",
        description="记录客户的付款承诺（合同号、付款日期、金额）",
        parameters={
            "type": "object",
            "properties": {
                "contract_no": {"type": "string", "description": "合同号"},
                "pay_date": {"type": "string", "description": "付款日期 YYYY-MM-DD"},
                "amount": {"type": "integer", "description": "承诺付款金额"},
            },
            "required": ["contract_no", "pay_date", "amount"],
        },
        error_codes=[ErrorCode.INVALID_ARG],
    ),
}
