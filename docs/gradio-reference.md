# Gradio 界面参考分析：dialog-sft-forge

> 参考项目：`E:\Coding_python\dialog-sft-forge`（离线多轮对话合成 / SFT 数据）
> 目标项目：`multi-turn-task-agent`（在线多轮任务型对话 Agent）
> 用途：为 G3 阶段补齐交互界面提供可复用实现与不兼容清单，**不修改参考项目**。

## 0. 结论摘要

| 分级 | 内容 | 处置 |
| --- | --- | --- |
| **A 可直接复用**（改 import 与文案即可） | 主题工厂、CSS 设计系统、Banner/导航、HTML 渲染器（热力图/信息卡/统计条）、`gr.update` + 元组返回 + 异常兜底、`gr.State` 传大数据、`DownloadButton` + tempfile、`gr.Progress` | 复制到目标项目 `src/ui/` 并适配命名 |
| **B 需改造** | 静态 Chatbot 展示 → 在线多轮对话；布局控制区语义（生成数/种子 → 会话/场景）；配置预览；`launch()` 参数；import 期自动生成数据 | 保留结构、替换数据源与事件语义 |
| **C 完全不兼容** | `run_generation` 全链路（Excel 话术 + 概率矩阵 + FSM 路径生成）、`ReActAgent.run`、`ToolRegistry`、`ShortTermMemory`、`AgentConfig` | 不移植；仅作为 Stretch 合成管线与历史实现参考 |

