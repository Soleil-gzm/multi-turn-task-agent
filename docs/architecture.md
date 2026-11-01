# 架构设计

> [返回主页](../README.md) · [场景设计](scenario-design.md) · [评测设计](eval-design.md) · [Roadmap](roadmap.md)

配置驱动的多轮任务型 Agent：代码不出现任何业务分支，场景差异全部由 `configs/scenarios/*.yaml` 注入。本文描述目录结构、五层结构、状态机、场景配置注入点与工具层；场景的业务语义见 [场景设计](scenario-design.md)。

## 1. 目录结构

```
multi-turn-task-agent/
├── README.md
├── Makefile                    # make demo / make test / make eval
├── requirements.txt            # 运行与测试依赖
├── configs/
│   ├── scenarios/receivables.yaml
│   └── prompts/receivables/system.txt
├── src/
│   ├── core/                   # config.py（C3 配置与启动预检）、trace.py（C6）
│   ├── state/machine.py        # 元组状态机 + 受限条件求值
│   ├── tools/
│   │   ├── registry.py         # C2 统一返回 + 工具注册表
│   │   └── receivables/tools.py # 6 个业务工具 + JSON Schema（mock 数据）
│   ├── agent/                  # loop.py（ReAct + 状态机）、llm.py（接口 + Mock）、demo.py
│   ├── guardrails/guardrails.py # C7 输入检测 / 工具侧隔离 / 输出校验
│   ├── synthesis/rule_based.py # 规则化生成 80 条评测集
│   ├── eval/                   # contracts.py（C1）、runner.py（评测运行器）
│   ├── adapters/               # （规划）公开数据集适配器
│   ├── retrieval/              # （规划）BM25 检索
│   ├── services/               # （规划）FastAPI 服务
│   └── mcp/                    # （规划）MCP 工具暴露
├── data/
│   ├── schemas/receivables/    # （规划）话术模板
│   └── knowledge/receivables/  # （规划）政策知识库
├── eval/
│   ├── datasets/receivables/cases.jsonl   # 80 条评测集（含 oracle 与 content_hash）
│   ├── graders/                # （规划）LLM Judge / 轨迹审计
│   ├── metrics/                # （规划）指标采集
│   └── reports/                # v0_baseline.json + traces/
└── tests/test_contracts.py     # 契约 / 状态机 / 工具单测
```

> 标注「（规划）」的目录当前只有包骨架或为空，无实现。

## 2. 五层结构

```
┌──────────────────────────────────────────┐
│ 目标层    Goal        ：任务定义与成功判据  │
├──────────────────────────────────────────┤
│ 状态机层  StateMachine：阶段流转与异常分支  │
├──────────────────────────────────────────┤
│ 工具层    Tools       ：6 个业务工具        │
├──────────────────────────────────────────┤
│ 策略层    Policy      ：话术策略与条件边界  │
├──────────────────────────────────────────┤
│ 红线层    Guardrails  ：合规检测与成本预算  │
└──────────────────────────────────────────┘
```

每层职责单一、可独立替换：换任务改 Goal，换流程改 StateMachine，换单据改 Tools，换话术改 Policy，换合规要求改 Guardrails——均不动框架代码。

## 3. 状态机（元组状态，非线性链）

状态表示为 `(stage, round_no, offer_state)` 三元组，而非单一线性阶段序列。`round_no` 按对话轮次递增（每轮用户输入 +1），转移本身不改变轮次——同一轮内可能连续发生多条转移，若在转移中累加会重复计数。

| stage | 含义 | 子状态 offer_state |
| --- | --- | --- |
| `INIT` | 初始化 | — |
| `IDENTIFY` | 核实身份 / 单据 | — |
| `NEGOTIATE` | 协商付款安排 | `probe` → `propose` → `counter` → `final` |
| `CONFIRM` | 确认日期金额 | — |
| `CLOSED` | 记录归档 | — |
| `ESCALATED` | 升级人工 | — |

**转移表（12 条，节选）**：

