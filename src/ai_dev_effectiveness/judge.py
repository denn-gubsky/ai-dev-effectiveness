"""AI judge: per-commit effort estimation by reading actual diffs.

The default provider is `claude-cli` — spawns the user's already-installed
Claude Code CLI in print mode, with a bundled subagent that has filesystem +
git read access (and optionally ast-index MCP for symbol-level lookups).

Provider selection (in order of preference for OSS users):

    claude-cli      Uses the developer's existing Claude subscription.
                    Zero out-of-pocket cost. Diffs never leave the local machine.
                    Requires `claude` on PATH and `init-judge` to install the
                    bundled subagent into the user's repo.

    anthropic-api   Uses the `anthropic` SDK with ANTHROPIC_API_KEY env var.
                    Metered. Sends diffs to api.anthropic.com.

    openai          Uses the `openai` SDK with OPENAI_API_KEY env var.

    ollama          Local model via Ollama (privacy-preserving, lower quality).

    stub            Deterministic, used in tests. No network, no subprocess.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import pandas as pd

ComplexityTier = Literal["trivial", "small", "medium", "large", "architectural"]
ConfidenceLevel = Literal["low", "medium", "high"]


@dataclass
class JudgeResult:
    sha: str
    human_hours: float
    ai_assisted_hours: float
    complexity: ComplexityTier
    confidence: ConfidenceLevel
    rationale: str
    cached: bool = False
    elapsed_sec: float = 0.0
    raw_response: str | None = None


class JudgeProvider(Protocol):
    """A provider knows how to take a SHA and return a JudgeResult."""
    def name(self) -> str: ...
    def model_id(self) -> str: ...
    def judge(self, sha: str, repo: Path) -> JudgeResult: ...


# ---------------------------------------------------------------------------
# config dataclass
# ---------------------------------------------------------------------------

@dataclass
class JudgeConfig:
    provider: str = "claude-cli"
    model: str = "sonnet"
    sample_size: int = 5
    judge_all: bool = False
    skip_below_loc: int = 5
    timeout_sec: int = 60
    max_retries: int = 2
    cache_dir: Path = Path(".ai-dev-effectiveness-cache")
    agent_path: Path = Path(".claude/agents/effort-judge.md")
    skill_path: Path = Path(".claude/skills/effort-estimation/SKILL.md")
    ignore_paths: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

class ClaudeCliJudge:
    """Default provider — spawns `claude --print` against the user's session.

    Requires:
      - `claude` binary on PATH
      - effort-judge agent installed at `cfg.agent_path` (run `init-judge`)
      - effort-estimation skill installed at `cfg.skill_path`
    """

    def __init__(self, cfg: JudgeConfig):
        self.cfg = cfg
        self._claude_path = self._find_claude()

    @staticmethod
    def _find_claude() -> str:
        from shutil import which
        path = which("claude")
        if not path:
            raise RuntimeError(
                "Could not find `claude` on PATH. Install Claude Code "
                "(https://claude.ai/code) or pick a different judge provider."
            )
        return path

    def name(self) -> str:
        return "claude-cli"

    def model_id(self) -> str:
        return self.cfg.model

    def judge(self, sha: str, repo: Path) -> JudgeResult:
        # `claude --agent <name>` resolves agents from <cwd>/.claude/agents/, NOT
        # from arbitrary paths. Since claude runs from the target dir but our
        # bundled subagent lives in the analyzer workspace, agent-by-name
        # discovery would fail (or load a target-side agent we don't control).
        #
        # Workaround: read the agent .md ourselves, extract the body and the
        # tools/model frontmatter, and pass them via --append-system-prompt
        # and --allowedTools. Combined with --json-schema, this gives us the
        # exact subagent behavior with no dependence on agent discovery.
        agent_path = Path(self.cfg.agent_path)
        agent_full_path = agent_path if agent_path.is_absolute() else (repo / agent_path)
        if not agent_full_path.exists():
            raise RuntimeError(
                f"Effort-judge agent not installed at {agent_full_path}. "
                f"Run `ai-dev-effectiveness init-judge` from your analyzer "
                f"workspace (the directory you run analyses from)."
            )

        agent_body, agent_fm = _read_agent_definition(agent_full_path)

        skill_path = Path(self.cfg.skill_path)
        skill_full_path = skill_path if skill_path.is_absolute() else (repo / skill_path)
        skill_body = ""
        if skill_full_path.exists():
            skill_body, _ = _read_agent_definition(skill_full_path)

        system_prompt = agent_body
        if skill_body:
            system_prompt += "\n\n## Skill: effort-estimation rubric\n\n" + skill_body

        # Tool allowlist. The agent frontmatter declares it; if absent, fall
        # back to the same set the bundled agent ships with.
        tools = agent_fm.get("tools") or _DEFAULT_JUDGE_TOOLS
        if isinstance(tools, list):
            tools_str = ",".join(t.strip() for t in tools)
        else:
            tools_str = str(tools)

        model = agent_fm.get("model") or self.cfg.model

        prompt = (
            f"Estimate engineering effort for commit {sha} in this git "
            f"repository. Read the diff with `git show`, investigate as needed, "
            f"and emit one JSON object matching the required schema. "
            f"Output ONLY the JSON object, no markdown fences, no commentary."
        )

        # `--output-format json` is required when using `--json-schema`: the
        # schema-validated payload comes back at `.structured_output` inside
        # the result wrapper. With `--output-format text`, schema-mode is silent
        # and stdout is empty.
        cmd = [
            self._claude_path, "--print",
            "--output-format", "json",
            "--model", str(model),
            "--append-system-prompt", system_prompt,
            "--allowedTools", tools_str,
            "--json-schema", _JUDGMENT_JSON_SCHEMA,
            prompt,
        ]
        # Subprocess CWD = target repo so Read/Grep/`git show` operate on
        # target content. Nothing is written; agent body & schema come from
        # the analyzer workspace via flags above.
        start = time.monotonic()
        proc = subprocess.run(
            cmd, cwd=str(repo), capture_output=True, text=True,
            timeout=self.cfg.timeout_sec, check=False,
        )
        elapsed = time.monotonic() - start

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
            )

        result = _parse_judge_json(proc.stdout, sha=sha,
                                   max_retries=self.cfg.max_retries)
        result.elapsed_sec = elapsed
        result.raw_response = proc.stdout
        return result


_DEFAULT_JUDGE_TOOLS = (
    "Read,Grep,Glob,Bash(git show:*),Bash(git diff:*),Bash(git log:*),mcp__ast-index__*"
)

# Single source of truth for the judgment schema — every provider derives its
# own format-specific representation from this dict.
_JUDGMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "human_hours": {"type": "number", "minimum": 0},
        "ai_assisted_hours": {"type": "number", "minimum": 0},
        "complexity": {
            "type": "string",
            "enum": ["trivial", "small", "medium", "large", "architectural"],
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "rationale": {"type": "string", "maxLength": 300},
    },
    "required": [
        "human_hours", "ai_assisted_hours", "complexity", "confidence", "rationale",
    ],
    "additionalProperties": False,
}

# String form for `claude --print --json-schema <STR>`.
_JUDGMENT_JSON_SCHEMA = json.dumps(_JUDGMENT_SCHEMA, separators=(",", ":"))


def _read_agent_definition(path: Path) -> tuple[str, dict]:
    """Read a Claude Code agent or skill .md file.

    Returns (body, frontmatter_dict). When the file has no YAML frontmatter,
    body is the entire content and frontmatter is empty.
    """
    content = path.read_text()
    if not content.startswith("---\n"):
        return content.strip(), {}
    end = content.find("\n---\n", 4)
    if end == -1:
        return content.strip(), {}
    frontmatter_raw = content[4:end]
    body = content[end + 5:]
    try:
        import yaml
        fm = yaml.safe_load(frontmatter_raw) or {}
    except Exception:
        fm = {}
    return body.strip(), fm


class StubJudge:
    """Deterministic provider for tests. No network, no subprocess.

    Maps LOC to a complexity tier and synthesizes plausible-looking hours.
    """

    def __init__(self, cfg: JudgeConfig):
        self.cfg = cfg

    def name(self) -> str:
        return "stub"

    def model_id(self) -> str:
        return "stub-v1"

    def judge(self, sha: str, repo: Path) -> JudgeResult:
        # `git log -1 --shortstat` to get LOC counts deterministically.
        # (Don't use `git show --shortstat --no-patch` — `--no-patch` suppresses
        # the shortstat output in some git versions.)
        proc = subprocess.run(
            ["git", "log", "-1", "--shortstat", "--format=", sha],
            cwd=str(repo), capture_output=True, text=True, check=True,
        )
        ins, dels = 0, 0
        m = re.search(r"(\d+) insertion", proc.stdout)
        if m:
            ins = int(m.group(1))
        m = re.search(r"(\d+) deletion", proc.stdout)
        if m:
            dels = int(m.group(1))
        loc = ins + dels

        if loc < 10:
            tier, hh, ai = "trivial", 0.4, 0.2
        elif loc < 100:
            tier, hh, ai = "small", 2.0, 1.0
        elif loc < 500:
            tier, hh, ai = "medium", 6.0, 3.0
        elif loc < 2000:
            tier, hh, ai = "large", 18.0, 9.0
        else:
            tier, hh, ai = "architectural", 60.0, 30.0

        return JudgeResult(
            sha=sha,
            human_hours=hh,
            ai_assisted_hours=ai,
            complexity=tier,
            confidence="medium",
            rationale=f"Stub judgment: {loc} LOC → {tier} tier.",
            cached=False,
            elapsed_sec=0.0,
        )


def make_provider(cfg: JudgeConfig) -> JudgeProvider:
    """Factory — instantiate the right provider based on cfg.provider."""
    if cfg.provider == "claude-cli":
        return ClaudeCliJudge(cfg)
    if cfg.provider == "stub":
        return StubJudge(cfg)
    if cfg.provider == "anthropic-api":
        return AnthropicApiJudge(cfg)
    if cfg.provider == "openai":
        return OpenAiJudge(cfg)
    if cfg.provider == "ollama":
        return OllamaJudge(cfg)
    raise ValueError(f"Unknown judge provider: {cfg.provider}")


# ---------------------------------------------------------------------------
# API provider helpers (anthropic-api, openai, ollama)
# ---------------------------------------------------------------------------

def _fetch_commit_for_judging(sha: str, repo: Path, max_lines: int = 800) -> str:
    """Run `git show --stat -p` and return a possibly-truncated string suitable
    for an LLM prompt.

    API providers don't have agentic tool access in our setup, so we feed them
    the diff inline. Truncation guards against pathological mega-commits that
    would blow context budgets and inflate cost; the trailing marker tells the
    model what was cut so it can downgrade confidence appropriately.
    """
    proc = subprocess.run(
        ["git", "show", "--stat", "--patch", "--no-color", sha],
        cwd=str(repo), capture_output=True, text=True, check=True,
    )
    text = proc.stdout
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text
    head = "\n".join(lines[:max_lines])
    return f"{head}\n\n<<<TRUNCATED — {len(lines) - max_lines} more lines hidden from this prompt>>>\n"


def _build_judging_prompts(cfg: JudgeConfig, repo: Path, sha: str) -> tuple[str, str]:
    """Construct (system_prompt, user_prompt) for an API-provider judgment.

    Identical to the claude-cli setup except the diff is embedded in the user
    message rather than fetched by the agent at runtime.
    """
    agent_path = Path(cfg.agent_path)
    agent_full_path = agent_path if agent_path.is_absolute() else (repo / agent_path)
    if not agent_full_path.exists():
        raise RuntimeError(
            f"Effort-judge agent not installed at {agent_full_path}. "
            f"Run `ai-dev-effectiveness init-judge` from your analyzer workspace."
        )
    agent_body, _ = _read_agent_definition(agent_full_path)

    skill_path = Path(cfg.skill_path)
    skill_full_path = skill_path if skill_path.is_absolute() else (repo / skill_path)
    skill_body = ""
    if skill_full_path.exists():
        skill_body, _ = _read_agent_definition(skill_full_path)

    system_prompt = agent_body
    if skill_body:
        system_prompt += "\n\n## Skill: effort-estimation rubric\n\n" + skill_body
    # API providers don't run `git show` themselves, so override the agent's
    # "step 1: run `git show <sha>`" instruction.
    system_prompt += (
        "\n\n## Override for API mode\n"
        "You DO NOT have shell access in this invocation. The user message "
        "below contains the full commit diff and stat. Judge the change "
        "directly from that text. Do not request additional tool calls."
    )

    diff_text = _fetch_commit_for_judging(sha, repo)
    user_prompt = (
        f"Estimate engineering effort for commit {sha}.\n\n"
        f"=== git show --stat --patch {sha} ===\n{diff_text}\n=== end ===\n\n"
        f"Output ONLY the JSON object matching the required schema."
    )
    return system_prompt, user_prompt


def _judge_result_from_dict(d: dict[str, Any], sha: str) -> JudgeResult:
    """Build a JudgeResult from a dict that already conforms to _JUDGMENT_SCHEMA.

    Centralizes type coercion so each provider doesn't reimplement it.
    """
    return JudgeResult(
        sha=sha,
        human_hours=float(d["human_hours"]),
        ai_assisted_hours=float(d["ai_assisted_hours"]),
        complexity=d["complexity"],
        confidence=d.get("confidence", "medium"),
        rationale=str(d.get("rationale", ""))[:300],
    )


# ---------------------------------------------------------------------------
# anthropic-api provider — Claude via the Anthropic SDK + ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------

class AnthropicApiJudge:
    """Anthropic-API provider: forces structured output via the tool_use pattern.

    We define a single tool whose `input_schema` IS our judgment schema, then
    set `tool_choice` to that tool. The model is forced to populate the tool
    input with a schema-conforming JSON object, which we lift directly from
    `tool_use.input`.

    Requires:
      - `pip install ai-dev-effectiveness[judge-anthropic]` (or the `anthropic` package)
      - `ANTHROPIC_API_KEY` env var
    """

    _DEFAULT_MODEL = "claude-sonnet-4-5"
    _MAX_TOKENS = 2048
    _TOOL_NAME = "submit_effort_judgment"

    def __init__(self, cfg: JudgeConfig):
        self.cfg = cfg
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "anthropic SDK not installed. Run: "
                "pip install ai-dev-effectiveness[judge-anthropic]"
            ) from e
        import os
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY env var is not set. Either set it, or use "
                "--judge claude-cli to use your Claude Code subscription instead."
            )

    def name(self) -> str:
        return "anthropic-api"

    def model_id(self) -> str:
        return self._normalize_model(self.cfg.model)

    @classmethod
    def _normalize_model(cls, alias: str) -> str:
        """Translate the short aliases (sonnet/opus/haiku) we use elsewhere
        into Anthropic's full model IDs. Pass-through for anything that already
        looks like a full model ID."""
        if alias in ("sonnet", "claude-sonnet"):
            return cls._DEFAULT_MODEL
        if alias in ("opus", "claude-opus"):
            return "claude-opus-4-5"
        if alias in ("haiku", "claude-haiku"):
            return "claude-haiku-4-5"
        return alias

    def judge(self, sha: str, repo: Path) -> JudgeResult:
        from anthropic import Anthropic

        system_prompt, user_prompt = _build_judging_prompts(self.cfg, repo, sha)

        client = Anthropic()
        start = time.monotonic()
        message = client.messages.create(
            model=self.model_id(),
            max_tokens=self._MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[{
                "name": self._TOOL_NAME,
                "description": "Submit your calibrated effort estimate for this commit.",
                "input_schema": _JUDGMENT_SCHEMA,
            }],
            tool_choice={"type": "tool", "name": self._TOOL_NAME},
        )
        elapsed = time.monotonic() - start

        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == self._TOOL_NAME:
                result = _judge_result_from_dict(dict(block.input), sha)
                result.elapsed_sec = elapsed
                return result

        raise ValueError(
            f"Anthropic API returned no tool_use block for {sha[:8]}; "
            f"stop_reason={getattr(message, 'stop_reason', '?')}"
        )


# ---------------------------------------------------------------------------
# openai provider — OpenAI / Azure-compatible models via the openai SDK
# ---------------------------------------------------------------------------

class OpenAiJudge:
    """OpenAI-compatible provider using `response_format` + JSON schema.

    Uses the structured-outputs feature: `response_format={"type": "json_schema",
    "json_schema": {"strict": True, "schema": ...}}`. Requires gpt-4o-class
    models or newer; older models will get a 4xx from the API (we surface as
    RuntimeError).

    Requires:
      - `pip install ai-dev-effectiveness[judge-openai]`
      - `OPENAI_API_KEY` env var
      - Model that supports structured outputs (gpt-4o, gpt-4o-mini, gpt-5*)
    """

    _DEFAULT_MODEL = "gpt-4o-2024-11-20"
    _MAX_TOKENS = 2048
    _SCHEMA_NAME = "EffortJudgment"

    def __init__(self, cfg: JudgeConfig):
        self.cfg = cfg
        try:
            import openai  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "openai SDK not installed. Run: "
                "pip install ai-dev-effectiveness[judge-openai]"
            ) from e
        import os
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY env var is not set.")

    def name(self) -> str:
        return "openai"

    def model_id(self) -> str:
        # Default-alias fallthrough: if the user keeps the cross-provider
        # default "sonnet", swap in our chosen GPT-4o snapshot.
        if self.cfg.model in ("sonnet", "opus", "haiku") or not self.cfg.model:
            return self._DEFAULT_MODEL
        return self.cfg.model

    def judge(self, sha: str, repo: Path) -> JudgeResult:
        from openai import OpenAI

        system_prompt, user_prompt = _build_judging_prompts(self.cfg, repo, sha)

        client = OpenAI()
        start = time.monotonic()
        completion = client.chat.completions.create(
            model=self.model_id(),
            max_tokens=self._MAX_TOKENS,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": self._SCHEMA_NAME,
                    "strict": True,
                    "schema": _JUDGMENT_SCHEMA,
                },
            },
        )
        elapsed = time.monotonic() - start

        choice = completion.choices[0]
        if choice.message.refusal:
            raise ValueError(
                f"OpenAI refused to judge {sha[:8]}: {choice.message.refusal}"
            )
        content = choice.message.content or ""
        if not content:
            raise ValueError(
                f"OpenAI returned empty content for {sha[:8]}; "
                f"finish_reason={choice.finish_reason}"
            )
        d = json.loads(content)
        result = _judge_result_from_dict(d, sha)
        result.elapsed_sec = elapsed
        return result


# ---------------------------------------------------------------------------
# ollama provider — local models via the Ollama API
# ---------------------------------------------------------------------------

class OllamaJudge:
    """Local-model provider via Ollama using `format=schema` for JSON output.

    Sends the JSON schema dict as the `format` parameter to `chat()`. Modern
    ollama (≥0.5) constrains generation to match the schema. For older models
    or older ollama versions, this gracefully degrades to "JSON-ish" output;
    we still parse with the same defensive helper as the cli judge.

    Requires:
      - `pip install ai-dev-effectiveness[judge-ollama]`
      - A running ollama server (default `http://localhost:11434`; override
        with the `OLLAMA_HOST` env var)
      - The model already pulled locally (`ollama pull llama3.1:70b`)
    """

    _DEFAULT_MODEL = "llama3.1:70b"

    def __init__(self, cfg: JudgeConfig):
        self.cfg = cfg
        try:
            import ollama  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "ollama SDK not installed. Run: "
                "pip install ai-dev-effectiveness[judge-ollama]"
            ) from e

    def name(self) -> str:
        return "ollama"

    def model_id(self) -> str:
        # Ollama model names look like "llama3.1:70b" — if the user kept the
        # cross-provider default "sonnet", they didn't pick an ollama model;
        # fall through to our default.
        if self.cfg.model in ("sonnet", "opus", "haiku") or not self.cfg.model:
            return self._DEFAULT_MODEL
        return self.cfg.model

    def judge(self, sha: str, repo: Path) -> JudgeResult:
        import os

        from ollama import Client

        system_prompt, user_prompt = _build_judging_prompts(self.cfg, repo, sha)

        client = Client(host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
        start = time.monotonic()
        response = client.chat(
            model=self.model_id(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            format=_JUDGMENT_SCHEMA,
            options={"temperature": 0},
        )
        elapsed = time.monotonic() - start

        # ollama 0.4+ returns a typed object, 0.3 returns a dict — handle both.
        msg = response.get("message", {}) if isinstance(response, dict) else response.message
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if not content:
            raise ValueError(f"Ollama returned empty content for {sha[:8]}.")

        # ollama may return JSON with extra whitespace or a markdown fence;
        # use the same defensive parser as the cli judge.
        for candidate in _candidate_json_blocks(content):
            try:
                d = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and "human_hours" in d:
                result = _judge_result_from_dict(d, sha)
                result.elapsed_sec = elapsed
                return result

        raise ValueError(
            f"Could not parse Ollama judgment for {sha[:8]} from "
            f"{len(content)} chars. First 200: {content[:200]!r}"
        )


# ---------------------------------------------------------------------------
# response parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _parse_judge_json(text: str, sha: str, max_retries: int = 0) -> JudgeResult:
    """Pull a valid JSON judgment out of `text` and validate fields.

    Strategies tried in order:
      1. Parse the whole stripped text as JSON; if it has a `.structured_output`
         field (the `--output-format json --json-schema` wrapper from
         `claude --print`), use that field as the judgment.
      2. Parse the whole stripped text as JSON directly (fallback for older
         claude versions or non-schema invocations).
      3. Pull a JSON object out of a ```json ... ``` markdown fence.
      4. Brace-count walk: find the first `{` and read until matching `}`.
    """
    for candidate in _candidate_json_blocks(text):
        try:
            d = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue

        # Unwrap the claude --output-format json envelope if present.
        if "structured_output" in d and isinstance(d["structured_output"], dict):
            d = d["structured_output"]

        try:
            return JudgeResult(
                sha=sha,
                human_hours=float(d["human_hours"]),
                ai_assisted_hours=float(d["ai_assisted_hours"]),
                complexity=d["complexity"],
                confidence=d.get("confidence", "medium"),
                rationale=str(d.get("rationale", ""))[:300],
            )
        except (KeyError, ValueError, TypeError):
            continue

    raise ValueError(
        f"Could not extract a valid JSON judgment for {sha[:8]} from "
        f"{len(text)} chars of output. First 200 chars: {text[:200]!r}"
    )


def _candidate_json_blocks(text: str):
    """Yield candidate JSON strings from a possibly-noisy LLM response."""
    stripped = text.strip()
    if stripped:
        yield stripped
    for m in _FENCE_RE.finditer(text):
        yield m.group(1)
    # Brace-count walk handles any other interleaving (prose before the JSON,
    # the JSON itself unfenced, then optional trailing prose).
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                yield text[start:i + 1]
                start = -1


# ---------------------------------------------------------------------------
# caching
# ---------------------------------------------------------------------------

class JudgeCache:
    """SHA-keyed JSON cache at .ai-dev-effectiveness-cache/judge/<provider>/<model>/<sha>.json.

    The cache key includes a hash of the bundled SKILL.md + agent definition,
    so cached judgments invalidate when either is bumped.
    """

    def __init__(self, cache_dir: Path, provider: str, model: str, prompt_version: str):
        self.dir = cache_dir / "judge" / provider / model / prompt_version
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, sha: str) -> JudgeResult | None:
        p = self.dir / f"{sha}.json"
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return JudgeResult(cached=True, **{k: v for k, v in d.items()
                                           if k != "raw_response"})

    def put(self, result: JudgeResult) -> None:
        p = self.dir / f"{result.sha}.json"
        payload = {
            "sha": result.sha,
            "human_hours": result.human_hours,
            "ai_assisted_hours": result.ai_assisted_hours,
            "complexity": result.complexity,
            "confidence": result.confidence,
            "rationale": result.rationale,
            "elapsed_sec": result.elapsed_sec,
        }
        p.write_text(json.dumps(payload, indent=2))


# Bump this when the judge invocation logic changes in a way that should
# invalidate cached judgments. (Cache also auto-invalidates when the bundled
# SKILL.md or agent.md content changes.)
_JUDGE_LOGIC_VERSION = "v4-api-providers-anthropic-openai-ollama"


def _prompt_version() -> str:
    """Stable hash of judge-logic + bundled SKILL.md + agent.md.

    Bumping `_JUDGE_LOGIC_VERSION` or modifying either bundled file
    automatically invalidates all cached judgments under the old key.
    """
    from importlib import resources
    h = hashlib.sha256()
    h.update(_JUDGE_LOGIC_VERSION.encode())
    for relpath in (
        "claude/skills/effort-estimation/SKILL.md",
        "claude/agents/effort-judge.md",
    ):
        try:
            content = resources.files("ai_dev_effectiveness.data").joinpath(relpath).read_text()
            h.update(content.encode())
        except (FileNotFoundError, ModuleNotFoundError):
            pass
    return h.hexdigest()[:12]


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------

_SIZE_BUCKETS = [
    ("tiny", 10),
    ("small", 100),
    ("medium", 500),
    ("large", 2000),
    ("mega", float("inf")),
]


def _size_bucket(total_loc: int) -> str:
    for name, ceiling in _SIZE_BUCKETS:
        if total_loc < ceiling:
            return name
    return "mega"


def stratified_sample(
    commits: pd.DataFrame, sample_size: int, skip_below_loc: int, judge_all: bool,
) -> pd.DataFrame:
    """Pick commits to judge.

    Stratifies by (primary_domain, size_bucket) and takes up to `sample_size` per stratum.
    Returns a copy with only the sampled rows.
    """
    if commits.empty:
        return commits

    eligible = commits[commits["insertions"] + commits["deletions"] >= skip_below_loc].copy()
    if judge_all:
        return eligible

    eligible["_size_bucket"] = (eligible["insertions"] + eligible["deletions"]).apply(_size_bucket)
    domain_col = "primary_domain" if "primary_domain" in eligible.columns else None

    if domain_col is None:
        eligible["_stratum"] = eligible["_size_bucket"]
    else:
        eligible["_stratum"] = eligible[domain_col].astype(str) + "::" + eligible["_size_bucket"]

    parts = []
    for _, group in eligible.groupby("_stratum"):
        parts.append(group.sample(n=min(sample_size, len(group)), random_state=42))
    if not parts:
        return eligible.iloc[0:0]
    sampled = pd.concat(parts).sort_values("date").reset_index(drop=True)
    sampled = sampled.drop(columns=["_size_bucket", "_stratum"])
    return sampled


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def judge_commits(
    commits: pd.DataFrame, repo: Path, cfg: JudgeConfig,
    progress_callback: callable | None = None,
) -> pd.DataFrame:
    """Run judgments on a sampled subset of commits and return enriched DataFrame.

    Adds columns: judge_human_hours, judge_ai_hours, judge_complexity,
                  judge_confidence, judge_rationale, judge_cached, judge_elapsed_sec.

    Un-sampled commits get NaN in the judge_* columns.
    """
    out = commits.copy()
    for col in ("judge_human_hours", "judge_ai_hours", "judge_complexity",
                "judge_confidence", "judge_rationale", "judge_cached",
                "judge_elapsed_sec"):
        out[col] = pd.NA

    sampled = stratified_sample(commits, cfg.sample_size, cfg.skip_below_loc, cfg.judge_all)
    if sampled.empty:
        return out

    provider = make_provider(cfg)
    cache = JudgeCache(cfg.cache_dir, provider.name(), provider.model_id(), _prompt_version())

    for i, sha in enumerate(sampled["sha"].tolist(), start=1):
        result = cache.get(sha)
        if result is None:
            if progress_callback:
                progress_callback(i, len(sampled), sha, cached=False)
            try:
                result = provider.judge(sha, repo)
            except Exception as e:
                # Record failure as 'low confidence' rather than crashing.
                result = JudgeResult(
                    sha=sha, human_hours=0, ai_assisted_hours=0,
                    complexity="trivial", confidence="low",
                    rationale=f"Judge failed: {type(e).__name__}: {str(e)[:120]}",
                )
            cache.put(result)
        elif progress_callback:
            progress_callback(i, len(sampled), sha, cached=True)

        idx = out.index[out["sha"] == sha]
        out.loc[idx, "judge_human_hours"] = result.human_hours
        out.loc[idx, "judge_ai_hours"] = result.ai_assisted_hours
        out.loc[idx, "judge_complexity"] = result.complexity
        out.loc[idx, "judge_confidence"] = result.confidence
        out.loc[idx, "judge_rationale"] = result.rationale
        out.loc[idx, "judge_cached"] = result.cached
        out.loc[idx, "judge_elapsed_sec"] = result.elapsed_sec

    return out


def summarize_judgments(commits: pd.DataFrame, total_commits: int) -> dict[str, Any]:
    """Stratified extrapolation: per-stratum mean × stratum count → total estimate."""
    judged = commits[commits["judge_human_hours"].notna()].copy()
    if judged.empty:
        return {}

    judged["_size_bucket"] = (judged["insertions"] + judged["deletions"]).apply(_size_bucket)
    domain_col = "primary_domain" if "primary_domain" in judged.columns else None

    if domain_col is None:
        judged["_stratum"] = judged["_size_bucket"]
        all_commits = commits.assign(
            _stratum=(commits["insertions"] + commits["deletions"]).apply(_size_bucket)
        )
    else:
        judged["_stratum"] = judged[domain_col].astype(str) + "::" + judged["_size_bucket"]
        all_commits = commits.assign(
            _stratum=commits[domain_col].astype(str) + "::" +
                     (commits["insertions"] + commits["deletions"]).apply(_size_bucket)
        )

    stratum_counts = all_commits["_stratum"].value_counts().to_dict()

    total_h, total_ai, by_complexity = 0.0, 0.0, defaultdict(int)
    per_stratum_h: list[float] = []
    for stratum, grp in judged.groupby("_stratum"):
        mean_h = float(grp["judge_human_hours"].mean())
        mean_ai = float(grp["judge_ai_hours"].mean())
        n = stratum_counts.get(stratum, len(grp))
        total_h += mean_h * n
        total_ai += mean_ai * n
        per_stratum_h.append(mean_h * n)
    for tier, count in judged["judge_complexity"].value_counts().items():
        by_complexity[tier] = int(count)

    multiplier = total_h / total_ai if total_ai else 0
    return {
        "total_human_hours": round(total_h, 1),
        "total_ai_hours": round(total_ai, 1),
        "multiplier": round(multiplier, 2),
        "n_judged": len(judged),
        "n_total": total_commits,
        "by_complexity": dict(by_complexity),
        "per_stratum_estimates": [round(x, 1) for x in per_stratum_h],
        "judged_provider": None,  # filled in by caller
    }


# ---------------------------------------------------------------------------
# init-judge: copy bundled artifacts into the user's repo
# ---------------------------------------------------------------------------

def install_judge_artifacts(repo: Path, force: bool = False) -> dict[str, str]:
    """Copy bundled effort-judge agent + skill into <repo>/.claude/.

    Returns a dict mapping action ('agent', 'skill', 'settings_recommended')
    to the path of the installed (or skipped) file.
    """
    from importlib import resources

    targets = {
        "agent": (
            "claude/agents/effort-judge.md",
            repo / ".claude" / "agents" / "effort-judge.md",
        ),
        "skill": (
            "claude/skills/effort-estimation/SKILL.md",
            repo / ".claude" / "skills" / "effort-estimation" / "SKILL.md",
        ),
    }
    actions: dict[str, str] = {}
    for kind, (src_rel, dst) in targets.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and not force:
            actions[kind] = f"SKIP  {dst} (already exists)"
            continue
        content = resources.files("ai_dev_effectiveness.data").joinpath(src_rel).read_text()
        dst.write_text(content)
        actions[kind] = f"WROTE {dst}"

    # Also write a copy of the recommended settings snippet so the user can merge it.
    rec = resources.files("ai_dev_effectiveness.data").joinpath(
        "claude/settings.recommended.json"
    ).read_text()
    rec_path = repo / ".claude" / "settings.recommended.json"
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    rec_path.write_text(rec)
    actions["settings_recommended"] = f"WROTE {rec_path}"

    return actions


def claude_on_path() -> str | None:
    """Return path to `claude` binary if installed, else None."""
    from shutil import which
    return which("claude")
