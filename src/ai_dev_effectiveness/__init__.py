"""ai-dev-effectiveness — Measure AI co-programming effectiveness on any git repo.

Public API:

    from ai_dev_effectiveness import analyze
    result = analyze(repo=".", config="ai_dev.yaml")
    result.to_html("report.html")
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from . import (
    agent_detector,
    ast_index,
    domain_classifier,
    effort_estimator,
    git_extractor,
    loc_scanner,
    report_renderer,
)
from . import (
    config as _config,
)
from . import (
    judge as _judge,
)
from . import (
    metrics as _metrics,
)
from .config import Config

__version__ = "0.1.0"
__all__ = ["analyze", "AnalysisResult", "Config", "__version__"]


@dataclass
class AnalysisResult:
    """Everything `analyze()` produced. Use the `to_*` methods to serialize."""
    config: Config
    commits: pd.DataFrame
    loc: pd.DataFrame
    metrics: _metrics.MetricsBundle
    figures: report_renderer.Figures
    workspace: Path = Path(".")
    out_dir: Path = Path(".")

    def default_out_path(self, fmt: str) -> Path:
        return self.out_dir / f"effectiveness-report.{fmt}"

    def to_html(self, path: str | Path | None = None) -> str:
        html = report_renderer.render_html(self.figures, self.config.project.name)
        if path is not None:
            Path(path).write_text(html)
        return html

    def to_json(self, path: str | Path | None = None) -> str:
        """Dump headline metrics, by_agent, by_domain, weekly aggregates, judge_summary."""
        payload: dict[str, Any] = {
            "version": __version__,
            "project": self.config.project.model_dump(),
            "headline": _serialize(self.metrics.headline),
            "by_agent": _df_to_records(self.metrics.by_agent),
            "by_author": _df_to_records(self.metrics.by_author),
            "by_domain": _df_to_records(self.metrics.by_domain),
            "weekly": _df_to_records(self.metrics.weekly),
            "judge_summary": self.metrics.judge_summary,
        }
        out = json.dumps(payload, indent=2, default=str)
        if path is not None:
            Path(path).write_text(out)
        return out


def analyze(
    repo: str | Path = ".",
    config: str | Path | Config | None = None,
    workspace: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> AnalysisResult:
    """Run the full pipeline against `repo` and return an `AnalysisResult`.

    Args:
        repo: path to the target git repo. Walks up to find .git/. Read-only.
        config: path to ai_dev.yaml, an already-loaded Config, or None for defaults.
        workspace: analyzer workspace where the bundled judge agent lives
                   (`<workspace>/.claude/agents/effort-judge.md`). Defaults to cwd.
        out_dir: where to write the report and cache. Defaults to:
                   - `cwd` if the resolved target is the same as cwd, OR
                   - `cwd/<basename(target)>` if the target is elsewhere.
                 Either way, NOTHING is ever written to the target repo.
    """
    repo_path = _find_repo(Path(repo).resolve())
    workspace_path = Path(workspace).resolve() if workspace else Path.cwd().resolve()
    out_dir_path = _resolve_out_dir(out_dir, workspace_path, repo_path)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    if isinstance(config, Config):
        cfg = config
    else:
        cfg = _config.load(config) if config is not None else Config()

    # Reroute judge artefacts to the analyzer workspace and the per-target
    # output dir, regardless of what the YAML config said. This guarantees
    # nothing lands inside the target repo.
    cfg.judge.cache_dir = str(out_dir_path / ".ai-dev-effectiveness-cache")
    cfg.judge.agent_path = str(workspace_path / ".claude/agents/effort-judge.md")
    cfg.judge.skill_path = str(workspace_path / ".claude/skills/effort-estimation/SKILL.md")

    # 1. extract commits
    commits = git_extractor.extract_commits(repo_path)
    commits = git_extractor.add_week_columns(commits)

    # 2. detect agents
    registry = agent_detector.load_builtin_registry()
    if cfg.agents.override is not None:
        registry = agent_detector.load_user_extensions(
            [a.model_dump() for a in cfg.agents.override]
        )
    else:
        registry = registry + agent_detector.load_user_extensions(
            [a.model_dump() for a in cfg.agents.extend]
        )
    commits = agent_detector.detect_agents(commits, registry)

    # 3. classify domains
    patterns = cfg.domain_patterns()
    if not patterns:
        patterns = domain_classifier.autoderive_patterns(repo_path, commits)
    commits = domain_classifier.classify(commits, patterns)

    # 4. effort estimation (bottom-up per commit)
    commits = effort_estimator.bottom_up_hours(
        commits,
        effort=cfg.effort_constants(),
        language_groups=cfg.language_groups(),
    )

    # 5. LOC scan
    packages = cfg.packages or list(patterns.keys())
    package_root = Path(cfg.project.package_root) if cfg.project.package_root else None
    loc_df = loc_scanner.scan(repo_path, packages,
                              language_groups=cfg.language_groups(),
                              package_root=package_root)

    # 6. roles
    roles_df = effort_estimator.top_down_person_months(cfg.roles_list())

    # 7. optional AI judge — runs only if config.judge.enabled
    judge_summary_dict = None
    if cfg.judge.enabled:
        # Best-effort: build the ast-index for the target so the bundled
        # subagent can use mcp__ast-index__* tools for symbol-level lookups.
        ok, msg = ast_index.build(repo_path)
        print(f"  ast-index: {msg}", flush=True)

        judge_cfg = _judge.JudgeConfig(
            provider=cfg.judge.provider, model=cfg.judge.model,
            sample_size=cfg.judge.sample_size, judge_all=cfg.judge.judge_all,
            skip_below_loc=cfg.judge.skip_below_loc,
            timeout_sec=cfg.judge.timeout_sec, max_retries=cfg.judge.max_retries,
            cache_dir=Path(cfg.judge.cache_dir),
            agent_path=Path(cfg.judge.agent_path),
            skill_path=Path(cfg.judge.skill_path),
            ignore_paths=tuple(cfg.judge.ignore_paths),
        )

        def _progress(i, n, sha):
            print(f"  judging {i}/{n}: {sha[:8]}", flush=True)

        commits = _judge.judge_commits(commits, repo_path, judge_cfg,
                                       progress_callback=_progress)
        judge_summary_dict = _judge.summarize_judgments(commits, total_commits=len(commits))
        judge_summary_dict["judged_provider"] = cfg.judge.provider

    # 8. metrics bundle
    project_months = _project_months(commits)
    loc_total = int(loc_df["total"].sum()) if not loc_df.empty else int(commits["insertions"].sum())
    bundle = _metrics.build_metrics_bundle(commits, loc_total, project_months,
                                           judge_summary_dict=judge_summary_dict)

    # 9. figures
    figures = report_renderer.build_figures(
        commits, bundle, loc_df, roles_df if not roles_df.empty else None,
        registry, project_name=cfg.project.name,
    )

    return AnalysisResult(
        config=cfg, commits=commits, loc=loc_df, metrics=bundle, figures=figures,
        workspace=workspace_path, out_dir=out_dir_path,
    )


def _find_repo(start: Path) -> Path:
    p = start
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    raise RuntimeError(f"No git repository found at or above {start}")


def _resolve_out_dir(out_dir: str | Path | None, workspace: Path, target: Path) -> Path:
    """Decide where to write reports and cache.

    Default rule: workspace itself if target == workspace; else workspace/<basename(target)>.
    Explicit `out_dir` always wins.
    """
    if out_dir is not None:
        return Path(out_dir).resolve()
    if target == workspace:
        return workspace
    return workspace / target.name


def _project_months(commits: pd.DataFrame) -> float | None:
    if commits.empty:
        return None
    span = commits["date"].max() - commits["date"].min()
    return round(span.total_seconds() / (60 * 60 * 24 * 30.44), 1)


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


def _serialize(obj):
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (pd.Timestamp, pd.Period)):
        return str(obj)
    return obj
