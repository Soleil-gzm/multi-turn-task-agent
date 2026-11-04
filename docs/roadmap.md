# Roadmap

> [返回主页](../README.md) · [架构设计](architecture.md) · [场景设计](scenario-design.md) · [评测设计](eval-design.md)

项目按里程碑推进。本页记录当前状态与范围取舍（按 ROI 判断，而非进度原因）。

## 当前状态

### 已完成

- 场景与架构设计定稿：[场景设计](scenario-design.md) / [架构设计](architecture.md)
- 评测体系设计定稿：[评测设计](eval-design.md)
- 数据集选型定案（三集分工）与工程契约规范：[方案评审与细化](方案评审与细化.md)
- 文档骨架与公开边界划分
- **W1 Walking Skeleton（已跑通）**：`make demo` 走完一次多轮并落 trace，终态 `CLOSED`、任务完成；`make test` 160 passed；`make eval` 产出 v0 报告
- **框架主干**：配置驱动 + 元组状态机（12 条转移、`NEGOTIATE` 4 个子状态）+ 工具注册表 + trace + 启动预检
- **工具层**：6 个业务工具（mock 数据）全部注册，含可返回矛盾数据的 `diff_statement`
- **状态机测试**：12 条转移逐条单测（含两条 `or` 条件分支）
- **轮次语义修正**：`round_no` 改为每轮用户输入 +1（转移不再累加），`round > 5` 升级分支可被真实触发，有 Agent 级单测覆盖
- **核对失败计数**：`verify_failures` 由「客户无法提供核验信息」与「合同号查不到」两类事件累加，`IDENTIFY → ESCALATED` 分支可被真实触发（boundary 终态匹配 50% → 75%）
- **评测路径对齐**：Mock 路由补齐「延期 / 对账争议」关键词，`statement_diff` 模拟用户在核对后按账面口径收敛、不再以拖延触发升级；80 条终态匹配 87.5% → 100%（Mock 链路验证，不代表模型能力）
- **评测集扩量**：80 条（24 core / 16 boundary / 40 adversarial），由 `src/synthesis/rule_based.py` 确定性生成、带真实 `content_hash`；对抗含 8 条 injection（其中 2 条间接注入）
- **升级留痕**：`escalate_to` + `escalate_tool` 配置驱动，进入升级状态自动调用 `escalate`（带合同号与触发条件），oracle 对 ESCALATED 期望该工具
- **矛盾数据告知**：`response.contradiction` 声明工具 / 字段 / 话术，循环内检测到两字段不一致即主动告知；评测新增「矛盾数据告知率」（k=3 池化当前 100%，18/18）
- **超政策拒绝话术**：`response.policy_refusal` 声明抽取正则 / 对应 policy 字段 / 拒绝模板，超限时显式拒绝并给政策内替代；评测新增「超政策拒绝话术率」（k=3 池化当前 100%，30/30），重复超限计入 `policy_violation_attempt` 触发升级
- **评测链路**：80 条 case 可加载、可评测、可出分层报告
- **评测口径拆分（已完成）**：终态不再用单一「完成率」表述，拆为四项互不替代的口径——业务目标达成率（仅应关单样本，要求 `goal.on_success` 工具确实成功调用）/ 处置正确率（含正确升级）/ 正常结案率 / 终态标签匹配率；oracle 新增 `expected_terminal.outcome`，报告新增 `signal.kind`（Mock=link_check、真实模型=capability）。同时修掉 `task_completed = stage == "CLOSED"` 这个把「结案」当「达成」的旧定义
- **W2 数据层**：话术表（39 条，YAML 唯一真源 + `make templates` 生成 xlsx，占位符三方一致校验入测试）+ policy 知识库（24 条片段：公开法规要点 + 虚构内部政策，BM25 检索已接入 `query_policy`）；160 个单测全绿
- **W0 prior-art 对照**：[prior-art.md](prior-art.md)——三份参照（RiSAWOZ / ABCD / τ-bench）职责边界 + 14 条逐项对照清单（7 条 ✅ 已对齐、6 条 ⬜ 待核对带行动项、1 条不适用），含指标可比性边界声明与许可声明
- **人工抽检 20 条**：20/20 通过（D1 20/20、D2 20/20、D3 18/18、D4 20/20）；P-01 ~ P-05 全部修复——P-05 通过 core 新增 `invoice_check` 子类型（4 条）+ Mock 发票路由补齐，报告新增「工具覆盖 6/6」维度防空转工具（见 [抽检记录](../eval/reports/manual_review_20.md)）
- **G3 交互界面（已完成）**：Gradio 单页（对话 + 评测两个 Tab），`Agent.start/session.send` 会话级增量、Trace 面板与 JSON 导出、评测页读取 `v0_baseline.json` 渲染四项终态口径 + 覆盖/矛盾/拒绝指标与 `k` / Pass^k；Gradio 6.31.0 已锁定，含构建与启动的端到端测试（见 [src/ui/app.py](../src/ui/app.py)、[gradio-reference.md](gradio-reference.md)）
- **milktea 第二场景（已实现）**：`configs/scenarios/milktea.yaml` + `scenarios/milktea/`（3 个工具 + 演示 LLM）+ 通用运行器 `scripts/run_scenario.py`；实测 `INIT → GREET → IDENTIFY → OFFER → CONFIRM → CLOSED`，异常分支 `ESCALATED` 可触发，trace schema 与 receivables 一致。4 条硬标准全部通过（含 `grep src/` 零场景名，由单测固化）
- **入口泛化（milktea 标准 2 收口）**：demo / ui / eval / retrieval / synthesis 改为配置发现（`default_scenario_path` / `dataset_dir`）与 `tools[].impl` 动态装配（`build_registry_from_config`）、LLM 工厂（`get_llm_client`）；`grep -R "milktea\|receivables" src/` 为 0，`python -m src.eval.runner` 退出码 0（工具覆盖 6/6）
- **G2 真实模型接入 + k≥3 评测（已跑通）**：`OpenAICompatibleClient`（OpenAI / DeepSeek / Qwen / vLLM 兼容，凭证走 `MTA_LLM_*`，缺配置 fail-fast）、Agent 工具调用消息协议修正（assistant `tool_calls` → `tool`）、runner 支持 `--k`（默认 3）与 `--limit`（冒烟）、`Pass^k` / `Pass@1` 与 mean ± std；Mock k=3 报告 Pass^3 100%、240 次试验；真实模型 `deepseek-flash` k=1 core 16 条冒烟：终态匹配 81.2%、工具覆盖 6/6、矛盾告知 3/3（见 `eval/reports/v1_smoke_k1.json`）

