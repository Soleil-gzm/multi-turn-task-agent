"""G2 真实模型接入与协议测试（不访问网络）。"""

from __future__ import annotations

import json

import httpx
import pytest

from src.agent.llm import (
    LLMError,
    MockLLMClient,
    OpenAICompatibleClient,
    get_llm_client,
)


def _client(handler) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="https://example.test/v1",
        api_key="sk-test",
        model="test-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_request_body_and_tool_call_parsing():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "query_receivable", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    client = _client(handler)
    resp = client.chat(
        messages=[{"role": "user", "content": "你好"}],
        tools=[{"type": "function", "function": {"name": "query_receivable"}}],
    )

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["temperature"] == 0.0
    assert captured["body"]["tool_choice"] == "auto"
    assert captured["body"]["tools"][0]["function"]["name"] == "query_receivable"
    assert resp["tool_calls"][0]["id"] == "call_1"
    assert resp["usage"]["prompt_tokens"] == 10


def test_full_endpoint_is_not_duplicated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = OpenAICompatibleClient(
        base_url="https://example.test/v1/chat/completions",
        api_key="sk-test",
        model="m",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.endpoint == "https://example.test/v1/chat/completions"
    assert client.chat([{"role": "user", "content": "hi"}])["content"] == "ok"


def test_http_error_raises_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error":"bad key"}')

    with pytest.raises(LLMError, match="HTTP 401"):
        _client(handler).chat([{"role": "user", "content": "hi"}])


def test_malformed_response_raises_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(LLMError, match="响应结构异常"):
        _client(handler).chat([{"role": "user", "content": "hi"}])


def test_network_error_raises_llm_error(monkeypatch):
    monkeypatch.setattr("src.agent.llm.time.sleep", lambda _s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(LLMError, match="模型请求失败"):
        _client(handler).chat([{"role": "user", "content": "hi"}])


def test_retry_recovers_transient_error(monkeypatch):
    monkeypatch.setenv("MTA_LLM_MAX_RETRIES", "1")
    monkeypatch.setattr("src.agent.llm.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = _client(handler)
    assert client.chat([{"role": "user", "content": "hi"}])["content"] == "ok"
    assert client.retry_count == 1


def test_from_env_reads_config(monkeypatch):
    monkeypatch.setenv("MTA_LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("MTA_LLM_API_KEY", "sk-env")
    monkeypatch.setenv("MTA_LLM_MODEL", "deepseek-chat")
    monkeypatch.delenv("MTA_LLM_TEMPERATURE", raising=False)
    monkeypatch.delenv("MTA_LLM_SEED", raising=False)

    client = OpenAICompatibleClient.from_env()
    assert client.model == "deepseek-chat"
    assert client.temperature == 0.0
    meta = client.describe()
    assert "sk-env" not in json.dumps(meta)
    assert meta["provider"] == "openai-compat"


def test_from_env_missing_required_fails_fast(monkeypatch):
    monkeypatch.delenv("MTA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("MTA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MTA_LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    with pytest.raises(LLMError, match="MTA_LLM_BASE_URL"):
        OpenAICompatibleClient.from_env()


def test_get_llm_client_specs(monkeypatch):
    monkeypatch.delenv("MTA_LLM", raising=False)
    assert isinstance(get_llm_client(None), MockLLMClient)
    assert isinstance(get_llm_client("mock"), MockLLMClient)

    # 显式 spec 优先于 MTA_LLM：CLI --model / 场景 demo.llm 不被 .env 覆盖
    monkeypatch.setenv("MTA_LLM", "mock")
    monkeypatch.setenv("MTA_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("MTA_LLM_API_KEY", "sk")
    monkeypatch.setenv("MTA_LLM_MODEL", "m")
    assert isinstance(get_llm_client("openai_compat"), OpenAICompatibleClient)
    assert isinstance(get_llm_client(None), MockLLMClient)  # 未显式指定时才读 MTA_LLM


def test_mock_describe_is_link_check_only():
    meta = MockLLMClient().describe()
    assert meta["provider"] == "mock"
    assert meta["model"] == "mock-llm"


class _ScriptedLLM:
    """第一次返回工具调用，之后返回普通文本。"""

    def __init__(self) -> None:
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.seen.append([dict(m) for m in messages])
        if len(self.seen) == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "query_receivable",
                            "arguments": json.dumps({"contract_no": "HT-Y2026-012"}),
                        },
                    }
                ],
            }
        return {"content": "已核实您的账款。", "tool_calls": None}


def test_tool_messages_follow_assistant_tool_calls():
    """OpenAI 协议要求 role=tool 前必须有带 tool_call_id 的 assistant 消息。"""
    from pathlib import Path

    from src.agent.loop import Agent
    from src.core.config import load_config
    from src.tools.registry import build_registry_from_config

    root = Path(__file__).resolve().parent.parent
    config = load_config(str(root / "configs" / "scenarios" / "receivables.yaml"))
    llm = _ScriptedLLM()
    agent = Agent(config, build_registry_from_config(config), llm)

    session = agent.start("s_protocol")
    session.send("你好，我的合同号是 HT-Y2026-012")
    session.send("那我先想想")

    second_call = llm.seen[1]
    tool_index = next(i for i, m in enumerate(second_call) if m["role"] == "tool")
    assistant_index = max(
        i
        for i, m in enumerate(second_call)
        if m["role"] == "assistant" and m.get("tool_calls")
    )
    assert assistant_index < tool_index
    assert second_call[assistant_index]["tool_calls"][0]["id"] == "call_1"
    assert second_call[tool_index]["tool_call_id"] == "call_1"
