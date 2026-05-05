"""Shared fixtures: synthesized git repos for testing without bundling real history."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest


@dataclass
class FakeCommit:
    """One commit's worth of changes for the git_fixture helper."""
    files: dict[str, str]                    # path -> content
    subject: str
    body: str = ""
    author_name: str = "Test User"
    author_email: str = "test@example.com"
    extra_trailers: dict[str, str] = field(default_factory=dict)

    def full_message(self) -> str:
        msg = self.subject
        if self.body or self.extra_trailers:
            msg += "\n\n"
        if self.body:
            msg += self.body + "\n"
        if self.extra_trailers:
            if self.body:
                msg += "\n"
            for key, val in self.extra_trailers.items():
                msg += f"{key}: {val}\n"
        return msg


@pytest.fixture
def git_fixture(tmp_path):
    """Build a synthesized git repo on demand.

    Usage:
        repo = git_fixture(commits=[
            FakeCommit(files={"a.py": "x = 1"}, subject="initial"),
            FakeCommit(files={"a.py": "x = 2"}, subject="feat: bump",
                       extra_trailers={"Co-Authored-By": "Claude Sonnet 4.6 <noreply@anthropic.com>"}),
        ])
    """
    def _build(commits: list[FakeCommit], repo_dir: Path | None = None) -> Path:
        repo = Path(repo_dir) if repo_dir else tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        _run(["git", "init", "-q", "-b", "main"], cwd=repo)
        _run(["git", "config", "user.name", "Test User"], cwd=repo)
        _run(["git", "config", "user.email", "test@example.com"], cwd=repo)

        for c in commits:
            for fpath, content in c.files.items():
                full = repo / fpath
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content)
                _run(["git", "add", fpath], cwd=repo)
            env = {"GIT_AUTHOR_NAME": c.author_name,
                   "GIT_AUTHOR_EMAIL": c.author_email,
                   "GIT_COMMITTER_NAME": c.author_name,
                   "GIT_COMMITTER_EMAIL": c.author_email}
            _run(["git", "commit", "-q", "-m", c.full_message()], cwd=repo, env=env)

        return repo
    return _build


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> None:
    import os
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    subprocess.run(cmd, cwd=str(cwd), check=True, env=full_env,
                   capture_output=True, text=True)