| from | to | 触发条件 |
| --- | --- | --- |
| INIT | IDENTIFY | 会话开始 |
| IDENTIFY | NEGOTIATE | 身份与单据核对通过 |
| IDENTIFY | ESCALATED | 核对失败 2 次（客户无法提供合同信息 / 合同号查不到，均计数） |
| NEGOTIATE/probe | NEGOTIATE/propose | 已告知账款事实 |
| NEGOTIATE/propose | NEGOTIATE/counter | 客户提出反方案 |
| NEGOTIATE/counter | NEGOTIATE/propose | 反方案超政策上限 |
| NEGOTIATE/counter | NEGOTIATE/final | 方案在政策内 |
| NEGOTIATE/* | ESCALATED | `round > 5` 或 违规尝试 ≥ 2 次 |
| NEGOTIATE/final | CONFIRM | 客户口头接受 |
| CONFIRM | CLOSED | 日期 + 金额已记录 |
| CONFIRM | NEGOTIATE/propose | 客户撤回 |
| 任意 | ESCALATED | 命中红线或客户明确拒绝 |

设计动机：协商过程天然存在反复（客户撤回、反方案被政策打回），元组状态 + 显式转移表让这些回退路径与正常路径同级建模，转移条件可枚举、可测试、可被评测器比对。

> 6 个 stage 与 [场景设计 · 8 个业务模块](scenario-design.md#8-模块--6-stage-映射)是层次关系而非两套口径：模块是功能语义单元，stage 是状态机阶段。`NEGOTIATE` 的 4 个子状态（probe/propose/counter/final）承载账款告知、条件协商、异议澄清三个模块，对账差异跨子状态标 `NEGOTIATE/*`；`INIT` 不对应业务模块。

## 4. 场景配置（业务注入点）

```yaml
scenario:
  id: receivables
  display_name: 应收账款协商
  version: 1

goal:
  success_criteria: "获取明确付款日期 + 金额"
  max_rounds: 15
  on_success: record_promise

state_machine:
  stages: [INIT, IDENTIFY, NEGOTIATE, CONFIRM, CLOSED, ESCALATED]
  escalate_to: ESCALATED
  # 进入升级状态时必须调用的工具（落工单，避免「只跳状态不留痕」）
  escalate_tool: escalate
  # NEGOTIATE 的子状态（offer_state），其余 stage 无子状态
  offer_states:
    NEGOTIATE: [probe, propose, counter, final]
  # 12 条转移，与 §3 转移表逐行对应；receivables.yaml 必须完整落地，不可只取子集
  transitions:
    - from: INIT
      to: IDENTIFY
      when: "session_start"
    - from: IDENTIFY
      to: NEGOTIATE/probe
      when: "identity_verified"
    - from: IDENTIFY
      to: ESCALATED
      when: "verify_failures >= 2"
    - from: NEGOTIATE/probe
      to: NEGOTIATE/propose
      when: "amount_informed"
    - from: NEGOTIATE/propose
      to: NEGOTIATE/counter
      when: "counter_offer"
    - from: NEGOTIATE/counter
      to: NEGOTIATE/propose
      when: "counter_exceeds_policy"
    - from: NEGOTIATE/counter
      to: NEGOTIATE/final
      when: "counter_within_policy"
    - from: NEGOTIATE/*
      to: ESCALATED
      when: "round > 5 or policy_violation_attempt >= 2"
    - from: NEGOTIATE/final
      to: CONFIRM
      when: "offer_accepted"
    - from: CONFIRM
      to: CLOSED
      when: "promise_recorded"
    - from: CONFIRM
      to: NEGOTIATE/propose
      when: "customer_withdraw"
    - from: "*"
      to: ESCALATED
      when: "redline_hit or customer_refuse"

entities:
  receivable: [contract_no, invoice_no, due_date, amount, overdue_days, status]

tools:
  - name: query_receivable
    impl: src.tools.receivables.tools.query_receivable
  - name: check_invoice
    impl: src.tools.receivables.tools.check_invoice
  - name: diff_statement
    impl: src.tools.receivables.tools.diff_statement
  - name: query_policy
    impl: src.tools.receivables.tools.query_policy
  - name: record_promise
    impl: src.tools.receivables.tools.record_promise
  - name: escalate
    impl: src.tools.receivables.tools.escalate

policy:
  max_installments: 3
  max_extension_days: 30
  max_waiver_ratio: 0.10
  forbidden_patterns: ["威胁", "起诉你", "冻结账户", "保证一定"]

# 矛盾数据告知（C4）：工具返回的两个字段不一致时，必须按 template 主动向客户说明
response:
  contradiction:
    tool: diff_statement
    fields: [book_amount, customer_claimed_amount, diff]
    template: "系统账面 {book_amount} 元，您主张 {customer_claimed_amount} 元，差额 {diff} 元，我们以系统账面为准，请您核对。"

knowledge:
  corpus: data/knowledge/receivables/
  top_k: 5

prompts:
  system: configs/prompts/receivables/system.txt

synthesis:
  talk_template: data/schemas/receivables/话术模板.xlsx
  prob_source: categories          # categories | excel
  cases_dir: data/schemas/receivables/cases/
  seed: 42

eval:
  dataset: eval/datasets/receivables/
  metrics: [task_completion, policy_violation, info_accuracy, no_fabrication, efficiency]
```

换场景 = 换一个 yaml + 一份数据 + 一份知识库。`milktea.yaml`（规划中，尚未创建）将使用同一 schema，仅字段值不同（见 [场景设计](scenario-design.md#4-场景二milktea业务规则注入验证)）。

## 5. 六个业务工具

| 工具 | 作用 | 评测价值 |
| --- | --- | --- |
| `query_receivable` | 查合同/发票/账期/逾期天数 | 参数与返回可对照 oracle |
| `check_invoice` | 查发票状态（已开/已抵扣/红冲） | 与客户主张交叉校验 |
| `diff_statement` | 对账差异比对 | **可返回矛盾数据**，制造真实难度 |
| `query_policy` | 查政策边界（分期/延期/减免） | 决策依据，可校验引用 |
| `record_promise` | 写入承诺（日期+金额） | 成功判据的落点 |
| `escalate` | 升级人工并附原因 | 异常分支的正确性 |

> `diff_statement` 是评测区分度的关键来源：它让"账实不符"成为真实约束，而非伪工具。评测体系详见 [评测设计](eval-design.md)。

### 5.1 入参、返回与错误码

六个工具统一走 C2 返回封装；失败时 `ok=false`，`error.code` 取 `NOT_FOUND / INVALID_ARG / TOOL_TIMEOUT / INTERNAL_ERROR / DEGRADED` 之一。

| 工具 | 入参（JSON Schema 有正则约束） | 成功时 `data` | 可能错误码 |
| --- | --- | --- | --- |
| `query_receivable` | `contract_no`：`^HT-Y2026-\d{3}$` | `contract_no`、`invoice_no`、`amount`、`due_date`、`overdue_days`、`status` | `NOT_FOUND`、`INVALID_ARG` |
| `check_invoice` | `invoice_no`：`^INV-\d{8}$` | `invoice_no`、`contract_no`、`status`（已开 / 已抵扣 / 红冲）、`amount`、`issued_date` | `NOT_FOUND`、`INVALID_ARG` |
| `diff_statement` | `contract_no` | `book_amount`、`customer_claimed_amount`、`diff`、`has_diff`、`customer_note` | `NOT_FOUND`、`INVALID_ARG` |
| `query_policy` | 无 | `max_installments`、`max_extension_days`、`max_waiver_ratio`、`forbidden_patterns` | — |
| `record_promise` | `contract_no`、`pay_date`（YYYY-MM-DD）、`amount`（整数） | 承诺记录（含 `recorded_at`） | `INVALID_ARG` |
| `escalate` | `contract_no`、`reason` | `ticket_id`（`ESC-0001` 起）、`status`（`created`） | `INVALID_ARG` |

> 当前 6 个工具均为 **mock 实现**：单据、发票、对账单存于内存字典，承诺与升级记录存于内存列表，进程重启即丢失（持久化在 W3 接入 SQLite）。接真实数据源时只替换实现，schema、返回封装与评测口径不变。

### 5.2 调用示例

```python
from src.tools.receivables.tools import build_registry

registry = build_registry()
print(registry.names())  # 6 个工具名

# 正常返回
print(registry.invoke("diff_statement", contract_no="HT-Y2026-012"))
# {"ok": True,
#  "data": {"book_amount": 380000, "customer_claimed_amount": 200000,
#           "diff": 180000, "has_diff": True, "customer_note": "..."},
#  "error": None,
#  "meta": {"source": "mock_statement", "latency_ms": 0}}

# 错误返回
print(registry.invoke("check_invoice", invoice_no="INV-99999999"))
# {"ok": False, "data": None,
#  "error": {"code": "NOT_FOUND", "msg": "发票 INV-99999999 不存在", "retryable": False},
#  "meta": {...}}
```

工具 schema 可直接转成 function calling 描述：`registry.function_calling_schemas()`，同时用于参数正确率评测与文档生成。

## 6. 契约层与启动预检

契约回答「具体长什么样」，完整定义见 [方案评审与细化 · 工程契约规范](方案评审与细化.md#2-工程契约规范)。代码只依赖契约，不依赖描述性文档：

| 契约 | 内容 | 代码落位 |
| --- | --- | --- |
| C1 数据 | case / oracle schema，三个判定边界（重复调用不惩罚、仅校验非空 order、部分完成按维度给分） | `src/eval/contracts.py` |
| C2 工具 | 统一返回封装 `{ok, data, error, meta}`；JSON Schema 同时用于 function calling、参数评测、文档生成 | `src/tools/registry.py` |
| C3 配置 | scenario.yaml 字段 schema | `src/core/config.py` |
| C4 降级 | 7 类故障的可降级路径（检测 / 恢复 / 判定）；MVP 实现 3 类 | `src/agent/loop.py`（`degraded` 标记 + 矛盾数据告知已落地；重试/降级未实现） |
| C5 API | `POST /chat`、`GET /sessions/{id}`、`GET /trace/{trace_id}`、`POST /eval`、`GET /eval/{task_id}` 的请求响应与错误码 | `src/services/app.py`（规划，未实现） |
| C6 Trace | 轨迹字段与 `type` 枚举（llm_call / tool_call / guardrail / state_transition / degradation） | `src/core/trace.py` |
| C7 安全 | 注入四层防护（输入标记 / 决策不靠模型 / 工具返回隔离 / 输出校验） | `src/guardrails/guardrails.py` |

**契约的落地顺序**：契约不等于必须一次实现。代码按 Walking Skeleton 先行（已跑通），当前已扩至 12 条转移 / 6 个工具 / 80 条 case；每份契约当前处于「仅定义 / 部分实现 / 已实现」哪一档，以 [Roadmap · 契约状态](roadmap.md#契约状态诚实反映代码进度每个里程碑更新) 为准。

**依赖方向**：`services`、`mcp` → `agent` → `state`、`tools`、`guardrails`、`retrieval` → `core`。只允许向下依赖；`adapters` 仅依赖 `core` 与 C1，数据源差异不进入 `agent` 层。

**启动预检（fail-fast）**：加载 scenario.yaml 时校验①必填字段与 `tools[].impl` 可导入；②转移表 `from`/`to` 合法（支持 `stage`、`stage/offer_state`、`*` 三种形式：`stage` 须在 `stages` 内，`offer_state` 须在对应 stage 的 `offer_states` 声明内，`*` 仅作通配源）、无孤立或不可达状态；③policy 数值合法；④`escalate_tool` 与 `response.contradiction.tool` 必须在 `tools` 中声明，矛盾告知模板的占位符必须属于该规则的 `fields`。任一失败退出码非 0，不静默降级。转移条件使用受限表达式白名单，不使用 `eval`。

**安全边界**：减免 / 延期数值必须来自 `query_policy` 工具返回，模型无权自行决定——权限不靠模型判断。工具返回与检索片段一律视为**不可信数据源**：进入上下文前加定界标记、长度截断并剥离指令片段，不作为指令来源（防间接注入）。
