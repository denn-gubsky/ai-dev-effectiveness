"""Optional integration with ast-index (https://github.com/defendend/Claude-ast-index-search).

When `ast-index` is on PATH, we run `ast-index rebuild` against the target repo
before invoking the judge. The bundled effort-judge subagent's tool allowlist
includes `mcp__ast-index__*`, so the agent can do symbol-level lookups during
its judgment if the user has the ast-index MCP server configured.

This module is best-effort: every function returns gracefully when ast-index
isn't installed or the build fails. The analysis pipeline never depends on it.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def is_installed() -> bool:
    """True iff `ast-index` is on PATH."""
    return shutil.which("ast-index") is not None


def build(target: Path, timeout_sec: int = 120) -> tuple[bool, str]:
    """Run `ast-index rebuild` in the target repo.

    Args:
        target: path to the repo to index. ast-index walks it itself.
        timeout_sec: kill the build after this many seconds.

    Returns:
        (success, message) — `success` is False when ast-index isn't installed,
        the build fails, or it times out. `message` is a short human-readable
        status suitable for printing to the user.
    """
    import os
    if os.environ.get("AI_DEV_EFFECTIVENESS_NO_AST_INDEX") == "1":
        return (False, "ast-index disabled via AI_DEV_EFFECTIVENESS_NO_AST_INDEX=1.")
    if not is_installed():
        return (False, "ast-index not installed; skipping (install with `pipx install ast-index`).")

    try:
        proc = subprocess.run(
            # The subcommand is `rebuild`, not `build` — ast-index aliases it that way
            # because indices are stateful and "build" implied a fresh-start build only.
            ["ast-index", "rebuild"],
            cwd=str(target),
            capture_output=True, text=True,
            timeout=timeout_sec, check=False,
        )
    except subprocess.TimeoutExpired:
        return (False, f"ast-index rebuild timed out after {timeout_sec}s.")
    except OSError as e:
        return (False, f"ast-index rebuild failed to start: {e}")

    if proc.returncode == 0:
        return (True, "ast-index rebuild OK")
    return (False, f"ast-index rebuild exited {proc.returncode}: {proc.stderr.strip()[:200]}")
