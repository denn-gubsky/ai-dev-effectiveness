"""Tests for the anthropic-api, openai, and ollama judge providers.

These tests don't make real API calls — they patch the SDK entry points
to return canned responses and verify:
  1. The provider builds the right request shape (model, messages, schema).
  2. The response parsing produces a valid JudgeResult.
  3. Missing API keys / missing SDKs raise clear RuntimeErrors.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_dev_effectiveness import judge
from ai_dev_effectiveness.judge import JudgeConfig
from tests.conftest import FakeCommit

_AGENT_BODY_FIXTURE = """---
name: effort-judge
tools: Read, Grep
model: sonnet
---
Test agent body."""


def _prepare_workspace(tmp_path: Path) -> Path:
    """Create a workspace with a minimal effort-judge agent + skill."""
    ws = tmp_path / "ws"
    (ws / ".claude" / "agents").mkdir(parents=True)
    (ws / ".claude" / "agents" / "effort-judge.md").write_text(_AGENT_BODY_FIXTURE)
    (ws / ".claude" / "skills" / "effort-estimation").mkdir(parents=True)
    (ws / ".claude" / "skills" / "effort-estimation" / "SKILL.md").write_text(
        "Test rubric: trivial=0.5h human, 0.25h ai."
    )
    return ws


def _cfg(workspace: Path, provider: str, model: str = "sonnet") -> JudgeConfig:
    return JudgeConfig(
        provider=provider, model=model,
        timeout_sec=30, max_retries=1,
        agent_path=workspace / ".claude" / "agents" / "effort-judge.md",
        skill_path=workspace / ".claude" / "skills" / "effort-estimation" / "SKILL.md",
    )


# ---------------------------------------------------------------------------
# Anthropic API provider
# ---------------------------------------------------------------------------

def test_anthropic_judge_requires_sdk(tmp_path, monkeypatch):
    """Clear error when the anthropic SDK isn't installed."""
    workspace = _prepare_workspace(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch.dict(sys.modules, {"anthropic": None}):
        with pytest.raises(RuntimeError, match="anthropic SDK not installed"):
            judge.AnthropicApiJudge(_cfg(workspace, "anthropic-api"))


def test_anthropic_judge_requires_api_key(tmp_path, monkeypatch):
    """Clear error when ANTHROPIC_API_KEY isn't set."""
    workspace = _prepare_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = MagicMock()
    with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            judge.AnthropicApiJudge(_cfg(workspace, "anthropic-api"))


def test_anthropic_judge_parses_tool_use_response(git_fixture, tmp_path, monkeypatch):
    """The provider must extract `tool_use.input` and translate it to a JudgeResult."""
    workspace = _prepare_workspace(tmp_path)
    repo = git_fixture(commits=[
        FakeCommit(files={"a.py": "x = 1\n" * 30}, subject="initial"),
        FakeCommit(files={"a.py": "x = 2\n" * 30}, subject="rev"),
    ])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    # Fake SDK module shape.
    fake_anthropic = types.ModuleType("anthropic")
    mock_client = MagicMock()
    fake_anthropic.Anthropic = MagicMock(return_value=mock_client)

    fake_block = MagicMock()
    fake_block.type = "tool_use"
    fake_block.name = "submit_effort_judgment"
    fake_block.input = {
        "human_hours": 4.0,
        "ai_assisted_hours": 2.0,
        "complexity": "medium",
        "confidence": "high",
        "rationale": "Mock judgment.",
    }
    fake_response = MagicMock()
    fake_response.content = [fake_block]
    fake_response.stop_reason = "tool_use"
    mock_client.messages.create.return_value = fake_response

    with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        provider = judge.AnthropicApiJudge(_cfg(workspace, "anthropic-api"))
        sha = subprocess_first_commit_sha(repo)
        result = provider.judge(sha, repo)

    assert result.human_hours == 4.0
    assert result.ai_assisted_hours == 2.0
    assert result.complexity == "medium"
    assert result.confidence == "high"

    # Verify the SDK was called with our schema as input_schema and forced tool_choice.
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["tools"][0]["input_schema"] == judge._JUDGMENT_SCHEMA
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": "submit_effort_judgment"}


def test_anthropic_judge_normalizes_short_model_aliases(tmp_path, monkeypatch):
    """`sonnet`/`opus`/`haiku` aliases map to full model IDs."""
    workspace = _prepare_workspace(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = MagicMock()

    with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        p_sonnet = judge.AnthropicApiJudge(_cfg(workspace, "anthropic-api", "sonnet"))
        p_opus = judge.AnthropicApiJudge(_cfg(workspace, "anthropic-api", "opus"))
        p_full = judge.AnthropicApiJudge(_cfg(workspace, "anthropic-api", "claude-sonnet-4-7-20251119"))

    assert p_sonnet.model_id().startswith("claude-sonnet")
    assert p_opus.model_id().startswith("claude-opus")
    # Full IDs pass through unchanged.
    assert p_full.model_id() == "claude-sonnet-4-7-20251119"


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------

def test_openai_judge_requires_sdk(tmp_path, monkeypatch):
    workspace = _prepare_workspace(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch.dict(sys.modules, {"openai": None}):
        with pytest.raises(RuntimeError, match="openai SDK not installed"):
            judge.OpenAiJudge(_cfg(workspace, "openai"))


def test_openai_judge_requires_api_key(tmp_path, monkeypatch):
    workspace = _prepare_workspace(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = MagicMock()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            judge.OpenAiJudge(_cfg(workspace, "openai"))


def test_openai_judge_parses_json_schema_response(git_fixture, tmp_path, monkeypatch):
    workspace = _prepare_workspace(tmp_path)
    repo = git_fixture(commits=[
        FakeCommit(files={"a.py": "x = 1\n" * 30}, subject="initial"),
        FakeCommit(files={"a.py": "x = 2\n" * 30}, subject="rev"),
    ])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    fake_openai = types.ModuleType("openai")
    mock_client = MagicMock()
    fake_openai.OpenAI = MagicMock(return_value=mock_client)

    import json as _json
    fake_choice = MagicMock()
    fake_choice.message.refusal = None
    fake_choice.message.content = _json.dumps({
        "human_hours": 6.0,
        "ai_assisted_hours": 3.0,
        "complexity": "medium",
        "confidence": "high",
        "rationale": "Mock OpenAI judgment.",
    })
    fake_choice.finish_reason = "stop"
    fake_completion = MagicMock()
    fake_completion.choices = [fake_choice]
    mock_client.chat.completions.create.return_value = fake_completion

    with patch.dict(sys.modules, {"openai": fake_openai}):
        provider = judge.OpenAiJudge(_cfg(workspace, "openai"))
        sha = subprocess_first_commit_sha(repo)
        result = provider.judge(sha, repo)

    assert result.human_hours == 6.0
    assert result.complexity == "medium"

    # The request must use response_format json_schema with strict=True.
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    rf = call_kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == judge._JUDGMENT_SCHEMA


def test_openai_judge_handles_refusal(git_fixture, tmp_path, monkeypatch):
    """Surfacing OpenAI's refusal field as a clear ValueError."""
    workspace = _prepare_workspace(tmp_path)
    repo = git_fixture(commits=[
        FakeCommit(files={"a.py": "x = 1\n" * 30}, subject="initial"),
        FakeCommit(files={"a.py": "x = 2\n" * 30}, subject="rev"),
    ])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    fake_openai = types.ModuleType("openai")
    mock_client = MagicMock()
    fake_openai.OpenAI = MagicMock(return_value=mock_client)
    fake_choice = MagicMock()
    fake_choice.message.refusal = "I cannot judge this commit."
    fake_choice.message.content = None
    fake_completion = MagicMock()
    fake_completion.choices = [fake_choice]
    mock_client.chat.completions.create.return_value = fake_completion

    with patch.dict(sys.modules, {"openai": fake_openai}):
        provider = judge.OpenAiJudge(_cfg(workspace, "openai"))
        sha = subprocess_first_commit_sha(repo)
        with pytest.raises(ValueError, match="refused to judge"):
            provider.judge(sha, repo)


# ---------------------------------------------------------------------------
# Ollama provider
# ---------------------------------------------------------------------------

def test_ollama_judge_requires_sdk(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    with patch.dict(sys.modules, {"ollama": None}):
        with pytest.raises(RuntimeError, match="ollama SDK not installed"):
            judge.OllamaJudge(_cfg(workspace, "ollama"))


def test_ollama_judge_parses_chat_response(git_fixture, tmp_path):
    workspace = _prepare_workspace(tmp_path)
    repo = git_fixture(commits=[
        FakeCommit(files={"a.py": "x = 1\n" * 30}, subject="initial"),
        FakeCommit(files={"a.py": "x = 2\n" * 30}, subject="rev"),
    ])

    fake_ollama = types.ModuleType("ollama")
    mock_client = MagicMock()
    fake_ollama.Client = MagicMock(return_value=mock_client)
    import json as _json
    mock_client.chat.return_value = {
        "message": {
            "content": _json.dumps({
                "human_hours": 1.0,
                "ai_assisted_hours": 0.5,
                "complexity": "small",
                "confidence": "medium",
                "rationale": "Local model judgment.",
            })
        }
    }

    with patch.dict(sys.modules, {"ollama": fake_ollama}):
        provider = judge.OllamaJudge(_cfg(workspace, "ollama", "llama3.1:70b"))
        sha = subprocess_first_commit_sha(repo)
        result = provider.judge(sha, repo)

    assert result.human_hours == 1.0
    assert result.complexity == "small"

    # Schema must be passed via the `format` kwarg.
    call_kwargs = mock_client.chat.call_args.kwargs
    assert call_kwargs["format"] == judge._JUDGMENT_SCHEMA
    assert call_kwargs["model"] == "llama3.1:70b"


def test_ollama_judge_falls_back_default_model(tmp_path, monkeypatch):
    """Cross-provider default `sonnet` → ollama default model."""
    workspace = _prepare_workspace(tmp_path)
    fake_ollama = types.ModuleType("ollama")
    fake_ollama.Client = MagicMock()
    with patch.dict(sys.modules, {"ollama": fake_ollama}):
        provider = judge.OllamaJudge(_cfg(workspace, "ollama", "sonnet"))
    assert provider.model_id() == judge.OllamaJudge._DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------

def test_make_provider_dispatches_correctly(tmp_path, monkeypatch):
    """All five provider keys instantiate without raising NotImplementedError."""
    workspace = _prepare_workspace(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = MagicMock()
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = MagicMock()
    fake_ollama = types.ModuleType("ollama")
    fake_ollama.Client = MagicMock()

    with patch.dict(sys.modules, {
        "anthropic": fake_anthropic, "openai": fake_openai, "ollama": fake_ollama,
    }):
        assert judge.make_provider(_cfg(workspace, "stub")).name() == "stub"
        assert judge.make_provider(_cfg(workspace, "anthropic-api")).name() == "anthropic-api"
        assert judge.make_provider(_cfg(workspace, "openai")).name() == "openai"
        assert judge.make_provider(_cfg(workspace, "ollama")).name() == "ollama"

    with pytest.raises(ValueError, match="Unknown judge provider"):
        judge.make_provider(_cfg(workspace, "imaginary-provider"))


# ---------------------------------------------------------------------------
# helpers used above
# ---------------------------------------------------------------------------

def subprocess_first_commit_sha(repo: Path) -> str:
    import subprocess
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%H"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()