**关键判断**：参考项目**没有可交互的聊天界面**。`gr.Chatbot` 仅用于回放已生成的静态对话（[demo/app.py#L597-L600](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L597-L600)），无 `ChatInterface`、无 `submit`/`stream` 事件。因此"对话 UI"部分属于**需重新实现**，而非复用。

## 1. 环境核验（conda: multi-turn-task-agent）

| 项 | 实测 |
| --- | --- |
| 解释器 | `E:\Anaconda_envs\envs\multi-turn-task-agent\python.exe` |
| Python | **3.11.18**（conda-forge） |
| 已装（本项目必需） | pydantic 2.14.2 / PyYAML 6.0.3 / pytest 9.2.0 / httpx 0.28.1 / fastapi 0.162.0 / uvicorn 0.54.0 / jieba 0.42.1 |
| 已装（顺带可用） | openpyxl、**gradio 6.31.0** |
| **缺失** | **pandas、numpy、tqdm**（参考项目合成链路需要，本项目不需要） |
| 测试基线 | `pytest tests/ -q` → 92 passed |

requirements 要求 `pydantic>=2.0 / pyyaml>=6.0 / pytest>=8.0 / httpx>=0.27 / fastapi>=0.110 / uvicorn>=0.27 / jieba>=0.42`，**全部满足**。

## 2. 参考项目整体结构（与本任务相关的部分）

```
dialog-sft-forge/
├── app.py                      # HF Spaces 入口，转发 demo.app.launch_demo
├── demo/
│   ├── app.py                  # 唯一的 Gradio 界面（展示型）
│   ├── config/demo_config.yaml # 场景配置（模块/A集/B集/终止/促销/再见）
│   └── data/{prob.xlsx, 话术模板.xlsx, case/}
├── agent/                      # 独立 ReAct Agent（CLI 入口 run_agent.py）
│   ├── core/{react_loop,llm_client,tool_registry,memory,config}.py
│   ├── rag/                    # BM25 / embedding / hybrid / vector_store
│   └── tools/{business_tools,knowledge_tools}.py
└── scripts_general/            # 离线合成管线（path/Speech/dedup/pressure）
```

关键事实：**UI 与 Agent 是两条互不相连的链路**。`demo/app.py` 调用的是 `scripts_general` 的合成管线（[demo/app.py#L262-L337](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L262-L337)），而 `agent/` 只有 CLI（[run_agent.py](file:///E:/Coding_python/dialog-sft-forge/agent/run_agent.py)）。即：参考项目里没有"UI → Agent"的接线可抄。

## 3. Gradio 界面实现详解

### 3.1 入口与主题

- Spaces 入口仅一行转发：`from demo.app import launch_demo`（[app.py](file:///E:/Coding_python/dialog-sft-forge/app.py)）
- `demo` 在模块级构建（`demo = create_demo()`，[demo/app.py#L641](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L641)），`launch_demo()` 统一注入主题与 CSS（[#L644-L646](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L644-L646)）
- 主题：自定义 `gr.themes.Color` 覆盖整条绿色色阶，解决内置 `green` 与自定义 CSS 色差；并覆盖 hover 色（[#L79-L108](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L79-L108)）

```python
milk_green = gr.themes.Color(name="milk_green", c50="#F2F8F5", ..., c500="#2E7D5B", ...)
return gr.themes.Soft(primary_hue=milk_green, secondary_hue="orange", neutral_hue="stone",
                      font=[gr.themes.GoogleFont("Noto Sans SC"), "ui-sans-serif", "system-ui"],
              ).set(button_primary_background_fill_hover="*primary_600")
```

```python
def launch_demo(**kwargs):
    demo.launch(theme=create_theme(), css=CSS, **kwargs)
```

> 复用价值：`theme` 与 `CSS` 是纯展示资产，与业务无关，可整体搬走。

### 3.2 组件树

| 区域 | 组件 | 关键配置 |
| --- | --- | --- |
| 顶部 | `gr.HTML` ×2 | Banner + 导航条（纯字符串 HTML） |
| 说明 | `gr.Markdown` | 项目一句话介绍 |
| 配置预览 | `gr.Accordion(open=False)` + `Row` + 2×`Column(scale=1/2)` | 左：模块/A集/B集/终止/自循环；右：概率矩阵热力图 HTML |
| 控制区 | `gr.Slider(1..20, value=5, step=1)`、`gr.Number(value=42)`、`gr.Button(variant="primary")` | 生成数 + 随机种子 + 生成按钮 |
| 结果区 | `gr.Row(visible=False)`（生成后才显示） | 初始隐藏，回调里 `gr.update(visible=True)` |
| 结果-左 | `gr.Code(lines=18, wrap_lines=True)`、`gr.Radio`、`gr.DownloadButton`、`gr.HTML` | 路径列表 / 对话选择器 / JSON 下载 / 统计条 |
| 结果-右 | `gr.HTML` + `gr.Chatbot(height=520)` | 会员档案卡 + 对话回放 |
| 状态 | `gr.State()` | 存整包 dialogues JSON 字符串 |

源码位置：[create_demo #L527-L634](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L527-L634)。

### 3.3 事件绑定（3 条）

| 事件 | 处理函数 | inputs | outputs |
| --- | --- | --- | --- |
| `generate_btn.click` | `on_generate` | `[num_slider, seed_input]` | `[chatbot, dialogue_state, paths_display, dialogue_selector, result_row, case_card, stats_html]`（7 个） |
| `dialogue_selector.change` | `on_select_dialogue` | `[dialogue_selector, dialogue_state]` | `[chatbot, case_card]`（2 个） |
| `download_btn.click` | `_make_json_file` | `[dialogue_state]` | `[download_btn]`（1 个） |

核心约定（值得照搬）：

1. **返回值是元组**，长度与 `outputs` 严格一致；新增输出必须同步两处。
2. **局部更新用 `gr.update(...)`**，而不是重建组件。
3. **异常分支也返回完整元组**，保证 UI 不崩（[#L459-L465](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L459-L465)）：

```python
except Exception as e:
    logger.error(f"生成失败: {e}", exc_info=True)
    empty_update = gr.update(choices=[], value=None)
    return ([], json.dumps({"error": str(e)}), f"生成失败: {e}",
            empty_update, gr.update(visible=False), "", "")   # 7 项，与 outputs 对齐
```

4. **重计算只在按钮点击时发生**；选择器只做"从 State 里取数渲染"，不重跑生成（[#L495-L512](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L495-L512)）。
5. `gr.Progress()` 作为默认参数注入，回调内 `progress(0.15, desc=...)`。

### 3.4 场景处理流程（离线链路）

```
ensure_demo_data()        # 模块导入时检查 demo/data → 缺失则生成（import 副作用）
      ↓
load_config(demo_config.yaml)
      ↓
load_prob_matrix(prob.xlsx) → modules
      ↓
sync_config_from_prob(config, modules)   # 模块清单以 prob 表为准
      ↓
RandomService(seed) → load_sheets(话术模板.xlsx) → PressureManager(促销话术)
      ↓
PathGenerator.generate(num, seed)        # FSM 随机路径（A集/B集/终止/自循环）
      ↓
DialogueBuilder.build(path, case, prompt) # 继承链 + 占位符 + 促销插入 + 再见终止
      ↓
dialogues[] → Chatbot / Code / Radio / 统计 / 下载
```

对应实现：[run_generation #L262-L337](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L262-L337)。

### 3.5 渲染辅助（可直接复用）

| 函数 | 作用 | 复用方式 |
| --- | --- | --- |
| `_prob_heatmap_html` [#L361-L388](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L361-L388) | 矩阵 → 带透明度色阶的 HTML 表格 | 换数据源即可渲染「stage 转移矩阵」「分层命中率矩阵」 |
| `_case_card_html` [#L391-L408](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L391-L408) | 字段字典 → 卡片（CSS Grid，自适应列） | 渲染 case 的 init_state / 合同信息 |
| `_stats_html` [#L430-L446](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L430-L446) | 指标条（标签 + 数值） | 渲染终态匹配率 / 完成率 / 工具均分 |
| `_dialogue_to_chatbot` [#L411-L427](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L411-L427) | 消息 → Chatbot messages，system 加 📋 标记 | 思路可用，但只支持静态回放（见 B1） |

## 4. 与本项目的架构差异

| 维度 | dialog-sft-forge | multi-turn-task-agent |
| --- | --- | --- |
| 定位 | 离线 SFT 对话**合成** + 数据展示 | 在线多轮**任务型 Agent** |
| 驱动 | Excel 话术 + 概率转移矩阵 | YAML 场景 + 元组状态机 |
| 状态模型 | 无对话状态；FSM 仅用于**生成路径** | `(stage, round_no, offer_state)`，12 条转移 |
| 会话 | 生成后静态回放，无逐轮交互 | 逐轮在线，`next_turn` 计数、终态收敛 |
| 工具返回 | 字符串 observation（`json.dumps`） | C2 封装 `{ok, data, error{code,msg,retryable}, meta}` |
| 配置 | `AgentConfig`（dict + 点号访问，无校验） | pydantic 模型 + 启动预检 fail-fast |
| 可观测 | `trace_enabled`（生成阶段日志） | C6 Trace：每步 `llm_call/tool_call/guardrail/state_transition` |
| 评测 | 无 | 80 条分层集 + 6 项指标 + v0 报告 |
| 持久化 | 输出 JSON/Excel 文件 | 内存（W3 计划 SQLite） |
| UI | Gradio 展示页（唯一） | 未实现（C5 仅契约） |

**结论**：两者共享"多轮对话 + 虚构业务数据"的叙事，但**数据流方向相反**——参考项目是"配置 → 批量生成 → 展示"，本项目是"配置 → 在线逐轮决策 → 评测"。UI 层可以借鉴视觉与交互模式，业务层不可移植。

## 5. 依赖与兼容性

| 包 | 参考项目 | 当前环境 | 判断 |
| --- | --- | --- | --- |
| gradio | `gradio`（**未锁版本**） | **6.31.0（已安装并锁定）** | 本项目 `requirements.txt` 固定该版本；Gradio 6 的 `Chatbot` 不接受 `type` 参数（默认即 messages 格式） |
| pandas | 有 | 未安装 | 仅参考项目合成链路用；本项目 UI 不需要，**不建议引入** |
| numpy | 有 | 未安装 | 同上，不建议引入 |
| tqdm | 有 | 未安装 | 批量生成用；本项目不需要 |
| openpyxl | 有 | **已安装** | 未来 `make templates` 生成 xlsx 已够用 |
| pyyaml / pytest | 有 | 有（6.0.3 / 9.2.0） | 兼容 |
| fastapi / uvicorn / httpx / pydantic | 参考项目不直接依赖 | 已装（0.162.0 / 0.54.0 / 0.28.1 / 2.14.2） | Gradio 会间接依赖 fastapi/uvicorn；**当前版本较新，需实测 Gradio 是否接受** |

**冲突点（按风险排序）**

1. **Gradio 未锁版本**：不同大版本的 `Chatbot` 数据格式差异大（tuples vs `{"role","content"}`），参考代码用的是 dict 形式，须按目标版本确认。
2. **Gradio 会拉取/约束 fastapi、uvicorn、pydantic、huggingface-hub、pillow 等**；当前环境 fastapi 0.162.0 / pydantic 2.14.2 较新，存在被降级或要求 `<某版本` 的可能 → 安装后必须重跑 `pytest` 确认 92 passed 不被破坏。
3. **Python 版本**：参考项目 `requires-python >=3.9`，当前 3.11.18，兼容。
4. **无 pandas/numpy 依赖**：这是优势，不要把参考项目的合成依赖带进来。

## 6. 复用分级清单（含代码位置）

### A. 可直接复用

| 编号 | 内容 | 位置 | 复用方式 |
| --- | --- | --- | --- |
| A1 | 主题工厂 `create_theme` | [#L79-L108](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L79-L108) | 改主色即可；保留 `set(button_primary_background_fill_hover=...)` 的做法 |
| A2 | CSS 设计系统 | [#L111-L225](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L111-L225) | 直接复制；`.dfs-*` 前缀建议改成项目前缀避免冲突 |
| A3 | Banner + 导航 | [#L227-L255](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L227-L255) | 换文案与链接 |
| A4 | HTML 渲染器 3 件套 | [#L361-L446](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L361-L446) | 见 §3.5 映射 |
| A5 | 回调约定（元组 + `gr.update` + 异常兜底） | [#L453-L492](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L453-L492) | 作为本项目所有 UI 回调的编码规范 |
| A6 | `gr.State` 存大对象 + Radio 选择 | [#L579-L590](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L579-L590)、[#L612-L616](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L612-L616) | 会话历史、trace 全量 JSON 都可这样传 |
| A7 | `DownloadButton` + `tempfile` | [#L619-L632](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L619-L632) | 导出 trace / 评测报告 |
| A8 | `gr.Progress` 进度提示 | [#L453-L458](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L453-L458) | 跑 80 条评测时用 |

### B. 需改造适配

| 编号 | 内容 | 参考位置 | 改造点 |
| --- | --- | --- | --- |
| B1 | 静态对话 → 在线对话 | `_dialogue_to_chatbot` [#L411-L427](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L411-L427) | 改为 `submit` 事件：用户输入追加 → 调 `Agent.run` 单轮 → assistant 追加；system 不再展示 |
| B2 | 页面布局语义 | `create_demo` [#L527-L634](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L527-L634) | 控制区：生成数/种子 → `session_id` / 场景选择 / 重置；结果区：路径/统计 → 状态机进度 + trace 表 |
| B3 | 启动参数 | `launch_demo` [#L644-L646](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L644-L646) | 增加 `server_name/server_port`、`queue()`（长任务需要）、`share` 策略 |
| B4 | 配置预览 | [#L542-L564](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L542-L564) | 数据源换成 `ScenarioConfig`：stages / offer_states / 12 条转移 / policy 上限；热力图换「转移表 + 实际触达次数」 |
| B5 | import 期自动生成数据 | `ensure_demo_data` [#L45-L56](file:///E:/Coding_python/dialog-sft-forge/demo/app.py#L45-L56) | 本项目不得在 import 时产生副作用；改为显式命令 + 启动预检报错 |
| B6 | `AgentConfig` 点号配置 | [agent/core/config.py](file:///E:/Coding_python/dialog-sft-forge/agent/core/config.py) | 不采用；统一走 `load_config()` + pydantic 校验 |

### C. 不兼容（不移植）

| 编号 | 内容 | 原因 |
| --- | --- | --- |
| C1 | `run_generation` 全链路（Excel 话术 / 概率矩阵 / 路径生成 / 促销 / 再见终止） | 目标不同：离线批量合成 vs 在线逐轮决策。仅可作为 Stretch「完整合成管线」参考（与本项目 `rule_based.py` 是两套实现，`case` schema 也不同） |
| C2 | `ReActAgent.run`（[react_loop.py](file:///E:/Coding_python/dialog-sft-forge/agent/core/react_loop.py)） | 单轮任务式、无状态机与 C6 trace、memory 语义不同；本项目 [loop.py](file:///e:/Coding_python/multi-turn-task-agent/src/agent/loop.py) 已有更完整实现（转移、护栏、trace、事实状态） |
| C3 | `ToolRegistry`（[tool_registry.py](file:///E:/Coding_python/dialog-sft-forge/agent/core/tool_registry.py)） | 返回字符串、错误只塞 `{"error": ...}`，无 `retryable/code`；本项目 C2 封装已契约化 |
| C4 | `ShortTermMemory`（[memory.py](file:///E:/Coding_python/dialog-sft-forge/agent/core/memory.py)） | 本项目会话状态由状态机 + `round_no` 承担；另其 `__len__` 导致空记忆被判定为 False 的坑（作者已在 react_loop 注释说明），不引入 |
| C5 | `scripts_general/` 合成管线 | 与本项目 `src/synthesis/rule_based.py` 功能重叠，且依赖 pandas/openpyxl 数据结构 |

## 7. 推荐融合方案

**方案对比**

| 方案 | 做法 | 优点 | 缺点 |
| --- | --- | --- | --- |
| A. Gradio 直连 Agent（推荐） | `src/ui/app.py` 内 `from src.agent.loop import Agent`，每轮直接 `agent.run` | 零新服务、当天可跑通；复用 A 级资产最多 | UI 与核心同进程，未来服务化需再拆一层 |
| B. Gradio → FastAPI | 先做 C5 五接口，再让 Gradio 走 HTTP | 架构最干净 | 依赖 C5 未实现，周期长 |
| C. 纯 FastAPI + 自写前端 | — | 完全可控 | 放弃 Gradio 生态，成本最高 |

**推荐 A**：与 Roadmap 的「先闭环」一致；Gradio 只是一个 `Agent` 调用方，等 C5 落地后把调用点从「直连」换成「HTTP」即可，UI 组件与事件绑定不变。

**目标界面结构（建议）**

```
Banner（复用 A3）
├── 左栏：场景摘要（stage / 转移表 / policy 上限，复用 A4 渲染器）
├── 右栏：对话区
│     ├── Chatbot（在线多轮，逐轮追加）
│     ├── Textbox + 发送按钮（submit 事件）
│     └── 状态条：stage / round_no / offer_state / 违规数（复用 A4 stats）
└── 折叠面板：Trace（steps 表格）+ 导出 JSON / 评测报告（复用 A7）
```

## 8. 落地任务与验收标准

| 序 | 任务 | 交付标准 | 依赖 |
| --- | --- | --- | --- |
| U1 | 安装并锁定 Gradio | `requirements.txt` 增加固定版本；`python -c "import gradio"` 通过；`pytest` 仍 92 passed | 环境写权限 |
| U2 | UI 骨架 | `python -m src.ui.app` 起服务；首页显示场景摘要 + 12 条转移 | U1 |
| U3 | 在线对话 | 输入 → `Agent.run` 单轮 → 消息追加；状态条实时更新 `stage/round_no` | U2 |
| U4 | Trace 面板 | 可展开查看每步 `type/tool/args/result`；可导出 JSON | U3 |
| U5 | 评测页（可选） | 读 `eval/reports/v0_baseline.json` 渲染 6 项指标 + 分层表 | U4 |

> 验收口径沿用项目惯例：一条命令起服务、一次完整对话到 `CLOSED`、trace 面板可见 14 步、退出后无残留临时文件。

## 9. 风险与注意事项

1. **Gradio 版本差异（已锁定 6.31.0）**：实测该版本 `Chatbot` **没有 `type` 参数**（默认 messages 格式），`launch()` 仍接受 `theme`/`css`，`queue()` 可用。参考项目代码中的 `gr.Chatbot(...)` 不含 `type`，与 6.x 兼容；若未来升级大版本需重新核对。
2. **依赖连带升级**：Gradio 可能调整 fastapi/uvicorn/pydantic 版本，安装后必须重跑 `pytest`（92 passed）与 `make eval`（100% / 35% / 0.80）确认无回归。
3. **不要引入 pandas/numpy**：那是参考项目合成链路的需要，本项目 UI 与评测都不需要。
4. **import 副作用**：参考项目 import 即生成数据（B5），本项目必须避免，保持「配置预检 fail-fast」风格。
5. **临时文件**：`DownloadButton` 用 `delete=False` 生成文件，长期运行会累积；建议落到 `eval/reports/` 或加清理。
6. **中文字体**：`gr.themes.GoogleFont("Noto Sans SC")` 依赖外网，内网环境会退化；建议保留 `ui-sans-serif, system-ui` 回退。
7. **多用户并发**：`gr.State` 是会话级隔离，但 `Agent` 实例与工具内的内存列表（`_PROMISES` / `_ESCALATIONS`）是进程级单例，多人同时对话会串数据 → 服务化前需按 `session_id` 隔离。