### 待办任务清单

按优先级（P0 本周 / P1 MVP 收口 / P2 Stretch）排序；工作量为相对量级（S≈半天内 / M≈1–3 天 / L≈3 天以上）。「依赖」列出必须先完成的前置任务编号。

| # | 优先级 | 工作量 | 依赖 | 目标 | 实现路径 | 验收标准 |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | P0 | M | — | 产出真实模型正式基线 `v1_real_k3`（80 条 × k=3） | 后台执行 `python -m src.eval.runner --model openai_compat --k 3 --report eval/reports/v1_real_k3.json`，完成后把指标写入 README 评测表与 UI 可读报告 | 报告生成且字段完整（含 `model_meta` / `signal.kind=capability` / `pass_power_k` / 四项终态口径）；README 与本文数字同步；`pytest` 仍全绿 |
| T2 | P0 | S | — | 修复冒烟暴露的模型侧行为问题 | 在 `loop.py` 系统提示中强化两条：① 客户声明以账面为准后不得再以账实差异为由拖延或升级；② 涉及政策类工具前先完成身份核验（`query_receivable` 在前） | core 16 条 k=1 冒烟终态匹配 ≥ 15/16；`record_promise` 早于 `query_policy` 的顺序违规为 0；无新增回归（Mock k=3 仍 Pass^3 100%） |
| T3 | P1 | L | T1 | C5 服务化：`POST /chat`、`GET /sessions/{id}`、`GET /trace/{trace_id}`、`POST /eval`、`GET /eval/{task_id}`，SQLite 持久化 + 幂等 + `docker compose up` | 在 `src/services/` 实现 FastAPI 路由（复用 `Agent.start/session.send` 与 `eval.runner.main`），会话与 trace 落 SQLite，`POST /chat` 幂等键去重 | 换机器一条命令（`docker compose up`）起服务并完成一次多轮对话；`POST /eval` 异步执行可查状态；契约状态 C5 升为「部分实现」 |
| T4 | P1 | M | T1、T2 | 真实模型对抗层验证：40 条 adversarial 产出注入拦截率 | runner 增加注入拦截判定（模型未执行越权指令且未泄露政策外信息），对 injection / indirect_injection 标签用例统计 | 报告含 `injection_block_rate`（8 条注入样本，含 2 条间接）；README 评测表「注入拦截率」由「未采集」变为真实数字 |
| T5 | P2 | M | T3 | C4 故障注入 MVP 3 类：工具超时 / 500 / schema 错 | 工具注册表增加可配置故障注入开关（按 meta 或环境变量触发），循环内按契约走降级/重试路径，trace 落 `degradation` 步 | 3 类各有 1 个评测用例且通过；trace 中可见 `degradation` 标记；契约状态 C4 升为「部分实现」 |
| T6 | P2 | M | T4 | C7 决策侧护栏补强：注入样本的模型侧违规统计与输出侧校验扩展 | 评测报告聚合「模型是否执行越权指令」；输出侧对编造单据号/金额的检测从 Mock 扩展到真实模型轨迹审计 | 报告含决策侧违规率；对抗层 Pass^k 不随护栏增强而异常下降（区分「正确拒绝」与「护栏误伤」） |
| T7 | P2 | S | T1 | 工具 mock 数据扩展：完整企业集合、金额分布、30% 脏数据 | 扩充 `src/tools/receivables/tools.py` 的 mock 单据（合同 / 发票 / 对账单 ≥ 30 个合同），按 scenario-design §3 的金额分布与脏数据比例生成 | 评测集抽样覆盖的合同在 mock 中均可查；脏数据用例在 boundary 层可复现 |
| T8 | P2 | L | T3 | RiSAWOZ 公开集接入与评测器校准（Stretch 预算 3–4 天，含降级预案） | `src/adapters/` 实现适配器 + 口径映射；第 3 天未跑通则降级为口径对照文档 | 端到端可评测，或产出字段映射表 + 粒度差异清单 + 无法映射项 |
| T9 | P2 | M | T4 | 弱模型对照报告：本地小模型（vLLM/Ollama）跑同套 80 条 | 用 `--model openai_compat` 指向本地端点，对比 Pass^k / 工具均分 | 产出对照表，证明评测集有区分度（弱模型显著低于 deepseek-flash） |
| T10 | P2 | M | T1 | LLM Judge + 轨迹审计：30 条校准集抽样复判 | 新增 `eval/graders/`：Judge prompt + 与规则评测器的一致性统计 | Judge 与规则评测器在 30 条校准集上的一致率 ≥ 80%，差异项有归因说明 |
| T11 | P2 | L | — | 完整合成管线（5 阶段 + 概率矩阵 + 300→120 筛选），评测集扩到 120 条 | 扩展 `src/synthesis/`，保持「合成即 oracle」与 content_hash 可重建 | 120 条（核心/边界按比例补齐，对抗 40 不变）；生成器确定性单测通过；Mock k=3 报告无回归 |

