# ai-dev-effectiveness

> Measure how much AI co-programming actually accelerates software delivery — on any git repo.

`ai-dev-effectiveness` reads your git history, detects which commits were co-authored by AI coding agents (Claude, Copilot, Cursor, Codex, Aider, …), and produces an interactive HTML report comparing your real delivery against a hypothetical traditional team. It triangulates three independent estimators — top-down specialist roles, bottom-up per-commit formulas, and an AI-judge that reads each diff — so the productivity multipliers are credible, not just plausible.

## Quickstart

```bash
pipx install git+https://github.com/denn-gubsky/ai-dev-effectiveness

# Pick a folder you'll run analyses from. NOT one of your project repos.
mkdir -p ~/dev-effectiveness && cd ~/dev-effectiveness

# Once: install the bundled judge subagent into THIS workspace.
ai-dev-effectiveness init-judge

# Now analyze any repo you want. Reports go to ./<target_basename>/.
ai-dev-effectiveness analyze /path/to/your/repo
open ./your-repo/effectiveness-report.html
```

The analyzer **never writes inside the target repo** — the bundled subagent, the judgment cache, and all reports live in your local workspace. Each target gets its own subfolder so you can compare repos side by side.

## What it measures

Three independent productivity multipliers:

1. **Top-down** — what would a traditional specialist team need? Person-months by role.
2. **Bottom-up** — per-commit effort using language-weighted formulas (base hours + per-LOC rate).
3. **AI judge** — Claude reads each diff and emits a calibrated estimate via the bundled subagent.

When all three agree within 2x, the multiplier is defensible. When they diverge, the report flags it.

## Optional: AI judge for diff-aware estimates

```bash
cd ~/dev-effectiveness          # the workspace where init-judge ran
ai-dev-effectiveness analyze /path/to/your/repo --judge claude-cli
```

The judge uses your existing Claude Code CLI session — no API key, no metered billing, no diffs leaving your machine. If you have [ast-index](https://github.com/defendend/Claude-ast-index-search) on your `PATH`, the analyzer runs `ast-index rebuild` against the target before the judge starts so the bundled subagent can use `mcp__ast-index__*` tools for symbol-level lookups (much faster than grep-then-read for "find callers of this function").

Other providers (opt-in): `--judge anthropic-api` (separate API key), `--judge openai`, `--judge ollama` (local model).

Disable ast-index for a run: `--no-ast-index`.

## Workspace layout

```
~/dev-effectiveness/                      # your analyzer workspace
├── .claude/
│   ├── agents/effort-judge.md           # ← installed by `init-judge`
│   ├── skills/effort-estimation/SKILL.md
│   └── settings.recommended.json
├── ai_dev.yaml                          # optional: custom config (init-config)
├── repo-a/                              # output for one analyzed target
│   ├── effectiveness-report.html
│   ├── effectiveness-report.json
│   └── .ai-dev-effectiveness-cache/
└── repo-b/                              # another target's outputs
    └── ...
```

Useful flags:

- `--workspace PATH` — override the analyzer workspace (default: `$PWD`).
- `--out-dir PATH` — override where this run's reports go.
- `--judge-dry-run` — show what the judge would do (sample size, cost, paths) without invoking it.

## Case study

[`examples/robotics-case-study/`](examples/robotics-case-study/) — anonymized analysis of a real 117K-LOC robotics project built by 1 dev + Claude over 10 months. Use it to see what a credible report looks like and to calibrate your own results.

## Customize

- `ai-dev-effectiveness init-config` — drops `ai_dev.yaml` into your workspace.
- Define your specialist roles, effort constants, agent signatures.

## Configuring specialist roles

The top-down half of the analysis answers "what would a traditional team need?"
by summing your declared specialist roles. Without roles, the report skips the
top-down chart and the three-way reconciliation falls to two-way (formula +
judge only). Adding even three or four roles makes the comparison defensible.

### Option A — let Claude propose the roles

```bash
cd ~/dev-effectiveness
ai-dev-effectiveness suggest-roles /path/to/your/repo
```

Surveys the target codebase via a bundled Claude Code subagent
(`roles-architect`), identifies natural domain clusters, and prints a YAML
snippet ready to paste under your `ai_dev.yaml` `roles:` key. Uses your
existing Claude subscription (no API key, no diffs leaving your machine).
Takes 60-180 seconds. Edit the result freely — the agent provides a starting
point grounded in your actual file layout, not the final word.

### Option B — write the roles yourself

Each role describes the slice of the codebase one specialist would own and
how many person-months a senior engineer (working without AI assistance)
would need to deliver it. Schema:

```yaml
roles:
  - { role: "Senior Backend Engineer", scope: "API, business logic, persistence",
      loc: 25000, pm_low: 6, pm_high: 8, color: "#DC2626" }
  - { role: "Frontend Engineer",       scope: "React UI, design system",
      loc: 18000, pm_low: 4, pm_high: 6, color: "#2563EB" }
  - { role: "DevOps",                  scope: "CI/CD, infra, deploys",
      loc:  4000, pm_low: 2, pm_high: 3, color: "#D97706" }
```

How to estimate `pm_low` / `pm_high`:

| Domain                                    | Productive LOC/day |
|-------------------------------------------|--------------------|
| C / C++ / Rust / CUDA / kernel            | 15-25              |
| TypeScript / Python / Go / Java           | 30-50              |
| Glue / config / IaC                       | 20-30              |
| ML training / distributed systems         | 15-25 (incl. ramp) |

`pm = loc / (rate_per_day * 22 days_per_month)`. Use the lower rate for
`pm_high`, the higher rate for `pm_low`. The notebook averages the range to
produce the headline multiplier — wider spread = more uncertainty.

### A worked example

[`examples/robotics-case-study/ai_dev.yaml`](examples/robotics-case-study/ai_dev.yaml)
is the actual config used to produce the case-study report — six roles
covering a multi-domain ROS2 / CUDA / ML / depth-vision project. Use it as a
template if your project has similar shape; otherwise let `suggest-roles`
generate one tailored to your codebase.

## Documentation

- [`docs/REPRODUCTION_GUIDE.md`](docs/REPRODUCTION_GUIDE.md) — full step-by-step
  walkthrough (legacy from the original notebook, covers manual notebook usage).
- [`docs/config-reference.md`](docs/config-reference.md) — every YAML field documented.

## License

MIT — see [LICENSE](LICENSE).
