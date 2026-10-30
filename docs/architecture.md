# 架构设计

> [返回主页](../README.md) · [场景设计](scenario-design.md) · [评测设计](eval-design.md) · [Roadmap](roadmap.md)

配置驱动的多轮任务型 Agent：代码不出现任何业务分支，场景差异全部由 `configs/scenarios/*.yaml` 注入。本文描述目录结构、五层结构、状态机、场景配置注入点与工具层；场景的业务语义见 [场景设计](scenario-design.md)。

## 1. 目录结构

```
multi-turn-task-agent/
├── README.md
├── docker-compose.yml          # API（单服务，SQLite 挂载卷）
├── Makefile                    # make up / make demo / make eval / make test
├── CHANGELOG.md
├── configs/
│   ├── scenarios/
│   │   ├── receivables.yaml    # 场景一（主线）
│   │   └── milktea.yaml        # 场景二（通用性验证）
│   └── prompts/
│       ├── receivables/
│       └── milktea/
├── src/
│   ├── core/                   # 配置加载、注册表、日志、随机服务
│   ├── state/                  # 状态机 + 持久化
│   ├── tools/                  # 工具注册表 + 按场景分实现
│   ├── retrieval/              # 手搓 BM25 + 向量 + RRF
│   ├── agent/                  # ReAct 循环 + 上下文工程
│   ├── guardrails/             # 政策校验 + 预算
│   ├── synthesis/              # 数据合成（声明式组装 + 概率生成 + 对话构建）
│   ├── services/               # FastAPI
│   └── mcp/                    # MCP 工具暴露
├── data/
│   ├── schemas/receivables/    # 话术模板、prob、cases（虚构）
│   └── knowledge/receivables/  # 公开资料整理的政策文档
├── eval/
│   ├── datasets/               # 分层评测集
│   ├── graders/                # 规则 / Judge / 轨迹审计
│   ├── metrics/                # 指标采集
│   └── reports/                # 评测报告
└── tests/
```

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

状态表示为 `(stage, round_no, offer_state)` 三元组，而非单一线性阶段序列：

| stage | 含义 | 子状态 offer_state |
|---|---|---|
| `INIT` | 初始化 | — |
| `IDENTIFY` | 核实身份 / 单据 | — |
| `NEGOTIATE` | 协商付款安排 | `probe` → `propose` → `counter` → `final` |
| `CONFIRM` | 确认日期金额 | — |
| `CLOSED` | 记录归档 | — |
| `ESCALATED` | 升级人工 | — |

**转移表（12 条，节选）**：

| from | to | 触发条件 |
|---|---|---|
| INIT | IDENTIFY | 会话开始 |
| IDENTIFY | NEGOTIATE | 身份与单据核对通过 |
| IDENTIFY | ESCALATED | 核对失败 2 次 |
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
  stages: [INIT, IDENTIFY, NEGOTIATE, CONFIRM, CLOSED]
  escalate_to: ESCALATED
  transitions:
    - from: NEGOTIATE
      to: CONFIRM
      when: "offer_accepted"
    - from: NEGOTIATE
      to: ESCALATED
      when: "round > 5 or policy_violation_attempt >= 2"

entities:
  receivable: [contract_no, invoice_no, due_date, amount, overdue_days, status]

tools:
  - name: query_receivable
    impl: src.tools.receivables.query_receivable
  - name: check_invoice
    impl: src.tools.receivables.check_invoice
  - name: diff_statement
    impl: src.tools.receivables.diff_statement
  - name: query_policy
    impl: src.tools.receivables.query_policy
  - name: record_promise
    impl: src.tools.receivables.record_promise
  - name: escalate
    impl: src.tools.receivables.escalate

policy:
  max_installments: 3
  max_extension_days: 30
  max_waiver_ratio: 0.10
  forbidden_patterns: ["威胁", "起诉你", "冻结账户", "保证一定"]

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

换场景 = 换一个 yaml + 一份数据 + 一份知识库。`milktea.yaml` 使用同一 schema，仅字段值不同（见 [场景设计](scenario-design.md#4-场景二milktea通用性验证)）。

## 5. 六个业务工具

| 工具 | 作用 | 评测价值 |
|---|---|---|
| `query_receivable` | 查合同/发票/账期/逾期天数 | 参数与返回可对照 oracle |
| `check_invoice` | 查发票状态（已开/已抵扣/红冲） | 与客户主张交叉校验 |
| `diff_statement` | 对账差异比对 | **可返回矛盾数据**，制造真实难度 |
| `query_policy` | 查政策边界（分期/延期/减免） | 决策依据，可校验引用 |
| `record_promise` | 写入承诺（日期+金额） | 成功判据的落点 |
| `escalate` | 升级人工并附原因 | 异常分支的正确性 |

> `diff_statement` 是评测区分度的关键来源：它让"账实不符"成为真实约束，而非伪工具。评测体系详见 [评测设计](eval-design.md)。