完成任一任务后同步更新：本表状态 → 契约状态表 → README 对应数字（保持「文档不先行」机制）。

### 契约状态（诚实反映代码进度，每个里程碑更新）

| 契约 | 内容 | 状态 | 验证方式 | 当前实现边界 |
| --- | --- | --- | --- | --- |
| C1 数据 | case / oracle schema + 三个判定边界 | 部分实现 | pydantic 可加载 + 单测 | schema、三个判定边界、80 条 case 与真实 `content_hash` 已落地并有单测；人工抽检 20 条已完成（20/20）；公开集校准未做 |
| C2 工具 | 统一返回 `{ok,data,error,meta}` + 入参 JSON Schema | 部分实现 | 80 条 case 能跑通 6 个工具 | 返回封装、注册表、6 个工具与 schema 一致性单测已落地；`query_policy` 已接知识库 BM25 检索，其余工具为 mock 数据 |
| C3 配置 | scenario.yaml schema + 启动预检 | 部分实现 | 坏配置能 fail-fast | 必填字段 / `impl` 可导入 / 转移合法性 / policy 数值四项预检已落地并有单测；「无孤立状态」为简化判定 |
| C4 降级 | 7 类故障恢复路径（MVP 3 类） | 仅定义 | 3 类各有 1 用例 + trace 有 `degradation` | trace 有 `degraded` 标记，矛盾告知与超政策拒绝话术已实现（配置驱动）；重试 / 降级逻辑未实现 |
| C5 API | `POST /chat`、`GET /sessions/{id}`、`GET /trace/{trace_id}`、`POST /eval`、`GET /eval/{task_id}` + 幂等与错误码 | 仅定义 | 一条命令起服务并完成一次对话 | `src/services/` 仅有包骨架，无路由 |
| C6 Trace | step 类型枚举 + 必填字段 | 部分实现 | 一次对话产出可解析 trace | 模型 + Builder 已落地，demo / eval 产出 trace 并落盘；`/trace` 查询接口未实现 |
| C7 安全 | 四层注入防护 | 部分实现 | 40 条对抗中 8 条 injection（含 ≥2 条间接注入）被拦截 | 输入侧模式检测、工具侧定界 / 截断 / 剥离、输出侧禁止话术与超比例校验已接入循环，剥离逻辑有单测；决策侧靠 prompt 约束；拦截率尚未产出（Mock） |

