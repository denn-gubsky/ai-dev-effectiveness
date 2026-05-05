# Config reference

Every field in `ai_dev.yaml`, with its type, default, and a one-line meaning.
Drop a starter file in your workspace with `ai-dev-effectiveness init-config`.

## Precedence

CLI flag > YAML config > built-in default. Anything you omit from `ai_dev.yaml`
falls through to the built-in default — the file is opt-in for everything.

## `project`

```yaml
project:
  name: "My Project"               # str   — appears in chart titles
  team_size: 1                     # int   — humans on the project; default 1
  human_daily_rate_usd: 800        # float — for cost-comparison chart
  ai_monthly_cost_usd: 200         # float — Claude/Cursor/Copilot subscription
  package_root: null               # str?  — e.g. "src" for src-layout repos
```

## `domains`

Map of name → regex+color. Each commit is tagged with every domain whose regex
matches at least one of its changed files. Charts roll up by `primary_domain`
(first match in declaration order).

```yaml
domains:
  frontend: { pattern: "^src/frontend/",      color: "#2563EB" }
  backend:  { pattern: "^src/api/",           color: "#DC2626" }
  shared:   { pattern: "^src/shared/",        color: "#059669" }
  tests:    { pattern: "^(tests|__tests__)/", color: "#7C3AED" }
  infra:    { pattern: "^(terraform|k8s)/",   color: "#D97706" }
```

If `domains` is empty/omitted, the analyzer auto-derives from top-level
directories with ≥ 20 commits each and falls back to top-5 by activity if
the threshold isn't met.

## `packages`

```yaml
packages: []                       # list[str] — directories to scan for LOC
```

Empty/omitted means "use the keys of `domains` as packages, looked up under
`project.package_root` if set, else under repo root or repo/src/".

## `languages`

Optional override of the built-in language groups. Each group has an extension
list and the per-LOC effort rates the bottom-up estimator uses.

```yaml
languages:
  compiled:
    extensions: [".cpp", ".hpp", ".h", ".rs", ".go"]
    base_hours: 2.0
    hours_per_loc: 0.03
  dynamic:
    extensions: [".py", ".ts", ".tsx", ".js"]
    base_hours: 1.0
    hours_per_loc: 0.015
  config:
    extensions: [".yaml", ".yml", ".json", ".toml", ".xml"]
    base_hours: 0.5
    hours_per_loc: 0.005
```

Omit the section to use defaults. Built-ins live in
`src/ai_dev_effectiveness/defaults.py`.

## `roles`

