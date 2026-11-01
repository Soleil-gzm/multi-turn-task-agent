"""C3 配置契约：scenario.yaml schema + 启动预检。

启动时校验三项，失败即 fail-fast（退出码非 0），不静默降级：
  1. 必填字段齐全，tools[].impl 可导入
  2. 转移表 from/to 合法（stage / stage/offer_state / *），无孤立或不可达状态
  3. policy 数值合法
"""

from __future__ import annotations

import importlib
import re
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class ScenarioMeta(BaseModel):
    id: str
    display_name: str
    version: int = 1


class Goal(BaseModel):
    success_criteria: str
    max_rounds: int = Field(15, ge=1)
    on_success: str | None = None


class Transition(BaseModel):
    from_: str = Field(..., alias="from")
    to: str
    when: str

    model_config = {"populate_by_name": True}


class StateMachine(BaseModel):
    stages: list[str]
    escalate_to: str | None = None
    escalate_tool: str | None = None
    offer_states: dict[str, list[str]] = Field(default_factory=dict)
    transitions: list[Transition] = Field(default_factory=list)


class ToolDecl(BaseModel):
    name: str
    impl: str


class Policy(BaseModel):
    max_installments: int = Field(3, ge=1)
    max_extension_days: int = Field(30, ge=0)
    max_waiver_ratio: float = Field(0.10, ge=0, le=1)
    forbidden_patterns: list[str] = Field(default_factory=list)


class Knowledge(BaseModel):
    corpus: str
    top_k: int = Field(5, ge=1)


class Prompts(BaseModel):
    system: str


class ContradictionRule(BaseModel):
    """矛盾数据告知规则：工具返回的两个字段不一致时，必须向客户说明。

    字段名与话术由场景配置声明，框架代码不出现业务字段。
    """

    tool: str
    fields: list[str] = Field(default_factory=list)
    template: str


class PolicyRefusalRule(BaseModel):
    """超政策请求的显式拒绝规则：从用户文本抽取数值，与 policy 上限比较。

    `pattern` 必须含 1 个捕获组（被抽取的数值）；`scale=percent` 表示抽取值需除以 100
    后再与 `policy_field` 比较。`template` 可用 `{value}`（原始数值）与 `{limit}`（上限展示值）。
    """

    pattern: str
    policy_field: str
    scale: str = "raw"  # raw | percent
    template: str


class ResponseRules(BaseModel):
    contradiction: ContradictionRule | None = None
    policy_refusal: list[PolicyRefusalRule] = Field(default_factory=list)


class ScenarioConfig(BaseModel):
    scenario: ScenarioMeta
    goal: Goal
    state_machine: StateMachine
    entities: dict[str, list[str]] = Field(default_factory=dict)
    tools: list[ToolDecl]
    policy: Policy
    knowledge: Knowledge | None = None
    prompts: Prompts
    response: ResponseRules | None = None
    synthesis: dict[str, Any] = Field(default_factory=dict)
    eval: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 启动预检
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """配置校验失败。"""


def _split_state(state: str) -> tuple[str, str | None]:
    """把 'NEGOTIATE/probe' 拆成 (stage, offer_state)；'*' 返回 ('*', None)。"""
    if state == "*":
        return "*", None
    if "/" in state:
        stage, offer = state.split("/", 1)
        return stage, offer
    return state, None


def _state_is_valid(state: str, cfg: ScenarioConfig) -> bool:
    """校验 from/to 中的状态引用是否合法。

    支持三种形式：
      - "*"：通配所有 stage
      - "stage"：纯 stage
      - "stage/offer"：指定子状态；offer="*" 表示该 stage 的任意子状态
    """
    if state == "*":
        return True
    stage, offer = _split_state(state)
    if stage not in cfg.state_machine.stages:
        return False
    if offer is not None:
        if offer == "*":
            # stage/* 要求该 stage 有定义 offer_states
            return bool(cfg.state_machine.offer_states.get(stage))
        allowed = cfg.state_machine.offer_states.get(stage, [])
        if offer not in allowed:
            return False
    return True