> 状态只有三档：**仅定义 / 部分实现 / 已实现**。文档不得使用「已完成」描述未通过验证的契约；契约状态与代码进度的偏差每周对齐一次。这也是防止「文档先行」变成「文档负债」的机制——文档越完整，越要标明哪部分还没落地。

### MVP（必做，承诺交付）

| 项 | 内容 | 验收锚点 | 状态 |
| --- | --- | --- | --- |
| Walking Skeleton | 2 状态（`IDENTIFY → NEGOTIATE`）+ 3 工具（查账款 / 查政策 / 记承诺）+ 10 条 case，端到端一条命令跑通 | `make demo` 走完一次多轮并落 trace；先跑通再扩充，不追求契约全覆盖 | 已完成（`make demo` 已验证，当前已扩到 12 转移 / 6 工具） |
| 框架主干 | 配置驱动 + 元组状态机 + 工具注册表 + trace + 启动预检 | 两场景零代码改动可跑（C3/C6 生效） | 已完成（组件均落地；milktea 场景验证同引擎可跑） |
| 第二场景 yaml | milktea：仅 yaml + 数据即可跑通 | 通用性验证 4 条硬标准 | 已完成（4/4 条通过：零框架改动、grep src/ 零场景名、同引擎、差异可列举） |
| 场景数据层 | receivables 话术表 + 50–80 条案例（规则化生成，含 oracle）+ policy 知识库 | 占位符三方一致校验通过 | 已完成（80 条 case 含 oracle 与 content_hash；39 条话术表 + 24 条知识库片段，占位符三方一致校验入测试） |
| 状态机与工具层 | 12 条转移 + 6 个业务工具（mock 单据） | 每条转移 1 个单测 | 已完成（12 条转移逐条单测，6 工具已实现） |
| 服务化 | FastAPI `/chat` + SQLite + `docker compose up` | 换机器一条命令起来 | 未开始 |
| 评测落地 | 规则评测器 + 50–80 条评测集 + v0 基线报告 + 人工抽检 20 条 + 对抗 40 条（含 ≥2 条间接注入） | `make eval` 一条命令出报告 | 部分完成（评测器与报告链路已通；评测集 80 条含对抗 40 条与 2 条间接注入；runner 支持 k 次重复与 Pass^k；当前 v0 为 Mock k=3：终态匹配 100.0% ± 0.0%、完成率 35.0% ± 0.0%、工具均分 0.80、Pass^3 100%（80/80）、矛盾告知率 100%（18/18）、超政策拒绝话术率 100%（30/30）；人工抽检 20 条 20/20 通过；真实模型已完成 core 16 条 k=1 冒烟（81.2%），80 条 k=3 正式基线见 T1） |
| 文档 | prior-art 对照（只读，不接入数据） | 对照清单可逐项勾选 | 已完成（[prior-art.md](prior-art.md)：7 条已对齐 / 6 条待核对 / 1 条不适用） |

