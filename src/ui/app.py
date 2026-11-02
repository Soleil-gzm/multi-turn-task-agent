"""Gradio 交互界面（骨架）。

运行：
    python -m src.ui.app            # 默认 MockLLMClient，直连 Agent
    python -m src.ui.app --port 7861

设计要点（对应 docs/gradio-reference.md）：
- A 级复用：主题 / CSS / HTML 渲染器 / 元组返回 + gr.update + 异常兜底 / Progress / 下载
- B 级改造：静态回放 → 在线多轮；配置预览 → 场景摘要 + 转移矩阵；import 无副作用
- 不引入 pandas / numpy；gradio 仅在 create_demo() 内惰性导入

当前骨架的行为边界（后续 U3/U4 补齐）：
- 每轮把「全部历史用户消息」重放给 Agent（Agent.run 目前是批量接口），
  取最后一条非空回复作为本轮 assistant 输出；会话级增量接口落地后再换。
- 会话达到终态（CLOSED / ESCALATED）后不再调用 Agent，只提示重置。
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
TRACE_DIR = ROOT / "eval" / "reports" / "ui_traces"

SCENARIO_PATH = ROOT / "configs" / "scenarios" / "receivables.yaml"

TERMINAL_STAGES = ("CLOSED", "ESCALATED")

BANNER_HTML = """
<div class="mta-banner">
  <div class="mta-banner-title">应收账款协商 · 多轮任务型 Agent</div>
  <div class="mta-banner-sub">
    配置驱动 · 元组状态机 · 6 个业务工具 · 每轮可查看状态与轨迹
  </div>
  <div class="mta-banner-tags">
    <span class="mta-tag">YAML 注入</span>
    <span class="mta-tag">12 条转移</span>
    <span class="mta-tag">C2 统一返回</span>
    <span class="mta-tag">C6 Trace</span>
  </div>
