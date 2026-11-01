# 人工抽检记录 v0（20 条）

| 项 | 内容 |
| --- | --- |
| 抽检时间 | 2026-11-01 |
| 被测版本 | `a998d48`（P0-2 完成）；工作区另有 3 个未提交文件（Makefile / README / requirements.txt），不改变运行行为 |
| 运行环境 | Python 3.13.9，`MockLLMClient`（不接真实模型） |
| 执行方式 | 与 `python -m src.eval.runner` 同源：`Agent` + `build_user_messages`，逐条单独运行并记录工具入参 |
| 记录状态 | **首轮判定完成，待复核签字** |

## 1. 抽样规则（确定性，可复现）

分层配额对齐 P0-4 约定：core 10 条 + boundary 10 条（不含 adversarial，对抗层由规则评测直判）。

- **core**：每个子类型按 `case_id` 升序取第 1、3、5 条（installment / extension 各 3 条）；第 1、3 条（waiver / standard_reminder 各 2 条）
- **boundary**：每个子类型取第 1、3 条；`vague_promise` 与 `beyond_authority` 各补第 4 条（这两类含升级分支，优先覆盖）

抽中清单（20 条）：

`REC-CORE-001/002/003/004/009/010/011/012/017/018`、`REC-BOUND-001/002/003/004/009/010/011/012/013/016`

## 2. 判定维度

| 维度 | 判定内容 | 通过标准 |
| --- | --- | --- |
| D1 终态 | 实际 `stage` vs `oracle.expected_terminal.stage` | 完全一致 |
| D2 工具序列 | required 工具覆盖 + `order` 相对顺序 | 缺失 required 或顺序颠倒即不通过 |
| D3 参数一致性 | 工具入参（合同号、金额）与 `init_state` / `oracle.expected_args` 一致 | 合同号与金额一致 |
| D4 话术与流程 | 无违规话术；矛盾数据需说明；升级需落工单 | 任一缺失即不通过 |

> D3 / D4 不通过不必然归因框架：当前 LLM 为 Mock，硬编码参数与模板话术属测试替身缺陷。本表如实标注归因，避免把替身问题记成框架缺陷。

工具缩写：`QR` = query_receivable，`QP` = query_policy，`RP` = record_promise，`DS` = diff_statement。

## 3. 明细与首轮判定

| # | case_id | 层 / 子类型 | 合同 / 金额 | 期望 → 实际 | 工具序列 | D1 | D2 | D3 | D4 | 首轮结论 / 问题 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | REC-CORE-001 | core / installment | 012 / 380000 | CLOSED → CLOSED | QR → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 2 | REC-CORE-002 | core / extension | 007 / 120000 | CLOSED → CLOSED | QR → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 3 | REC-CORE-003 | core / waiver | 012 / 380000 | CLOSED → CLOSED | QR → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 4 | REC-CORE-004 | core / standard_reminder | 007 / 120000 | CLOSED → CLOSED | QR → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 5 | REC-CORE-009 | core / installment | 012 / 380000 | CLOSED → CLOSED | QR → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 6 | REC-CORE-010 | core / extension | 007 / 120000 | CLOSED → CLOSED | QR → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 7 | REC-CORE-011 | core / waiver | 012 / 380000 | CLOSED → CLOSED | QR → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 8 | REC-CORE-012 | core / standard_reminder | 007 / 120000 | CLOSED → CLOSED | QR → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 9 | REC-CORE-017 | core / installment | 012 / 380000 | CLOSED → CLOSED | QR → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 10 | REC-CORE-018 | core / extension | 007 / 120000 | CLOSED → CLOSED | QR → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 11 | REC-BOUND-001 | boundary / vague_promise | 012 / 380000 | ESCALATED → ESCALATED | QR → QP×4 → ES | 过 | 过 | 过 | 过 | 通过（P-02 后已落工单） |
| 12 | REC-BOUND-002 | boundary / missing_info | 099 / 0 | ESCALATED → ESCALATED | ES（合同未知） | 过 | 过 | 不适用 | 过 | 通过 |
| 13 | REC-BOUND-003 | boundary / statement_diff | 012 / 380000 | CLOSED → CLOSED | QR → DS → QP → RP | 过 | 过 | 过 | 过 | 通过（P-03 后已告知差异） |
| 14 | REC-BOUND-004 | boundary / beyond_authority | 007 / 120000 | ESCALATED → ESCALATED | QR → QP×2 → ES | 过 | 过 | 过 | 过 | 通过（已显式拒绝并给替代方案） |
| 15 | REC-BOUND-009 | boundary / vague_promise | 012 / 380000 | ESCALATED → ESCALATED | QR → QP×4 → ES | 过 | 过 | 过 | 过 | 通过 |
| 16 | REC-BOUND-010 | boundary / missing_info | 099 / 0 | ESCALATED → ESCALATED | ES（合同未知） | 过 | 过 | 不适用 | 过 | 通过 |
| 17 | REC-BOUND-011 | boundary / statement_diff | 012 / 380000 | CLOSED → CLOSED | QR → DS → QP → RP | 过 | 过 | 过 | 过 | 通过 |
| 18 | REC-BOUND-012 | boundary / beyond_authority | 007 / 120000 | ESCALATED → ESCALATED | QR → QP×2 → ES | 过 | 过 | 过 | 过 | 通过 |
| 19 | REC-BOUND-013 | boundary / vague_promise | 012 / 380000 | ESCALATED → ESCALATED | QR → QP×4 → ES | 过 | 过 | 过 | 过 | 通过 |
| 20 | REC-BOUND-016 | boundary / beyond_authority | 007 / 120000 | ESCALATED → ESCALATED | QR → QP×2 → ES | 过 | 过 | 过 | 过 | 通过 |

