"""Classify each commit by which 'domain' (subsystem) it touches.

A commit is associated with every domain whose pattern matches at least one
of its changed files. Patterns are regexes applied to file paths.

The auto-derivation path picks domains from the most-touched top-level
directories when the user provides no patterns — gives a useful zero-config
report on any repo.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pandas as pd

from .defaults import DEFAULT_DOMAIN_PALETTE


def classify(commits: pd.DataFrame, patterns: dict[str, str]) -> pd.DataFrame:
    """Add `domain_<name>` boolean columns, plus `primary_domain` and `n_domains`.

    Args:
        commits: DataFrame with `files_changed` column (list[str] per row).
        patterns: {domain_name: regex_pattern}. Patterns are searched, not matched.

    Returns:
        Copy of `commits` with one boolean column per domain plus `domain_other`,
        `primary_domain` (str), and `n_domains` (int).
    """
    out = commits.copy()
    if commits.empty or not patterns:
        # Even with no patterns, populate the bookkeeping columns so downstream
        # consumers don't blow up.
        out["primary_domain"] = "other"
        out["n_domains"] = 0
        out["domain_other"] = True
        return out

    compiled = {name: re.compile(pat) for name, pat in patterns.items()}

    for name, regex in compiled.items():
        col = f"domain_{name}"
        # Bind regex into a default arg to avoid late-binding in the loop.
        out[col] = out["files_changed"].apply(
            lambda files, _r=regex: any(_r.search(f) for f in (files or []))
        )

    domain_cols = [f"domain_{n}" for n in patterns.keys()]
    out["domain_other"] = ~out[domain_cols].any(axis=1)

    def primary(row) -> str:
        for name in patterns.keys():
            if row[f"domain_{name}"]:
                return name
        return "other"

    out["primary_domain"] = out.apply(primary, axis=1)
    out["n_domains"] = out[domain_cols + ["domain_other"]].sum(axis=1)
    return out


def autoderive_patterns(
    repo_dir: Path,
    commits: pd.DataFrame,
    min_commits: int = 20,
) -> dict[str, str]:
    """Pick domains from top-level dirs touched by ≥`min_commits` commits.

    Returns {dir_name: regex_anchored_to_dir_prefix}.
    Falls back to the most-touched dirs (regardless of count) if none meet the
    threshold — guarantees at least 3 domains for a non-trivial repo.
    """
    if commits.empty:
        return {}

    top_level_counter: Counter[str] = Counter()
    for files in commits["files_changed"]:
        seen_in_commit: set[str] = set()
        for f in files or []:
            if "/" not in f:
                continue
            top = f.split("/", 1)[0]
            if top in seen_in_commit:
                continue
            seen_in_commit.add(top)
            top_level_counter[top] += 1

    # Filter dirs that exist in the repo today (avoid stale historical dirs).
    repo_dir = Path(repo_dir)
    existing = {d.name for d in repo_dir.iterdir() if d.is_dir() and not d.name.startswith(".")}

    qualified = [(name, n) for name, n in top_level_counter.most_common()
                 if name in existing and n >= min_commits]

    if len(qualified) < 3:
        # Fall back to top 5 existing dirs by count.
        qualified = [(name, n) for name, n in top_level_counter.most_common()
                     if name in existing][:5]

    return {name: f"^{re.escape(name)}/" for name, _ in qualified}


def assign_domain_colors(domain_names: list[str]) -> dict[str, str]:
    """Cycle through the default palette to give each domain a color."""
    palette = DEFAULT_DOMAIN_PALETTE
    out: dict[str, str] = {}
    for i, name in enumerate(domain_names):
        out[name] = palette[i % len(palette)]
    out.setdefault("other", palette[-1])
    return out