</div>
"""

CSS = """
.mta-banner {
  background: linear-gradient(135deg, #1F6B4E 0%, #2E7D5B 45%, #62B88F 100%);
  border-radius: 16px; padding: 22px 26px; margin-bottom: 12px;
  box-shadow: 0 8px 22px rgba(31, 107, 78, 0.22);
}
.mta-banner-title { color: #FFFFFF; font-size: 24px; font-weight: 700; margin-bottom: 6px; }
.mta-banner-sub { color: #DCF3E7; font-size: 14px; margin-bottom: 12px; }
.mta-banner-tags { display: flex; flex-wrap: wrap; gap: 8px; }
.mta-tag {
  background: rgba(255, 255, 255, 0.16); border: 1px solid rgba(255, 255, 255, 0.35);
  color: #FFFFFF; border-radius: 999px; padding: 3px 12px; font-size: 12px;
}
.mta-matrix { overflow-x: auto; max-height: 380px; border-radius: 10px; }
.mta-matrix table { border-collapse: collapse; font-size: 12px; width: 100%; }
.mta-matrix th, .mta-matrix td {
  padding: 4px 8px; text-align: center; white-space: nowrap;
  border: 1px solid #EAF1ED;
}
.mta-matrix thead th, .mta-matrix .rowh {
  background: #F4F8F6; color: #33473E; font-weight: 600;
}
.mta-card {
  background: #F7FBF9; border: 1px solid #DCE9E2; border-left: 5px solid #2E7D5B;
  border-radius: 10px; padding: 12px 16px; margin-bottom: 8px;
}
.mta-card-title { color: #1F6B4E; font-weight: 700; font-size: 13px; margin-bottom: 8px; }
.mta-card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 6px 14px; }
.mta-card-item { font-size: 12px; color: #33473E; }
.mta-card-item b { color: #1F6B4E; }
.mta-stats { display: flex; gap: 10px; flex-wrap: wrap; margin: 4px 0 8px; }
.mta-stat { background: #F1F5F3; border-radius: 8px; padding: 6px 14px; font-size: 13px; color: #33473E; }
.mta-stat b { color: #1F6B4E; font-size: 15px; }
.mta-trace table { border-collapse: collapse; width: 100%; font-size: 12px; }
.mta-trace th, .mta-trace td { border: 1px solid #EAF1ED; padding: 4px 8px; text-align: left; }
.mta-trace th { background: #F4F8F6; color: #33473E; }
footer { visibility: hidden; }
"""


# ---------------------------------------------------------------------------
# 纯渲染函数（不依赖 gradio，可单测）
# ---------------------------------------------------------------------------

def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _stage_of(state: str) -> str:
    """'NEGOTIATE/probe' → 'NEGOTIATE'；'*' → '*'。"""
    return state.split("/", 1)[0]


def transition_counts(config) -> dict[tuple[str, str], int]:
    """统计 stage → stage 的转移条数（子状态折叠到 stage，通配符展开）。"""
    stages = list(config.state_machine.stages)
    counts: dict[tuple[str, str], int] = {}
    for t in config.state_machine.transitions:
        sources = stages if t.from_ == "*" else [_stage_of(t.from_)]
        targets = stages if t.to == "*" else [_stage_of(t.to)]
        for s in sources:
            for tgt in targets:
                counts[(s, tgt)] = counts.get((s, tgt), 0) + 1
    return counts


def transition_matrix_html(config) -> str:
    """阶段转移矩阵：单元格数字 = 该方向上的转移条数，颜色深浅 = 条数。"""
    stages = list(config.state_machine.stages)
    counts = transition_counts(config)
    peak = max(counts.values()) if counts else 1

    parts = ['<div class="mta-matrix"><table><thead><tr><th>from \\ to</th>']
    parts.extend(f"<th>{_esc(s)}</th>" for s in stages)
    parts.append("</tr></thead><tbody>")
    for src in stages:
        parts.append(f'<tr><th class="rowh">{_esc(src)}</th>')
        for dst in stages:
            n = counts.get((src, dst), 0)
            if n == 0:
                parts.append('<td style="color:#CBD5E1">·</td>')
            else:
                alpha = min(0.15 + (n / peak) * 0.85, 1.0)
                color = "#FFFFFF" if alpha > 0.5 else "#12402F"
                parts.append(f'<td style="background:rgba(46,125,91,{alpha:.2f});color:{color}">{n}</td>')
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def scenario_summary_html(config) -> str:
    """场景摘要卡片：stage / 子状态 / policy 上限。"""
    sm = config.state_machine
    p = config.policy
    items = [
        ("场景", config.scenario.display_name),
        ("stages", "、".join(sm.stages)),
        ("offer_states", "；".join(f"{k}: {'/'.join(v)}" for k, v in sm.offer_states.items()) or "—"),
        ("转移条数", len(sm.transitions)),
        ("分期上限", f"{p.max_installments} 期"),
        ("延期上限", f"{p.max_extension_days} 天"),
        ("减免上限", f"{p.max_waiver_ratio:.0%}"),
    ]
    body = "".join(f'<div class="mta-card-item"><b>{_esc(k)}</b>：{_esc(v)}</div>' for k, v in items)
    return (
        f'<div class="mta-card"><div class="mta-card-title">场景配置</div>'
        f'<div class="mta-card-grid">{body}</div></div>'
    )


def case_card_html(init_state: dict[str, Any] | None) -> str:
    """一案一卡：渲染 case.init_state（复用参考项目的信息卡样式）。"""
    if not init_state:
        return ""
    body = "".join(
        f'<div class="mta-card-item"><b>{_esc(k)}</b>：{_esc(v)}</div>'
        for k, v in init_state.items()
        if v not in (None, "")
    )
    return (
        f'<div class="mta-card"><div class="mta-card-title">当前 case</div>'
        f'<div class="mta-card-grid">{body}</div></div>'
    )


def status_html(turn: dict[str, Any] | None) -> str:
    """状态条：stage / round / offer_state / 工具调用 / 违规数。"""
    if not turn:
        return '<div class="mta-stats"><div class="mta-stat">等待输入…</div></div>'
    cards = [
        ("stage", turn.get("stage", "-")),
        ("round", turn.get("round_no", "-")),
        ("offer_state", turn.get("offer_state") or "—"),
        ("工具调用", turn.get("tool_count", 0)),
        ("违规", turn.get("violations", 0)),
        ("任务完成", "是" if turn.get("task_completed") else "否"),
    ]
    body = "".join(f'<div class="mta-stat">{_esc(k)} <b>{_esc(v)}</b></div>' for k, v in cards)
    return f'<div class="mta-stats">{body}</div>'


def _trace_detail(step) -> str:
    """单步摘要：按 step.type 取最有信息量的字段。"""
    kind = getattr(step.type, "value", str(step.type))
    if kind == "tool_call":
        args = json.dumps(step.args or {}, ensure_ascii=False)
        ok = "ok" if (step.result or {}).get("ok") else "fail"
        return f"{step.tool}({args}) → {ok}"
    if kind == "state_transition":
        return f"{step.state_before} → {step.state_after}"
    if kind == "guardrail":
        return f"{step.rule}: {step.detail}"
    if kind == "llm_call":
        return f"in={step.input_tokens} out={step.output_tokens}"
    return step.detail or ""


def trace_table_html(trace) -> str:
    """C6 Trace → HTML 表格（最多 40 步，超出提示）。"""
    if trace is None:
        return ""
    steps = list(trace.steps)[:40]
    rows = "".join(
        f"<tr><td>{s.step}</td><td>{_esc(getattr(s.type, 'value', s.type))}</td>"
        f"<td>{_esc(_trace_detail(s)[:160])}</td><td>{s.latency_ms}</td></tr>"
        for s in steps
    )
    more = (
        f'<div class="mta-card-item">仅展示前 40 步，共 {len(trace.steps)} 步</div>'
        if len(trace.steps) > 40
        else ""
    )
    return (
        '<div class="mta-trace"><table><thead><tr>'
        "<th>#</th><th>type</th><th>detail</th><th>ms</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>{more}"
    )


# ---------------------------------------------------------------------------
# 会话逻辑（不依赖 gradio，可单测）
# ---------------------------------------------------------------------------

def new_turn_state(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "turns": [],          # 历史用户输入（Agent.run 的入参）
        "messages": [],       # Chatbot 展示用
        "ended": False,
        "trace_json": None,   # 落盘路径，供下载
        "last": None,         # 最近一轮的状态摘要
    }


def latest_reply(result) -> str:
    """取本轮回复：AgentResult.replies 里最后一条非空；都空时给占位说明。"""
    for text in reversed(result.replies):
        if text.strip():
            return text
    return "（已执行工具调用，本回合无自然语言回复）"


def turn_summary(result, round_no: int) -> dict[str, Any]:
    stage = result.stage
    offer = None
    if stage == "NEGOTIATE" and result.trace.steps:
        # 终态轨迹里最后一个 state_transition 的 after 形如 NEGOTIATE/probe
        for s in reversed(result.trace.steps):
            if getattr(s.type, "value", "") == "state_transition" and s.state_after:
                offer = s.state_after.split("/", 1)[1] if "/" in s.state_after else None
                break
    return {
        "stage": stage,
        "round_no": round_no,
        "offer_state": offer,
        "tool_count": len(result.tool_calls),
        "violations": len(result.violations),
        "task_completed": result.task_completed,
    }


def dump_trace(result) -> str:
    """把本轮 trace 落盘到 eval/reports/ui_traces/，返回路径字符串。"""
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    path = TRACE_DIR / f"{result.trace.trace_id}.json"
    path.write_text(
        result.trace.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return str(path)


# ---------------------------------------------------------------------------
# Gradio 界面（惰性导入 gradio）
# ---------------------------------------------------------------------------

def create_demo(config=None, agent=None):
    """构建 Blocks 界面。config / agent 可注入，便于测试与替换真实模型。"""
    import gradio as gr

    if config is None:
        from src.core.config import load_config

        config = load_config(str(SCENARIO_PATH))

    if agent is None:
        from src.agent.llm import get_llm_client
        from src.agent.loop import Agent
        from src.tools.receivables.tools import build_registry

        agent = Agent(config, build_registry(), get_llm_client())

    def _on_submit(user_text: str, state: dict[str, Any] | None):
        """发送一条用户消息：追加 → 调 Agent → 更新消息 / 状态 / trace / 下载。"""
        state = state or new_turn_state(_new_session())
        history = list(state["messages"])

        text = (user_text or "").strip()
        if not text:
            return history, state, status_html(state.get("last")), "", None

        history.append({"role": "user", "content": text})

        if state["ended"]:
            history.append({
                "role": "assistant",
                "content": "当前会话已结束，请点击「重置会话」后重新开始。",
            })
            return history, state, status_html(state.get("last")), "", state.get("trace_json")

        turns = state["turns"] + [text]
        try:
            result = agent.run(state["session_id"], turns, case_id=None)
        except Exception as e:  # 单轮异常不拖垮界面
            history.append({"role": "assistant", "content": f"运行失败：{e}"})
            return history, state, status_html(state.get("last")), "", state.get("trace_json")

        history.append({"role": "assistant", "content": latest_reply(result)})

        trace_path = dump_trace(result)
        new_state = {
            **state,
            "turns": turns,
            "messages": history,
            "ended": result.stage in TERMINAL_STAGES,
            "trace_json": trace_path,
            "last": turn_summary(result, round_no=len(turns)),
        }
        return (
            history,
            new_state,
            status_html(new_state["last"]),
            trace_table_html(result.trace),
            trace_path,
        )

    def _on_reset():
        state = new_turn_state(_new_session())
        return [], state, status_html(None), "", None

    with gr.Blocks(title="多轮任务型 Agent") as demo:
        gr.HTML(BANNER_HTML)

        with gr.Row():
            with gr.Column(scale=1):
                gr.HTML(scenario_summary_html(config))
                with gr.Accordion("阶段转移矩阵", open=False):
                    gr.HTML(
                        '<div class="mta-card-item" style="margin-bottom:6px">'
                        '单元格 = stage→stage 的转移条数；通配符（NEGOTIATE/*、*）已展开为多条，'
                        '因此合计会大于配置里的 12 条规则。</div>'
                    )
                    gr.HTML(transition_matrix_html(config))

            with gr.Column(scale=2):
                # Gradio 6 起 Chatbot 默认即为 messages 格式（role/content），无需 type 参数
                chatbot = gr.Chatbot(
                    label="对话",
                    height=420,
                )
                with gr.Row():
                    user_input = gr.Textbox(
                        placeholder="例如：合同号 HT-Y2026-012",
                        label="输入",
                        scale=4,
                    )
                    send_btn = gr.Button("发送", variant="primary", scale=1)
                with gr.Row():
                    reset_btn = gr.Button("重置会话")

                status = gr.HTML(status_html(None))

        with gr.Accordion("轨迹（C6 Trace）", open=False):
            trace_html = gr.HTML("")
            trace_file = gr.DownloadButton("下载本轮 trace", value=None)

        session_state = gr.State(new_turn_state(_new_session()))

        outputs = [chatbot, session_state, status, trace_html, trace_file]
        send_btn.click(_on_submit, inputs=[user_input, session_state], outputs=outputs)
        user_input.submit(_on_submit, inputs=[user_input, session_state], outputs=outputs)
        reset_btn.click(_on_reset, inputs=[], outputs=outputs)

    return demo


def _new_session() -> str:
    from src.agent.loop import new_session_id

    return new_session_id()


def launch(port: int = 7860, share: bool = False) -> None:
    import gradio as gr

    demo = create_demo()
    demo.queue()  # 多轮调用耗时可能超默认时限
    demo.launch(
        server_name="127.0.0.1",
        server_port=port,
        share=share,
        css=CSS,
        theme=gr.themes.Soft(primary_hue="green", neutral_hue="stone"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="多轮任务型 Agent · Gradio 界面")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--share", action="store_true", help="生成公网分享链接")
    args = parser.parse_args()

    try:
        import gradio  # noqa: F401
    except ImportError:
        print("未安装 gradio。请先执行：python -m pip install gradio")
        return 1

    launch(port=args.port, share=args.share)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
