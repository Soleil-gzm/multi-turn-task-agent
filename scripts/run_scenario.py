#!/usr/bin/env python3
"""通用场景运行器：任意 scenario.yaml 一条命令跑通（不修改框架代码）。

用法：
  python scripts/run_scenario.py --config configs/scenarios/milktea.yaml
  python scripts/run_scenario.py --config configs/scenarios/milktea.yaml -i

约定：
  - 工具由 `tools[].impl` 动态装配（工具模块需暴露 SCHEMAS）
  - 演示用 LLM 由 yaml 的 `demo.llm` 指定（缺省回落 MockLLMClient）
  - 演示对话脚本由 yaml 的 `demo.messages` 提供
本脚本不含任何场景名或业务分支。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.loop import Agent, new_session_id  # noqa: E402
from src.core.config import load_config  # noqa: E402
from scenarios.loader import (  # noqa: E402
    build_llm,
    build_registry_from_config,
    load_raw_yaml,
)


def build_agent(config_path: str) -> tuple[Agent, dict]:
    config = load_config(config_path)
    raw = load_raw_yaml(config_path)
    demo = raw.get("demo") or {}
    agent = Agent(
        config,
        build_registry_from_config(config),
        build_llm(demo.get("llm")),
    )
    return agent, demo


def run_scripted(agent: Agent, messages: list[str]) -> list[str]:
    """按脚本逐轮发送，返回访问过的 stage 序列（含本轮内的一次性转移）。"""
    session = agent.start(new_session_id(), case_id="SCENARIO-DEMO")
    visited: list[str] = ["INIT"]

    def _record(turn) -> None:
        """从本轮新增的 state_transition 中提取 stage（同一轮可能连跳多级）。"""
        for step in turn.trace.steps[-turn.new_steps:]:
            if getattr(step.type, "value", "") == "state_transition" and step.state_after:
                stage = step.state_after.split("/", 1)[0]
                if stage not in visited:
                    visited.append(stage)

    for msg in messages:
        turn = session.send(msg)
        _record(turn)
        print(
            f"[轮 {turn.round_no}] 用户: {msg}\n"
            f"          助手: {turn.reply or '（无自然语言回复）'}\n"
            f"          状态: {turn.stage}"
            + (f"/{turn.offer_state}" if turn.offer_state else "")
            + f"  工具: {turn.tool_calls or '—'}  （本轮新增 {turn.new_steps} 步）"
        )
        if turn.ended:
            break

    result = session.result()
    print("\n" + "=" * 60)
    print(f"场景: {agent.config.scenario.display_name}")
    print(f"终态: {result.stage}  任务完成: {result.task_completed}")
    print(f"访问阶段: {' → '.join(visited)}")
    print(f"工具调用: {result.tool_calls}")
    print(f"违规: {result.violations or '无'}")
    print(f"trace: {len(result.trace.steps)} 步, totals={result.trace.totals.model_dump()}")

    trace_dir = ROOT / "eval" / "reports" / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"{result.trace.trace_id}.json"
    trace_path.write_text(
        json.dumps(result.trace.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"trace 已落盘: {trace_path}")
    return visited


def run_interactive(agent: Agent) -> None:
    session = agent.start(new_session_id(), case_id="SCENARIO-CHAT")
    print("进入交互模式，输入 exit 退出。")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            print("已退出。")
            return
        turn = session.send(text)
        print(f"助手: {turn.reply or '（无自然语言回复）'}")
        print(f"状态: {turn.stage}" + (f"/{turn.offer_state}" if turn.offer_state else ""))
        if turn.ended:
            print("会话已到终态，进程退出。")
            return


def main() -> int:
    parser = argparse.ArgumentParser(description="通用场景运行器")
    parser.add_argument("--config", required=True, help="scenario.yaml 路径")
    parser.add_argument("-i", "--interactive", action="store_true", help="交互模式")
    args = parser.parse_args()

    agent, demo = build_agent(args.config)
    print(f"场景: {agent.config.scenario.display_name}")
    print(f"stages: {agent.config.state_machine.stages}")
    print(f"工具: {agent.registry.names()}\n")

    if args.interactive:
        run_interactive(agent)
    else:
        messages = demo.get("messages") or []
        if not messages:
            parser.error("yaml 未提供 demo.messages，请用 -i 进入交互模式")
        run_scripted(agent, messages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
