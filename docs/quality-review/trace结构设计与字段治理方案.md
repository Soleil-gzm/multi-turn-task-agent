# Trace 结构设计与字段治理方案

> 适用项目：multi-turn-task-agent（配置驱动多轮任务型对话 Agent 框架）
> 首个场景：B2B 应收账款协商
> 文档目的：作为 trace 重构的实施依据 + 字段长期治理规范
> 版本：v1.0
> 创建日期：2026-11-05

---

## 目录

1. [设计总则](#一设计总则)
2. [总体架构](#二总体架构)
3. [Trace 根层（Turn 级）](#三trace-根层turn-级)
4. [LLM 决策 Span](#四llm-决策-span)
5. [工具调用 Span](#五工具调用-span)
6. [状态转移 Span](#六状态转移-span)
7. [红线检查 Span](#七红线检查-span)
8. [降级故障 Span](#八降级故障-span)
9. [Session 级聚合](#九session-级聚合)
10. [字段与指标映射表](#十字段与指标映射表)
11. [字段治理方案](#十一字段治理方案)
12. [字段变更 SOP](#十二字段变更-sop)
13. [实施步骤](#十三实施步骤)
14. [附录：字段必要性总表](#附录字段必要性总表)

---

## 一、设计总则

### 1.1 六条核心原则

| # | 原则 | 解决的问题 |
|---|---|---|
| 1 | **一轮一 Trace**，多轮通过 `session_id` 横向关联 | 避免长会话 trace 无限膨胀 |
| 2 | **树形嵌套**，通过 `parent_span_id` 建立父子关系 | 从"发生了什么"升级到"谁触发了谁、卡在哪一环" |
| 3 | **统一 Span + attributes 字典** | 降低后续加字段的成本 |
| 4 | **字段名遵循 OTel GenAI 语义约定** | 把改名成本外包给标准 |
| 5 | **`schema_version` + 读取适配器** | 保证历史数据永远可比 |
| 6 | **评测字段与观测字段分离** | 观测字段自动采集，评测字段由评测器回写为 Score |

### 1.2 为什么是这个结构

当前 trace 是**扁平事件流**（每步一条记录），只能回答"发生了什么"。

树形 Span 能回答"**谁触发了谁、这次失败卡在哪一环**"。

**这不是为了好看，是为了可计算。** 当前基线中 core 层 Pass^3 41.7% 而 adversarial 层 80.0% 的倒挂现象、56/240 次重复工具调用、23 条模型行为失败——这些问题用扁平事件流需要人工翻日志，用树形 Span 可以用一句查询回答。

### 1.3 技术选型决定

| 决策点 | 选择 | 理由 |
|---|---|---|
| Span 结构 | **统一 Span 类 + `span_type` + `attributes` 字典** | 加字段成本 = 往字典加一个 key；与 OTel 原生设计一致 |
| 字段命名 | **OTel GenAI 语义约定** | 标准演进时社区同步，适配层跟着改即可 |
| 存储格式 | **JSON / SQLite** | 复用现有基础设施 |
| 读取方式 | **经适配器归一** | 禁止评测器直接读存储层 key |
| 采集方式 | **异步（队列 + 后台 flush）** | 不拖累主流程耗时，保护"0 超时"基线 |

---

## 二、总体架构

### 2.1 层级结构

```
Session (session_id 横向关联)
  │
  └── Trace (一次 turn = 一条 trace, trace_id)
       │
       ├── Span: LLM 决策        (span_type = "llm")
       ├── Span: 工具调用         (span_type = "tool")     ← 可多个
       ├── Span: 状态转移         (span_type = "state_transition")
       ├── Span: 红线检查         (span_type = "guard")    ← 可多个
       └── Span: 降级/故障        (span_type = "degradation") ← 可多个
```

### 2.2 数据流

```
Agent 主流程
   ↓ 产生事件（异步，不阻塞）
事件队列
   ↓ 后台 flush
Trace 存储（JSON / SQLite, 带 schema_version）
   ↓ 读取适配器（按 schema_version 归一）
   ├── 评测器 → 计算指标 → 回写 Score
   ├── Langfuse 上报（OTel 格式）
   └── 分析脚本 / Δ 表生成
```

**关键约束**：适配器是唯一的读取入口。任何消费方（评测器、Langfuse、分析脚本）都不直接读存储层的 key。

### 2.3 命名规范

| 类别 | 规范 | 示例 |
|---|---|---|
| OTel 标准字段 | 直接用标准名 | `gen_ai.request.model` |
| 业务自定义字段 | `snake_case`，不加前缀 | `session_id`、`turn_index` |
| Span 通用字段 | OTel Trace 标准 | `trace_id`、`span_id`、`parent_span_id` |
| Score 字段 | 动词过去式或名词 | `terminal_match`、`business_goal_achieved` |

---

## 三、Trace 根层（Turn 级）

**一次 turn = 一条 trace。这是最小可分析单元。**

### 3.1 通用字段

| 字段 | 类型 | 必要性 | 说明 | 来源 |
|---|---|---|---|---|
| `trace_id` | str | **必有** | 全局唯一 | 自动 |
| `span_id` | str | **必有** | 本 span ID | 自动 |
| `parent_span_id` | str\|null | **必有** | 根 span 为 null | 自动 |
| `span_type` | str | **必有** | 固定 `"turn"` | — |
| `schema_version` | str | **必有** | 如 `"1.0"` | 配置 |
| `session_id` | str | **必有** | **多轮横向关联键**（必须放最外层） | 业务注入 |
| `turn_index` | int | **必有** | 第几轮（从 1 开始） | 业务注入 |
| `user_id` | str | **必有** | 用户标识（**脱敏后**） | 业务注入 |
| `scenario_id` | str | **必有** | 如 `b2b_ar_collection` | 配置注入 |
| `case_id` | str\|null | 预留 | 评测用例 ID，非评测时为 null | 评测器注入 |
| `eval_layer` | enum\|null | 预留 | `core` / `boundary` / `adversarial` / null | 评测器注入 |
| `state_before` | str | **必有** | 入口状态（如 `NEGOTIATE/probe`） | 状态机 |
| `state_after` | str | **必有** | 出口状态 | 状态机 |
| `start_time` | ts | **必有** | 时间戳 | 自动 |
| `end_time` | ts | **必有** | 时间戳 | 自动 |
| `duration_ms` | int | **必有** | 本 turn 总耗时 | 自动 |
| `status` | enum | **必有** | `ok` / `error` / `degraded` | 自动 |

> **`session_id` 必须在最外层**，不要依赖框架内部 context。这是跨框架追踪时唯一共通的业务字段。

### 3.2 评测 Score（由评测器回写，非采集时打点）

| Score 名 | 口径 | 对应指标 |
|---|---|---|
| `terminal_match` | 终态标签是否匹配 oracle（**只比标签，不含业务含义**） | 终态匹配率 92.9% |
| `business_goal_achieved` | 业务目标是否达成（**仅应关单样本入分母**） | 业务目标达成率 79.8% |
| `tool_sequence_score` | 工具序列与期望序列的匹配度 | 工具均分 0.79 |
| `pass_power_3` | case 级指标，非 turn 级 | Pass^3 71.2% |

> ⚠️ **`terminal_match` 与 `business_goal_achieved` 的口径差异必须在 trace 中可见**，否则会重演早期版本"终态匹配 100% vs 任务完成 35%"的自相矛盾。

---

## 四、LLM 决策 Span

**成本与延迟的主要来源，也是模型行为失败的第一现场。**

### 4.1 字段表

| 字段 | OTel 语义名 | 类型 | 必要性 | 说明 |
|---|---|---|---|---|
| `span_type` | — | str | **必有** | 固定 `"llm"` |
| `gen_ai.request.model` | ✅ 标准 | str | **必有** | `deepseek-flash` |
| `gen_ai.response.model` | ✅ 标准 | str | 必有 | 实际响应模型（**检测静默升级**） |
| `gen_ai.usage.input_tokens` | ✅ 标准 | int | **必有** | 输入 token（**成本指标之一**） |
| `gen_ai.usage.output_tokens` | ✅ 标准 | int | **必有** | 输出 token |
| `gen_ai.request.temperature` | ✅ 标准 | float | 必有 | 采样参数 |
| `decision_type` | — | enum | **必有** | `tool_call` / `final_answer` / `clarify` |
| `tool_call_count` | — | int | **必有** | 本次决策发起几个工具调用 |
| `prompt_hash` | — | str | **必有** | prompt 内容哈希（可复现性） |
| `prompt_version` | — | str | 必有 | 人工可读的 prompt 版本标签 |
| `duration_ms` | — | int | **必有** | 耗时 |
| `status` | — | enum | **必有** | `ok` / `timeout` / `api_error` |
| `retry_count` | — | int | **预留** | 重试次数（**T5 故障注入时启用**） |
| `gen_ai.request.max_tokens` | ✅ 标准 | int | **待定** | 除非做 max_tokens 消融实验，否则可删 |

### 4.2 prompt / response 存储策略

| 场景 | 存储内容 |
|---|---|
| 正常 case | `prompt_hash` + **截断版（前 500 token）** |
| bad case | **完整 prompt + 完整 response**，落盘独立文件，trace 只存引用 |

**理由**：trace 主存储是 SQLite，若每条都存完整 prompt，240 次试验的 trace 会达数十 MB，影响上传与查询性能。

`prompt_hash` + `prompt_version` 是消融实验的基础——改 prompt 后能写出"prompt v3 相比 v2，core 层 Pass^3 从 41.7% 提升到 X%"这样的可复现结论。

---

## 五、工具调用 Span

**最需要可视化的部分**——"工具顺序违规""重复调用 23%"都发生在这里。

### 5.1 字段表

| 字段 | 类型 | 必要性 | 说明 |
|---|---|---|---|
| `span_type` | str | **必有** | 固定 `"tool"` |
| `tool_name` | str | **必有** | 如 `query_policy` / `record_promise` |
| `tool_category` | enum | **必有** | `read` / `write` / `side_effect` |
| `call_index_in_turn` | int | **必有** | 本轮内第几次调用（**顺序违规判定依据**） |
| `call_index_in_session` | int | **必有** | 会话内累计调用序号 |
| `arguments` | json | **必有** | 入参（**脱敏**：金额转区间、企业名哈希） |
| `arguments_hash` | str | **必有** | 参数哈希（**重复调用检测**） |
| `result_status` | enum | **必有** | `success` / `error` / `timeout` / `schema_mismatch` |
| `result_summary` | str | **必有** | 返回摘要（完整返回存文件） |
| `result_bytes` | int | 必有 | 返回大小（T5 输出截断依据） |
| `duration_ms` | int | **必有** | 耗时 |
| `is_duplicate` | bool | **必有** | 与本 session 内某次调用重复（同 tool + 同 args_hash） |
| `tool_registry_version` | str | **待定** | 工具 schema 版本；schema 稳定则延后 |
| `idempotency_key` | str | **待定** | 幂等键；写类工具为 mock 时暂不需要 |

### 5.2 关键字段说明

**`is_duplicate`**

```
is_duplicate = (同 session 内已存在 tool_name 相同 且 arguments_hash 相同的调用)
```

价值远超统计用途：
- 当前基线 56/240 次重复调用（23.3%）靠它自动标记
- "重复调用集中在哪些工具"从翻日志变成一句查询
- 真实系统中写类工具重复调用会造成**重复登记**，属于业务事故
- 可据此新增评测红线：**"写类工具重复调用 = 直接判 0"**

**`call_index_in_turn`**

工具顺序违规（如"先查政策后核身份"）本质上是 `call_index` 与期望顺序不符。有了此字段即可自动检测，无需人工看 trace。

---

## 六、状态转移 Span

**状态机是架构核心，但在当前 trace 中是隐形的。必须补上。**

### 6.1 字段表

| 字段 | 类型 | 必要性 | 说明 |
|---|---|---|---|
| `span_type` | str | **必有** | 固定 `"state_transition"` |
| `from_state` | str | **必有** | 如 `NEGOTIATE/probe` |
| `to_state` | str | **必有** | 如 `NEGOTIATE/propose` |
| `transition_id` | str | **必有** | 转移规则 ID（对应 12 条状态机单测） |
| `trigger` | enum | **必有** | `tool_result` / `llm_decision` / `policy_rule` / `timeout` |
| `guard_passed` | bool | **必有** | 转移前置条件是否通过 |
| `guard_failed_reason` | str\|null | **必有** | 失败原因（**归因关键**） |
| `is_expected_transition` | bool\|null | 预留 | 是否符合期望路径（评测器回写） |

### 6.2 为什么这层重要

core 层"该关单未关单 15 条"——**这 15 条必然卡在某个状态转移上**。

有了 `guard_failed_reason`，就能回答：
- 是 `CONFIRM → CLOSED` 的 guard 太严？
- 还是 `NEGOTIATE` 没走到 `final` 子状态？

**这比看 LLM 回复文本有用得多。** 模型行为问题的一半定位依赖这一层。

---

## 七、红线检查 Span

**C7 部分实现，这层是安全能力的可视化窗口。**

### 7.1 字段表

| 字段 | 类型 | 必要性 | 说明 |
|---|---|---|---|
| `span_type` | str | **必有** | 固定 `"guard"` |
| `guard_stage` | enum | **必有** | `input` / `output` / `decision` |
| `rule_id` | str | **必有** | 命中的规则 ID（如 `R_FENQI_MAX`） |
| `rule_category` | enum | **必有** | `分期期数` / `延期天数` / `减免比例` / `威胁话术` / `伪造法律后果` |
| `check_result` | enum | **必有** | `pass` / `violation` / `warning` |
| `violation_detail` | str\|null | **必有** | 违规细节（6 条安全话术违规的具体原因） |
| `action_taken` | enum | **必有** | `allow` / `block` / `escalate` / `rewrite` |
| `detection_method` | enum | **预留** | `rule` / `llm_judge` / `nemo`（**为语义级校验预留**） |
| `confidence` | float\|null | **预留** | 置信度（为语义级校验预留） |

### 7.2 预留字段的用法

`detection_method` 与 `confidence` 是为未来接入语义级校验（如 JEV 意图分类）预留的。

**入选理由属于"不可回溯"类**——判定方法和置信度是**当时**的结果，事后无法补采。

接入后 trace 无需改结构即可承载：`detection_method: "nemo"` + `confidence: 0.87`。

有了这层，6 条安全话术违规可以看清：是规则漏检（`check_result: pass` 但实际违规）？还是规则命中但 `action_taken` 判断错误？

---

## 八、降级 / 故障 Span

**C4 当前"仅定义"，T5 要做 3 类故障注入（超时 / 500 / schema 错）。这层提前设计，T5 实现时不返工。**

### 8.1 字段表

| 字段 | 类型 | 必要性 | 说明 |
|---|---|---|---|
| `span_type` | str | **必有** | 固定 `"degradation"` |
| `failure_type` | enum | **必有** | `timeout` / `http_500` / `schema_error` / `rate_limit` |
| `failure_location` | str | **必有** | 发生在哪个 span（父 span_id） |
| `recovery_strategy` | enum | **必有** | `retry` / `fallback_model` / `circuit_break` / `abort` |
| `retry_attempt` | int | **必有** | 第几次重试 |
| `recovery_success` | bool | **必有** | 恢复是否成功 |
| `total_impact_ms` | int | **必有** | 总延迟影响 |

### 8.2 产出价值

该层能直接产出 T5 验收报告（7 类故障恢复路径覆盖率），无需手工记录。

---

## 九、Session 级聚合

**多轮对话项目区别于普通 Agent 的关键视图。**

### 9.1 字段表

| 字段 | 类型 | 必要性 | 说明 |
|---|---|---|---|
| `session_id` | str | **必有** | 关联键（必须在最外层） |
| `total_turns` | int | **必有** | 会话总轮数 |
| `terminal_state` | str | **必有** | 会话最终状态（`CLOSED` / `ESCALATED`） |
| `total_tokens` | int | **必有** | 会话总 token（**成本归属**） |
| `total_tool_calls` | int | **必有** | 会话总工具调用数 |
| `duplicate_call_rate` | float | **必有** | 重复调用率（当前 23.3% 挂在这里） |
| `escalation_triggered` | bool | **必有** | 是否升级人工 |
| `escalation_reason` | str\|null | **必有** | 升级原因 |

---

## 十、字段与指标映射表

**每个指标都能找到数据源——这是"trace 不是装饰品"的证明。**

| 指标 | 数据来源字段 | 可自动化 |
|---|---|---|
| 终态匹配率 | `terminal_match`（Score 回写） | ✅ |
| 业务目标达成率 | `business_goal_achieved`（Score 回写） | ✅ |
| 工具序列均分 | `tool_sequence_score` | ✅ |
| Pass^k | 对 `terminal_match` 做多轮聚合计算 | ✅ |
| 平均调用次数 | `count(tool_spans)` per turn | ✅ |
| 平均 Token | `gen_ai.usage.input/output_tokens` | ✅ |
| 单位成功成本 CPS | 总 token / 成功 case 数 | ✅ |
| 平均耗时 | `duration_ms` 聚合 | ✅ |
| **重复调用率** | `is_duplicate = true` 占比 | ✅ **新能力** |
| **工具顺序违规率** | `call_index_in_turn` 与期望序列比对 | ✅ **新能力** |
| **状态转移合规率** | `is_expected_transition` | ✅ **新能力** |
| **红线拦截率** | `guard.check_result = violation` 占比 | ✅ **新能力** |
| 安全话术违规数 | `action_taken = block` 且漏检 | ✅ |
| 正常结案率 | `terminal_state = CLOSED` 占比（**需标注分母**） | ✅ |
| 升级率 | `escalation_triggered` 占比 | ✅ |
| 故障恢复率 | `degradation.recovery_success` | ✅（T5 后） |

> **标"新能力"的四项是当前做不到、trace 重构后自动可算的。这就是重构的真实收益：不是好看，是可计算。**

---

## 十一、字段治理方案

### 11.1 三档标记制度

每个字段必须标注必要性档位：

| 档位 | 定义 | 处理方式 |
|---|---|---|
| **必有** | 现在就有明确消费者（评测指标 / 可视化 / 断言） | 立即实现 |
| **预留** | 有明确的未来场景 + 时间点 | 现在实现，标注对应里程碑 |
| **待定** | 说不上何时使用 | 先放着，**季度复查** |

**季度清理规则**：满 3 个月未被任何读取者使用的"待定"字段，直接删除。

### 11.2 "字段必须有读取者"纪律

**禁止字段先行。**

规则：每加一个字段，必须在同一 PR 中加入"谁读它"——要么是某个评测指标，要么是某个可视化视图，要么是某个断言。

这与项目已有的"禁止文档先行"原则同构，把同一精神延伸到字段层。

**落地工具**：跑脚本扫描 trace 字段在生产/评测中的读取频率，输出"字段使用热力图"。**零读取字段即候选删除项。**

### 11.3 判断"该不该加字段"的五问清单

| # | 问题 | 若答案为"否" |
|---|---|---|
| 1 | 有明确的读取者吗（指标 / 视图 / 断言）？ | 先不加 |
| 2 | 采集成本是否接近零（不额外调用、不额外存储）？ | 慎重 |
| 3 | 数据是否**不可回溯**（事后无法补采）？ | **必须现在加** |
| 4 | 是否有明确的使用时间点？ | 可以延后 |
| 5 | 是否与已有字段重复？ | 删一个 |

> **第 3 条是"预先留字段"唯一站得住脚的理由。** 历史数据一旦跑过，缺失字段永远补不回来。
>
> 典型"不可回溯"字段：`gen_ai.usage.input_tokens`、`is_duplicate`、`guard.check_result`、`guard.detection_method`、`guard.confidence`。这些一旦不记，240 次试验的数据就永远缺一块。

---

## 十二、字段变更 SOP

### 12.1 变更五步

```
Step 1  判断变更类型
        ├─ 加字段（可回溯）→ 按需，遵守"字段必须有读取者"
        ├─ 加字段（不可回溯）→ 必须立即加
        ├─ 改字段名 → 走适配器映射，保留旧 key 兼容
        └─ 删字段 → 确认无读取者后删除

Step 2  更新 schema_version
        ├─ 向后兼容的加字段 → 次版本号 +0.1
        └─ 破坏性变更（改名/删字段/改语义）→ 主版本号 +1

Step 3  更新读取适配器
        ├─ 老数据缺字段 → 适配器补默认值
        └─ 字段改名 → 适配器做 key 映射

Step 4  跑全量评测，生成 Δ 表
        ├─ Δ 表变化 → 变更影响了评测口径（是 bug，不是特性）
        └─ Δ 表未变 → 变更无副作用（安全）

Step 5  更新字段表三档标记 + 同步 README 数字
```

### 12.2 关键约束

**适配器是唯一读取入口。** 任何消费方（评测器、Langfuse、分析脚本）都不直接读存储层 key。

**"改字段名 = 改评测器 + 改历史分析脚本"** 是最常见的技术债来源。有了适配器，历史 trace 永远可读，评测结果跨版本可比。

**这对本项目尤其重要**——因为评测会持续多轮（T1 基线 → T2 复测 → T9 弱模型对照 → T11 合成管线）。若每次改 trace 都让新旧基线不可比，Δ 表回归机制就失效了。

### 12.3 schema_version 规范

```
trace.schema_version = "1.0"
```

一个字段解决三个问题：
1. 适配器知道按哪个版本解析
2. 可视化与上报能按版本分组
3. 自己一眼看出"这批 trace 是什么时候的"

---

## 十三、实施步骤

**trace 重构与 T3 服务化合并做。**

| 步骤 | 做什么 | 产出 |
|---|---|---|
| 1 | 定义 Span 数据类（复用 pydantic 类型系统） | `TraceSpan` / `LLMSpan` / `ToolSpan` / `StateSpan` / `GuardSpan` / `DegradationSpan` |
| 2 | 加 `parent_span_id` + `span_type` + `attributes`，扁平事件流改为树 | 树形 trace JSON |
| 3 | **本地 Jaeger 验证**（`docker run jaegertracing/all-in-one`） | 可视化调用树（**不引入任何外部依赖**） |
| 4 | 字段名对齐 OTel GenAI 语义约定 | 可移植的 span |
| 5 | 加 `schema_version` + 读取适配器 | 历史数据可比 |
| 6 | T3 的 `/trace/{trace_id}` 接口直接返回树形结构 | 服务化接口无返工 |
| 7 | docker compose 加 langfuse 服务，接入 SDK | 观测平台上线 |
| 8 | 评测器把 4 类 Score 回写到 trace | 评测自动化 |

### 实施提醒

| # | 提醒 | 理由 |
|---|---|---|
| 1 | **先 Jaeger 后 Langfuse** | 第 3 步是"免费的验证"，结构错了在这里改成本最低 |
| 2 | **trace 落盘必须异步** | 用队列 + 后台 flush，保护"0 超时"这个基线数字不被 trace I/O 拖累 |
| 3 | **脱敏在采集层做** | 金额转区间、企业名哈希。自建 Langfuse 解决数据不出境，但字段级脱敏仍要在源头做 |
| 4 | **不要用框架的 span 命名** | 保持自定义 schema，把未来任何框架当"事件源"。这是自研框架的核心优势 |
| 5 | **Langfuse 是显示器，不是计算器** | E1-E4 分层评测逻辑、级联判定、Pass^k 计算仍在自己的评测器里 |

---

## 附录：字段必要性总表

### 必有（立即实现）

| 所属 Span | 字段 |
|---|---|
| Turn | `trace_id` `span_id` `parent_span_id` `span_type` `schema_version` `session_id` `turn_index` `user_id` `scenario_id` `state_before` `state_after` `start_time` `end_time` `duration_ms` `status` |
| LLM | `gen_ai.request.model` `gen_ai.response.model` `gen_ai.usage.input_tokens` `gen_ai.usage.output_tokens` `gen_ai.request.temperature` `decision_type` `tool_call_count` `prompt_hash` `prompt_version` `duration_ms` `status` |
| Tool | `tool_name` `tool_category` `call_index_in_turn` `call_index_in_session` `arguments` `arguments_hash` `result_status` `result_summary` `result_bytes` `duration_ms` `is_duplicate` |
| State | `from_state` `to_state` `transition_id` `trigger` `guard_passed` `guard_failed_reason` |
| Guard | `guard_stage` `rule_id` `rule_category` `check_result` `violation_detail` `action_taken` |
| Degradation | `failure_type` `failure_location` `recovery_strategy` `retry_attempt` `recovery_success` `total_impact_ms` |
| Session | `session_id` `total_turns` `terminal_state` `total_tokens` `total_tool_calls` `duplicate_call_rate` `escalation_triggered` `escalation_reason` |

### 预留（现在实现，标注对应里程碑）

| 所属 Span | 字段 | 启用时间点 |
|---|---|---|
| Turn | `case_id` `eval_layer` | 评测器接入时 |
| LLM | `retry_count` | **T5 故障注入** |
| State | `is_expected_transition` | 评测器回写接入时 |
| Guard | `detection_method` `confidence` | **语义级校验接入时**（如 JEV） |

### 待定（季度复查，无读取者则删除）

| 所属 Span | 字段 | 判断理由 |
|---|---|---|
| LLM | `gen_ai.request.max_tokens` | 除非做 max_tokens 消融实验，否则可删 |
| Tool | `tool_registry_version` | 工具 schema 稳定则延后 |
| Tool | `idempotency_key` | 写类工具为 mock 时暂不需要，先用 `is_duplicate` 足够 |

---

## 附：本方案与项目已有机制的衔接

| 已有机制 | 衔接方式 |
|---|---|
| 契约状态表（仅定义 / 部分实现 / 已实现） | trace 各 Span 也按此三档维护，禁止"字段先行" |
| R1 回归机制（每次改动跑全量 + Δ 表） | **schema 变更必须走 R1 验证** |
| 16 项指标 | 见第十节映射表，其中 4 项为重构后新增能力 |
| `eval-design.md` 可复现性条款 | `prompt_hash` + `schema_version` + `model_id` 共同构成可复现证据链 |
| 明确不做清单 | trace 追加：不做 Prometheus / ELK 全家桶、不做 eBPF、不做时间旅行调试（MVP 阶段 ROI 不足） |

---

*文档结束。实施过程中如遇字段设计冲突，以本方案第十一节"字段治理方案"的判断标准为准。*
