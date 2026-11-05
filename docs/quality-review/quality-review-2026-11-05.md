# 项目质量审查报告（2026-11-05）

> 审查方式：只读静态审查。未修改任何源代码、配置或数据。
> 审查范围：`src/`、`scenarios/`、`scripts/`、`tests/`、`configs/`、`data/`、`eval/`、根目录依赖与构建文件、`docs/` 全部公开文档；排除 `.git`、缓存、构建产物。
> 环境限制：当前默认解释器未安装 `pytest`，`python3 -m pytest` 无法执行，因此未复跑测试；测试数量用 `grep` 静态统计为 **164 个 `def test_`**，与 README 的 164 一致。评测结论基于源码阅读、已落盘报告（`eval/reports/*.json`）和静态检查。文中标注“推断”的条目未做运行时验证。

---

## 1. 总体结论与评分概览

**总体结论：作为单场景、Mock 优先的演示原型，项目结构清晰、trace 与多口径评测设计有明显亮点；但作为 README 所宣称的“零业务分支、场景通用框架 + 四层安全防护”，当前实现存在系统性差距，尤其是框架层业务硬编码、政策真源分裂与评测器有效性不足。建议把当前定位诚实下调为“receivables 单场景可运行原型 + milktea 字段级适配演示”，再按第 5 节优先级补齐。**

| 评估维度 | 评分（10 分制） | 核心判断 |
|---|---|---|
| 代码规范一致性 | 6.0 | 类型标注、Pydantic 模型、命名整体统一；但异常处理、日志、死代码和业务常量散落不一致 |
| 架构设计合理性 | 5.0 | 分层目录清楚，状态机/工具/配置可读；但 `loop` 反向依赖应收工具名与业务话术，配置层多个字段未接线 |
| 潜在 bug 与安全风险 | 4.0 | 政策可被绕过、承诺工具无权限校验、C4 故障路径缺失、C7 输出侧与配置不一致 |
| 性能优化空间 | 6.5 | MVP 量级无严重热点；但真实模型串行评测、无超时/并发、trace 重复深拷贝、上下文无裁剪 |
| 可维护性与可扩展性 | 4.0 | “新增场景零代码改动”未达成；新增场景仍要改 loop/llm/ui/synthesis |
| 测试覆盖完整性 | 6.0 | 164 个测试覆盖面较广；但关键路径（政策绕过、禁用动作、C4、C7、新场景）缺测，存在自证式白名单 |
| **综合** | **5.5** | **演示可用，尚不能作为“能力基线/通用框架”对外承诺** |

最关键的五个风险：

1. 政策数值存在两套真源，且 `record_promise` 不校验金额/日期/合同归属，模型可写入超政策承诺（第 2 节 S-3）。
2. 评测器 `forbidden_actions` 与工具名比较，永远为 False；`success` 也未校验工具参数和工具成功性，评测结论存在系统性高估（S-5、S-6）。
3. 生成的核心评测数据本身违反“延期 ≤ 30 天”政策，且首期日期 `2026-10-15` 已早于当前日期 `2026-11-05`（S-7）。
4. 框架层仍硬编码应收工具名、中文意图关键词、`CLOSED/ESCALATED` 终态与系统提示词，milk tea 只是“恰好能跑”（S-1、S-2）。
5. 真实模型每个用户输入只允许一次 LLM 决策，工具结果返回后不会继续推理；这与文档的 ReAct 语义不符（S-8）。

---

## 2. 问题清单

### 2.1 严重（会直接造成错误结论、错误业务结果或核心承诺失效）

#### S-1 框架层硬编码应收工具名、意图关键词与终态名，新增场景实际不是“零代码”