Specialist roles for the top-down comparison. See
[Configuring specialist roles](../README.md#configuring-specialist-roles) for
the calibration rubric.

```yaml
roles:
  - { role: "Senior Backend Engineer", scope: "API + business logic",
      loc: 25000, pm_low: 6, pm_high: 8, color: "#DC2626" }
```

| Field    | Type   | Required | Notes                                       |
|----------|--------|----------|---------------------------------------------|
| `role`   | str    | yes      | Display name                                |
| `scope`  | str    | no       | Short phrase for the chart legend           |
| `loc`    | int    | no       | LOC this role would own (informational)     |
| `pm_low` | number | yes      | Person-month estimate, low end              |
| `pm_high`| number | yes      | Person-month estimate, high end             |
| `color`  | str    | no       | Hex color for chart bars                    |

Omit the section entirely to skip the top-down chart.

`ai-dev-effectiveness suggest-roles TARGET` generates this section
automatically by surveying the codebase.

## `effort`

Bottom-up estimator overrides.

```yaml
effort:
  integration_multiplier: 1.3      # multiplier for commits touching > 1 domain
  test_debug_multiplier:  1.5      # global testing/debugging overhead
  max_hours_per_commit:  40        # hard cap (= 1 work-week)
  loc_soft_cap:        2000        # cap inside the per-LOC term
```

Defaults: 1.3, 1.5, 40, 2000.

## `agents`

```yaml
agents:
  extend: []                       # list — appended to the built-in registry
  override: null                   # list — replaces the registry entirely
  assume_untagged: null            # str  — name of an agent to attribute
                                   #         untagged non-merge commits to
```

Built-in registry covers Claude Opus/Sonnet/Haiku, Codex, Cursor, Copilot,
Aider, Cody, Continue, Windsurf, Codeium, Tabnine, Devin. Run
`ai-dev-effectiveness list-agents` to see all entries.

`assume_untagged: "Claude Opus"` is the escape hatch when GitHub squash-merges
strip your trailers or your workflow doesn't add them. Merge commits are
excluded from the sweep.

`extend` adds entries to the built-in list. Each entry has the same schema as
the built-in registry (`name`, `pattern`, `kind`, `category`, `model_family`,
`vendor`, `color`).

## `milestones`

Map of commit-subject substring → annotation label. Used to mark major
milestones on the cumulative-LOC timeline.

```yaml
milestones:
  "initial commit": "Project Start"
  "v1.0":           "v1.0 Release"
```

## `output`

```yaml
output:
  formats: ["html"]                # list — html | json | pdf
  out_dir: "."                     # str  — where reports land
  hide_code: true                  # bool — suppress code cells in nbconvert HTML
```

`out_dir` is overridden at runtime by the `--out-dir` CLI flag (which itself
defaults to `$PWD/<target_basename>/` when running outside the target).

## `judge`

Optional AI-judge. Disabled by default.

```yaml
judge:
  enabled:        false
  provider:       claude-cli       # claude-cli | anthropic-api | openai | ollama | stub
  model:          sonnet           # passed to claude --print or the API SDK
  sample_size:    5                # commits per (domain × size_bucket) stratum
  judge_all:      false            # ignore sample_size and judge every commit
  skip_below_loc: 5                # commits below this LOC are skipped
  timeout_sec:    60               # subprocess timeout for claude-cli
  max_retries:    2                # JSON-parse retry budget
  cache_dir:     ".ai-dev-effectiveness-cache"
  agent_path:    ".claude/agents/effort-judge.md"          # populated by init-judge
  skill_path:    ".claude/skills/effort-estimation/SKILL.md"
  ignore_paths:  ["dist/", "node_modules/", "*.lock", "*.svg"]
```

In practice you only need `enabled: true` and `provider: claude-cli` — the
analyzer rewrites `cache_dir`, `agent_path`, and `skill_path` to absolute
paths inside the workspace at runtime, so any value you put here is ignored.

`provider: claude-cli` requires `claude` on PATH and `init-judge` to have run
once in your workspace. `provider: stub` is the deterministic test provider
(no network, no claude subprocess).

`provider: anthropic-api` uses the Anthropic API (requires `ANTHROPIC_API_KEY`,
defaults to `claude-sonnet-4-5`). `provider: openai` uses OpenAI's structured-
outputs feature (requires `OPENAI_API_KEY`, requires gpt-4o or newer, defaults
to `gpt-4o-2024-11-20`). `provider: ollama` uses a local ollama server
(defaults to `llama3.1:70b`, override `OLLAMA_HOST` env var to point at a
non-localhost server). All four real providers (claude-cli + 3 API) share the
same JSON schema for output, so judgment results are comparable across them.

API providers receive the diff embedded in the user prompt rather than fetching
it themselves — they have less agentic context than `claude-cli` (which can
follow callers via ast-index, read related files, etc.). For most commits the
difference is negligible; for cross-cutting refactors `claude-cli` produces
meaningfully better estimates.

## CLI overrides

These flags override the corresponding config field for one run:

| Flag                         | Config field            |
|------------------------------|-------------------------|
| `--judge claude-cli`         | `judge.enabled` + `judge.provider` |
| `--judge-all`                | `judge.judge_all`       |
| `--judge-model sonnet`       | `judge.model`           |
| `--judge-dry-run`            | (preview only, no run)  |
| `--no-ast-index`             | sets `AI_DEV_EFFECTIVENESS_NO_AST_INDEX=1` |
| `--assume-untagged "Claude"` | `agents.assume_untagged`|
| `--workspace PATH`           | (analyzer workspace)    |
| `--out-dir PATH`             | `output.out_dir`        |
| `--format html|json|all`     | `output.formats`        |
