"""LLM 客户端抽象层。

两种实现：
  - `MockLLMClient`：按预设脚本响应，用于离线演示与单测
  - `OpenAICompatibleClient`：OpenAI 兼容的 `/chat/completions`（DeepSeek / Qwen / vLLM 等），
    凭证与模型来自环境变量（见 `from_env`），不写入仓库

真实接入只需实现 `LLMClient.chat`（function calling 格式）。
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from src.core.env import load_env_file


_CONTRACT_RE = re.compile(r"HT-Y2026-\d{3}")
_INVOICE_RE = re.compile(r"INV-\d+")
_AMOUNT_RE = re.compile(r"'amount':\s*(\d+)")


class LLMError(RuntimeError):
    """真实模型调用失败（配置缺失、网络错误、非 2xx、响应结构异常）。"""


class LLMClient(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """返回 {"content": str | None, "tool_calls": [...] | None}。"""


class MockLLMClient(LLMClient):
    """用于演示与测试的 mock LLM，按预设脚本响应。

    参数单一真源：
      - contract_no 取自用户输入（最近一条含合同号的用户消息）
      - amount 取自已查询的账面数据（最近一条 query_receivable 工具返回）
    任何分支都不再硬编码合同号/金额，避免跨合同样本查到同一份数据。
    """

    def __init__(self) -> None:
        # 简单的关键词 → 响应脚本
        self._script = [
            {"role": "assistant", "content": "您好，我是财务专员小账。请提供您的合同号以便核实。"},
        ]
        self._call_count = 0

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._call_count += 1
        last_user = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user = m["content"]
                break

        # 参数真源：合同号来自用户输入，金额来自已查询的账面数据
        contract_no = self._extract_contract_no(messages)
        amount = self._extract_amount(messages)

        # 关键词路由
        if "合同" in last_user and "HT-" in last_user:
            return self._tool_call("query_receivable", {"contract_no": contract_no}, "call_1")
        if "金额不对" in last_user or "对账" in last_user or "不符" in last_user or "差异" in last_user:
            return self._tool_call("diff_statement", {"contract_no": contract_no}, "call_4")
        if "发票" in last_user:
            return self._tool_call(
                "check_invoice",
                {"invoice_no": self._extract_invoice_no(messages)},
                "call_5",
            )
        if (
            "政策" in last_user
            or "分期" in last_user
            or "减免" in last_user
            or "延期" in last_user
            or "宽限" in last_user
        ):
            return self._tool_call("query_policy", {}, "call_2")
        if "承诺" in last_user or "付款" in last_user or "同意" in last_user:
            return self._tool_call(
                "record_promise",
                {"contract_no": contract_no, "pay_date": "2026-11-15", "amount": amount},
                "call_3",
            )
        # 默认：给出协商回复（不假设已查询，避免编造）
        return {
            "content": f"好的，关于您提到的「{last_user[:20]}」，我帮您核实一下。",
            "tool_calls": None,
        }

    @staticmethod
    def _extract_contract_no(messages: list[dict[str, str]]) -> str:
        """从最近一条含合同号的用户消息提取合同号（真源：用户输入）。"""
        for m in reversed(messages):
            if m.get("role") != "user":
                continue
            found = _CONTRACT_RE.search(m.get("content", ""))
            if found:
                return found.group(0)
        return "HT-Y2026-012"

    @staticmethod
    def _extract_invoice_no(messages: list[dict[str, str]]) -> str:
        """从最近一条含发票号的用户消息提取发票号（真源：用户输入）。"""
        for m in reversed(messages):
            if m.get("role") != "user":
                continue
            found = _INVOICE_RE.search(m.get("content", ""))
            if found:
                return found.group(0)
        return "INV-20260123"

    @staticmethod
    def _extract_amount(messages: list[dict[str, str]]) -> int:
        """从最近一条工具返回中提取 amount（真源：query_receivable 账面数据）。"""
        for m in reversed(messages):
            if m.get("role") != "tool":
                continue
            found = _AMOUNT_RE.search(m.get("content", ""))
            if found:
                return int(found.group(1))
        return 380000

    @staticmethod
    def _tool_call(name: str, args: dict, call_id: str) -> dict[str, Any]:
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        }

    def describe(self) -> dict[str, Any]:
        """报告用模型元信息：Mock 仅验证链路，不作为能力基线。"""
        return {
            "provider": "mock",
            "model": "mock-llm",
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": None,
        }


class OpenAICompatibleClient(LLMClient):
    """OpenAI 兼容协议的真实模型客户端。

    仅依赖 `httpx`，适配任意提供 `POST /chat/completions` 的服务
    （OpenAI / DeepSeek / Qwen / vLLM 等）。评测口径要求 temperature=0；
    `seed` 仅在供应商支持时下发（不支持的服务会忽略或报错，故默认不下发）。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        top_p: float = 1.0,
        seed: int | None = None,
        timeout: float = 60.0,
        max_tokens: int | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not base_url or not model:
            raise LLMError("真实模型缺少 base_url 或 model")
        if not api_key:
            raise LLMError("真实模型缺少 API key")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.seed = seed
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._client = http_client

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    @classmethod
    def from_env(cls) -> "OpenAICompatibleClient":
        """从环境变量构造；缺失必填项时 fail-fast，不静默回落到 Mock。

        必填：`MTA_LLM_BASE_URL`（或 `OPENAI_BASE_URL`）、`MTA_LLM_API_KEY`
        （或 `OPENAI_API_KEY`）、`MTA_LLM_MODEL`。
        可选：`MTA_LLM_TEMPERATURE`（默认 0）、`MTA_LLM_TOP_P`（默认 1）、
        `MTA_LLM_SEED`、`MTA_LLM_TIMEOUT`（默认 60）、`MTA_LLM_MAX_TOKENS`。
        仓库根目录存在 `.env` 时会先加载（不覆盖已有环境变量）。
        """
        load_env_file()
        base_url = os.environ.get("MTA_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        api_key = os.environ.get("MTA_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        model = os.environ.get("MTA_LLM_MODEL") or os.environ.get("OPENAI_MODEL")
        missing = [
            name
            for name, value in (
                ("MTA_LLM_BASE_URL", base_url),
                ("MTA_LLM_API_KEY", api_key),
                ("MTA_LLM_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise LLMError(f"真实模型配置缺失环境变量: {', '.join(missing)}")
        try:
            temperature = float(os.environ.get("MTA_LLM_TEMPERATURE", "0"))
            top_p = float(os.environ.get("MTA_LLM_TOP_P", "1"))
            timeout = float(os.environ.get("MTA_LLM_TIMEOUT", "60"))
            raw_seed = os.environ.get("MTA_LLM_SEED")
            seed = int(raw_seed) if raw_seed else None
            raw_max = os.environ.get("MTA_LLM_MAX_TOKENS")
            max_tokens = int(raw_max) if raw_max else None
        except ValueError as e:
            raise LLMError(f"真实模型环境变量格式非法: {e}") from e
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            timeout=timeout,
            max_tokens=max_tokens,
        )

    def describe(self) -> dict[str, Any]:
        """报告用的模型元信息（不含 key）。"""
        return {
            "provider": "openai-compat",
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "seed": self.seed,
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if self.seed is not None:
            body["seed"] = self.seed
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            resp = client.post(self.endpoint, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(f"模型请求失败（{self.endpoint}）: {e}") from e
        finally:
            if self._client is None:
                client.close()

        if resp.status_code >= 400:
            raise LLMError(
                f"模型返回 HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            payload = resp.json()
            message = payload["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"模型响应结构异常: {resp.text[:200]}") from e

        return {
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls"),
            "usage": payload.get("usage"),
        }


def get_llm_client(spec: str | None = None) -> LLMClient:
    """获取 LLM 客户端。

    spec 来源：环境变量 `MTA_LLM` 优先，其次场景 yaml 的 `demo.llm`。
    支持：
      - 缺省 / `mock`：MockLLMClient
      - `openai_compat`（别名 `openai-compatible` / `openai`）：读取环境变量构造真实客户端
      - `module.factory`：自定义实现（无参调用，便于场景注入）
    框架侧不写死任何场景名。
    """
    load_env_file()
    spec = os.environ.get("MTA_LLM") or spec
    if not spec or spec == "mock":
        return MockLLMClient()
    if spec in ("openai_compat", "openai-compatible", "openai"):
        return OpenAICompatibleClient.from_env()
    import importlib

    module_path, _, func_name = spec.rpartition(".")
    if not module_path:
        raise ValueError(f"LLM spec 必须形如 module.factory，实际为 {spec!r}")
    module = importlib.import_module(module_path)
    factory = getattr(module, func_name)
    return factory()
