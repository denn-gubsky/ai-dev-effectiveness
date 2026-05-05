"""Pydantic config schema + YAML loader + merge with defaults.

The user's `ai_dev.yaml` is merged on top of the built-in defaults. Any field
the user omits keeps its default value.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .defaults import (
    DEFAULT_DOMAIN_PALETTE,
    DEFAULT_EFFORT,
    DEFAULT_IGNORE_PATHS,
    DEFAULT_JUDGE,
    DEFAULT_LANGUAGE_GROUPS,
    EffortConstants,
    LanguageGroup,
)
from .effort_estimator import Role


class ProjectConfig(BaseModel):
    name: str = "Project"
    team_size: int = 1
    human_daily_rate_usd: float = DEFAULT_EFFORT.senior_engineer_daily_rate_usd
    ai_monthly_cost_usd: float = DEFAULT_EFFORT.ai_subscription_monthly_usd
    package_root: str | None = None  # e.g. "src" for src-layout repos
    # Free-form label describing the actual team that built the project,
    # e.g. "1 developer + Claude Code (Opus 4.7)". Used as the bar label in
    # the team-composition comparison chart. If omitted, auto-derived from
    # the author count and detected AI agents.
    team_description: str | None = None


class DomainPattern(BaseModel):
    pattern: str
    color: str = "#888888"


class LanguageGroupConfig(BaseModel):
    extensions: list[str]
    base_hours: float
    hours_per_loc: float


class EffortOverrides(BaseModel):
    integration_multiplier: float | None = None
    test_debug_multiplier: float | None = None
    max_hours_per_commit: float | None = None
    loc_soft_cap: int | None = None


class RoleConfig(BaseModel):
    role: str
    scope: str = ""
    loc: int = 0
    pm_low: float
    pm_high: float
    color: str = "#888888"


class AgentExtension(BaseModel):
    name: str
    pattern: str
    kind: str = "commit_trailer"
    category: str = "ai_agent"
    model_family: str = "unknown"
    vendor: str = "unknown"
    color: str = "#888888"


class AgentsConfig(BaseModel):
    extend: list[AgentExtension] = Field(default_factory=list)
    override: list[AgentExtension] | None = None  # if set, replaces the built-in registry
    # When the user knows the project was AI-assisted but commits don't carry
    # the trailer (squash-merges, pre-trailer-convention work, manual-commit
    # workflows), set this to a registry agent name (e.g. "Claude Opus") to
    # attribute every untagged non-merge commit to that agent.
    assume_untagged: str | None = None


class JudgeConfig(BaseModel):
    enabled: bool = False
    provider: str = DEFAULT_JUDGE.provider
    model: str = DEFAULT_JUDGE.model
    sample_size: int = DEFAULT_JUDGE.sample_size
    judge_all: bool = DEFAULT_JUDGE.judge_all
    skip_below_loc: int = DEFAULT_JUDGE.skip_below_loc
    timeout_sec: int = DEFAULT_JUDGE.timeout_sec
    max_retries: int = DEFAULT_JUDGE.max_retries
    cache_dir: str = DEFAULT_JUDGE.cache_dir
    agent_path: str = DEFAULT_JUDGE.agent_path
    skill_path: str = DEFAULT_JUDGE.skill_path
    ignore_paths: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORE_PATHS))


class OutputConfig(BaseModel):
    formats: list[str] = Field(default_factory=lambda: ["html"])
    out_dir: str = "."
    hide_code: bool = True


class Config(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    domains: dict[str, DomainPattern] = Field(default_factory=dict)
    packages: list[str] = Field(default_factory=list)
    languages: dict[str, LanguageGroupConfig] | None = None
    roles: list[RoleConfig] = Field(default_factory=list)
    effort: EffortOverrides = Field(default_factory=EffortOverrides)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    milestones: dict[str, str] = Field(default_factory=dict)
    output: OutputConfig = Field(default_factory=OutputConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)

    # ---- accessors that return non-pydantic types ready for the modules ----

    def language_groups(self) -> dict[str, LanguageGroup]:
        if self.languages is None:
            return DEFAULT_LANGUAGE_GROUPS
        return {
            name: LanguageGroup(
                extensions=tuple(g.extensions),
                base_hours=g.base_hours,
                hours_per_loc=g.hours_per_loc,
            )
            for name, g in self.languages.items()
        }

    def effort_constants(self) -> EffortConstants:
        d = DEFAULT_EFFORT.__dict__.copy()
        if self.effort.integration_multiplier is not None:
            d["integration_multiplier"] = self.effort.integration_multiplier
        if self.effort.test_debug_multiplier is not None:
            d["test_debug_multiplier"] = self.effort.test_debug_multiplier
        if self.effort.max_hours_per_commit is not None:
            d["max_hours_per_commit"] = self.effort.max_hours_per_commit
        if self.effort.loc_soft_cap is not None:
            d["loc_soft_cap"] = self.effort.loc_soft_cap
        d["senior_engineer_daily_rate_usd"] = self.project.human_daily_rate_usd
        d["ai_subscription_monthly_usd"] = self.project.ai_monthly_cost_usd
        return EffortConstants(**d)

    def domain_patterns(self) -> dict[str, str]:
        return {name: dp.pattern for name, dp in self.domains.items()}

    def domain_colors(self) -> dict[str, str]:
        out = {name: dp.color for name, dp in self.domains.items()}
        out.setdefault("other", DEFAULT_DOMAIN_PALETTE[-1])
        return out

    def roles_list(self) -> list[Role]:
        return [
            Role(role=r.role, scope=r.scope, loc=r.loc,
                 pm_low=r.pm_low, pm_high=r.pm_high, color=r.color)
            for r in self.roles
        ]


def load(path: Path | None) -> Config:
    """Load YAML config from `path` and validate. If path is None, return defaults."""
    if path is None:
        return Config()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    return Config(**raw)


def example_config_text() -> str:
    """Return the contents of `data/example-config.yaml` shipped with the package."""
    return resources.files("ai_dev_effectiveness.data").joinpath("example-config.yaml").read_text()
