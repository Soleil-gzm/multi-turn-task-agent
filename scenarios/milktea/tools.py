"""milktea 场景的业务工具（mock 数据）。

与 receivables 保持同一套 C2 契约：统一 `{ok, data, error{code,msg,retryable}, meta}` 返回。
工具模块需暴露 `SCHEMAS`，供 `src.tools.registry.build_registry_from_config` 动态装配。
"""

from __future__ import annotations

from src.tools.registry import ErrorCode, ToolRegistry, ToolSchema, fail, ok

# ---------------------------------------------------------------------------
# mock 数据（100% 虚构）
# ---------------------------------------------------------------------------

_MEMBERS: dict[str, dict] = {
    "M1001": {
        "member_id": "M1001",
        "name": "陈小雨",
        "level": "金卡",
        "points": 1200,
        "expire_date": "2026-12-31",
    },
    "M1002": {
        "member_id": "M1002",
        "name": "林一舟",
        "level": "银卡",
        "points": 380,
        "expire_date": "2026-11-30",
    },
    "M1003": {
        "member_id": "M1003",
        "name": "周然",
        "level": "普通",
        "points": 0,
        "expire_date": "2026-10-31",
    },
}

# 会员权益：积分可兑换的券（mock）
_BENEFITS: dict[str, list[dict]] = {
    "M1001": [
        {"coupon": "满 30 减 10", "cost_points": 200, "valid_until": "2026-12-31"},
        {"coupon": "第二杯半价", "cost_points": 300, "valid_until": "2026-12-31"},
    ],
    "M1002": [
        {"coupon": "满 20 减 5", "cost_points": 150, "valid_until": "2026-11-30"},
    ],
}

_BOOKINGS: list[dict] = []


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

def query_member(member_id: str) -> dict:
    """查询会员档案（等级、积分、到期日）。"""
    if not member_id:
        return fail(ErrorCode.INVALID_ARG, "member_id 不能为空")
    record = _MEMBERS.get(member_id)
    if record is None:
        return fail(ErrorCode.NOT_FOUND, f"会员 {member_id} 不存在")
    return ok(record, source="mock_member_db")


def check_benefit(member_id: str) -> dict:
    """查询会员可用权益（积分可兑换券）。"""
    if not member_id:
        return fail(ErrorCode.INVALID_ARG, "member_id 不能为空")
    member = _MEMBERS.get(member_id)
    if member is None:
        return fail(ErrorCode.NOT_FOUND, f"会员 {member_id} 不存在")
    return ok(
        {
            "member_id": member_id,
            "points": member["points"],
            "expire_date": member["expire_date"],
            "coupons": _BENEFITS.get(member_id, []),
        },
        source="mock_benefit_db",
    )


def book_visit(member_id: str, visit_date: str) -> dict:
    """登记到店预约（会员号 + 日期）。"""
    if not member_id or not visit_date:
        return fail(ErrorCode.INVALID_ARG, "member_id / visit_date 均必填")
    if member_id not in _MEMBERS:
        return fail(ErrorCode.NOT_FOUND, f"会员 {member_id} 不存在")
    booking = {
        "booking_id": f"BK-{len(_BOOKINGS) + 1:04d}",
        "member_id": member_id,
        "visit_date": visit_date,
        "status": "booked",
    }
    _BOOKINGS.append(booking)
    return ok(booking, source="mock_booking_store")


# ---------------------------------------------------------------------------
# Schema（供 function calling / 评测 / 文档复用）
# ---------------------------------------------------------------------------

SCHEMAS: dict[str, ToolSchema] = {
    "query_member": ToolSchema(
        name="query_member",
        description="查询会员档案：等级、积分余额、积分到期日",
        parameters={
            "type": "object",
            "properties": {
                "member_id": {
                    "type": "string",
                    "pattern": "^M\\d{4}$",
                    "description": "会员号，如 M1001",
                }
            },
            "required": ["member_id"],
        },
        error_codes=[ErrorCode.NOT_FOUND, ErrorCode.INVALID_ARG],
    ),
    "check_benefit": ToolSchema(
        name="check_benefit",
        description="查询会员可用权益：积分可兑换的优惠券与有效期",
        parameters={
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "description": "会员号"},
            },
            "required": ["member_id"],
        },
        error_codes=[ErrorCode.NOT_FOUND, ErrorCode.INVALID_ARG],
    ),
    "book_visit": ToolSchema(
        name="book_visit",
        description="登记会员到店预约（会员号 + 到店日期）",
        parameters={
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "description": "会员号"},
                "visit_date": {"type": "string", "description": "到店日期 YYYY-MM-DD"},
            },
            "required": ["member_id", "visit_date"],
        },
        error_codes=[ErrorCode.NOT_FOUND, ErrorCode.INVALID_ARG],
    ),
}

_TOOL_IMPLS = {
    "query_member": query_member,
    "check_benefit": check_benefit,
    "book_visit": book_visit,
}


def build_registry(registry: ToolRegistry | None = None) -> ToolRegistry:
    """注册 milktea 的 3 个业务工具（场景内自用；通用装配见 src.tools.registry）。"""
    registry = registry if registry is not None else ToolRegistry()
    for name, fn in _TOOL_IMPLS.items():
        registry.register(name, fn, SCHEMAS[name])
    return registry
