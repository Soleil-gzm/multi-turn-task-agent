"""`.env` 加载器测试（不依赖 python-dotenv）。"""

from __future__ import annotations

import os

import pytest

from src.agent.llm import MockLLMClient, OpenAICompatibleClient, get_llm_client
from src.core import env as env_module
from src.core.env import load_env_file


@pytest.fixture(autouse=True)
def _restore_environ():
    """load_env_file 会直接写 os.environ，这里保证测试间不串味。"""
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


def test_load_env_file_parses_comments_quotes_and_export(tmp_path, monkeypatch):
    monkeypatch.delenv("MTA_TEST_A", raising=False)
    monkeypatch.delenv("MTA_TEST_B", raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "# 注释\n"
        "\n"
        "MTA_TEST_A=plain\n"
        "export MTA_TEST_B='quoted value'\n"
        "INVALID_LINE\n",
        encoding="utf-8",
    )

    applied = load_env_file(path)
    assert applied["MTA_TEST_A"] == "plain"
    assert applied["MTA_TEST_B"] == "quoted value"
    assert os.environ["MTA_TEST_A"] == "plain"
    assert os.environ["MTA_TEST_B"] == "quoted value"
    assert "INVALID_LINE" not in applied


def test_load_env_file_does_not_override_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("MTA_TEST_C", "from-shell")
    path = tmp_path / ".env"
    path.write_text("MTA_TEST_C=from-file\n", encoding="utf-8")

    applied = load_env_file(path)
    assert applied == {}
    assert os.environ["MTA_TEST_C"] == "from-shell"


def test_get_llm_client_reads_project_env(tmp_path, monkeypatch):
    """入口调用 get_llm_client 时，仓库根 .env 自动生效。"""
    for name in ("MTA_LLM", "MTA_LLM_BASE_URL", "MTA_LLM_API_KEY", "MTA_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "MTA_LLM=openai_compat\n"
        "MTA_LLM_BASE_URL=https://example.test/v1\n"
        "MTA_LLM_API_KEY=sk-test\n"
        "MTA_LLM_MODEL=test-model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_module, "ROOT", tmp_path)

    client = get_llm_client()
    assert isinstance(client, OpenAICompatibleClient)
    assert client.model == "test-model"


def test_missing_env_still_fails_fast(tmp_path, monkeypatch):
    for name in ("MTA_LLM", "MTA_LLM_BASE_URL", "MTA_LLM_API_KEY", "MTA_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(env_module, "ROOT", tmp_path)

    assert isinstance(get_llm_client(), MockLLMClient)
    with pytest.raises(Exception, match="MTA_LLM_BASE_URL"):
        get_llm_client("openai_compat")
