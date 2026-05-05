# ai-dev-effectiveness

> Measure how much AI co-programming actually accelerates software delivery — on any git repo.

`ai-dev-effectiveness` reads your git history, detects which commits were co-authored by AI coding agents (Claude, Copilot, Cursor, Codex, Aider, …), and produces an interactive HTML report (and a structured JSON sidecar) comparing your real delivery against a hypothetical traditional team. It triangulates three independent estimators — top-down specialist roles, bottom-up per-commit formulas, and an AI judge that reads each diff — so the productivity multipliers are credible, not just plausible.

## Quickstart

```bash
# Install (macOS):
brew install pipx && pipx ensurepath
pipx install git+https://github.com/denn-gubsky/ai-dev-effectiveness

# Pick a folder DEDICATED to running analyses — NOT one of your project repos.
mkdir -p ~/dev-effectiveness && cd ~/dev-effectiveness

# One-time: install the bundled subagents (effort-judge + roles-architect).
ai-dev-effectiveness init-judge

# One-time per target: have Claude survey the repo and propose specialist roles.
ai-dev-effectiveness suggest-roles ~/work/your-repo --apply

# Every run: just go. Config auto-loads, JSON sidecar emits by default.
ai-dev-effectiveness analyze ~/work/your-repo --judge claude-cli \
    --team "1 developer + Claude Code (Opus 4.7)"
open ./your-repo/effectiveness-report.html
```

That's the full happy-path. Each step is explained below.

The analyzer **never writes inside the target repo** — bundled subagents, the judgment cache, and all reports live in your workspace. Each target gets its own subfolder so you can compare repos side by side.

## What it measures

Three independent productivity multipliers triangulating the same question:

1. **Top-down** — what would a traditional specialist team need? Person-months by role.
2. **Bottom-up** — per-commit effort using language-weighted formulas (base hours + per-LOC rate).
3. **AI judge** — Claude reads each diff and emits a calibrated estimate via the bundled subagent.

When all three agree within 2×, the multiplier is defensible. When they diverge, the report flags it.

## End-to-end flow

### 1. Workspace setup (once)

```bash
mkdir -p ~/dev-effectiveness && cd ~/dev-effectiveness
ai-dev-effectiveness init-judge
```

