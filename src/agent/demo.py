"""Walking Skeleton 演示：一条命令跑通任意场景的多轮对话并落 trace。

用法：
    python -m src.agent.demo
    python -m src.agent.demo --config configs/scenarios/<name>.yaml

场景由配置发现（`MTA_CONFIG` > 带评测集的场景 > 第一个场景），演示脚本取自 yaml 的 `demo.messages`。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.llm import get_llm_client
from src.agent.loop import Agent, new_session_id
from src.core.config import default_scenario_path, load_config, load_raw_config
from src.tools.registry import build_registry_from_config


def main() -> None:
    parser = argparse.ArgumentParser(description="多轮对话演示（任意场景）")
    parser.add_argument("--config", default=None, help="scenario.yaml 路径，缺省自动发现")
    parser.add_argument(
        "--model",
        default=None,
        help="模型 spec：mock / openai_compat / module.factory（缺省读 MTA_LLM / yaml demo.llm）",
    )
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else default_scenario_path()
    demo = load_raw_config(config_path).get("demo") or {}

    print(f"[1/5] 加载配置: {config_path}")
    config = load_config(str(config_path))
    print(f"      场景: {config.scenario.display_name}，stages={config.state_machine.stages}")

    print("[2/5] 注册工具:", end=" ")
    registry = build_registry_from_config(config)
    print(registry.names())

    print("[3/5] 初始化 Agent")
    agent = Agent(config, registry, get_llm_client(args.model or demo.get("llm")))

    messages = demo.get("messages") or ["你好"]
    print("[4/5] 运行多轮对话（happy path）")
    result = agent.run(new_session_id(), messages, case_id="DEMO-001")

    print("[5/5] 对话结束")
    print(f"      终态 stage: {result.stage}")
    print(f"      任务完成: {result.task_completed}")
    print(f"      调用工具: {result.tool_calls}")
    print(f"      违规: {result.violations if result.violations else '无'}")
    print()
    print("=" * 60)
    print("Trace 摘要:")
    print(f"  trace_id: {result.trace.trace_id}")
    print(f"  steps: {len(result.trace.steps)}")
    for s in result.trace.steps:
        extra = ""
        if s.type == "tool_call":
            extra = f" tool={s.tool} ok={s.result.get('ok') if s.result else '?'}"
        elif s.type == "state_transition":
            extra = f" {s.state_before} -> {s.state_after}"
        elif s.type == "guardrail":
            extra = f" rule={s.rule}"
        print(f"  [{s.step}] {s.type}{extra}")
    print(f"  totals: {result.trace.totals.model_dump()}")
    print(f"  outcome: {result.trace.outcome.model_dump() if result.trace.outcome else None}")

    # 落盘 trace
    trace_dir = ROOT / "eval" / "reports" / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"{result.trace.trace_id}.json"
    with open(trace_path, "w", encoding="utf-8") as f:
        json.dump(result.trace.model_dump(), f, ensure_ascii=False, indent=2)
    print(f"\nTrace 已落盘: {trace_path}")


if __name__ == "__main__":
    main()
