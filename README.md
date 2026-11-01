# multi-turn-task-agent

配置驱动的**多轮任务型对话 Agent 框架**：以「目标 — 状态机 — 工具 — 策略 — 红线」五层结构实现多轮任务推进，具备程序化生成评测数据的能力，以及「规则 + LLM Judge + 轨迹审计」三级评测体系与服务化、可观测能力。

框架代码零业务分支，业务规则全部由场景配置注入。首个落地场景为 **B2B 应收账款协商**（AI 财务专员与企业财务负责人协商逾期账款的付款安排）。

## 设计原则

1. **配置驱动**：业务规则外置于 `configs/scenarios/*.yaml`，代码不出现任何业务分支
2. **场景可迁移**：框架适用于"多轮 + 工具 + 状态 + 政策边界"同构问题（续费、售后、供应链协同、政务办理等），换场景 = 换一个 yaml + 一份数据 + 一份知识库
3. **评测先行**：评测标签由程序化生成（MVP 用规则化生成器，Stretch 换完整合成管线），不依赖人工标注，详见 [评测设计](docs/eval-design.md)

## 核心架构

```
┌──────────────────────────────────────────┐
│ 目标层    Goal        ：任务定义与成功判据  │
├──────────────────────────────────────────┤
│ 状态机层  StateMachine：阶段流转与异常分支  │
├──────────────────────────────────────────┤
│ 工具层    Tools       ：业务工具调用       │
├──────────────────────────────────────────┤
│ 策略层    Policy      ：话术策略与条件边界  │
├──────────────────────────────────────────┤
│ 红线层    Guardrails  ：合规检测与成本预算  │
└──────────────────────────────────────────┘
```

完整架构（目录结构、元组状态机、场景配置注入点、工具层）见 [架构设计](docs/architecture.md)。

## 快速开始
 
> **当前状态**：设计阶段完成，**W1 已完成、进入 W2 数据层**——Walking Skeleton 已跑通（`make demo` 走完一次多轮并落 trace），并完成 12 条转移的配置与逐条单测、6 个 mock 业务工具、80 条评测 case、66 个单测与 v0 基线报告（Mock）；W2 的话术表与 policy 知识库尚未开始。LLM 当前为 `MockLLMClient`（按脚本响应，不接真实模型），API 服务与真实模型尚未接入。各契约实现状态见 [Roadmap · 契约状态](docs/roadmap.md#契约状态诚实反映代码进度每个里程碑更新)。

### 环境准备

- Python **3.10+**：代码仅使用 `from __future__ import annotations`、`X | Y` 等 3.10 语法，未使用 3.11+ 专有特性
- 本地实测版本 **3.13.9**（`python --version`）；不强制某一小版本，以实际解释器为准
- `pydantic` / `pyyaml` / `pytest` 为运行与测试必需；`fastapi` / `uvicorn` / `httpx` / `jieba` 为服务化与检索阶段预留

```bash
conda create -n mta python=3.13 -y     # 3.10+ 均可
conda activate mta
python -m pip install -r requirements.txt
```

### 常用命令

Windows PowerShell 通常没有 `make`，直接执行右列等价命令即可；两列行为一致（Makefile 只是封装）。

| 目的 | 有 `make` | 无 `make`（Windows PowerShell） |
| --- | --- | --- |
| 多轮对话演示，trace 落盘 `eval/reports/traces/` | `make demo` | `python -m src.agent.demo` |
| 契约与工具单元测试（66 passed） | `make test` | `python -m pytest tests/ -v` |
| 跑 80 条评测 case，生成分层 v0 报告 | `make eval` | `python -m src.eval.runner` |
| 重新生成 80 条评测集（确定性，可复现） | `make dataset` | `python -m src.synthesis.rule_based` |
| 安装依赖 | `make install` | `python -m pip install -r requirements.txt` |
| 清理 `__pycache__` | `make clean` | `python -c "import shutil,pathlib;[shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__')]"` |

`docker compose up` 尚未实现（C5 API 仅定义契约）。

## 使用示例

### 跑一次多轮对话

```python
from src.agent.llm import MockLLMClient
from src.agent.loop import Agent, new_session_id
from src.core.config import load_config
from src.tools.receivables.tools import build_registry

config = load_config("configs/scenarios/receivables.yaml")
agent = Agent(config, build_registry(), MockLLMClient())

result = agent.run(
    new_session_id(),
    ["你好", "合同号 HT-Y2026-012", "我想分期 3 期", "好的，我同意"],
)

print(result.stage, result.task_completed)
# CLOSED True
print(result.tool_calls)
# ['query_receivable', 'query_policy', 'record_promise']
print(result.trace.totals)
# llm_calls=4 tool_calls=3 tokens=... latency_ms=...
```

### 接入真实模型

实现 `LLMClient.chat(messages, tools)`，返回 `{"content": str | None, "tool_calls": [...] | None}`（OpenAI function calling 格式），替换 `MockLLMClient` 即可，Agent、状态机、工具与评测代码均不改动。

### 新增一个场景

1. 复制 `configs/scenarios/receivables.yaml`，改 `scenario` / `goal` / `state_machine` / `policy`
2. 按 `tools[].impl` 提供工具实现（沿用 C2 统一返回）
3. 提供 system prompt 与该场景的 case 数据
4. `load_config` 启动预检通过即可运行，`src/` 不需要任何业务分支

## 评测结果

> 指标定义见 [评测设计](docs/eval-design.md)。当前被测模型为 **MockLLMClient**，下表只验证「评测集 → Agent → 指标 → 报告」链路，**不代表模型能力基线**，也不进版本间 Δ 表。

| 指标 | 当前 v0（Mock） | 说明 |
| --- | --- | --- |
| 终态匹配率 | 100.0% | core 100.0% / boundary 100.0% / adversarial 100.0% |
| 任务完成率 | 35.0% | 终态为 `CLOSED` 的样本占比 |
| 工具序列均分 | 0.80 | 覆盖率 × 0.5 + 顺序 × 0.3，命中禁用动作扣 0.5 |
| 矛盾数据告知率 | 100.0%（6/6） | 仅统计真实出现账实不符且调用了 `diff_statement` 的样本 |
| 承诺合规率 | 未采集 | 需真实模型；对抗层样本 40 条 |
| 注入拦截率 | 未采集 | 数据集含 8 条 injection（其中 2 条间接注入） |
| 幻觉率 | 未采集 | 需真实模型 |

完整结果见 `eval/reports/v0_baseline.json`（含分层指标与逐条明细）。

## 文档导航

| 文档 | 内容 |
|---|---|
| [架构设计](docs/architecture.md) | 目录结构、五层结构、状态机、场景配置、工具层、契约层 |
| [场景设计](docs/scenario-design.md) | 场景选型、模块语义、政策边界、公开数据集接入、虚拟数据规范、合成管线 |
| [评测设计](docs/eval-design.md) | 评测对象、三级评测方法、指标清单、可复现性条款 |
| [方案评审与细化](docs/方案评审与细化.md) | 数据集选型对比、工程契约规范、数据流转与模块依赖、阶段与验收 |
| [Roadmap](docs/roadmap.md) | 当前状态、契约状态、MVP / Stretch 分层、明确不做的取舍 |

---

> **数据声明**：本项目所有业务数据（企业、合同、发票、金额、话术）均为程序化生成的虚构数据，不含任何真实客户信息；政策边界为公开资料整理的简化模型。
>
> **公开数据集声明**：RiSAWOZ、ABCD、τ-bench 仅用于方法论对照与评测器校准，本项目不重新分发其原始数据；如需使用请前往原始发布页获取并遵守其许可条款。
