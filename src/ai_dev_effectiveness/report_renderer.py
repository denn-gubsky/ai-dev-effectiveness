"""Build Plotly figures and assemble them into a standalone HTML report.

The figures are returned as a `Figures` dataclass so callers (CLI or notebook)
can either render the full HTML or display individual charts inline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from jinja2 import BaseLoader, Environment

from .defaults import DEFAULT_COLORS

pio.templates.default = "plotly_white"


@dataclass
class Figures:
    """Lazily populated bag of Plotly figures for the report."""
    headline_table: go.Figure | None = None
    loc_by_package: go.Figure | None = None
    commits_per_week: go.Figure | None = None
    weekly_loc_changes: go.Figure | None = None
    cumulative_loc: go.Figure | None = None
    insertions_by_domain: go.Figure | None = None
    by_agent_bar: go.Figure | None = None
    agent_evolution: go.Figure | None = None
    team_composition: go.Figure | None = None  # roles vs actual team
    pm_comparison: go.Figure | None = None
    cost_comparison: go.Figure | None = None
    three_way_reconciliation: go.Figure | None = None  # only when judge ran
    extras: dict[str, go.Figure] = field(default_factory=dict)


def build_figures(
    commits: pd.DataFrame,
    metrics,           # MetricsBundle
    loc_df: pd.DataFrame,
    roles_df: pd.DataFrame | None,
    registry,
    project_name: str = "Project",
    team_description: str | None = None,
    actual_pm: float = 0.0,
) -> Figures:
    figs = Figures()
    granularity = getattr(metrics, "granularity", "weekly")
    figs.headline_table = _headline_table(metrics.headline, project_name)
    if not loc_df.empty:
        figs.loc_by_package = _loc_by_package(loc_df)
    if not metrics.weekly.empty:
        figs.commits_per_week = _commits_per_period(metrics.weekly, granularity)
        figs.weekly_loc_changes = _period_loc_changes(metrics.weekly, granularity)
        figs.cumulative_loc = _cumulative_loc(metrics.weekly)
    if not metrics.by_agent.empty:
        figs.by_agent_bar = _by_agent_bar(metrics.by_agent, registry)
        figs.agent_evolution = _agent_evolution(commits, registry)
    if not metrics.by_domain.empty and "primary_domain" in commits.columns:
        figs.insertions_by_domain = _insertions_by_domain(commits, granularity)
    if roles_df is not None and not roles_df.empty:
        project_months = metrics.headline.get("project_months") or 0
        figs.team_composition = _team_composition(
            roles_df, actual_pm or project_months, team_description or "Actual team",
        )
        figs.pm_comparison = _pm_comparison(roles_df, project_months)
        figs.cost_comparison = _cost_comparison(roles_df, project_months)
    if metrics.judge_summary:
        figs.three_way_reconciliation = _three_way(metrics, roles_df)
    return figs


# ---------------------------------------------------------------------------
# individual chart builders
# ---------------------------------------------------------------------------

def _headline_table(headline: dict[str, Any], project_name: str) -> go.Figure:
    rows = [
        ("Project", project_name),
        ("Total commits", f'{headline["n_commits"]:,}'),
        ("AI-assisted commits", f'{headline["n_ai_assisted"]:,} '
                                f'({headline.get("ai_assisted_pct", 0):.0%})'),
        ("Distinct authors", str(headline["n_authors"])),
        ("Total LOC (current)", f'{headline["loc_total"]:,}'),
    ]
    if headline.get("first_commit") is not None:
        rows.append(("First commit", headline["first_commit"].strftime("%Y-%m-%d")))
    if headline.get("last_commit") is not None:
        rows.append(("Last commit", headline["last_commit"].strftime("%Y-%m-%d")))
    if headline.get("project_months"):
        rows.append(("Project span (months)", f'{headline["project_months"]:.1f}'))

    fig = go.Figure(go.Table(
        header=dict(values=["Metric", "Value"], fill_color=DEFAULT_COLORS["primary"],
                    font=dict(color="white", size=13), align="left"),
        cells=dict(values=[[r[0] for r in rows], [r[1] for r in rows]],
                   fill_color=DEFAULT_COLORS["light"], align="left",
                   font=dict(size=12), height=28),
    ))
    fig.update_layout(title="Headline", height=320, margin=dict(t=40, b=10))
    return fig


def _loc_by_package(loc_df: pd.DataFrame) -> go.Figure:
    df = loc_df.sort_values("total", ascending=True)
    fig = go.Figure()
    group_cols = [c for c in df.columns if c not in ("package", "total")]
    palette = ["#2563EB", "#059669", "#D97706", "#DC2626", "#7C3AED", "#9CA3AF"]
    for i, col in enumerate(group_cols):
        fig.add_trace(go.Bar(
            y=df["package"], x=df[col], name=col, orientation="h",
            marker_color=palette[i % len(palette)],
        ))
    fig.update_layout(
        title="Lines of Code by Package", xaxis_title="Lines of Code",
        barmode="stack", height=max(300, 40 * len(df) + 100),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _period_labels(granularity: str) -> tuple[str, str, str]:
    """Return (per-period adjective, short noun, title-cased adjective) for chart labels."""
    if granularity == "daily":
        return ("per Day", "day", "Daily")
    return ("per Week", "week", "Weekly")


def _commits_per_period(weekly: pd.DataFrame, granularity: str = "weekly") -> go.Figure:
    per_period, noun, _ = _period_labels(granularity)
    fig = go.Figure(go.Scatter(
        x=weekly["week_start"], y=weekly["commits"], mode="lines+markers",
        line=dict(color=DEFAULT_COLORS["primary"], width=2), marker=dict(size=4),
        name=f"Commits/{noun}",
    ))
    fig.update_layout(title=f"Commits {per_period}", xaxis_title="Date",
                      yaxis_title="Commits", height=380)
    return fig


# Back-compat shim for any external caller pinned to the old name.
_commits_per_week = _commits_per_period


def _period_loc_changes(weekly: pd.DataFrame, granularity: str = "weekly") -> go.Figure:
    _, _, adjective = _period_labels(granularity)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=weekly["week_start"], y=weekly["insertions"], name="Insertions",
        fill="tozeroy", line=dict(color=DEFAULT_COLORS["accent"]),
    ))
    fig.add_trace(go.Scatter(
        x=weekly["week_start"], y=-weekly["deletions"], name="Deletions",
        fill="tozeroy", line=dict(color=DEFAULT_COLORS["danger"]),
    ))
    fig.update_layout(title=f"{adjective} Code Changes", xaxis_title="Date",
                      yaxis_title="LOC (insertions − deletions)", height=380)
    return fig


_weekly_loc_changes = _period_loc_changes


def _cumulative_loc(weekly: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=weekly["week_start"], y=weekly["cumulative_loc"], mode="lines",
        fill="tozeroy", line=dict(color=DEFAULT_COLORS["primary"], width=2),
        fillcolor="rgba(37, 99, 235, 0.1)",
    ))
    fig.update_layout(title="Cumulative Net LOC Over Time", xaxis_title="Date",
                      yaxis_title="Cumulative Net LOC", height=400)
    return fig


def _insertions_by_domain(commits: pd.DataFrame, granularity: str = "weekly") -> go.Figure:
    if granularity == "daily":
        key_col, start_col, adjective = "year_day", "day_start", "Daily"
    else:
        key_col, start_col, adjective = "year_week", "week_start", "Weekly"
    if key_col not in commits.columns:
        return go.Figure()
    pivot = (commits.assign(_=commits["insertions"])
             .groupby([key_col, "primary_domain"])["_"].sum().unstack(fill_value=0))
    period_starts = commits.groupby(key_col)[start_col].first().reindex(pivot.index)

    fig = go.Figure()
    for domain in pivot.columns:
        fig.add_trace(go.Scatter(
            x=period_starts, y=pivot[domain], name=domain,
            stackgroup="one", mode="lines",
        ))
    fig.update_layout(title=f"{adjective} Insertions by Domain (stacked)",
                      xaxis_title="Date", yaxis_title="Insertions",
                      height=420, hovermode="x unified")
    return fig


def _by_agent_bar(by_agent: pd.DataFrame, registry) -> go.Figure:
    color_map = {sig.name: sig.color for sig in registry}
    colors = [color_map.get(name, "#888888") for name in by_agent["agent"]]
    fig = go.Figure(go.Bar(
        x=by_agent["agent"], y=by_agent["commits"],
        marker_color=colors, text=by_agent["commits"], textposition="outside",
    ))
    fig.update_layout(title="Commits by AI Agent",
                      xaxis_title="Agent", yaxis_title="Commits", height=380)
    return fig


def _agent_evolution(commits: pd.DataFrame, registry) -> go.Figure:
    """Stacked bar chart of commits per month, broken down by primary_agent."""
    df = commits.copy()
    df["month"] = df["date"].dt.to_period("M").astype(str)
    df["primary_agent"] = df["primary_agent"].fillna("(no AI)")
    pivot = df.groupby(["month", "primary_agent"]).size().unstack(fill_value=0)

    color_map = {sig.name: sig.color for sig in registry}
    color_map["(no AI)"] = DEFAULT_COLORS["muted"]

    fig = go.Figure()
    for agent_name in pivot.columns:
        fig.add_trace(go.Bar(
            x=pivot.index, y=pivot[agent_name], name=agent_name,
            marker_color=color_map.get(agent_name, "#888888"),
        ))
    fig.update_layout(title="AI-Tooling Evolution — Commits per Month",
                      xaxis_title="Month", yaxis_title="Commits",
                      barmode="stack", height=400,
                      legend=dict(orientation="h", yanchor="bottom",
                                  y=1.02, xanchor="right", x=1))
    return fig


def _team_composition(
    roles_df: pd.DataFrame, actual_pm: float, actual_team_label: str,
) -> go.Figure:
    """Side-by-side stacked bars comparing the suggested vs actual team.

    Left column: traditional team — one stacked segment per specialist role,
    sized by mid-point person-months, colored from the role config.
    Right column: actual team — single block sized by `actual_pm`, labeled
    with `actual_team_label`.
    """
    fig = go.Figure()

    # Stacked segments for the suggested team — one trace per role so each
    # gets its own legend entry and color.
    for _, r in roles_df.iterrows():
        fig.add_trace(go.Bar(
            x=["Traditional team (suggested)"],
            y=[r["pm_mid"]],
            name=f"{r['role']} ({r.get('scope', '')[:40]})",
            marker_color=r.get("color") or "#888888",
            text=f"{r['pm_mid']:.1f} PM",
            textposition="inside",
            insidetextanchor="middle",
            hovertemplate=(
                f"<b>{r['role']}</b><br>"
                f"Scope: {r.get('scope', '')}<br>"
                f"PM range: {r.get('pm_low', 0):.0f} – {r.get('pm_high', 0):.0f} "
                f"(mid {r['pm_mid']:.1f})<br>LOC: {r.get('loc', 0):,}<extra></extra>"
            ),
        ))

    # The actual team is a single block; legend entry uses the user-supplied
    # description so the contrast is unambiguous.
    fig.add_trace(go.Bar(
        x=["Actual team"],
        y=[actual_pm],
        name=actual_team_label,
        marker_color=DEFAULT_COLORS["secondary"],
        text=f"{actual_pm:.2f} PM",
        textposition="outside",
        hovertemplate=f"<b>{actual_team_label}</b><br>{actual_pm:.2f} person-months<extra></extra>",
    ))

    total_trad_pm = float(roles_df["pm_mid"].sum())
    multiplier_str = (
        f"{total_trad_pm / actual_pm:.1f}×" if actual_pm > 0 else "—"
    )

    fig.update_layout(
        title=(
            f"Team composition — {len(roles_df)} specialist roles "
            f"({total_trad_pm:.0f} PM) vs {actual_team_label} "
            f"({actual_pm:.2f} PM, {multiplier_str} multiplier)"
        ),
        yaxis_title="Person-months",
        barmode="stack",
        height=520,
        legend=dict(
            orientation="v", yanchor="top", y=1.0, xanchor="left", x=1.02,
            font=dict(size=10),
        ),
        margin=dict(r=320),  # leave room for the legend
    )
    return fig


def _pm_comparison(roles_df: pd.DataFrame, project_months: float) -> go.Figure:
    pm_mid = float(roles_df["pm_mid"].sum())
    pm_low = float(roles_df["pm_low"].sum())
    pm_high = float(roles_df["pm_high"].sum())
    actual_pm = project_months  # 1 dev × N months for solo case; multiply if team_size > 1

    fig = go.Figure(go.Bar(
        x=["Traditional team (estimated)", "Actual"],
        y=[pm_mid, actual_pm],
        error_y=dict(type="data",
                     symmetric=False,
                     array=[pm_high - pm_mid, 0],
                     arrayminus=[pm_mid - pm_low, 0]),
        text=[f"{pm_mid:.0f} PM", f"{actual_pm:.0f} PM"],
        textposition="outside",
        marker_color=[DEFAULT_COLORS["muted"], DEFAULT_COLORS["secondary"]],
    ))
    multiplier = pm_mid / actual_pm if actual_pm else 0
    fig.update_layout(
        title=f"Person-Months: Traditional vs Actual ({multiplier:.1f}× multiplier)",
        yaxis_title="Person-Months", height=400,
    )
    return fig


def _cost_comparison(roles_df: pd.DataFrame, project_months: float) -> go.Figure:
    from .defaults import DEFAULT_EFFORT
    pm_mid = float(roles_df["pm_mid"].sum())
    rate = DEFAULT_EFFORT.senior_engineer_daily_rate_usd
    sub = DEFAULT_EFFORT.ai_subscription_monthly_usd
    trad_cost = pm_mid * 22 * rate
    actual_cost = project_months * 22 * rate + project_months * sub

    fig = go.Figure(go.Bar(
        x=["Traditional team", "Actual (1 dev + AI)"],
        y=[trad_cost, actual_cost],
        text=[f"${trad_cost:,.0f}", f"${actual_cost:,.0f}"],
        textposition="outside",
        marker_color=[DEFAULT_COLORS["muted"], DEFAULT_COLORS["secondary"]],
    ))
    multiplier = trad_cost / actual_cost if actual_cost else 0
    fig.update_layout(
        title=f"Estimated Total Cost ({multiplier:.1f}× savings)",
        yaxis_title="USD", height=400,
    )
    return fig


def _three_way(metrics, roles_df: pd.DataFrame | None) -> go.Figure:
    """Top-down × bottom-up × judge reconciliation chart."""
    js = metrics.judge_summary or {}
    bottom_up = float(metrics.weekly["traditional_hours"].sum()) if "traditional_hours" in metrics.weekly.columns else 0.0
    top_down = 0.0
    if roles_df is not None and not roles_df.empty:
        top_down = float(roles_df["pm_mid"].sum()) * 160  # PM → hours
    judge = float(js.get("total_human_hours", 0.0))

    labels = ["Top-down (roles × 160h)", "Bottom-up (formula)", "AI judge"]
    values = [top_down, bottom_up, judge]
    fig = go.Figure(go.Bar(
        x=labels, y=values,
        text=[f"{v:,.0f}" for v in values], textposition="outside",
        marker_color=[DEFAULT_COLORS["primary"], DEFAULT_COLORS["accent"], DEFAULT_COLORS["secondary"]],
    ))
    nonzero = [v for v in values if v > 0]
    if len(nonzero) >= 2 and max(nonzero) / min(nonzero) > 2:
        fig.update_layout(title="Three-Way Reconciliation — ⚠️ estimators disagree (>2× spread)")
    else:
        fig.update_layout(title="Three-Way Reconciliation — estimators agree within 2×")
    fig.update_layout(yaxis_title="Estimated total human-hours", height=420)
    return fig


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ai-dev-effectiveness — {{ project_name }}</title>
<style>
  body { font: 14px/1.5 -apple-system, system-ui, sans-serif; max-width: 1100px;
         margin: 2rem auto; padding: 0 1.5rem; color: #111827; }
  h1 { color: #2563EB; }
  h2 { color: #1F2937; border-bottom: 1px solid #E5E7EB; padding-bottom: 0.4rem;
       margin-top: 2.5rem; }
  .chart { margin: 1.5rem 0; }
  .footer { color: #6B7280; font-size: 12px; margin-top: 3rem;
            border-top: 1px solid #E5E7EB; padding-top: 1rem; }
  .callout { background: #FEF3C7; border-left: 4px solid #D97706;
             padding: 0.8rem 1rem; margin: 1rem 0; border-radius: 4px; }
</style>
</head>
<body>
<h1>AI Co-Programming Effectiveness — {{ project_name }}</h1>
<p>Generated by <a href="https://github.com/dennisgubsky/ai-dev-effectiveness">ai-dev-effectiveness</a>.</p>

<h2>Headline</h2>
{{ figs.headline_table }}

{% if figs.loc_by_package %}<h2>Codebase</h2>{{ figs.loc_by_package }}{% endif %}

<h2>Velocity</h2>
{% if figs.commits_per_week %}<div class="chart">{{ figs.commits_per_week }}</div>{% endif %}
{% if figs.weekly_loc_changes %}<div class="chart">{{ figs.weekly_loc_changes }}</div>{% endif %}
{% if figs.cumulative_loc %}<div class="chart">{{ figs.cumulative_loc }}</div>{% endif %}
{% if figs.insertions_by_domain %}<div class="chart">{{ figs.insertions_by_domain }}</div>{% endif %}

<h2>AI Co-Programming</h2>
{% if figs.by_agent_bar %}<div class="chart">{{ figs.by_agent_bar }}</div>
{% else %}<div class="callout">No AI co-author signatures detected. If you expected
some, run <code>ai-dev-effectiveness list-agents</code> to see what the built-in
registry covers, or extend it via <code>ai_dev.yaml</code>.</div>{% endif %}
{% if figs.agent_evolution %}<div class="chart">{{ figs.agent_evolution }}</div>{% endif %}

{% if figs.team_composition %}<h2>Team Composition</h2>
<div class="chart">{{ figs.team_composition }}</div>{% endif %}

{% if figs.pm_comparison %}<h2>Productivity Multipliers</h2>
<div class="chart">{{ figs.pm_comparison }}</div>
<div class="chart">{{ figs.cost_comparison }}</div>
{% else %}<h2>Productivity Multipliers</h2>
<div class="callout">No specialist roles configured — top-down comparison
skipped. Run <code>ai-dev-effectiveness suggest-roles &lt;target&gt; --apply</code>
to have Claude propose the team composition for you, or define
<code>roles:</code> manually in your <code>ai_dev.yaml</code>.</div>
{% endif %}

{% if figs.three_way_reconciliation %}<h2>Estimator Reconciliation</h2>
<div class="chart">{{ figs.three_way_reconciliation }}</div>{% endif %}

<div class="footer">
  Generated {{ now }} ·
  Triangulates top-down (specialist roles), bottom-up (per-commit formula), and
  optional AI judge (Claude reading actual diffs). When all three agree within
  2×, the multipliers are credible.
</div>
</body>
</html>
"""


def render_html(figures: Figures, project_name: str = "Project") -> str:
    """Embed every populated figure into a single self-contained HTML page."""
    from datetime import datetime

    env = Environment(loader=BaseLoader(), autoescape=False)
    tmpl = env.from_string(_HTML_TEMPLATE)

    def _fig_html(fig: go.Figure | None) -> str:
        if fig is None:
            return ""
        return pio.to_html(fig, include_plotlyjs="cdn", full_html=False,
                           config={"displaylogo": False})

    rendered = {name: _fig_html(getattr(figures, name)) for name in Figures.__dataclass_fields__
                if name != "extras"}

    class _Wrap:
        pass
    wrap = _Wrap()
    for name, html in rendered.items():
        setattr(wrap, name, html)

    return tmpl.render(
        figs=wrap,
        project_name=project_name,
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )


def write_html(figures: Figures, path: Path, project_name: str = "Project") -> None:
    Path(path).write_text(render_html(figures, project_name))
