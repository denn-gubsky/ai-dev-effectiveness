"""Default constants for ai-dev-effectiveness.

These ship with the package and are merged with user config. The numbers
are calibrated against industry benchmarks and the original HockeyBot analysis;
they're conservative defaults, not gospel.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LanguageGroup:
    """A bucket of file extensions sharing the same effort rate."""
    extensions: tuple[str, ...]
    base_hours: float          # hours for any commit touching this language
    hours_per_loc: float       # additional hours per LOC changed


# Default language groups. Users override via config.
# Compiled languages: ~15-25 productive LOC/day → ~2h base + 0.03h/LOC
# Dynamic/scripting:  ~30-50 LOC/day            → ~1h base + 0.015h/LOC
# Config:             trivial                    → ~0.5h + 0.005h/LOC
DEFAULT_LANGUAGE_GROUPS: dict[str, LanguageGroup] = {
    "compiled": LanguageGroup(
        extensions=(".cpp", ".hpp", ".h", ".c", ".cc", ".cxx",
                    ".rs", ".go", ".java", ".kt", ".swift", ".m", ".mm"),
        base_hours=2.0,
        hours_per_loc=0.03,
    ),
    "dynamic": LanguageGroup(
        extensions=(".py", ".ts", ".tsx", ".js", ".jsx", ".rb", ".php",
                    ".lua", ".r", ".sh", ".bash", ".zsh", ".launch.py"),
        base_hours=1.0,
        hours_per_loc=0.015,
    ),
    "config": LanguageGroup(
        extensions=(".yaml", ".yml", ".json", ".toml", ".xml", ".ini",
                    ".cfg", ".conf", ".sql", ".urdf", ".xacro",
                    ".msg", ".srv", ".action"),
        base_hours=0.5,
        hours_per_loc=0.005,
    ),
}


@dataclass(frozen=True)
class EffortConstants:
    """Cross-language multipliers and caps for the bottom-up estimator."""
    integration_multiplier: float = 1.3   # multi-domain commits
    test_debug_multiplier: float = 1.5    # testing + debugging overhead
    max_hours_per_commit: float = 40.0    # hard cap = 1 work-week
    loc_soft_cap: int = 2000              # soft cap inside the per-LOC term
    senior_engineer_daily_rate_usd: float = 800.0
    ai_subscription_monthly_usd: float = 200.0


DEFAULT_EFFORT = EffortConstants()


# Default Plotly colors — tasteful, colorblind-friendly enough.
DEFAULT_COLORS: dict[str, str] = {
    "primary": "#2563EB",
    "secondary": "#7C3AED",
    "accent": "#059669",
    "warning": "#D97706",
    "danger": "#DC2626",
    "muted": "#6B7280",
    "light": "#F3F4F6",
}


# Domains autoderived from top-level dirs get colors cycling through this palette.
DEFAULT_DOMAIN_PALETTE: tuple[str, ...] = (
    "#2563EB", "#DC2626", "#059669", "#7C3AED", "#D97706",
    "#0891B2", "#EC4899", "#8B5CF6", "#F59E0B", "#10B981",
    "#6B7280",  # 'other' bucket
)


# Path patterns that are almost never meaningful effort. Used by the judge
# to skip auto-generated content; users override via config.
DEFAULT_IGNORE_PATHS: tuple[str, ...] = (
    "dist/", "build/", "node_modules/", "vendor/", "third_party/",
    ".venv/", "venv/", "__pycache__/",
    "*.lock", "*.svg", "*.min.js", "*.min.css",
    "package-lock.json", "yarn.lock", "poetry.lock", "Cargo.lock",
)


@dataclass
class JudgeDefaults:
    """Defaults for the optional AI-judge module."""
    provider: str = "claude-cli"
    model: str = "sonnet"
    sample_size: int = 5
    judge_all: bool = False
    skip_below_loc: int = 5
    timeout_sec: int = 60
    max_retries: int = 2
    cache_dir: str = ".ai-dev-effectiveness-cache"
    agent_path: str = ".claude/agents/effort-judge.md"
    skill_path: str = ".claude/skills/effort-estimation/SKILL.md"
    ignore_paths: tuple[str, ...] = field(default_factory=lambda: DEFAULT_IGNORE_PATHS)


DEFAULT_JUDGE = JudgeDefaults()
