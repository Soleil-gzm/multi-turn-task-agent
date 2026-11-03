"""场景资产包。

本包存放**场景专属**内容（工具实现、离线演示用 LLM），与 `src/` 下的框架代码分离：
新增一个场景只需要在这里加一个子包 + 一份 yaml，不需要改动框架。
工具装配、LLM 工厂与配置发现由框架侧提供
（`src.tools.registry.build_registry_from_config`、`src.agent.llm.get_llm_client`、
`src.core.config.default_scenario_path`）。
"""
