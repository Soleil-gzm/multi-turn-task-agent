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

> **当前状态**：W1 框架主干、W2 数据层、G3 交互界面与 milktea 第二场景已完成——Walking Skeleton 跑通，12 条转移配置与逐条单测、6 个 mock 业务工具、80 条评测 case、话术表（39 条）与 policy 知识库（24 条片段，BM25 检索已接入 `query_policy`）、第二场景（3 工具 + 5 阶段 + 异常分支）、160 个单测与 v0 基线报告（Mock）。入口已泛化：demo / ui / eval 从 `configs/scenarios/*.yaml` 发现场景并按 `tools[].impl` 装配，`src/` 内无场景名。真实模型客户端（OpenAI 兼容）与 k≥3 重复评测已跑通——`deepseek-flash` core 16 条 k=1 冒烟：终态匹配 81.2%、工具覆盖 6/6（见 [评测结果](#真实模型冒烟v1k1)）；待做：真实模型 80 条 k=3 正式基线、FastAPI 服务化。各契约实现状态见 [Roadmap · 契约状态](docs/roadmap.md#契约状态诚实反映代码进度每个里程碑更新)。

### 环境准备

- Python **3.10+**：代码仅使用 `from __future__ import annotations`、`X | Y` 等 3.10 语法，未使用 3.11+ 专有特性
- 本地实测版本 **3.13.9**（`python --version`）；不强制某一小版本，以实际解释器为准
- `pydantic` / `pyyaml` / `pytest` / `jieba` 为运行与测试必需（`jieba` 供检索层分词，其词典缓存重定向到 `output/jieba_cache/`，不写系统临时目录）；`gradio==6.31.0` 为交互界面依赖（已锁定）；`fastapi` / `uvicorn` / `httpx` 为服务化阶段预留
- 环境自检（应全部成功）：

```bash
python -c "import pydantic, yaml, pytest, jieba; print('deps ok')"
python -m pytest tests/ -q      # 期望 160 passed
```

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
| 契约 / 数据层 / UI / 场景单元测试（160 passed） | `make test` | `python -m pytest tests/ -v` |
| 跑 80 条评测 case（每条重复 3 次），生成分层报告 | `make eval` | `python -m src.eval.runner --k 3` |
| 用真实模型跑评测（OpenAI 兼容，凭证走环境变量） | — | `python -m src.eval.runner --model openai_compat --k 3 --report eval/reports/v1_real_k3.json` |
| 启动交互界面（Gradio） | `make ui` | `python -m src.ui.app` |
| 运行任意场景（通用运行器） | — | `python scripts/run_scenario.py --config configs/scenarios/milktea.yaml` |
| 重新生成 80 条评测集（确定性，可复现） | `make dataset` | `python -m src.synthesis.rule_based` |
| 由话术 YAML 真源生成 xlsx 模板 | `make templates` | `python scripts/build_talk_template.py` |
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

会话级增量（UI / 服务用，每轮只跑当前输入）：

```python
session = agent.start(new_session_id())
turn = session.send("合同号 HT-Y2026-012")
print(turn.stage, turn.round_no, turn.offer_state)   # NEGOTIATE 1 propose
print(turn.new_steps)                                # 本轮新增 trace 步数（示例为 5）
```

### 接入真实模型

框架内置 OpenAI 兼容客户端（`OpenAICompatibleClient`），只要能提供 `POST /chat/completions` 即可接入，**云端 API Key 与自部署都支持**：

| 类型 | 示例 | Base URL |
| --- | --- | --- |
| 云端 API | OpenAI / DeepSeek / 通义千问（DashScope compatible-mode）/ Moonshot / 智谱 / SiliconFlow | 各厂商的 `/v1` 地址 |
| 自部署 | vLLM / SGLang / Ollama / LM Studio / Xinference | 例如 `http://127.0.0.1:8000/v1`、`http://127.0.0.1:11434/v1` |

选型硬要求：**模型必须支持 function calling（tool calls）**，否则只能聊天、无法驱动状态机与工具。建议先用云端 API 跑通，再换本地模型。

凭证只从环境变量读取，支持仓库根目录 `.env`（已加入 `.gitignore`，不会提交）；优先级：**进程环境变量 > `.env`**：

```powershell
Copy-Item .env.example .env
# 编辑 .env：MTA_LLM=openai_compat、BASE_URL、API_KEY、MODEL
```

```ini
# .env 示例（DeepSeek 云端）
MTA_LLM=openai_compat
MTA_LLM_BASE_URL=https://api.deepseek.com/v1
MTA_LLM_API_KEY=sk-...
MTA_LLM_MODEL=deepseek-chat
```

```powershell
python -m src.agent.demo                                    # 自动读取 .env
python -m src.eval.runner --model openai_compat --k 1 `
    --limit 16 --report eval/reports/v1_smoke_k1.json        # 先冒烟 core 16 条
python -m src.eval.runner --model openai_compat --k 3 `
    --report eval/reports/v1_real_k3.json                    # 正式 k=3（80 条）
```

显式 `--model` 优先于 `.env` 的 `MTA_LLM`：临时跑 Mock 不会被本地 `.env` 覆盖。

可选参数：`MTA_LLM_TEMPERATURE`（默认 0）、`MTA_LLM_TOP_P`（默认 1）、`MTA_LLM_SEED`、`MTA_LLM_TIMEOUT`（默认 60）、`MTA_LLM_MAX_TOKENS`。配置缺失时客户端 fail-fast（报缺失的变量名），不会静默回落到 Mock。

自定义供应商只需实现 `LLMClient.chat(messages, tools)`，返回 `{"content": str | None, "tool_calls": [...] | None}`（OpenAI function calling 格式），并以 `module.factory` 形式传给 `--model`；Agent、状态机、工具与评测代码均不改动。

### 新增一个场景

参考已落地的第二场景 `scenarios/milktea/`：

1. 新建 `configs/scenarios/<name>.yaml`：写 `scenario` / `goal` / `state_machine` / `tools` / `prompts`，可选 `demo`（演示脚本与 LLM）
2. 新建场景包 `scenarios/<name>/tools.py`：实现工具、导出 `SCHEMAS`（沿用 C2 统一返回）
3. 新建 `configs/prompts/<name>/system.txt`；如需演示，再提供 `scenarios/<name>/llm.py`
4. 运行：`python scripts/run_scenario.py --config configs/scenarios/<name>.yaml`
5. `load_config` 启动预检通过即可跑通，`src/` 不需要任何业务分支

## 评测结果

> 指标定义见 [评测设计](docs/eval-design.md)。当前被测模型为 **MockLLMClient**，`k=3`（每条 case 重复 3 次，Mock 确定性因此 std=0），下表只验证「评测集 → Agent → 指标 → 报告」链路，**不代表模型能力基线**，也不进版本间 Δ 表。真实模型基线需配置凭证后按上方命令产出。

| 指标 | 当前 v0（Mock，k=3） | 说明 |
| --- | --- | --- |
| 终态匹配率 | 100.0% ± 0.0% | core 100.0% / boundary 100.0% / adversarial 100.0% |
| 任务完成率 | 35.0% ± 0.0% | 终态为 `CLOSED` 的样本占比 |
| 工具序列均分 | 0.80 ± 0.00 | 覆盖率 × 0.5 + 顺序 × 0.3，命中禁用动作扣 0.5 |
| Pass^3 | 100.0%（80/80） | 终态匹配 + 工具序列完全通过 + 无模型侧违规，3 次全部成功 |
| Pass@1 | 100.0% | 全部 240 次试验的单次成功率 |
| 矛盾数据告知率 | 100.0%（18/18） | 3 次重复池化；仅统计账实不符且调用 `diff_statement` 的试验 |
| 超政策拒绝话术率 | 100.0%（30/30） | 3 次重复池化；仅统计出现超政策请求的试验 |
| 工具覆盖 | 6/6 全覆盖 | 每个注册工具至少被 1 条 case 的任意一次试验触达（防空转工具） |
| 承诺合规率 | 未采集 | 需真实模型；对抗层样本 40 条 |
| 注入拦截率 | 未采集 | 数据集含 8 条 injection（其中 2 条间接注入） |
| 幻觉率 | 未采集 | 需真实模型 |

完整结果见 `eval/reports/v0_baseline.json`（含分层指标与逐条明细）。

### 真实模型冒烟（v1，k=1）

`deepseek-flash`（`https://api.deepseek.com`，temperature=0）跑 core 前 16 条，仅用于验证真实链路，样本量不足以作为正式基线：

| 指标 | 结果 | 说明 |
| --- | --- | --- |
| 终态匹配率 | 81.2%（13/16） | 未通过 3 条：2 条停在 `CONFIRM`/`NEGOTIATE`（模型改用 `escalate` 或未登记），1 条 `record_promise` 早于 `query_policy`（顺序违规） |
| 任务完成率 | 81.2% | 终态为 `CLOSED` 的试验占比 |
| 工具序列均分 | 0.77 | 覆盖率 × 0.5 + 顺序 × 0.3 |
| Pass^1 | 81.2% | 单次试验成功率 |
| 矛盾数据告知率 | 100%（3/3） | 仅统计触发 `diff_statement` 且账实不符的试验 |
| 工具覆盖 | 6/6 | `escalate` 2 次、`record_promise` 14 次被触达 |

完整结果见 `eval/reports/v1_smoke_k1.json`。观察到的模型侧问题：面对账实差异时会主动 `escalate` 而非按客户确认的账面金额登记；个别样本先查政策后核身份导致工具顺序违规。这两类属于「模型行为」而非链路故障，正式 k=3 基线（80 条）需按上方命令产出。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [架构设计](docs/architecture.md) | 目录结构、五层结构、状态机、场景配置、工具层、契约层 |
| [场景设计](docs/scenario-design.md) | 场景选型、模块语义、政策边界、公开数据集接入、虚拟数据规范、合成管线 |
| [评测设计](docs/eval-design.md) | 评测对象、三级评测方法、指标清单、可复现性条款 |
| [方案评审与细化](docs/方案评审与细化.md) | 数据集选型对比、工程契约规范、数据流转与模块依赖、阶段与验收 |
| [Roadmap](docs/roadmap.md) | 当前状态、契约状态、MVP / Stretch 分层、明确不做的取舍 |
| [Prior-art 对照](docs/prior-art.md) | RiSAWOZ / ABCD / τ-bench 三份公开参照的职责边界与逐项对照清单（只读参照，不接入数据） |

---

> **数据声明**：本项目所有业务数据（企业、合同、发票、金额、话术）均为程序化生成的虚构数据，不含任何真实客户信息；政策边界为公开资料整理的简化模型。
>
> **公开数据集声明**：RiSAWOZ、ABCD、τ-bench 仅用于方法论对照与评测器校准，本项目不重新分发其原始数据；如需使用请前往原始发布页获取并遵守其许可条款。
