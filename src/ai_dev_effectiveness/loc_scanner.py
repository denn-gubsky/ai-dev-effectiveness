"""Walk the repo and count LOC by package × language group.

A 'package' is just a directory; the user supplies a list of them via config.
With no config we fall back to top-level directories that the domain
classifier autoderived.
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from .defaults import DEFAULT_LANGUAGE_GROUPS, LanguageGroup


def scan(
    repo_dir: Path,
    packages: Iterable[str],
    language_groups: dict[str, LanguageGroup] | None = None,
    package_root: Path | None = None,
) -> pd.DataFrame:
    """Scan each package directory and count lines per language group.

    Args:
        repo_dir: repo root.
        packages: directory names to scan (relative to `package_root`, or to
                  `repo_dir` if package_root is None).
        language_groups: {group_name: LanguageGroup}. Defaults to built-ins.
        package_root: optional sub-directory under repo_dir that contains the
                      packages (e.g. 'src' for src-layout repos). If None, packages
                      are looked up directly under repo_dir, then under repo_dir/src.

    Returns:
        DataFrame with columns: package, <group1>, <group2>, ..., total.
        One row per package.
    """
    repo_dir = Path(repo_dir).resolve()
    groups = language_groups or DEFAULT_LANGUAGE_GROUPS

    # Build extension → group lookup. Longest extension first so '.launch.py'
    # wins over '.py' when both are present.
    ext_to_group: list[tuple[str, str]] = []
    for gname, g in groups.items():
        for ext in g.extensions:
            ext_to_group.append((ext, gname))
    ext_to_group.sort(key=lambda x: -len(x[0]))

    rows: list[dict] = []
    for pkg in packages:
        pkg_path = _resolve_package_dir(repo_dir, pkg, package_root)
        if pkg_path is None:
            continue

        per_group_loc: dict[str, int] = {gname: 0 for gname in groups.keys()}
        for fpath in _walk_files(pkg_path):
            group_name = _classify_extension(fpath.name, ext_to_group)
            if group_name is None:
                continue
            try:
                with open(fpath, errors="ignore") as fh:
                    loc = sum(1 for _ in fh)
            except OSError:
                continue
            per_group_loc[group_name] += loc

        row = {"package": pkg}
        row.update(per_group_loc)
        row["total"] = sum(per_group_loc.values())
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("total", ascending=False).reset_index(drop=True)
    return df


def _resolve_package_dir(repo_dir: Path, pkg: str, package_root: Path | None) -> Path | None:
    """Find a package directory using a few common layouts."""
    candidates = []
    if package_root is not None:
        candidates.append(repo_dir / package_root / pkg)
    candidates += [
        repo_dir / pkg,
        repo_dir / "src" / pkg,
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _walk_files(root: Path):
    """Yield every regular file under root, skipping noisy dirs."""
    skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules",
                 "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
                 ".ruff_cache", ".idea", ".vscode"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for f in filenames:
            yield Path(dirpath) / f


def _classify_extension(filename: str, ext_to_group: list[tuple[str, str]]) -> str | None:
    """Return the group name for a filename, or None if no extension matches."""
    for ext, gname in ext_to_group:
        if filename.endswith(ext):
            return gname
    return None
