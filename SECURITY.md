# Security

If you discover a vulnerability — for example, a way to leak repository contents through the AI-judge subprocess, or a path-traversal in the config loader — please email the maintainer at `project-hockeybot@waverleysoftware.com` rather than opening a public issue.

There is no bounty. We aim to acknowledge reports within 7 days and ship a fix within 30.

## Threat model notes

- The default `claude-cli` judge spawns `claude --print` as a subprocess in the user's repo directory. The bundled subagent's `tools` allowlist limits what it can do (Read, Grep, `git show`/`git diff`/`git log`, optional ast-index MCP). It does **not** include Write, Edit, or unrestricted Bash. If you need to lock this down further, edit `.claude/agents/effort-judge.md` after `init-judge`.
- Diffs sent to opt-in API providers (`anthropic-api`, `openai`) leave your machine. `ollama` keeps everything local.
- The on-disk cache at `.ai-dev-effectiveness-cache/` contains commit SHAs, diffs, and judgments. Add it to `.gitignore` (the tool does this automatically when you run `init-judge`).
