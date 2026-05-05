"""Estimate engineering effort two ways: bottom-up per-commit and top-down per-role.

The bottom-up estimator is a faithful port of the original notebook's
formula:

    For each commit:
        loc_capped = min(insertions + deletions, loc_soft_cap)
        For each language group g present in the commit:
            hours_g = frac_g × (g.base_hours + loc_capped × g.hours_per_loc)
        hours = Σ hours_g
        if commit touches >1 domain:   hours ×= integration_multiplier
        hours ×= test_debug_multiplier
        hours = min(hours, max_hours_per_commit)

The top-down estimator just sums user-specified person-month ranges per role.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .defaults import DEFAULT_EFFORT, DEFAULT_LANGUAGE_GROUPS, EffortConstants, LanguageGroup


@dataclass
class Role:
    role: str
    scope: str
    loc: int
    pm_low: float
    pm_high: float
    color: str = "#888888"

    @property
    def pm_mid(self) -> float:
        return (self.pm_low + self.pm_high) / 2


def bottom_up_hours(
    commits: pd.DataFrame,
    effort: EffortConstants | None = None,
    language_groups: dict[str, LanguageGroup] | None = None,
) -> pd.DataFrame:
    """Add `traditional_hours_est` column to commits.

    Returns a copy.
    """
    effort = effort or DEFAULT_EFFORT
    groups = language_groups or DEFAULT_LANGUAGE_GROUPS

    # Build extension → (group_name, group) lookup. Longest extension wins.
    ext_lookup: list[tuple[str, str, LanguageGroup]] = []
    for gname, g in groups.items():
        for ext in g.extensions:
            ext_lookup.append((ext, gname, g))
    ext_lookup.sort(key=lambda x: -len(x[0]))

    def _classify(filename: str) -> str | None:
        for ext, gname, _ in ext_lookup:
            if filename.endswith(ext):
                return gname
        return None

    out = commits.copy()
    if commits.empty:
        out["traditional_hours_est"] = []
        return out

    n_domains_col = out["n_domains"] if "n_domains" in out.columns else pd.Series([1] * len(out))

    estimates: list[float] = []
    for files, ins, dels, n_dom in zip(
        out["files_changed"], out["insertions"], out["deletions"], n_domains_col,
        strict=False,
    ):
        if not files:
            estimates.append(0.0)
            continue

        total_loc = (ins or 0) + (dels or 0)
        loc_capped = min(total_loc, effort.loc_soft_cap)

        # Per-group file fractions.
        per_group_count: dict[str, int] = {gname: 0 for gname in groups.keys()}
        unclassified = 0
        for f in files:
            g = _classify(f)
            if g is None:
                unclassified += 1
            else:
                per_group_count[g] += 1

        # Unclassified files count as 'dynamic' if the group exists, else as 'config'.
        fallback = "dynamic" if "dynamic" in groups else next(iter(groups.keys()))
        if unclassified:
            per_group_count[fallback] = per_group_count.get(fallback, 0) + unclassified

        total_files = sum(per_group_count.values()) or 1

        hours = 0.0
        for gname, count in per_group_count.items():
            if count == 0:
                continue
            frac = count / total_files
            g = groups[gname]
            hours += frac * (g.base_hours + loc_capped * g.hours_per_loc)

        if (n_dom or 0) > 1:
            hours *= effort.integration_multiplier

        hours *= effort.test_debug_multiplier
        hours = min(hours, effort.max_hours_per_commit)
        estimates.append(hours)

    out["traditional_hours_est"] = estimates
    return out


def top_down_person_months(roles: list[Role]) -> pd.DataFrame:
    """Return DataFrame with role, scope, pm_low, pm_high, pm_mid, loc, color.

    The summary totals (sum across rows) are downstream consumers' job.
    """
    rows = [
        {
            "role": r.role,
            "scope": r.scope,
            "loc": r.loc,
            "pm_low": r.pm_low,
            "pm_high": r.pm_high,
            "pm_mid": r.pm_mid,
            "color": r.color,
        }
        for r in roles
    ]
    return pd.DataFrame(rows)


def total_person_months(roles_df: pd.DataFrame) -> tuple[float, float, float]:
    """Return (low, high, mid) totals from the roles DataFrame."""
    if roles_df.empty:
        return (0.0, 0.0, 0.0)
    return (
        float(roles_df["pm_low"].sum()),
        float(roles_df["pm_high"].sum()),
        float(roles_df["pm_mid"].sum()),
    )
