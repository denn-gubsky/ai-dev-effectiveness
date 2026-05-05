"""Suggest specialist roles for a target codebase using the bundled
`roles-architect` Claude Code subagent.

The output is a list of `{role, scope, loc, pm_low, pm_high, color}` dicts —
the same shape the YAML config's `roles:` section accepts. The CLI's
`suggest-roles` subcommand renders this as a YAML snippet for the user to
paste into their `ai_dev.yaml`.

Like the judge, this module spawns `claude --print` with the bundled subagent
loaded inline (via `--append-system-prompt` + `--allowedTools` + `--json-schema`)
so it works regardless of whether the agent is registered in the target's
agent discovery path.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .judge import _read_agent_definition

_BUNDLED_AGENT_RELPATH = "claude/agents/roles-architect.md"


_ROLES_JSON_SCHEMA = (
    '{"type":"object",'
    '"properties":{'
    '"roles":{"type":"array","minItems":1,"items":{'
    '"type":"object",'
    '"properties":{'
    '"role":{"type":"string"},'
    '"scope":{"type":"string"},'
    '"loc":{"type":"integer","minimum":0},'
    '"pm_low":{"type":"number","minimum":0},'
    '"pm_high":{"type":"number","minimum":0},'
    '"color":{"type":"string"}'
    '},'
    '"required":["role","scope","loc","pm_low","pm_high"]'
    '}},'
    '"rationale":{"type":"string","maxLength":600}'
    '},'
    '"required":["roles"],'
    '"additionalProperties":false}'
)


@dataclass
class RolesSuggestion:
    roles: list[dict[str, Any]]
    rationale: str
    elapsed_sec: float = 0.0
    raw_response: str | None = None


def install_roles_architect(workspace: Path, force: bool = False) -> Path:
    """Copy the bundled `roles-architect.md` agent into `<workspace>/.claude/agents/`.

    Mirrors the install flow used by `init-judge` for the effort-judge
    subagent. Returns the destination path (whether newly written or already
    present).
    """
    dst = workspace / ".claude" / "agents" / "roles-architect.md"
    if dst.exists() and not force:
        return dst
    src = resources.files("ai_dev_effectiveness.data").joinpath(_BUNDLED_AGENT_RELPATH)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text())
    return dst


def suggest_roles(
    target: Path,
    workspace: Path,
    model: str = "sonnet",
    timeout_sec: int = 180,
) -> RolesSuggestion:
    """Run `claude --print` with the bundled roles-architect agent against `target`.

    Args:
        target: path to the target git repo (read-only).
        workspace: analyzer workspace (where the bundled agent lives — auto-installed if missing).
        model: claude model name; defaults to "sonnet".
        timeout_sec: kill the subprocess after this many seconds.

    Returns:
        RolesSuggestion with parsed `.roles` list and `.rationale`.

    Raises:
        RuntimeError if `claude` isn't on PATH or the subprocess fails.
        ValueError if the response isn't valid structured output.
    """
    from shutil import which

    from .judge import _candidate_json_blocks

    claude_bin = which("claude")
    if not claude_bin:
        raise RuntimeError(
            "Could not find `claude` on PATH. Install Claude Code "
            "(https://claude.ai/code) — `suggest-roles` runs a Claude Code "
            "subagent against your target."
        )

    agent_path = install_roles_architect(workspace)
    agent_body, fm = _read_agent_definition(agent_path)
    tools = fm.get("tools") or "Read,Grep,Glob,Bash(git ls-files:*),Bash(find:*),Bash(wc:*)"
    if isinstance(tools, list):
        tools_str = ",".join(t.strip() for t in tools)
    else:
        tools_str = str(tools)

    chosen_model = fm.get("model") or model

    prompt = (
        "Survey this git repository and propose the specialist roles a "
        "traditional (no-AI) team would need to build it. Output ONLY the JSON "
        "object matching the schema in your agent definition."
    )

    cmd = [
        claude_bin, "--print",
        "--output-format", "json",
        "--model", str(chosen_model),
        "--append-system-prompt", agent_body,
        "--allowedTools", tools_str,
        "--json-schema", _ROLES_JSON_SCHEMA,
        prompt,
    ]

    start = time.monotonic()
    proc = subprocess.run(
        cmd, cwd=str(target), capture_output=True, text=True,
        timeout=timeout_sec, check=False,
    )
    elapsed = time.monotonic() - start

    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
        )

    for candidate in _candidate_json_blocks(proc.stdout):
        try:
            d = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        if "structured_output" in d and isinstance(d["structured_output"], dict):
            d = d["structured_output"]
        if "roles" in d and isinstance(d["roles"], list) and d["roles"]:
            return RolesSuggestion(
                roles=d["roles"],
                rationale=str(d.get("rationale", ""))[:600],
                elapsed_sec=elapsed,
                raw_response=proc.stdout,
            )

    raise ValueError(
        f"Could not extract a valid roles suggestion from {len(proc.stdout)} "
        f"chars of output. First 200 chars: {proc.stdout[:200]!r}"
    )


def render_yaml(suggestion: RolesSuggestion, indent: str = "  ") -> str:
    """Render a RolesSuggestion as a YAML snippet ready to paste under `roles:`.

    We don't pull in PyYAML's dumper here so the output is opinionated and
    consistent — flow style for each role's leaf fields, block style for the
    list, with the rationale as a leading comment.
    """
    lines: list[str] = []
    if suggestion.rationale:
        for sentence in suggestion.rationale.split(". "):
            s = sentence.strip().rstrip(".")
            if s:
                lines.append(f"# {s}.")
        lines.append("")
    lines.append("roles:")
    for r in suggestion.roles:
        role = _yaml_str(r["role"])
        scope = _yaml_str(r["scope"])
        loc = int(r["loc"])
        pm_low = r["pm_low"]
        pm_high = r["pm_high"]
        color = r.get("color") or "#888888"
        lines.append(
            f"{indent}- {{ role: {role}, scope: {scope}, "
            f"loc: {loc}, pm_low: {pm_low}, pm_high: {pm_high}, color: {_yaml_str(color)} }}"
        )
    return "\n".join(lines) + "\n"


def _yaml_str(s: str) -> str:
    """Quote strings that need it; leave simple bare strings alone for readability."""
    if not s:
        return '""'
    if any(c in s for c in ":,#\"'\n\t{}[]&*?|>!%@`"):
        return json.dumps(s)
    if s != s.strip():
        return json.dumps(s)
    if s[0].isdigit() or s.lower() in ("yes", "no", "true", "false", "null", "~"):
        return json.dumps(s)
    return s