def _check_transitions(cfg: ScenarioConfig) -> None:
    sm = cfg.state_machine
    if not sm.stages:
        raise ConfigError("state_machine.stages 不能为空")

    # 1. 每个 from/to 必须合法
    for i, t in enumerate(sm.transitions, 1):
        if not _state_is_valid(t.from_, cfg):
            raise ConfigError(f"转移 #{i} 的 from={t.from_!r} 不在 stages 或 offer_states 内")
        if not _state_is_valid(t.to, cfg):
            raise ConfigError(f"转移 #{i} 的 to={t.to!r} 不在 stages 或 offer_states 内")

    # 2. 无孤立状态（除 escalate_to 外，每个 stage 至少出现在某条转移的 from 或 to 中）
    mentioned: set[str] = set()
    for t in sm.transitions:
        mentioned.add(_split_state(t.from_)[0])
        mentioned.add(_split_state(t.to)[0])
    if "*" in mentioned:
        mentioned.discard("*")
        mentioned.update(sm.stages)  # 通配符覆盖所有 stage
    isolated = set(sm.stages) - mentioned
    if sm.escalate_to:
        isolated.discard(sm.escalate_to)
    if isolated:
        raise ConfigError(f"存在孤立状态（无任何转移引用）: {sorted(isolated)}")

    # 3. 无不可达状态（每个非起始 stage 必须能被某条转移的 to 到达）
    #    简化检查：每个 stage 要么是 INIT，要么出现在某 to 中
    reachable: set[str] = set()
    for t in sm.transitions:
        reachable.add(_split_state(t.to)[0])
    if "*" in reachable:
        reachable.discard("*")
        reachable.update(sm.stages)
    unreachable = set(sm.stages) - reachable - {"INIT"}
    if unreachable:
        raise ConfigError(f"存在不可达状态（无任何转移指向）: {sorted(unreachable)}")


def _check_tools(cfg: ScenarioConfig) -> None:
    for t in cfg.tools:
        module_path, _, func_name = t.impl.rpartition(".")
        if not module_path:
            raise ConfigError(f"工具 {t.name!r} 的 impl={t.impl!r} 格式错误，应为 module.func")
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ConfigError(f"工具 {t.name!r} 的 impl={t.impl!r} 无法导入: {e}") from e
        if not hasattr(module, func_name):
            raise ConfigError(f"工具 {t.name!r} 的 impl={t.impl!r} 中无 {func_name!r}")


def _check_policy(cfg: ScenarioConfig) -> None:
    p = cfg.policy
    if p.max_installments < 1:
        raise ConfigError("policy.max_installments 必须 >= 1")
    if p.max_extension_days < 0:
        raise ConfigError("policy.max_extension_days 必须 >= 0")
    if not (0 <= p.max_waiver_ratio <= 1):
        raise ConfigError("policy.max_waiver_ratio 必须在 [0, 1] 之间")


def _check_response(cfg: ScenarioConfig) -> None:
    """校验升级工具与矛盾告知规则引用的工具、字段、占位符。"""
    sm = cfg.state_machine
    tool_names = {t.name for t in cfg.tools}

    if sm.escalate_tool:
        if sm.escalate_tool not in tool_names:
            raise ConfigError(f"state_machine.escalate_tool={sm.escalate_tool!r} 未在 tools 中声明")

    rule = cfg.response.contradiction if cfg.response else None
    if rule is not None:
        if rule.tool not in tool_names:
            raise ConfigError(f"response.contradiction.tool={rule.tool!r} 未在 tools 中声明")
        if len(rule.fields) < 2:
            raise ConfigError("response.contradiction.fields 至少需要 2 个字段（前两个用于判断是否矛盾）")
        placeholders = set(re.findall(r"\{(\w+)\}", rule.template))
        unknown = placeholders - set(rule.fields)
        if unknown:
            raise ConfigError(
                f"response.contradiction.template 引用了未声明的字段: {sorted(unknown)}"
            )

    for i, refusal in enumerate(cfg.response.policy_refusal if cfg.response else [], 1):
        try:
            compiled = re.compile(refusal.pattern)
        except re.error as e:
            raise ConfigError(f"response.policy_refusal #{i} 的 pattern 非法: {e}") from e
        if compiled.groups < 1:
            raise ConfigError(f"response.policy_refusal #{i} 的 pattern 必须含 1 个捕获组（被抽取的数值）")
        if refusal.policy_field not in Policy.model_fields:
            raise ConfigError(
                f"response.policy_refusal #{i} 的 policy_field={refusal.policy_field!r} 不在 policy 中"
            )
        if refusal.scale not in ("raw", "percent"):
            raise ConfigError(f"response.policy_refusal #{i} 的 scale 只能是 raw 或 percent")
        placeholders = set(re.findall(r"\{(\w+)\}", refusal.template))
        unknown = placeholders - {"value", "limit"}
        if unknown:
            raise ConfigError(
                f"response.policy_refusal #{i} 的 template 只支持 {{value}} / {{limit}}，"
                f"发现: {sorted(unknown)}"
            )


def load_config(path: str) -> ScenarioConfig:
    """加载并校验 scenario.yaml，失败抛 ConfigError。"""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    cfg = ScenarioConfig.model_validate(raw)
    _check_transitions(cfg)
    _check_tools(cfg)
    _check_policy(cfg)
    _check_response(cfg)
    return cfg