> MVP 的判定标准是「完整可讲」：有框架、有业务场景、有评测数字、有服务化入口。**先保证闭环，再叠加亮点。**

### Stretch（有余力再做，不承诺日期）

> 可执行化拆解（依赖与验收标准）统一收敛到上方「待办任务清单」T5–T11，本表保留范围取舍的原始理由。

| 项 | 内容 | 为什么可以后置 |
| --- | --- | --- |
| RiSAWOZ 实际接入 | 3–4 天：适配器 + 口径映射 + 评测器校准 | 只影响「外部校准」这一条证据链，不影响主闭环；MVP 已由 prior-art 对照 + 人工抽检提供可信度 |
| 完整合成管线 | 5 阶段 + 概率矩阵 + 300→120 筛选 | 最耗时的一块；MVP 用规则化生成 80 条已能支撑评测与演示 |
| LLM Judge + 轨迹审计 | 30 条校准集、抽样复判 | 规则评测器已覆盖主要维度，Judge 是补充口径 |
| 弱模型对照报告 | 换本地小模型跑同套测试集 | 区分度验证，需 GPU 环境 |
| MCP 暴露 / Gradio 演示 | 2 个读类工具 + 对话与 trace 展示 | 展示层，不改变能力边界 |
| 故障注入规划中 3 类 | 矛盾数据 / 非法 JSON / 上下文超限 | 契约已定义，实现可按 ROI 排后 |

> **RiSAWOZ 接入预算与降级分支**（仅在进入 Stretch 时生效）：预算 3–4 天（下载与 split 冻结 0.5 + 适配器 1.5 + 口径映射 1 + 评测器校准 0.5–1）。若第 3 天仍未跑通端到端，**降级为只做口径对照文档**（字段映射表 + 粒度差异清单 + 无法映射项及原因），不承诺实际接入；框架通用性验证由 milktea 承担（4 条硬标准），评测器可信度由人工抽检承担。降级不视为失败，而是有预案的范围收敛。

## 明确不做

| 不做 | 判断依据 |
| --- | --- |
| Postgres | 无多租户需求，SQLite 足够；引入只会扩大故障面 |
| 异步任务队列 | 单轮响应在秒级，队列解决的是不存在的瓶颈 |
| Redis（首版） | SQLite 已能演示持久化恢复，Redis 无差异化价值 |
| 其余 3 类故障注入（矛盾数据 / 非法 JSON / 上下文超限） | 契约已定义 7 类恢复路径，MVP 先实现 3 类（超时 / 500 / schema 错），这 3 类覆盖主要降级路径，按 ROI 后排期 |
| MCP 暴露全部工具 | MCP 面向外部客户端，暴露写类工具有越权风险，只暴露 2 个读类 |
| 23 项指标全埋点 | ROI 低于评测集质量 |
| Streamlit | Gradio 已具备多轮组件，换栈无收益 |
| 120 场对抗 | 40 条对抗 case 已足够暴露边界 |
| 独立 L5 回归层 | 回归是机制而非评测对象，并入 R1（见 [评测设计 · 回归机制](eval-design.md#2-回归机制r1单列不并入评测对象)） |
| 模型微调 | 不在本阶段范围，避免分散精力 |
