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

_JUDGMENT_JSON_SCHEMA = (
    '{"type":"object",'
    '"properties":{'
    '"human_hours":{"type":"number","minimum":0},'
    '"ai_assisted_hours":{"type":"number","minimum":0},'
    '"complexity":{"enum":["trivial","small","medium","large","architectural"]},'
    '"confidence":{"enum":["low","medium","high"]},'
    '"rationale":{"type":"string","maxLength":300}'
    '},'
    '"required":["human_hours","ai_assisted_hours","complexity","confidence","rationale"],'
    '"additionalProperties":false}'
)


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
        raise NotImplementedError("anthropic-api provider not implemented yet")
    if cfg.provider == "openai":
        raise NotImplementedError("openai provider not implemented yet")
    if cfg.provider == "ollama":
        raise NotImplementedError("ollama provider not implemented yet")
    raise ValueError(f"Unknown judge provider: {cfg.provider}")


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
_JUDGE_LOGIC_VERSION = "v3-output-format-json-structured-output"


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
        if progress_callback:
            progress_callback(i, len(sampled), sha)

        result = cache.get(sha)
        if result is None:
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
