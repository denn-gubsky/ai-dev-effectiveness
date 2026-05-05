"""Detect AI coding-agent signatures in commits.

Signatures are matched in order; first match wins per commit, but a single
commit may match multiple signatures (e.g. Cursor + Claude). The detector
adds three columns to the commits DataFrame:

    agents          list[str]  — every signature that matched
    primary_agent   str | None — first signature in registry order
    agent_vendors   list[str]  — unique vendors (anthropic, openai, …)

The registry itself is loaded from `data/agents.yaml` (built-in) and merged
with user-provided extensions. See AgentSignature for the schema.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml

SignatureKind = Literal[
    "commit_trailer",   # match against `body` text after newline (any trailer)
    "commit_message",   # match against subject + body
    "author_email",     # match against author_email
    "author_name",      # match against author_name
]


@dataclass(frozen=True)
class AgentSignature:
    name: str                     # display name, e.g. "Claude Opus"
    pattern: str                  # regex (literal patterns OK; we use re.search)
    kind: SignatureKind = "commit_trailer"
    category: str = "ai_agent"    # ai_agent | ai_assistant | ai_ide | ai_tool | other
    model_family: str = "unknown"
    vendor: str = "unknown"
    color: str = "#888888"
    _compiled: re.Pattern | None = field(default=None, compare=False, repr=False)

    def compile(self) -> AgentSignature:
        """Return a copy with the compiled regex cached."""
        if self._compiled is not None:
            return self
        return AgentSignature(
            name=self.name, pattern=self.pattern, kind=self.kind,
            category=self.category, model_family=self.model_family,
            vendor=self.vendor, color=self.color,
            _compiled=re.compile(self.pattern, re.IGNORECASE | re.MULTILINE),
        )

    def matches(self, commit: dict) -> bool:
        """Test whether this signature matches the given commit row."""
        regex = self._compiled or re.compile(self.pattern, re.IGNORECASE | re.MULTILINE)

        if self.kind == "commit_trailer":
            # Match against any trailer line; we serialize trailers as 'Key: value'.
            trailers = commit.get("trailers") or {}
            for key, values in trailers.items():
                for val in values:
                    if regex.search(f"{key}: {val}"):
                        return True
            # Also fall back to scanning the raw body — some tools emit pseudo-trailers.
            body = commit.get("body") or ""
            return bool(regex.search(body))
        if self.kind == "commit_message":
            text = (commit.get("subject") or "") + "\n" + (commit.get("body") or "")
            return bool(regex.search(text))
        if self.kind == "author_email":
            return bool(regex.search(commit.get("author_email") or ""))
        if self.kind == "author_name":
            return bool(regex.search(commit.get("author_name") or ""))
        return False


def load_builtin_registry() -> list[AgentSignature]:
    """Load and compile the registry shipped inside the package."""
    pkg_files = resources.files("ai_dev_effectiveness.data").joinpath("agents.yaml")
    with pkg_files.open("r") as f:
        data = yaml.safe_load(f)
    return [_dict_to_sig(entry).compile() for entry in (data or {}).get("agents", [])]


def load_user_extensions(extensions: list[dict]) -> list[AgentSignature]:
    """Compile user-provided extensions from config (config.agents.extend)."""
    return [_dict_to_sig(d).compile() for d in extensions or []]


def _dict_to_sig(d: dict) -> AgentSignature:
    return AgentSignature(
        name=d["name"],
        pattern=d["pattern"],
        kind=d.get("kind", "commit_trailer"),
        category=d.get("category", "ai_agent"),
        model_family=d.get("model_family", "unknown"),
        vendor=d.get("vendor", "unknown"),
        color=d.get("color", "#888888"),
    )


def detect_agents(
    commits: pd.DataFrame,
    registry: list[AgentSignature],
) -> pd.DataFrame:
    """Add `agents`, `primary_agent`, `agent_vendors` columns to commits."""
    if commits.empty:
        commits = commits.copy()
        commits["agents"] = pd.Series([], dtype=object)
        commits["primary_agent"] = pd.Series([], dtype=object)
        commits["agent_vendors"] = pd.Series([], dtype=object)
        return commits

    agents_per_row: list[list[str]] = []
    primary_per_row: list[str | None] = []
    vendors_per_row: list[list[str]] = []

    for row in commits.to_dict(orient="records"):
        matched: list[AgentSignature] = []
        for sig in registry:
            if sig.matches(row):
                matched.append(sig)
        agents_per_row.append([s.name for s in matched])
        primary_per_row.append(matched[0].name if matched else None)
        vendors = sorted({s.vendor for s in matched})
        vendors_per_row.append(vendors)

    out = commits.copy()
    out["agents"] = agents_per_row
    out["primary_agent"] = primary_per_row
    out["agent_vendors"] = vendors_per_row
    return out


def vendor_color_map(registry: list[AgentSignature]) -> dict[str, str]:
    """Pick a representative color per vendor (first signature's color wins)."""
    out: dict[str, str] = {}
    for sig in registry:
        out.setdefault(sig.vendor, sig.color)
    return out


def write_user_settings_template(target: Path) -> None:
    """Stub for future use; not called yet."""
    target.write_text("# User-defined agent signatures go here.\nagents:\n  extend: []\n")
