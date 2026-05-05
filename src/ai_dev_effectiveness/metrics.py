"""Pure aggregation functions over the commits + LOC DataFrames.

Each function takes the enriched commits DataFrame (after agent_detector +
domain_classifier + effort_estimator) and returns a small DataFrame or dict
suitable for charts. No plotly here — this module is testable on its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class MetricsBundle:
    """Everything the report renderer needs, in one place."""
    headline: dict[str, Any]
    weekly: pd.DataFrame
    by_agent: pd.DataFrame
    by_author: pd.DataFrame
    by_domain: pd.DataFrame
    by_complexity: pd.DataFrame | None       # populated only when judge ran
    cumulative_loc: pd.DataFrame
    author_agent_matrix: pd.DataFrame
    judge_summary: dict[str, Any] | None     # populated only when judge ran


def headline(commits: pd.DataFrame, loc_total: int, project_months: float | None) -> dict[str, Any]:
    """Top-of-report numbers."""
    if commits.empty:
        return {
            "n_commits": 0, "n_ai_assisted": 0, "n_authors": 0,
            "loc_total": loc_total, "project_months": project_months,
            "first_commit": None, "last_commit": None,
        }
    n_ai = int((commits["primary_agent"].notna()).sum())
    return {
        "n_commits": len(commits),
        "n_ai_assisted": n_ai,
        "ai_assisted_pct": n_ai / len(commits) if len(commits) else 0,
        "n_authors": commits["author_name"].nunique(),
        "loc_total": loc_total,
        "project_months": project_months,
        "first_commit": commits["date"].min(),
        "last_commit": commits["date"].max(),
    }


def derive_team_description(commits: pd.DataFrame, team_size: int | None = None) -> str:
    """Generate a default team-description label from observed git data.

    Used when the user doesn't supply `project.team_description`. Combines
    author count with the top 1–2 detected AI agents, producing labels like:

        "1 developer + Claude Opus"
        "2 developers + Claude Opus, GitHub Copilot"
        "1 developer (no AI detected)"
    """
    if commits.empty:
        return "1 developer"
    n_humans = team_size or commits["author_name"].nunique() or 1
    suffix = "developer" if n_humans == 1 else "developers"

    if "agents" in commits.columns:
        from collections import Counter
        counter: Counter[str] = Counter()
        for agents_list in commits["agents"]:
            for a in (agents_list or []):
                if not a:
                    continue
                # Skip the catch-all "Claude (other)" entry — it's noise that
                # always pairs with the more-specific match.
                if a == "Claude (other)":
                    continue
                counter[a] += 1
        top_agents = [name for name, _ in counter.most_common(2)]
    else:
        top_agents = []

    if top_agents:
        return f"{n_humans} {suffix} + {', '.join(top_agents)}"
    return f"{n_humans} {suffix} (no AI detected)"


def by_agent(commits: pd.DataFrame) -> pd.DataFrame:
    """Per-agent totals.

    Each commit can match multiple agents — counts here are 'commits where
    this agent appeared at least once', so they may sum to > total commits.
    """
    if commits.empty or "agents" not in commits.columns:
        return pd.DataFrame(columns=["agent", "commits", "insertions", "deletions", "net_loc"])

    exploded = commits.explode("agents").rename(columns={"agents": "agent"})
    exploded = exploded[exploded["agent"].notna()]
    if exploded.empty:
        return pd.DataFrame(columns=["agent", "commits", "insertions", "deletions", "net_loc"])

    agg = exploded.groupby("agent").agg(
        commits=("sha", "count"),
        insertions=("insertions", "sum"),
        deletions=("deletions", "sum"),
        net_loc=("net_loc", "sum"),
    ).reset_index().sort_values("commits", ascending=False)
    return agg


def by_author(commits: pd.DataFrame) -> pd.DataFrame:
    """Per-human-author totals."""
    if commits.empty:
        return pd.DataFrame(columns=["author_name", "commits", "insertions", "deletions",
                                     "net_loc", "ai_assisted_commits"])
    has_agent = commits["primary_agent"].notna()
    agg = commits.groupby("author_name").agg(
        commits=("sha", "count"),
        insertions=("insertions", "sum"),
        deletions=("deletions", "sum"),
        net_loc=("net_loc", "sum"),
    ).reset_index()
    ai_per_author = commits[has_agent].groupby("author_name").size().rename("ai_assisted_commits")
    agg = agg.merge(ai_per_author, how="left", on="author_name").fillna({"ai_assisted_commits": 0})
    agg["ai_assisted_commits"] = agg["ai_assisted_commits"].astype(int)
    return agg.sort_values("commits", ascending=False)


def by_domain(commits: pd.DataFrame) -> pd.DataFrame:
    """Per-domain totals using `primary_domain`."""
    if commits.empty or "primary_domain" not in commits.columns:
        return pd.DataFrame(columns=["domain", "commits", "insertions", "deletions", "net_loc"])
    agg = commits.groupby("primary_domain").agg(
        commits=("sha", "count"),
        insertions=("insertions", "sum"),
        deletions=("deletions", "sum"),
        net_loc=("net_loc", "sum"),
    ).reset_index().rename(columns={"primary_domain": "domain"})
    return agg.sort_values("commits", ascending=False)


def weekly_aggregates(commits: pd.DataFrame) -> pd.DataFrame:
    """Group commits by ISO week. Adds traditional_hours sum if column present."""
    if commits.empty or "year_week" not in commits.columns:
        return pd.DataFrame()

    agg_kwargs = {
        "commits": ("sha", "count"),
        "insertions": ("insertions", "sum"),
        "deletions": ("deletions", "sum"),
        "net_loc": ("net_loc", "sum"),
        "files_changed_total": ("n_files", "sum"),
        "week_start": ("week_start", "first"),
    }
    if "traditional_hours_est" in commits.columns:
        agg_kwargs["traditional_hours"] = ("traditional_hours_est", "sum")
    if "primary_agent" in commits.columns:
        # Count of AI-assisted commits per week.
        commits = commits.copy()
        commits["_ai_assisted"] = commits["primary_agent"].notna().astype(int)
        agg_kwargs["ai_assisted_commits"] = ("_ai_assisted", "sum")

    weekly = commits.groupby("year_week").agg(**agg_kwargs).reset_index().sort_values("week_start")
    weekly["cumulative_loc"] = weekly["net_loc"].cumsum()
    weekly["actual_hours"] = 40.0
    return weekly


def cumulative_loc(commits: pd.DataFrame) -> pd.DataFrame:
    """Per-commit cumulative net LOC, useful for the milestone chart."""
    if commits.empty:
        return pd.DataFrame()
    out = commits[["date", "net_loc", "subject"]].copy().sort_values("date")
    out["cumulative_loc"] = out["net_loc"].cumsum()
    return out


def author_agent_matrix(commits: pd.DataFrame) -> pd.DataFrame:
    """Counts of commits per (human-author × agent) pair."""
    if commits.empty or "agents" not in commits.columns:
        return pd.DataFrame()
    exploded = commits.explode("agents").rename(columns={"agents": "agent"})
    exploded = exploded[exploded["agent"].notna()]
    if exploded.empty:
        return pd.DataFrame()
    return (
        exploded.groupby(["author_name", "agent"])
        .size()
        .reset_index(name="commits")
        .sort_values("commits", ascending=False)
    )


def detect_first_ai_commit(commits: pd.DataFrame) -> pd.Timestamp | None:
    """Date of the first commit with any AI signature — used for phase comparison."""
    if commits.empty or "primary_agent" not in commits.columns:
        return None
    masked = commits[commits["primary_agent"].notna()]
    if masked.empty:
        return None
    return masked["date"].min()


def build_metrics_bundle(
    commits: pd.DataFrame,
    loc_total: int,
    project_months: float | None,
    judge_summary_dict: dict[str, Any] | None = None,
) -> MetricsBundle:
    """Top-level entry point — builds every aggregate the renderer needs."""
    return MetricsBundle(
        headline=headline(commits, loc_total, project_months),
        weekly=weekly_aggregates(commits),
        by_agent=by_agent(commits),
        by_author=by_author(commits),
        by_domain=by_domain(commits),
        by_complexity=_by_complexity(commits) if "judge_complexity" in commits.columns else None,
        cumulative_loc=cumulative_loc(commits),
        author_agent_matrix=author_agent_matrix(commits),
        judge_summary=judge_summary_dict,
    )


def _by_complexity(commits: pd.DataFrame) -> pd.DataFrame:
    """Distribution of judged complexity, if the judge ran."""
    judged = commits[commits["judge_complexity"].notna()]
    if judged.empty:
        return pd.DataFrame()
    return (
        judged.groupby("judge_complexity")
        .agg(commits=("sha", "count"),
             mean_human_hours=("judge_human_hours", "mean"),
             mean_ai_hours=("judge_ai_hours", "mean"))
        .reset_index()
    )