This installs both bundled subagents into `~/dev-effectiveness/.claude/agents/` (`effort-judge` for the per-commit AI judge, `roles-architect` for proposing specialist roles) plus a `settings.recommended.json` with the optional [ast-index](https://github.com/defendend/Claude-ast-index-search) MCP snippet.

### 2. Configure roles for each target (once per repo)

```bash
ai-dev-effectiveness suggest-roles ~/work/your-repo --apply
```

The `roles-architect` subagent surveys your codebase, identifies natural domain clusters, and writes calibrated specialist roles directly into `~/dev-effectiveness/ai_dev.yaml`. Takes 60–180 seconds, uses your existing Claude subscription, no diffs leave your machine. The original config (if any) is backed up as `ai_dev.yaml.bak`.

If you'd rather inspect the YAML before applying, drop `--apply` to print to stdout, or use `--out roles.yaml` to write to a file.

You can also write roles by hand — see [Configuring specialist roles](#configuring-specialist-roles) below.

### 3. Run analyses (every time)

```bash
ai-dev-effectiveness analyze ~/work/your-repo --judge claude-cli
```

What happens:

- **Workspace config auto-pickup** — `<workspace>/ai_dev.yaml` is loaded automatically; you only need `--config` for non-standard paths.
- **`ast-index rebuild`** runs against the target if `ast-index` is on `PATH`.
- **Stratified sampling** of ~30–50 commits across (domain × size_bucket) for the judge.
- **Reports written** to `<workspace>/<target_basename>/`:
  - `effectiveness-report.html` — interactive Plotly dashboard
  - `effectiveness-report.json` — structured AI-readable analysis

The JSON contains everything needed for downstream report generation (Claude Cowork, custom dashboards, etc.) — three estimators side-by-side, cost comparison, per-agent / per-author / per-domain / per-package breakdowns, and an audit sample of judge rationales.

### 4. Describe the actual team (optional but recommended)

The report compares your **suggested team** (the specialist roles, sized in person-months) against your **actual team** (the humans + AI that actually shipped the code). Without a label, the report's "Team Composition" chart auto-derives one from author count + detected agents (e.g. `"1 developer + Claude Opus"`). To set it explicitly:

```bash
ai-dev-effectiveness analyze ~/work/your-repo \
    --team "1 developer + Claude Code (Opus 4.7)" \
    --team-size 1
```

Or persist in `ai_dev.yaml`:

```yaml
project:
  team_size: 1
  team_description: "1 developer + Claude Code (Opus 4.7)"
```

`--team-size` controls the math (actual person-months = `team_size × project_months`); `--team` is purely the chart label. Precedence: CLI > YAML > auto-derive.

### 5. When trailers are missing

Real-world git histories often under-report AI assistance — GitHub squash-merges strip Co-Authored-By trailers, and some workflows commit without invoking the AI tool's commit flow. If you know your project was AI-paired but trailer detection looks too low:

```bash
ai-dev-effectiveness analyze ~/work/your-repo \
    --assume-untagged "Claude Opus" \
    --judge claude-cli
```

Every untagged non-merge commit gets attributed to the named agent. Merge commits stay un-attributed (they don't represent direct authorship). Persist the choice in `ai_dev.yaml`:

```yaml
agents:
  assume_untagged: "Claude Opus"
```

Run `ai-dev-effectiveness list-agents` to see valid agent names.

## Workspace layout

```
~/dev-effectiveness/                      # your analyzer workspace
├── .claude/
│   ├── agents/effort-judge.md           # bundled by init-judge
│   ├── agents/roles-architect.md
│   ├── skills/effort-estimation/SKILL.md
│   └── settings.recommended.json
├── ai_dev.yaml                          # auto-loaded by `analyze`
├── ai_dev.yaml.bak                      # written by `suggest-roles --apply`
├── your-repo/
│   ├── effectiveness-report.html
│   ├── effectiveness-report.json        # auto-emitted by default
│   └── .ai-dev-effectiveness-cache/     # location-stable judge cache
└── another-repo/
    └── ...
```

## CLI reference

| Command | Purpose |
|---|---|
| `init-judge [WORKSPACE]` | Install bundled subagents into workspace's `.claude/`. |
| `init-config [--out PATH]` | Drop a starter `ai_dev.yaml` template. |
| `suggest-roles TARGET [--apply]` | Survey TARGET and propose specialist roles. With `--apply`, write directly into workspace's `ai_dev.yaml`. |
| `list-agents` | Print the built-in AI-agent signature registry. |
| `validate-config FILE` | Schema-check an `ai_dev.yaml` without running. |
| `analyze TARGET [flags]` | Run the analysis. |

Useful `analyze` flags:

| Flag | Effect |
|---|---|
| `--judge claude-cli` | Enable the AI judge using your Claude subscription. |
| `--judge-dry-run` | Show what the judge would do (sample size, paths) without running it. |
| `--judge-all` | Judge every commit instead of stratified sampling. |
| `--assume-untagged "Claude Opus"` | Attribute untagged non-merge commits to a named agent. |
| `--team "1 dev + Claude Code"` | Label for the actual team in the comparison chart. |
| `--team-size N` | Number of human developers (default 1). Affects actual-PM math. |
| `--workspace PATH` | Override the workspace (default `$PWD`). |
| `--out-dir PATH` | Override report destination (cache stays at the default location). |
| `--format html\|json\|both` | Output formats. Default `both` writes HTML + JSON. |
| `--no-ast-index` | Skip the `ast-index rebuild` step. |
| `--config FILE` | Use a non-standard config path (otherwise auto-picked). |

## Optional providers

The default AI judge uses your Claude Code CLI subscription (no API key, no metered billing, no diffs leaving your machine). Other providers exist for users who'd rather pay metered:

- `--judge anthropic-api` — uses `ANTHROPIC_API_KEY` env var
- `--judge openai` — uses `OPENAI_API_KEY` env var
- `--judge ollama` — local model via Ollama (privacy-preserving)
- `--judge stub` — deterministic, used in CI tests

The `anthropic-api`, `openai`, and `ollama` providers are stubbed in v1.0 and will ship in a follow-up minor release.

## Case study

[`examples/robotics-case-study/`](examples/robotics-case-study/) — anonymized analysis of a real 117K-LOC ROS2 robotics project built by 1 dev + Claude over 10 months. Use it to see what a credible report looks like and to calibrate your own results.

## Configuring specialist roles

The top-down half of the analysis answers "what would a traditional team need?" by summing your declared specialist roles. Without roles, the top-down chart is skipped and the three-way reconciliation falls to two-way (bottom-up + judge only).

### Option A — let Claude propose them

```bash
ai-dev-effectiveness suggest-roles ~/work/your-repo --apply
```

Writes roles directly into your workspace's `ai_dev.yaml`. Drop `--apply` to print to stdout for inspection first.

### Option B — write them by hand

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

`pm = loc / (rate_per_day × 22 working_days_per_month)`. Use the lower rate for `pm_high`, the higher rate for `pm_low`. The tool averages the range — wider spread = more uncertainty.

[`examples/robotics-case-study/ai_dev.yaml`](examples/robotics-case-study/ai_dev.yaml) is a worked example covering a multi-domain ROS2 / CUDA / ML project.

## JSON output structure

```jsonc
{
  "tool": "ai-dev-effectiveness",
  "version": "1.0.0",
  "generated_at": "2026-05-05T...",
  "project": { "name": "...", "team_size": 1, "team_description": "...", ... },
  "actual_team": {
    "description": "1 developer + Claude Code (Opus 4.7)",
    "team_size": 1,
    "n_authors": 2,
    "person_months": 0.1
  },
  "headline": { "n_commits": 68, "n_ai_assisted": 56, "loc_total": 22277, ... },
  "estimators": {
    "top_down":  { "roles": [...], "total_pm_mid": 23, "multiplier": 230 },
    "bottom_up": { "total_traditional_hours": 863, "multiplier": 10.8 },
    "judge":     { "total_human_hours": ..., "multiplier": 2.13, ... }
  },
  "cost_comparison": { "traditional_cost_usd": 404800, "savings_multiplier": 227 },
  "by_agent":    [...],
  "by_author":   [...],
  "by_domain":   [...],
  "by_package":  [...],
  "by_complexity": [...],
  "author_agent_matrix": [...],
  "judge_samples": [...],   // up to 15 judged commits with rationale
  "weekly":      [...]
}
```

Every section a Claude Cowork report generator (or your own dashboard) needs to author a credible productivity narrative.

## Documentation

- [`docs/REPRODUCTION_GUIDE.md`](docs/REPRODUCTION_GUIDE.md) — full first-run walkthrough including troubleshooting.
- [`docs/config-reference.md`](docs/config-reference.md) — every YAML field documented with type, default, and meaning.

## License

MIT — see [LICENSE](LICENSE).
