"""Gradio 交互界面（骨架）。

运行：
    python -m src.ui.app            # 默认 MockLLMClient，直连 Agent
    python -m src.ui.app --port 7861

设计要点（对应 docs/gradio-reference.md）：
- A 级复用：主题 / CSS / HTML 渲染器 / 元组返回 + gr.update + 异常兜底 / Progress / 下载
- B 级改造：静态回放 → 在线多轮；配置预览 → 场景摘要 + 转移矩阵；import 无副作用
- 不引入 pandas / numpy；gradio 仅在 create_demo() 内惰性导入

当前交互方式（U3）：
- 每轮只调用 `AgentSession.send(msg)`（会话级增量），不重放历史输入；
- 会话对象存在 `gr.State` 中，跨轮保留状态机阶段、事实状态与累计 trace；
- 到达终态（CLOSED / ESCALATED）后 `send` 不再调用 LLM，只提示重置。
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
        "session": None,      # AgentSession（进程内对象，跨轮保留）
        "messages": [],       # Chatbot 展示用
        "ended": False,
        "trace_json": None,   # 落盘路径，供下载
        "last": None,         # 最近一轮的状态摘要
    }


def latest_reply(result) -> str:
    """取本轮回复：兼容 TurnResult.reply 与 AgentResult.replies。"""
    replies = getattr(result, "replies", None)
    if replies:
        for text in reversed(replies):
            if text.strip():
                return text
    text = (getattr(result, "reply", "") or "").strip()
    return text or "（已执行工具调用，本回合无自然语言回复）"


def turn_summary(result, round_no: int | None = None) -> dict[str, Any]:
    stage = result.stage
    offer = getattr(result, "offer_state", None)
    if offer is None and stage == "NEGOTIATE":
        # 批量结果的 AgentResult 没有 offer_state，从最后一个转移反推
        for s in reversed(result.trace.steps):
            if getattr(s.type, "value", "") == "state_transition" and s.state_after:
                offer = s.state_after.split("/", 1)[1] if "/" in s.state_after else None
                break
    tool_count = result.trace.totals.tool_calls if result.trace else len(result.tool_calls)
    return {
        "stage": stage,
        "round_no": getattr(result, "round_no", round_no),
        "offer_state": offer,
        "tool_count": tool_count,
        "violations": len(result.violations),
        "task_completed": result.task_completed,
    }


def dump_trace(result) -> str:
    """把累计 trace 落盘到 eval/reports/ui_traces/，返回路径字符串。

    兼容 TurnResult（取 .trace 快照）与 Trace 对象本身。
    """
    trace = getattr(result, "trace", result)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    path = TRACE_DIR / f"{trace.trace_id}.json"
    path.write_text(
        trace.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return str(path)


# ---------------------------------------------------------------------------
# 评测报告渲染（读取 eval/reports/v0_baseline.json）
# ---------------------------------------------------------------------------

REPORT_PATH = ROOT / "eval" / "reports" / "v0_baseline.json"


def load_report(path: str | Path | None = None) -> dict[str, Any] | None:
    """读取评测报告；文件缺失或解析失败返回 None。"""
    p = Path(path) if path else REPORT_PATH
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _fmt_pct(value: Any) -> str:
    return "—" if value is None else f"{value:.1%}"


def _fmt_num(value: Any, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def metrics_html(report: dict[str, Any] | None) -> str:
    """六项指标卡：终态 / 完成率 / 工具均分 / 工具覆盖 / 矛盾告知 / 超政策拒绝。"""
    if not report:
        return (
            '<div class="mta-card"><div class="mta-card-title">评测报告</div>'
            '<div class="mta-card-item">尚未找到 eval/reports/v0_baseline.json，'
            "请点击「运行 80 条评测」或先执行 make eval。</div></div>"
        )

    contra = report.get("contradiction") or {}
    refusal = report.get("policy_refusal") or {}
    coverage = report.get("tool_coverage") or {}
    untouched = report.get("untouched_tools") or []
    total_tools = len(coverage) + len(untouched)

    cards = [
        ("终态匹配率", _fmt_pct(report.get("stage_match_rate"))),
        ("任务完成率", _fmt_pct(report.get("task_completion_rate"))),
        ("工具序列均分", _fmt_num(report.get("avg_tool_score"))),
        ("工具覆盖", f"{len(coverage)}/{total_tools}"),
        (
            "矛盾告知率",
            f"{_fmt_pct(contra.get('rate'))}"
            f"（{contra.get('followed', 0)}/{contra.get('applicable', 0)}）",
        ),
        (
            "超政策拒绝率",
            f"{_fmt_pct(refusal.get('rate'))}"
            f"（{refusal.get('followed', 0)}/{refusal.get('applicable', 0)}）",
        ),
    ]
    body = "".join(f'<div class="mta-stat">{_esc(k)} <b>{_esc(v)}</b></div>' for k, v in cards)
    untouch_note = (
        f'<div class="mta-card-item" style="margin-top:6px">未触达工具：{_esc("、".join(untouched))}</div>'
        if untouched
        else ""
    )
    return (
        f'<div class="mta-card"><div class="mta-card-title">评测指标</div>'
        f'<div class="mta-stats">{body}</div>'
        f'<div class="mta-card-item">model={_esc(report.get("model", "-"))} · '
        f'样本数={_esc(report.get("total_cases", "-"))}</div>{untouch_note}</div>'
    )


def layer_table_html(report: dict[str, Any] | None) -> str:
    """分层结果表：core / boundary / adversarial。"""
    by_layer = (report or {}).get("by_layer") or {}
    if not by_layer:
        return ""
    rows = "".join(
        f"<tr><td>{_esc(layer)}</td><td>{_esc(s.get('count', '-'))}</td>"
        f"<td>{_esc(_fmt_pct(s.get('stage_match_rate')))}</td>"
        f"<td>{_esc(_fmt_num(s.get('avg_tool_score')))}</td></tr>"
        for layer, s in by_layer.items()
    )
    return (
        '<div class="mta-trace"><table><thead><tr>'
        "<th>layer</th><th>样本</th><th>终态匹配</th><th>工具均分</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def eval_page_html(report: dict[str, Any] | None) -> str:
    """评测页整体 HTML：指标卡 + 分层表 + 口径提醒。"""
    note = (
        '<div class="mta-card-item" style="margin:6px 0">'
        "口径提醒：当前 model=mock-llm，数字只验证「评测集 → Agent → 指标 → 报告」链路，"
        "不代表模型能力，也不进版本间 Δ 表。</div>"
    )
    return metrics_html(report) + layer_table_html(report) + note


def run_eval() -> dict[str, Any] | None:
    """跑一遍 80 条评测并返回新报告（UI 按钮用，耗时约 1–2 秒）。"""
    from src.eval import runner

    runner.main()
    return load_report()


# ---------------------------------------------------------------------------
# 会话回调（模块级，便于不启动界面也能测试）
# ---------------------------------------------------------------------------

def submit_turn(
    agent,
    state: dict[str, Any] | None,
    user_text: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, str, str | None]:
    """处理一轮输入，返回 (聊天记录, 新状态, 状态条 HTML, Trace HTML, trace 路径)。

    与 Gradio 的 outputs 顺序一一对应；不依赖 gradio，可直接单测。
    """
    state = state or new_turn_state(_new_session())
    session = state.get("session") or agent.start(state["session_id"])
    history = list(state["messages"])

    text = (user_text or "").strip()
    if not text:
        return history, state, status_html(state.get("last")), "", state.get("trace_json")

    history.append({"role": "user", "content": text})
    # 会话已结束的情况由 session.send 自己兜底，返回提示话术
    try:
        result = session.send(text)
    except Exception as e:  # 单轮异常不拖垮界面
        history.append({"role": "assistant", "content": f"运行失败：{e}"})
        return (
            history,
            {**state, "session": session, "messages": history},
            status_html(state.get("last")),
            "",
            state.get("trace_json"),
        )

    history.append({"role": "assistant", "content": latest_reply(result)})

    trace_path = dump_trace(result)
    new_state = {
        **state,
        "session": session,
        "messages": history,
        "ended": result.ended,
        "trace_json": trace_path,
        "last": turn_summary(result, round_no=result.round_no),
    }
    return (
        history,
        new_state,
        status_html(new_state["last"]),
        trace_table_html(result.trace),
        trace_path,
    )


def reset_state() -> tuple[list, dict[str, Any], str, str, None]:
    """重置会话，返回与 submit_turn 相同长度的空结果。"""
    state = new_turn_state(_new_session())
    return [], state, status_html(None), "", None


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

    with gr.Blocks(title="多轮任务型 Agent") as demo:
        gr.HTML(BANNER_HTML)

        with gr.Tabs():
            with gr.Tab("对话"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.HTML(scenario_summary_html(config))
                        with gr.Accordion("阶段转移矩阵", open=False):
                            gr.HTML(
                                '<div class="mta-card-item" style="margin-bottom:6px">'
                                '单元格 = stage→stage 的转移条数；通配符（NEGOTIATE/*、*）已展开为多条，'
                                "因此合计会大于配置里的 12 条规则。</div>"
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
                send_btn.click(
                    lambda text, st: submit_turn(agent, st, text),
                    inputs=[user_input, session_state],
                    outputs=outputs,
                )
                user_input.submit(
                    lambda text, st: submit_turn(agent, st, text),
                    inputs=[user_input, session_state],
                    outputs=outputs,
                )
                reset_btn.click(lambda: reset_state(), inputs=[], outputs=outputs)

            with gr.Tab("评测"):
                with gr.Row():
                    refresh_btn = gr.Button("刷新报告")
                    run_btn = gr.Button("运行 80 条评测", variant="primary")
                eval_html = gr.HTML(eval_page_html(load_report()))

                refresh_btn.click(lambda: eval_page_html(load_report()), outputs=[eval_html])
                run_btn.click(lambda: eval_page_html(run_eval()), outputs=[eval_html])

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
