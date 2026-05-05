# ai-dev-effectiveness

> Measure how much AI co-programming actually accelerates software delivery — on any git repo.

`ai-dev-effectiveness` reads your git history, detects which commits were co-authored by AI coding agents (Claude, Copilot, Cursor, Codex, Aider, …), and produces an interactive HTML report comparing your real delivery against a hypothetical traditional team. It triangulates three independent estimators — top-down specialist roles, bottom-up per-commit formulas, and an AI-judge that reads each diff — so the productivity multipliers are credible, not just plausible.

## Quickstart

```bash
pipx install git+https://github.com/denn-gubsky/ai-dev-effectiveness
cd /path/to/your/repo
ai-dev-effectiveness analyze .
open effectiveness-report.html
```

That's it. Zero config required for the first run — the tool auto-detects domains from your top-level dirs, classifies commits by language, and identifies AI co-authors from a built-in registry covering Claude (Opus/Sonnet/Haiku), GitHub Copilot, Cursor, Codex, Aider, Cody, Continue, Windsurf, Codeium, Tabnine, and Devin.

## What it measures

Three independent productivity multipliers:

1. **Top-down** — what would a traditional specialist team need? Person-months by role.
2. **Bottom-up** — per-commit effort using language-weighted formulas (base hours + per-LOC rate).
3. **AI judge** — Claude reads each diff and emits a calibrated estimate via the bundled subagent.

When all three agree within 2x, the multiplier is defensible. When they diverge, the report flags it.

## Optional: AI judge for diff-aware estimates

```bash
ai-dev-effectiveness init-judge          # installs the bundled subagent into .claude/
ai-dev-effectiveness analyze . --judge claude-cli
```

The judge uses your existing Claude Code CLI session — no API key, no metered billing, no diffs leaving your machine. If you have [ast-index](https://github.com/defendend/Claude-ast-index-search) MCP configured, it's used for symbol-level lookups.

Other providers: `--judge anthropic-api` (separate API key), `--judge openai`, `--judge ollama` (local model).

## Case study

[`examples/robotics-case-study/`](examples/robotics-case-study/) — anonymized analysis of a real 117K-LOC robotics project built by 1 dev + Claude over 10 months. Use it to see what a credible report looks like and to calibrate your own results.

## Customize

- `ai-dev-effectiveness init-config` — drops `ai_dev.yaml` into your repo
- Define your specialist roles, effort constants, agent signatures
- See [`docs/REPRODUCTION_GUIDE.md`](docs/REPRODUCTION_GUIDE.md) and [`docs/config-reference.md`](docs/config-reference.md)

## License

MIT — see [LICENSE](LICENSE).
