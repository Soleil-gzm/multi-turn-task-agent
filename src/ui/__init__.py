"""UI 包：Gradio 交互界面（骨架）。

约束：
1. `app.py` 不在 import 期导入 gradio —— 渲染函数可单独测试，未装 gradio 也能 import；
2. 不引入 pandas / numpy，所有展示用纯 HTML 字符串；
3. UI 只负责展示与调用，不复制任何业务逻辑（状态机、工具、评测仍在原模块）。
"""
