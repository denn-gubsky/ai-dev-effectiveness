"""Run `git log --numstat` and parse it into a normalized commits DataFrame.

Output columns:
    sha, date, author_name, author_email, subject, body,
    insertions, deletions, net_loc, files_changed (list[str]),
    n_files, trailers (dict[str, list[str]])
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pandas as pd

# Sentinel that's effectively impossible to appear in a real commit body.
_DELIM = "<<<COMMIT::AIDE>>>"
_BODY_END = "<<<BODY_END::AIDE>>>"
# %P = parent SHAs (space-separated, multiple parents → merge commit).
_LOG_FORMAT = f"{_DELIM}%n%H%n%P%n%ai%n%an%n%ae%n%s%n%b%n{_BODY_END}"

# Trailer regex: matches "Token: value" lines at the end of a commit body
# (RFC 5322-ish). git's own trailer parser is more permissive but this
# captures the cases we care about.
_TRAILER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*?):\s*(.+?)\s*$")


def _parse_trailers(body: str) -> dict[str, list[str]]:
    """Extract `Token: value` trailers from the bottom of a commit body.

    Multiple values for the same token (e.g. several Co-Authored-By lines)
    are collected into a list.
    """
    trailers: dict[str, list[str]] = {}
    if not body:
        return trailers

    # Walk from the end up; stop at the first blank line that breaks the trailer block.
    lines = body.rstrip("\n").split("\n")
    trailer_lines: list[str] = []
    for line in reversed(lines):
        if line.strip() == "":
            if trailer_lines:
                break
            continue
        m = _TRAILER_RE.match(line)
        if m:
            trailer_lines.append(line)
        else:
            # Non-trailer line — stop walking up. (This mirrors git's behavior.)
            break

    for line in reversed(trailer_lines):
        m = _TRAILER_RE.match(line)
        if m:
            key, val = m.group(1), m.group(2)
            trailers.setdefault(key, []).append(val)

    return trailers


def extract_commits(repo_dir: Path, branch: str | None = None) -> pd.DataFrame:
    """Run `git log --numstat` against `repo_dir` and return commits DataFrame.

    Args:
        repo_dir: path to a git repository (the .git/ directory must exist).
        branch: optional branch name; if None, uses the current branch.

    Raises:
        RuntimeError: if `git log` fails.
    """
    repo_dir = Path(repo_dir).resolve()
    if not (repo_dir / ".git").exists():
        # Allow worktrees where .git is a file pointing elsewhere.
        if not (repo_dir / ".git").is_file():
            raise RuntimeError(f"Not a git repository: {repo_dir}")

    cmd = ["git", "log", f"--format={_LOG_FORMAT}", "--numstat"]
    if branch:
        cmd.append(branch)

    proc = subprocess.run(
        cmd, cwd=str(repo_dir), capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git log failed: {proc.stderr.strip()}")

    raw_commits = proc.stdout.split(_DELIM)[1:]  # skip pre-first-delim chunk

    rows = []
    for raw in raw_commits:
        lines = raw.lstrip("\n").rstrip("\n").split("\n")
        if len(lines) < 5:
            continue

        sha = lines[0].strip()
        parents = [p for p in lines[1].strip().split() if p]
        date_str = lines[2].strip()
        author_name = lines[3].strip()
        author_email = lines[4].strip()
        subject = lines[5].strip()

        # Find the body terminator and the numstat tail.
        body_end_idx = None
        for i, line in enumerate(lines[6:], start=6):
            if line.strip() == _BODY_END:
                body_end_idx = i
                break

        if body_end_idx is None:
            body = "\n".join(lines[6:]).strip()
            numstat_lines: list[str] = []
        else:
            body = "\n".join(lines[6:body_end_idx]).strip()
            numstat_lines = lines[body_end_idx + 1:]

        insertions = 0
        deletions = 0
        files_changed: list[str] = []
        for ns in numstat_lines:
            ns = ns.strip()
            if not ns:
                continue
            parts = ns.split("\t")
            if len(parts) < 3:
                continue
            ins = 0 if parts[0] == "-" else int(parts[0]) if parts[0].isdigit() else 0
            dels = 0 if parts[1] == "-" else int(parts[1]) if parts[1].isdigit() else 0
            insertions += ins
            deletions += dels
            files_changed.append(parts[2])

        rows.append({
            "sha": sha,
            "parents": parents,
            "is_merge": len(parents) > 1,
            "date": pd.to_datetime(date_str, utc=True).tz_localize(None),
            "author_name": author_name,
            "author_email": author_email,
            "subject": subject,
            "body": body,
            "insertions": insertions,
            "deletions": deletions,
            "net_loc": insertions - deletions,
            "files_changed": files_changed,
            "n_files": len(files_changed),
            "trailers": _parse_trailers(body),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df = df.sort_values("date").reset_index(drop=True)
    return df


def add_week_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add ISO year_week and week_start (Monday) columns. Idempotent."""
    if df.empty:
        return df
    if "year_week" in df.columns and "week_start" in df.columns:
        return df

    iso = df["date"].dt.isocalendar()
    df = df.copy()
    df["year_week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    # week_start = Monday of the ISO week
    df["week_start"] = df["date"] - pd.to_timedelta(df["date"].dt.weekday, unit="D")
    df["week_start"] = df["week_start"].dt.normalize()
    return df