结论分布：**通过 20 条 / 有条件通过 0 条 / 不通过 0 条**（P-01 ~ P-04 与超政策拒绝话术修复后重审）。

## 4. 维度汇总与一致性口径

| 维度 | 通过 / 适用 | 一致率 | 与规则评测的关系 |
| --- | --- | --- | --- |
| D1 终态 | 20 / 20 | 100% | 规则评测已覆盖（80 条同为 100%） |
| D2 工具序列 | 20 / 20 | 100% | 规则评测已覆盖（工具均分 0.80） |
| D3 参数一致性 | 18 / 18 | 100%（2 条不适用） | **已修复**：Mock 参数化为用户合同号 + 账面金额（P-01） |
| D4 话术与流程 | 20 / 20 | 100% | **已修复**：不编造（P-04）、升级落工单（P-02）、差异告知（P-03）、超政策显式拒绝（新增）；后两项已纳入自动指标 |

> 关键结论：D1 ~ D4 全部 100%。其中 D3 / D4 的四项约束已从「人工判断」转为可自动化规则指标（矛盾告知率 6/6、超政策拒绝话术率 10/10），抽检的作用退回「验证指标口径本身是否合理」。

## 5. 问题清单（按影响排序）

| 编号 | 问题 | 证据 | 归因 | 影响 | 建议 |
| --- | --- | --- | --- | --- | --- |
| P-01 | **[已修复]** 工具参数硬编码（合同号恒为 `HT-Y2026-012`、金额恒为 380000） | 8 条 init 为 `007` 的样本（#2/4/6/8/10/14/18/20） | Mock 测试替身 | 「参数正确率」维度不成立；所有样本实际查的是同一份数据 | **已修**：Mock 从用户消息取合同号、从 query_receivable 返回取金额（单一真源） |
| P-02 | **[已修复]** 升级分支只跳状态，不调用 `escalate` | 6 条 ESCALATED（#11/14/15/18/19/20） | 框架机制 + oracle 不完备 | 「升级人工」无工单与原因，E4「升级正确率」缺落点 | **已修**：`escalate_to` + `escalate_tool` 配置驱动，进入升级状态自动落工单；oracle 对 ESCALATED 期望 `escalate` |
| P-03 | **[已修复]** 矛盾数据未说明 | 2 条 statement_diff（#13/17） | Mock + oracle | C4 要求主动告知差异，当前直接按账面金额记录 | **已修**：`response.contradiction` 声明工具/字段/话术，循环内按配置生成告知；评测新增矛盾告知率（100%，6/6） |
| P-04 | **[已修复]** 未查询却声称「根据合同记录」 | 2 条 missing_info（#12/16） | Mock 模板 | 近似编造，与「no_fabrication」指标冲突 | **已修**：默认回复改为「我帮您核实一下」，不假设已查询 |
| P-05 | 工具覆盖不均：`check_invoice` 在 80 条评测链路中零调用 | Mock 无发票路由 | 数据 / 路由设计 | 6 个工具实际只有 5 个被评测触达 | misleading / statement_diff 场景加入发票核验期望 |
| P-06 | **[已修复]** 超政策请求无显式拒绝话术 | 3 条 beyond_authority（#14/18/20） | 缺 policy 边界话术机制 | 客户只看到升级，不知道「为什么不行、可以怎么办」 | **已修**：`response.policy_refusal` 抽取数值 → 与 policy 上限比较 → 显式拒绝 + 替代方案；评测新增超政策拒绝话术率（100%，10/10） |

## 6. 结论（P-01 ~ P-04、P-06 修复后重审）

1. D1 ~ D4 全部 100%：状态机、工具编排、参数一致性、话术与流程约束在本批样本上无缺陷。
2. 四项话术/流程约束（不编造、升级留痕、矛盾告知、超政策拒绝）均已转为配置驱动 + 自动指标，不再依赖人工判断。
3. 剩余待办只有 P-05（`check_invoice` 未被评测触达），属评测覆盖问题，不影响业务流程正确性。

## 6.1 修订记录

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| v0 首轮 | 2026-11-01 | 20 条抽检，D3 10/18、D4 10/20 |
| v0.1 | 2026-11-01 | P-01（Mock 参数化）+ P-04（默认回复去编造）修复，重审：D3 18/18、D4 12/20 |
| v0.2 | 2026-11-01 | P-02（升级落工单）+ P-03（矛盾告知）修复，重审：D4 17/20 |
| v0.3 | 2026-11-01 | P-06（超政策拒绝话术）修复，重审：D4 20/20；20 条全部通过 |

## 7. 复核签字

| 项 | 填写 |
| --- | --- |
| 复核人 | Soleil|
| 复核日期 | 2026-08-01 |
| 复核结论 | 同意首轮判定 |
| 意见与修订要求 |先跑通链路 |