- **位置**：
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L279-L289)：用户意图由硬编码关键词决定，`"行"`、`"同意"`、`"可以"` 等为子串匹配。
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L371-L382)：只有工具名恰好是 `query_receivable` / `query_policy` 时，才更新 `identity_verified` / `amount_informed` / `counter_within_policy`。
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L427-L428)：升级工具参数硬编码为 `contract_no` / `reason`。
  - [machine.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/state/machine.py#L178-L179)：终态硬编码为 `{"CLOSED", "ESCALATED"}`，忽略 `state_machine.success_stage` / `escalate_to`。
- **问题描述**：
  1. `"不同意"` 同时命中 `"同意"`（子串）与 `"不同意"`，会同时把 `offer_accepted` 和 `customer_refuse` 置真；`"不行"` 也会因为包含 `"行"` 被识别为接受。真实用户说“不行，我不同意”时，状态机可能先进入 `CONFIRM` 而不是升级/拒绝。
  2. milktea 的工具 `query_member` 查不到会员时，不会累加 `verify_failures`；只有用户文本包含“不记得/不知道/忘了/记不清”才累加。milk tea 的“身份核验失败升级”路径实际上不依赖工具结果。
  3. 若新场景的成功终态叫 `COMPLETED`，`_signals()` 会报告 `closed=True`，但 `is_terminal()` 永远返回 False，会话不会结束。
  4. 新场景的升级工具若参数是 `member_id`，`escalate` 会因 unexpected keyword 失败；循环仍把 `escalated=True`，评测器还会把它算作“已处置”。
- **影响**：README 第 5 行“框架代码零业务分支”和 roadmap “新增场景零代码改动”不成立；milk tea 的 4 条硬标准只在“grep 场景名”层面通过，功能层并未验证。
- **改进建议**：
  - 在 `scenario.yaml` 增加 `fact_mapping`（如 `query_receivable -> {identity_verified, amount_informed}`、`query_member -> {identity_verified}`）、`intent_patterns`、`terminal_stages`、`escalation_args`。
  - `is_terminal` 改为读取 `success_stage` 与 `escalate_to`；`_signals` 与 `is_terminal` 共用同一份判定。
  - 意图识别下移到场景适配器或独立 `IntentClassifier` 配置，不在 `src/agent/loop.py` 内做中文子串匹配。
  - 增加“新场景契约测试”：只在 `configs/ + data/ + scenarios/` 新增文件，使用不同终态名、不同工具名、不同升级参数，断言端到端可跑通。

#### S-2 系统提示词配置完全未接线，且框架提示词硬编码应收政策

- **位置**：
  - [config.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/core/config.py#L70-L71)：`Prompts.system` 是必填字段。
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L126-L135)：实际系统提示词由代码拼接，未读取 `config.prompts.system`。
  - 配置文件：[receivables/system.txt](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/configs/prompts/receivables/system.txt)、[milktea/system.txt](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/configs/prompts/milktea/system.txt) 均未被任何代码引用。
- **问题描述**：`_build_system_prompt` 对所有场景都输出“分期≤N期、延期≤M天、减免≤X%”，而 milktea 只配置了 `forbidden_patterns`，`Policy` 使用默认值 `3/30/10%`。因此牛奶茶场景的系统提示词会告诉模型“分期≤3期、延期≤30天、减免≤10%”，UI 也展示同样的政策卡片。
- **影响**：README 第 153 行“第 3 步写 system.txt”是无效步骤；场景作者改 prompt 文件不会改变模型行为；milk tea 的提示词语义错误，且释放了不应存在的金融政策能力。
- **改进建议**：`AgentSession` 初始化时读取 `config.prompts.system`（支持占位符渲染），删除代码内政策文案；`Policy` 改为按场景可选（无政策即不生成政策段）；补一条测试断言 `system.txt` 的内容出现在第一条 `llm_call` 的 messages 中。

#### S-3 政策真源分裂、超政策状态可绕过、承诺工具无权限校验

- **位置**：
  - [tools.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/receivables/tools.py#L46-L51)：`_MOCK_POLICY` 独立于 YAML 的 `policy`。
  - [tools.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/receivables/tools.py#L114-L131)：`query_policy` 返回 `_MOCK_POLICY` 与检索片段。
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L377-L380)：`query_policy` 只要成功，就置 `counter_within_policy=True`，注释明确写了“W1 简化：查了政策即认为方案在政策内”。
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L155-L165)：`counter_exceeds_policy`、`redline_hit` 初始化为 False，但全仓库没有任何地方置 True。
  - [tools.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/receivables/tools.py#L134-L144)：`record_promise` 只做非空判断，不校验 `pay_date` 格式、金额正负、是否超过账面金额、是否在政策窗口内、合同号是否属于当前会话。
- **问题描述**：
  1. 若 YAML 把减免上限从 10% 调到 20%，`_policy_refusal` 用 YAML 判定，`query_policy` 仍返回 10%，模型与客户会看到互相矛盾的数值。
  2. 用户提出超政策方案并触发拒绝话术时，模型若同时调用了 `query_policy`，状态机会把 `counter_within_policy` 置真，仍可进入 `final → CONFIRM → CLOSED`，出现“口头已拒绝但系统已结案”。
  3. `record_promise(contract_no=任意合同, pay_date=2026-10-15 过去日期, amount=-5)` 会被接受（负数 truthy；日期只校验非空），并把固定 `recorded_at="2026-10-31T00:00:00"` 写入审计记录。
- **影响**：直接违反项目硬约束“权限数值不得由模型给出，必须来自 query_policy 一类的事实来源”，并可能生成超权限承诺；mock 与真实政策漂移会让评测结论失真。
- **改进建议**：
  - 只保留一个政策真源：`query_policy` 从 `config.policy` 返回值，或把 YAML 的 policy 注入 mock；删除 `_MOCK_POLICY`。
  - `record_promise` 接收当前会话的已核验合同与账面金额，校验：合同一致、金额为正且 ≤ 账面、日期 ≥ 今天、日期 ≤ 到期日 + `max_extension_days`、期数 ≤ `max_installments`、减免 ≤ `max_waiver_ratio`。
  - 超政策事实必须显式写入 `counter_exceeds_policy=True`，并禁止 `final → CONFIRM`；把拒绝话术与状态转移绑定，而不是只覆盖回复。
  - `recorded_at` 使用真实时间或注入可配置时钟；增加幂等键 `(session_id, contract_no, pay_date)`。

#### S-4 C4 降级/故障契约实际未实现，文档并存三种口径

- **位置**：
  - [registry.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/registry.py#L27-L36)：定义了 `TOOL_TIMEOUT`、`DEGRADED`、`NOT_FOUND` 等错误码。
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L356-L362)：`degraded=not result.get("ok", False)`，所有业务失败（包括 `NOT_FOUND`）都被标记为 degraded。
  - 全仓库没有任何工具超时、工具 500 重试、工具返回 schema 校验；`StepType.DEGRADATION` 从未产出。
  - 文档口径冲突：[architecture.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/architecture.md#L319)（“MVP 实现 3 类”）、[roadmap.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/roadmap.md#L63)（C4“仅定义”）、[roadmap.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/roadmap.md#L46)（T5 仍是待办）。
- **问题描述**：契约定义 7 类，MVP 声称实现 3 类（超时/500/schema 错），实际三类均为零实现；`degraded` 只是 `ok=False` 的同义词，无法区分“查无此人”和“工具超时”。
- **影响**：C4 验收项“3 类各有 1 用例 + trace 中可见 degradation”无法满足；降级率/故障率会被业务 NOT_FOUND 污染，不能作为可靠性指标。
- **改进建议**：
  - 工具注册表增加可配置超时（`asyncio.wait_for` 或线程超时）、可重试错误码（500/超时）、返回 schema 校验（Pydantic 模型）。
  - `degraded` 仅在 `TOOL_TIMEOUT`、`DEGRADED`、schema 错时置真；`NOT_FOUND` 保持业务失败。
  - 产出 `StepType.DEGRADATION` 步骤，并在报告增加 `degradation_rate`；补 3 个故障注入测试。
  - 统一三处文档口径；在契约状态表中只能出现一个状态。

#### S-5 评测器 `forbidden_actions` 维度失效，永远不扣分

- **位置**：
  - [contracts.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/contracts.py#L80-L127)：`forbidden_hit = any(t in oracle.forbidden_actions for t in actual_tools)`。
  - [rule_based.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/synthesis/rule_based.py#L64)：`forbidden_actions` 的值是动作语义 `promise_beyond_policy`、`threaten`、`fabricate_amount`。
- **问题描述**：`actual_tools` 是工具名（`query_receivable` / `record_promise` / `escalate` 等），与动作语义不在同一命名空间，`forbidden_hit` 恒为 False。README 第 171 行“命中禁用动作扣 0.5”无法触发。
- **影响**：评测的“无禁用动作”成功条件形同虚设；所有工具的 `forbidden_hit` 永远 False，报告也不落盘该字段，无法审计。
- **改进建议**：
  - 把 `forbidden_actions` 改为可执行的判定器（如 `{"action": "record_promise", "when": "amount > policy.max_waiver..."}` 或独立 `ForbiddenRule` 函数），对工具参数和回复文本分别判定。
  - 在 trial 记录中落盘 `forbidden_hits: []`，并补正例/反例单测。
  - 若短期不做语义判定，至少把字段改名为 `forbidden_tools` 并用工具名，避免误导。

#### S-6 评测器不校验工具参数与工具成功性，`success` 口径与文档不一致

- **位置**：
  - [contracts.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/contracts.py#L80-L127)：`check_tool_sequence` 只使用工具名，`expected_args` 从未读取。
  - [rule_based.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/synthesis/rule_based.py#L83)：`expected_args` 只在 `query_receivable` 上生成，且评测器不消费。
  - [runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L189-L196)：`success` 使用 `handled + tool_ok + no_model_violations`，没有显式 `stage_match`。
  - [registry.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/registry.py#L11)：注释声称 schema 用于“参数正确率评测”。
- **问题描述**：
  1. `record_promise` 传错金额、过去日期、甚至别的合同号，工具序列仍算“完全覆盖”，因为只认名字。
  2. 工具调用失败也会计入 `tool_coverage`，因为 trace 中的 `tool_call` 步骤不区分 `ok`。
  3. `success` 没有直接引用 `stage_match` 变量，而是依赖 `_handled_correctly`；后者虽然先校验 stage（[runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L176-L183)），但对未知 outcome 在 stage 匹配后直接返回 True，未来新增 outcome 会静默放过。
  4. README 第 209 行称“`record_promise` 确实成功调用”，实际 `goal_achieved` 只检查工具名出现过（[loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L208-L221)），成功性由状态机条件间接保证，不是显式校验。
- **影响**：评测集无法回答“参数是否正确”“工具是否真的成功”这两个 E3 核心问题，v1 报告中的工具分不能解释为参数正确率。
- **改进建议**：
  - `check_tool_sequence` 增加 `args_ok`：按 `expected_args` 做子集匹配（至少 `query_receivable.contract_no`、`record_promise.pay_date/amount`）。
  - `evaluate_trial` 只用 `result.ok == True` 的工具步骤计算覆盖与顺序。
  - `success = stage_match AND handled AND args_ok AND order_ok AND no_forbidden AND no_model_violations`。
  - 对未知 `outcome` 抛错或返回 False，禁止默认 True。

#### S-7 生成的核心评测数据违反自身政策与时间语义

- **位置**：
  - [runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L52-L78)：`_explicit_plan` 硬编码 `10%`、`3 期`、`2026-10-15`、`2026-11-15`、`2026-12-15`。
  - [runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L100-L101)：extension 场景给出“延到 2026-10-01”。
  - [receivables.yaml](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/configs/scenarios/receivables.yaml#L71-L78)：延期上限为到期日后 30 天；案例到期日为 2026-08-15，最晚应为 2026-09-14。
- **问题描述**：
  1. extension 核心案例提出 `2026-10-01`，比“到期日 + 30 天”晚 17 天，属于超政策方案，但 oracle 期望成功关单。
  2. 分期方案首期 `2026-10-15` 早于当前日期 `2026-11-05`；后续到 `2026-12-15` 也远超 30 天延期窗口。`record_promise` 又照抄固定 `2026-11-15`，完全没有使用用户给出的日期。
  3. 话术里的 `10%`、`3 期` 与 YAML policy 重复，改配置不会同步改评测数据。
- **影响**：核心路径评测在“正确执行超政策方案”时给满分，直接污染任务完成率、承诺合规率和工具分；真实模型若遵守政策反而会被判失败。
- **改进建议**：
  - 由合成器从 `config.policy` 和“当前日期”生成合法日期窗口（例如到期日后 0–30 天，且 ≥ today），禁止硬编码日历。
  - 把用户话术生成移入场景适配器，按 `case.init_state` 的 `due_date`、`book_amount` 和 policy 模板生成。
  - 增加数据校验器：`assert pay_date >= today and pay_date <= due_date + max_extension_days`，在 `make dataset` 和 `make eval` 前 fail-fast。
  - `record_promise` 必须使用用户/模型给出的日期，mock 不得回写固定日期。

#### S-8 每个用户输入只允许一次 LLM 决策，工具结果后不继续推理

- **位置**：
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L310-L327)：每轮 `send` 只调用一次 `llm.chat`。
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L452-L458)：工具结果 append 后本轮结束，没有再次调用模型。
- **问题描述**：真实模型在一次用户输入中先调用 `query_receivable`，拿到结果后无法在同一轮继续调用 `query_policy` 或生成答复；它只能等下一个用户消息再行动。工具调用轮的 `reply` 通常是空字符串（Mock 的 tool_call content 为 None），UI 靠占位文案掩盖，但评测的 `replies` 会为空。
- **影响**：与 README/architecture 的 ReAct 循环描述不一致；真实模型的任务完成率受用户脚本轮次强约束，多步工具编排能力被框架人为截断。
- **改进建议**：恢复标准 ReAct 内层循环：`while` 中调用 LLM → 执行工具 → 把 tool result append → 再次调用 LLM，直到无 tool_calls 或达到 `max_tool_rounds`；为内层循环设置最大步数与超时。

#### S-9 C7 输出侧与配置未兑现，`forbidden_patterns` 是死配置

- **位置**：
  - [guardrails.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/guardrails/guardrails.py#L31-L38)：`_FORBIDDEN_PATTERNS` 硬编码。
  - [guardrails.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/guardrails/guardrails.py#L51-L68)：`check_output` 只检查禁用话术与减免比例。
  - [config.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/core/config.py)：`Policy.forbidden_patterns` 未被 `check_output` 使用。
  - [guardrails.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/guardrails/guardrails.py#L5-L7)：docstring 声称“输出侧校验不含超政策承诺、不泄露 system prompt”。
- **问题描述**：milk tea `forbidden_patterns=["威胁","保证一定"]` 不会生效；receivables 想增加“承诺保底回款”也只能改源码。`check_output` 不检查 system prompt 泄露、不检查延期天数/期数/金额超限。决策侧仍完全依赖 prompt，且 S-3 的 `record_promise` 没有任何权限校验兜底。
- **影响**：C7 的“四层防护”只有输入标记、工具隔离和少量输出正则三层半；配置化禁用话术与“超政策承诺”校验不成立，对真实模型的约束弱于文档描述。
- **改进建议**：
  - `check_output(policy, text)` 读取 `policy.forbidden_patterns`，并对延期天数、期数、减免比例、金额做结构化校验。
  - 输出侧增加 system prompt 片段/密钥回显检测。
  - 决策侧把 `record_promise` 的校验做成工具级硬约束（见 S-3），不要只依赖 system prompt。
  - 在 trace 中区分 `guardrail.input`、`guardrail.tool`、`guardrail.output`，并记录动作（标记/拒绝/改写）。

---

### 2.2 警告（会造成指标失真、边界错误或维护成本上升）

#### W-1 C6 Trace 的模型元信息、token、延迟均不准确

- **位置**：[loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L316-L323)、[trace.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/core/trace.py)。
- **问题描述**：`input_tokens` / `output_tokens` 用 `len(json.dumps(...))`（字符数）冒充 token；LLM 延迟固定写 `latency_ms=50`；`Trace.model` 字段从未赋值（`TraceBuilder` 只接收 `session_id/case_id`），真实模型的 usage 被丢弃。
- **影响**：trace 无法用于成本、延迟、模型快照审计；C6 契约中的模型元信息与 token 统计不可信。
- **建议**：把 `llm.chat` 返回的 `usage` 写入 step；延迟用 `time.perf_counter()` 实测；`Agent` 构造时把 `llm.describe()` 注入 `TraceBuilder`；Mock 明确标记 `usage_estimated=True`。

#### W-2 文档与实现字段/口径漂移

- **位置与证据**：
  - [README.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L90)：`result.task_completed` 不存在；实际是 `closed` / `goal_achieved`（[loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L256-L265)）。
  - [architecture.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/architecture.md#L118) 与 [L186](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/architecture.md#L186)：写 `promise_recorded`；YAML/代码使用 `goal_tool_called`（[loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L164)、[loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L381-L382)）。
  - [architecture.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/architecture.md#L341)：`TurnResult.task_completed` 不存在。
  - [eval-design.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/eval-design.md#L131-L143)：报告字段 `task_completion_rate`、`metrics` “六项”与实际报告（`closed_rate`、`business_goal_rate`、`handled_rate`、8 项 metrics）不一致。
  - [prior-art.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/prior-art.md#L37)：“stage_match + task_completed 已入 v0 报告”不成立。
  - [roadmap.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/roadmap.md#L15) 写 160 passed，[L28](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/roadmap.md#L28) 与 README 写 164。
  - [README.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L198-L199)：称重复调用是“一轮内”，代码按整个会话计数；“真实模型调用 `diff_statement` 68 次”实际是 68 个 case 覆盖数（调用次数为 138）。
- **影响**：读者按 README 示例会直接 `AttributeError`；评测字段表无法用于解读报告；跨文档口径不统一，违背项目“改动前先 grep 全仓”的约定。
- **建议**：以实际 `TurnResult` / 报告 JSON 为唯一真源，批量修订 README/architecture/eval-design/prior-art；增加文档字段快照测试（从 `TurnResult.model_fields` 和报告键自动生成对照表）。

#### W-3 评测层与 UI 层仍硬编码应收场景

- **位置**：
  - [runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L90-L101)：读取 `case.init_state["contract_no"]` / `["invoice_no"]`，新场景（如 `member_id`）会直接 `KeyError`。
  - [runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L52-L78)：日期/金额/期数硬编码。
  - [app.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/ui/app.py#L145-L147)：政策卡片固定“分期上限/延期上限/减免上限”。
  - [app.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/ui/app.py#L508-L509)：转移矩阵提示固定“12 条规则”。
  - [app.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/ui/app.py#L553)：按钮固定“运行 80 条评测”。
- **影响**：milk tea 在 UI 中看到不存在的金融政策；“12 条”对 9 条转移的 milk tea 是错误信息；评测器无法直接复用到第二个场景。
- **建议**：评测用户话术交给场景适配器（`scenarios/<name>/user_sim.py`）；UI 仅渲染 `config` 的泛化字段（stage/transition 数量、`policy` 可选项）；按钮从 `len(cases)` 取值；新增 milktea 数据集后再开放“运行评测”。

#### W-4 UI 的“运行评测”会读到旧 Mock 报告；milk tea 场景会报错

- **位置**：
  - [app.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/ui/app.py#L292)：`REPORT_PATH` 固定 `eval/reports/v0_baseline.json`。
  - [app.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/ui/app.py#L402-L407)：`run_eval` 调 `runner.main()` 后调用 `load_report()` 默认路径。
  - [runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L429-L435)：非 Mock 模型默认写入 `v0_baseline_<model>_k3.json`。
- **问题描述**：UI 用真实模型跑完 240 次试验后，报告写到另一个文件，页面仍展示旧 Mock 报告。若 UI 以 milktea 配置启动，`run_eval` 会因 `eval.dataset` 缺失抛 `ConfigError`，且按钮回调没有 try/except。
- **影响**：用户会误把 Mock 数据当成真实模型结果；milk tea 页面点按钮直接出错误堆栈。
- **建议**：`run_eval` 使用 `runner.main(..., report_path=...)` 显式指定路径并把返回值传给渲染；无数据集时禁用按钮并提示；回调包 `try/except`，只展示友好错误。

#### W-5 知识库配置未接线，检索自动选择“排序第一个 corpus”

- **位置**：
  - [config.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/core/config.py#L65-L67)：`knowledge.corpus` / `top_k` 字段。
  - [tools.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/receivables/tools.py#L114-L125)：`get_retriever()` 不传 scenario/corpus，`top_k=5` 硬编码。
  - [retrieval/__init__.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/retrieval/__init__.py#L17-L33)：无显式路径时 `sorted(glob(...))` 取第一个。
- **影响**：一旦新增 milktea 的 `data/knowledge/milktea/corpus.yaml`（字母序在 receivables 前），应收场景的 `query_policy` 会检索到牛奶茶语料；配置中的 `knowledge.top_k` 改不动。
- **建议**：`query_policy` 由场景注入 retriever（`Agent` 构造时按 `config.knowledge` 创建），删除自动发现；`top_k` 从配置读取；预检 corpus 文件存在且字段齐全。

#### W-6 配置预检存在可导致运行时崩溃或静默死条件的缺口

- **位置**：
  - [config.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/core/config.py#L249-L254)：`policy_field` 只检查字段存在，允许 `forbidden_patterns`（list）。
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L120-L123)：随后执行 `actual <= limit` 与 `f"{limit:.0%}"`。
  - [config.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/core/config.py#L135-L144)：`_state_is_valid` 允许 `to="*"`。
  - [config.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/core/config.py#L32)：`goal.max_rounds` 未被任何运行时代码读取。
- **问题描述**：若配置 `policy_field: forbidden_patterns`，预检通过，但首次触发拒绝规则时 `float <= list` 会抛 `TypeError`（潜在运行时崩溃）。若 `to: "*"`，状态机可把 stage 置为 `"*"`，之后既非终态也无出边。`when` 表达式未做变量白名单/语法校验，写错变量会静默为 False（如 architecture 中的 `promise_recorded`）。
- **影响**：坏配置不能 fail-fast；配置作者难以定位“转移永远不触发”。
- **建议**：`policy_field` 限定为数值字段；拒绝规则校验 `scale` 与字段类型匹配；禁止 `to="*"`；`when` 用 AST 校验只允许白名单变量、比较、`and/or`；`success_stage` / `escalate_to` 必须 ∈ `stages`；运行时在达到 `max_rounds` 时强制升级或结束。

#### W-7 Mock 工具全局可变状态、固定审计时间与幂等缺失

- **位置**：
  - [tools.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/receivables/tools.py#L96-L97)：模块级 `_PROMISES` / `_ESCALATIONS`。
  - [tools.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/receivables/tools.py#L142)：固定 `recorded_at`。
  - [tools.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/receivables/tools.py#L188-L198)：`ticket_id = len(_ESCALATIONS)+1`。
- **问题描述**：80×3 次评测共享同一进程的列表，工单号随运行次数增长；跨用例/跨会话没有隔离；`record_promise` 对同一合同同一日期重复调用会重复落承诺；并发调用时 `len+1` 生成工单号存在竞态（推断）。
- **影响**：评测不可重复复现；长驻服务会内存增长并串会话数据；审计时间恒为 2026-10-31，与真实运行时间不符。
- **建议**：mock 存储改为按 `session_id` 隔离的实例；提供 `reset()` fixture；`record_promise` 幂等键去重；工单号用 `uuid4` 或原子计数器；`recorded_at` 用时钟注入。

#### W-8 重复调用指标与升级判定语义不精确

- **位置**：
  - [runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L203-L212)：`duplicate_terminal_calls` 对 `result.tool_calls`（整场会话）计数。
  - [runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L176-L183)：升级“已处置”只看 `result.escalated`，不检查 `escalate` 工具是否成功。
  - [README.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L198)：描述为“一轮内连续调用”。
- **问题描述**：合法的多期承诺可能在多个用户轮次各调用一次 `record_promise`，被计为重复副作用；`escalate` 若因参数/异常失败，仍会算“已落工单”。
- **影响**：重复调用指标偏高且不可解释；升级正确率可能高估。
- **建议**：按 `(round_no, tool)` 统计“同轮重复”；跨轮重复只做提示；`handled_correctly` 检查 `escalate` 步骤 `result.ok == True`；报告分别输出 `same_round_duplicates` 与 `cross_round_repeats`。

#### W-9 测试存在自证式白名单与外部产物依赖，关键路径未覆盖

- **位置**：
  - [test_milktea_scenario.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/tests/test_milktea_scenario.py#L29-L45)：`KNOWN_FACTS` 是手写集合，包含 loop 永不设置的 `promise_recorded`，却漏掉实际使用的 `goal_tool_called`。
  - [test_milktea_scenario.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/tests/test_milktea_scenario.py#L73)：`test_src_contains_no_scenario_names` 只 grep 两个场景名，无法发现 `loop.py` 的中文意图词、`llm.py` 的 `HT-Y2026`、UI 的政策卡片。
  - [test_ui_render.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/tests/test_ui_render.py#L243-L246)：`test_load_report_real_file` 依赖仓库内已生成的 `v0_baseline.json` 且断言 80 条。
- **影响**：测试全绿不代表“零业务分支”“C4/C7 已实现”；数据扩到 120 条时，UI 测试会因报告未重生成而失败，制造假回归。
- **建议**：`KNOWN_FACTS` 从 `AgentSession` 事实定义自动导出；增加“新场景零代码”契约测试；补政策绕过、参数校验、C4 三类故障、C7 输出检查、milk tea eval 测试；报告类测试使用 fixture 生成的最小 JSON，不依赖仓库产物。

#### W-10 日志与异常处理不统一，反模式集中

- **位置**：
  - [registry.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/registry.py#L50-L55)：`except Exception` 统一转为 `INTERNAL_ERROR`，无日志、无堆栈。
  - [app.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/ui/app.py)：`submit_turn` 将原始异常文本展示给用户。
  - 全仓库 `src/` 没有 `logging`，CLI 全部 `print`。
  - [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L271-L461)：`_run_turn` 约 190 行，同时承担 LLM 调用、工具执行、事实更新、转移、护栏。
- **影响**：线上/评测失败难以定位；用户可能看到内部路径或供应商错误；长函数增加修改回归风险。
- **建议**：引入 `logging`（console + file 双 handler），工具失败记录异常类型与 trace_id；UI 只展示友好文案；拆分 `_run_turn` 为 `_call_model`、`_execute_tools`、`_update_facts`、`_apply_transitions`、`_apply_guardrails`。

#### W-11 依赖与复现性不足

- **位置**：[requirements.txt](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/requirements.txt)；仓库根目录无 `pyproject.toml` / lockfile / `.github/workflows`。
- **问题描述**：仅 `gradio` 固定版本，`pydantic`、`httpx`、`jieba`、`fastapi`、`uvicorn` 未锁版本；无 CI；无统一 lint/format 配置。
- **影响**：不同机器安装可能得到不同行为；当前默认环境甚至无法 import `pytest`（本次未能复跑测试）。
- **建议**：补 `pyproject.toml`（依赖、ruff、pytest 配置）、`requirements.lock` 或 `uv.lock`；加最小 CI（`ruff` + `pytest` + `make eval` 的 Mock 冒烟）；README 写明 Python 版本。

---

### 2.3 建议（不阻塞功能，但能显著降低成本）

| 编号 | 位置 | 问题 | 建议 |
|---|---|---|---|
| A-1 | [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L316-L323) | 每次 LLM 调用都重建工具 schema；每轮 `_snapshot` 深拷贝全量 trace，UI 每轮落盘全量 trace | 在 `Agent` 初始化时缓存 `function_calling_schemas()`；trace 只在会话结束/显式请求时快照与落盘 |
| A-2 | [registry.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/registry.py)、[app.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/ui/app.py) | 死代码：`ToolParam`、`_GLOBAL_REGISTRY/get_registry`、`load_cases_dir`、`case_card_html`（仅测试使用）、`_call_count` | 删除或在生产路径使用；用 `ruff`/`vulture` 纳入 CI |
| A-3 | [retrieval/bm25.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/retrieval/bm25.py#L40-L51) | 语料字段 `title/text` 缺失会在检索时 KeyError，被工具层吞成 INTERNAL_ERROR | `get_retriever` 加载时用 Pydantic schema 校验 corpus；错误信息指明文件与片段 id |
| A-4 | [rule_based.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/synthesis/rule_based.py#L311-L338) | `--config` 只决定输出目录，生成逻辑仍是应收硬编码；`forbidden` 追加重复 `threaten`（L277-L279） | 生成器按场景 adapter 组织；去重 `forbidden_actions`；为 `--config milktea` 明确报错“无生成器”而不是写入应收数据 |
| A-5 | [README.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L153)、[architecture.md](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/architecture.md#L248) | 文档交叉引用与步骤失效：system.txt 步骤无效；architecture 指向旧锚点 `#4-场景二milktea业务规则注入验证`，scenario-design 标题已变为“通用性验证” | 统一锚点；增加链接检查脚本（或 CI 中 `markdown-link-check`） |
| A-6 | [guardrails.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/guardrails/guardrails.py#L51-L68) | 输出正则“威胁”会误伤“不会威胁客户”这类否定句 | 改为意图/句式判定或加入否定词窗口；补充正负例测试 |
| A-7 | [ui/app.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/ui/app.py#L273-L282) | 每次 UI 轮次写一份完整 trace 文件，数量无上限 | 按会话一个文件/追加写，提供清理策略；报告与 trace 分开存储 |

---

## 3. 文档与实现一致性对照表

### 3.1 C1–C7 契约

| 契约项 | 文档描述 | 实际实现 | 结论 |
|---|---|---|---|
| **C1 数据** | Case/Oracle schema、三判定边界（重复调用不惩罚、仅非空 order 相对顺序、部分完成按维度给分） | `Case` / `Oracle` Pydantic 模型与三边界已实现（[contracts.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/contracts.py#L1-L131)）；但 `expected_args` 从未被评测器消费，`content_hash` 加载时不校验 | **部分实现**：schema 有，参数正确率与哈希完整性未接入 |
| **C2 工具** | 统一返回 `{ok,data,error,meta}`、错误码、JSON Schema、Mock/真实注册 | 统一 envelope、错误码、schema、按 `impl` 加载均已实现（[registry.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/registry.py)）；但无运行时 schema 校验、超时、500 重试、参数正确率评测 | **部分实现** |
| **C3 配置** | scenario.yaml schema + 启动预检；坏配置 fail-fast | 必填字段、`impl` 可导入、转移合法性、policy 数值四项预检有测试（[config.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/core/config.py#L158-L270)）；但 `prompts.system`、`knowledge`、`goal.max_rounds` 未接线；`policy_field` 可为 list；`to:"*"` 未禁止；`when` 变量未校验 | **部分实现**，且存在静默死条件与潜在崩溃 |
| **C4 降级** | 7 类故障；MVP 3 类（超时/500/schema 错）；trace 有 degradation | 三类均为零实现；无超时/重试/schema 校验；`degraded=not ok` 把 `NOT_FOUND` 也算降级；`StepType.DEGRADATION` 从未产出。文档口径：architecture 319 写“MVP 实现 3 类”，roadmap 63 写“仅定义”，roadmap 46 仍为待办 | **未实现**；文档三处口径冲突 |
| **C5 API** | `POST /chat` 等 5 个端点 + 幂等/错误码 | `src/services/` 仅包骨架；Gradio 本地 UI 可用 | **仅定义**（与 roadmap 一致） |
| **C6 Trace** | step 类型枚举 + 必填字段；模型元信息/token/延迟 | step/guardrail/tool/transition 已落 trace；但 `Trace.model` 未赋值、token=字符数、LLM 延迟固定 50ms；`TraceOutcome` 字段为 `closed/goal_achieved`，文档写 `task_completed` | **部分实现**；元数据与字段名不一致 |
| **C7 安全** | 输入侧标记、决策侧权限、工具侧隔离、输出侧校验；不泄露 system prompt/不输出未授权金额 | 输入侧标记、工具 `[tool_data]` + 2KB 截断 + 剥离指令已实现；输出侧只查禁用话术与减免比例；决策侧依赖 prompt；`policy.forbidden_patterns` 未使用；无 prompt 泄露/金额/期数/延期校验 | **部分实现**；四层防护未闭合 |

### 3.2 其他关键文档声明

| 文档位置 | 声明 | 实际 | 结论 |
|---|---|---|---|
| [README.md#L5](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L5) | “框架代码零业务分支” | `loop.py` 硬编码 `query_receivable/query_policy`、中文意图词；`llm.py` 硬编码 `HT-Y2026-*` 与 380000；UI 硬编码金融政策 | 不成立 |
| [README.md#L90](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L90) | `result.task_completed` | `TurnResult` / `AgentResult` 字段为 `closed`、`goal_achieved` | 不成立；示例会 AttributeError |
| [README.md#L153](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L153) | 新场景第 3 步写 `system.txt` 并生效 | `_build_system_prompt` 不读取该文件 | 不成立 |
| [README.md#L198-L199](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L198-L199) | 重复调用指“一轮内”；真实模型 `diff_statement` 68 次 | 重复统计覆盖整场会话；68 是 case 覆盖数，实际调用 138 次 | 口径错误 |
| [README.md#L44](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L44) | 164 passed | 静态统计 164 个测试函数；本次环境无 pytest，未复跑 | 数量一致，结果未验证 |
| [roadmap.md#L15](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/roadmap.md#L15) | W1 160 passed | 同一文档 L28 与 README 写 164 | 文档内不一致 |
| [roadmap.md#L33](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/roadmap.md#L33) | demo/ui/eval/retrieval/synthesis “改为配置发现” | retrieval 自动发现；eval 用户话术与字段硬编码；synthesis 只发现输出路径；UI 政策卡片硬编码 | 部分成立，整体不成立 |
| [architecture.md#L118](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/architecture.md#L118)、[L186](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/architecture.md#L186) | `promise_recorded` 驱动 CLOSED | 实际事实是 `goal_tool_called` | 文档过时 |
| [architecture.md#L341](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/architecture.md#L341) | `TurnResult.task_completed` | 实际无该字段 | 文档过时 |
| [eval-design.md#L93](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/eval-design.md#L93) | 评测集构成 120 条（标题） | MVP 80 条；文档 L104 已说明 80 条 | 标题未标注版本，易误读 |
| [eval-design.md#L139-L143](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/eval-design.md#L139-L143) | `task_completion_rate`；`metrics` 六项 | 实际 `closed_rate`/`business_goal_rate`/`handled_rate`；8 项 metrics | 字段表已漂移 |
| [prior-art.md#L37](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/prior-art.md#L37) | stage_match + task_completed 已入 v0 报告 | 实际无 task_completed | 不成立 |
| [architecture.md#L248](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/architecture.md#L248) | 链接 `scenario-design.md#4-场景二milktea业务规则注入验证` | 目标标题已是“通用性验证”，旧锚点失效（推断：GitHub 会生成新锚点） | 交叉引用失效 |
| [方案评审与细化.md §2.5](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/docs/方案评审与细化.md) | MVP 实现 3 类故障 | 代码零实现；roadmap 63 写“仅定义” | 文档内部冲突 |

---

## 4. 优秀实践与可复用设计亮点

| 亮点 | 位置 | 价值 |
|---|---|---|
| 工具返回隔离 | [guardrails.py#L71-L81](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/guardrails/guardrails.py#L71-L81)、[loop.py#L384-L386](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L384-L386) | 统一加 `[tool_data]` 定界、2KB 截断、剥离“忽略以上规则”等指令片段，是间接注入防护的正确骨架 |
| 无 `eval/exec` 的条件解析器 | [machine.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/state/machine.py#L1-L83) | 只支持比较、`and/or`、白名单变量与字面量；比动态执行配置字符串安全得多 |
| 启动预检 fail-fast | [config.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/core/config.py#L158-L270) | 校验必填、`impl` 导入、转移合法性、policy 数值范围；坏配置在启动阶段暴露 |
| 统一工具 envelope 与注册 | [registry.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/registry.py#L27-L40)、[registry.py#L153-L170](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/tools/registry.py#L153-L170) | `ok/data/error/meta` 一致；按 YAML 动态加载，场景工具可外置 |
| 工具覆盖维度 | [runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L356-L365) | 自动计算 `untouched_tools`，能发现评测集未触达的注册工具，已满足“评测器覆盖维度”的硬要求 |
| 三独立结果口径 | [loop.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/agent/loop.py#L208-L221)、[README.md#L209](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L209) | `closed`、`goal_achieved`、`escalated` 分开统计，避免把“结案”直接等同“业务目标达成”；报告对“应升级样本”不计入业务目标分母 |
| Mock/真实信号分级 | [runner.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/eval/runner.py#L389-L400) | `link_check` 与 `capability` 分开，README 明确写 Mock 天花板，避免把 100%/35% 当成真实能力 |
| 确定性合成 + content hash | [rule_based.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/synthesis/rule_based.py#L94-L99)、[rule_based.py#L311-L338](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/synthesis/rule_based.py#L311-L338) | 同规则可重建 80 条 case；oracle 随生成产生，降低人工标注成本 |
| 可测试 UI 回调 | [app.py](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/src/ui/app.py#L402-L462) | UI 逻辑抽成模块级函数，测试无需启动 Gradio；`dump_trace` 支持下载 |
| 诚实的报告 caveat | [README.md#L195-L209](file:///home/GUO_Zimeng/coding/learning/multi-turn-task-agent/README.md#L195-L209) | 明确 Mock 天花板、重复调用未扣分、跨模型不可横比、样本量偏小，优于多数“只报最好数字”的项目 |

---

## 5. 修复优先级建议

### P0（在对外宣称“可复现基线/场景通用/安全防护”之前必须修）

| 顺序 | 事项 | 对应问题 | 验收标准 |
|---|---|---|---|
| 1 | 政策单一真源 + `record_promise` 硬校验 + 超政策状态阻断 | S-3 | 改 YAML 政策后工具返回同步；传负数/过去日期/超账面/超期数/他合同必须 `INVALID_ARG`；超政策方案不能到达 CLOSED；有单测与 eval case |
| 2 | 评测器修复：禁用动作可执行判定、参数校验、工具成功性、显式 stage_match | S-5、S-6 | `forbidden_hit` 在反例中为 True；`record_promise` 参数错误会失败；`success` 公式与 README 一致；新增 4 条单测 |
| 3 | 评测数据合法化：日期 ≥ today、付款日 ≤ due+30、金额/期数/减免由 policy 生成 | S-7 | 数据校验器在 `make dataset` 阶段失败即停；extension/installment 核心 case 不再超政策；Mock 使用用户日期 |
| 4 | 系统提示词改为读取 `prompts.system`，删除框架内应收政策文案 | S-2 | 修改 `system.txt` 后 trace 第一条消息变化；milk tea 不再出现分期/延期/减免政策 |
| 5 | 框架业务硬编码清理或明确降级定位 | S-1、W-3 | 任一选择：a) 配置化事实/终态/意图/升级参数，新增场景零 src 改动；b) README/roadmap 明确“仅 receivables 单场景，milk tea 为字段级演示” |

### P1（影响结论可信度或日常使用，1–2 个迭代内修）

| 顺序 | 事项 | 对应问题 |
|---|---|---|
| 6 | C4 三类故障 + `degradation` step + 文档口径统一 | S-4 |
| 7 | ReAct 内层循环（工具结果后继续决策） | S-8 |
| 8 | C6 trace 模型/token/延迟真实化 | W-1 |
| 9 | UI 报告路径、milk tea 无数据集处理、错误友好提示 | W-4 |
| 10 | 配置预检补强（policy 数值类型、`to:"*"`、`when` 白名单、`max_rounds`） | W-6 |
| 11 | 补关键测试：政策绕过、参数错误、C4、C7、新场景契约 | W-9 |
| 12 | 清理文档字段/口径漂移（README `task_completed`、eval 字段表、`promise_recorded`） | W-2 |

### P2（可延后，但不应遗忘）

| 顺序 | 事项 | 对应问题 |
|---|---|---|
| 13 | mock 状态隔离/幂等/真实审计时间/工单号 | W-7 |
| 14 | 重复调用指标按轮次拆分；升级校验工具成功 | W-8 |
| 15 | 知识库 corpus 配置接线与 BM25 schema 校验 | W-5、A-3 |
| 16 | 结构化日志、拆分 `_run_turn`、清理死代码 | W-10、A-2 |
| 17 | 依赖锁定 + CI（ruff/pytest/Mock eval） | W-11 |
| 18 | trace 快照/落盘策略、缓存工具 schema、锚点检查 | A-1、A-4、A-5 |
| 19 | C5 `POST /chat` 等服务化 | 契约 C5（当前仅定义） |

---

## 6. 建议新增的回归测试（按可执行性排序）

1. **政策边界**：`record_promise` 传入 `amount=-1`、`amount=700000`、`pay_date=2026-10-15`（过去）、`contract_no` 不属于当前会话，均应返回 `INVALID_ARG`，且状态不得关闭。
2. **超政策状态**：用户提出“减免 50%”，同时模型调用 `query_policy`；断言拒绝话术出现且状态**不**进入 `final/CONFIRM/CLOSED`。
3. **禁用动作**：构造一个 `record_promise` 金额超政策的 trial；断言 `forbidden_hit=True` 且 `success=False`。
4. **参数正确率**：`expected_args` 中 `query_receivable.contract_no` 与错误合同不匹配时，`args_ok=False`。
5. **C4**：工具超时、HTTP 500、返回 schema 错各一条；断言 `degradation` step 存在、`degraded=True`，且业务 `NOT_FOUND` 不置 `degraded`。
6. **零代码新场景**：新增一个测试场景 yaml，终态叫 `COMPLETED`、目标工具叫 `book_room`、升级参数为 `member_id`；断言无 `src/` 改动即可跑到终态。
7. **Prompt 接线**：修改 `configs/prompts/<scenario>/system.txt` 的哨兵字符串，断言出现在首条 LLM 消息中。
8. **数据合法**：遍历 `cases.jsonl`，断言所有 `pay_date >= today` 且 `pay_date <= due_date + max_extension_days`，所有分期期数 ≤ `max_installments`。
9. **UI 报告**：真实模型报告文件名与 `run_eval` 返回值一致；milk tea 无数据集时按钮禁用/友好提示。
10. **Trace**：真实模型 usage 的 prompt/completion tokens 与 step 记录一致；`Trace.model.model_id` 非空。

---

## 7. 结语

项目在“状态机可读性、trace 结构、评测多口径、Mock/真实分级、数据隔离”上做了不少正确设计，文档也罕见地写明了 Mock 天花板和不可横比约束。这些问题并非“代码写得差”，而是**框架抽象没有跟上业务扩展、评测器判定没有跟上安全契约**。建议优先按 P0 修复 5 项，再考虑对外使用“通用框架 / 四层安全 / C4 已实现”这类表述；在此之前，更准确的定位是：

> “receivables 单场景可运行原型 + 确定性合成评测集 + milk tea 配置/工具外置演示；C4/C5 未实现，C7 部分实现，场景通用性尚未验收。”
