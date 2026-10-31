"""C2 工具契约：统一返回封装 + 工具注册表 + JSON Schema。

统一返回：
  成功：{"ok": true, "data": {...}, "error": null, "meta": {...}}
  失败：{"ok": false, "data": null, "error": {"code", "msg", "retryable"}, "meta": {...}}

错误码枚举：NOT_FOUND / INVALID_ARG / TOOL_TIMEOUT / INTERNAL_ERROR / DEGRADED

每个工具的 JSON Schema 同时用于三处：
  - 模型 function calling
  - 参数正确率评测
  - 文档生成
"""

from __future__ import annotations

import time
from typing import Any, Callable

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 错误码
# ---------------------------------------------------------------------------

class ErrorCode:
    NOT_FOUND = "NOT_FOUND"
    INVALID_ARG = "INVALID_ARG"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    DEGRADED = "DEGRADED"


class ToolError(BaseModel):
    code: str
    msg: str
    retryable: bool = False


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: ToolError | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


def ok(data: Any, **meta: Any) -> dict[str, Any]:
    return ToolResult(ok=True, data=data, meta=meta).model_dump()


def fail(code: str, msg: str, retryable: bool = False, **meta: Any) -> dict[str, Any]:
    return ToolResult(
        ok=False,
        error=ToolError(code=code, msg=msg, retryable=retryable),
        meta=meta,
    ).model_dump()


# ---------------------------------------------------------------------------
# 工具 Schema
# ---------------------------------------------------------------------------

class ToolParam(BaseModel):
    type: str
    description: str = ""
    pattern: str | None = None
    enum: list[Any] | None = None
    items: dict[str, Any] | None = None


class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema 对象
    error_codes: list[str] = Field(default_factory=list)


def build_function_calling_schema(schema: ToolSchema) -> dict[str, Any]:
    """把 ToolSchema 转成 LLM function calling 格式。"""
    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": schema.parameters,
        },
    }


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

ToolFn = Callable[..., dict[str, Any]]


class ToolEntry:
    def __init__(self, name: str, fn: ToolFn, schema: ToolSchema) -> None:
        self.name = name
        self.fn = fn
        self.schema = schema

    def invoke(self, **args: Any) -> dict[str, Any]:
        start = time.time()
        try:
            result = self.fn(**args)
            result.setdefault("meta", {})
            result["meta"]["latency_ms"] = int((time.time() - start) * 1000)
            return result
        except Exception as e:  # noqa: BLE001
            return fail(
                ErrorCode.INTERNAL_ERROR,
                f"工具 {self.name} 执行异常: {e}",
                retryable=False,
            )


class ToolRegistry:
    """工具注册表。工具按场景注册，代码不出现业务分支。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register(self, name: str, fn: ToolFn, schema: ToolSchema) -> None:
        if name in self._tools:
            raise ValueError(f"工具 {name!r} 已注册")
        self._tools[name] = ToolEntry(name, fn, schema)

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def function_calling_schemas(self) -> list[dict[str, Any]]:
        return [build_function_calling_schema(e.schema) for e in self._tools.values()]

    def invoke(self, name: str, **args: Any) -> dict[str, Any]:
        entry = self._tools.get(name)
        if entry is None:
            return fail(ErrorCode.NOT_FOUND, f"未知工具: {name}", retryable=False)
        return entry.invoke(**args)


_GLOBAL_REGISTRY = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _GLOBAL_REGISTRY
