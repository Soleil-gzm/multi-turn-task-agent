"""场景装配器：按 scenario.yaml 动态构建工具注册表与 LLM。

约定（对场景包的要求，而非框架的分支）：
  - 工具实现模块暴露 `SCHEMAS: dict[str, ToolSchema]`
  - 可选：yaml 的 `demo.llm` 指向一个返回 LLMClient 的工厂函数

本模块不出现任何场景名或业务字段，新增场景无需改动它。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import yaml

from src.core.config import ScenarioConfig
from src.tools.registry import ToolRegistry


def load_raw_yaml(path: str | Path) -> dict[str, Any]:
    """读取原始 yaml（用于 demo 段等框架 schema 之外的配置）。"""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_registry_from_config(config: ScenarioConfig) -> ToolRegistry:
    """按 `tools[].impl` 动态装配注册表：module.SCHEMAS[name] + module.func。"""
    registry = ToolRegistry()
    for decl in config.tools:
        module_path, _, func_name = decl.impl.rpartition(".")
        if not module_path:
            raise ValueError(f"工具 {decl.name!r} 的 impl 必须形如 module.func，实际为 {decl.impl!r}")
        module = importlib.import_module(module_path)
        schemas = getattr(module, "SCHEMAS", None)
        if not isinstance(schemas, dict) or decl.name not in schemas:
            raise ValueError(f"工具模块 {module_path!r} 未在 SCHEMAS 中声明 {decl.name!r}")
        registry.register(decl.name, getattr(module, func_name), schemas[decl.name])
    return registry


def build_llm(spec: str | None) -> Any:
    """按 `module.factory` 构建 LLM；spec 为空时回落到 MockLLMClient。"""
    if not spec:
        from src.agent.llm import MockLLMClient

        return MockLLMClient()
    module_path, _, func_name = spec.rpartition(".")
    module = importlib.import_module(module_path)
    return getattr(module, func_name)()
