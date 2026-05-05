# Reproduction guide

End-to-end walkthrough for running `ai-dev-effectiveness` on your own repo
and getting credible productivity multipliers out of it.

## Prerequisites

- macOS or Linux, Python 3.10+
- `git` on PATH (any modern version)
- For the AI judge: [Claude Code](https://claude.ai/code) on PATH (`claude`
  binary). The judge uses your existing Claude Pro/Max subscription — no
  API key needed.
- Optional but recommended: [`ast-index`](https://github.com/defendend/Claude-ast-index-search)
  on PATH. The analyzer auto-runs `ast-index rebuild` against your target
  before the judge starts so the bundled subagent can do symbol-level
  lookups instead of plain grep.

## Install

```bash
# pipx is the cleanest path on macOS:
brew install pipx
pipx ensurepath  # open a new terminal so PATH updates

pipx install git+https://github.com/denn-gubsky/ai-dev-effectiveness
```

Alternatives: `uv tool install git+...` or `python3 -m pip install --user git+...`.

## One-time workspace setup

Pick (or create) a folder DEDICATED to running analyses. Do **not** use one
of your project repos — the analyzer keeps its bundled subagents and per-
target output directories here, and you don't want to mix that with project
config.

```bash
mkdir -p ~/dev-effectiveness && cd ~/dev-effectiveness

# Installs the bundled effort-judge AND roles-architect subagents into
# ~/dev-effectiveness/.claude/agents/, plus a recommended-settings snippet.
ai-dev-effectiveness init-judge
```

You can also drop a starter `ai_dev.yaml` in the workspace if you want to
keep persistent options (custom domains, roles, agent registry extensions):

```bash
ai-dev-effectiveness init-config        # writes ./ai_dev.yaml
$EDITOR ai_dev.yaml
```

`ai_dev.yaml` is optional — the tool runs with sensible defaults and no
config at all.

## Running an analysis

```bash
cd ~/dev-effectiveness
ai-dev-effectiveness analyze ~/work/your-repo
# → reports land in ~/dev-effectiveness/your-repo/effectiveness-report.html
```

The target repo (`~/work/your-repo`) is **read-only**. Reports, the judge
cache, and any per-target state live in `~/dev-effectiveness/your-repo/`.

To enable the AI judge (third estimator, ~10s per sampled commit):

```bash
ai-dev-effectiveness analyze ~/work/your-repo --judge claude-cli
```

To check what the judge would do without spending tokens:

```bash
ai-dev-effectiveness analyze ~/work/your-repo --judge claude-cli --judge-dry-run
```

### Other judge providers

If you'd rather use a metered API instead of your Claude subscription, or if
your policy requires fully local inference, four other providers are wired
up:

```bash
# Anthropic API (separate billing relationship)
pipx install 'git+https://github.com/denn-gubsky/ai-dev-effectiveness#egg=ai-dev-effectiveness[judge-anthropic]'
export ANTHROPIC_API_KEY=sk-ant-...
ai-dev-effectiveness analyze REPO --judge anthropic-api

# OpenAI (gpt-4o or newer required for structured outputs)
pipx install 'git+https://github.com/denn-gubsky/ai-dev-effectiveness#egg=ai-dev-effectiveness[judge-openai]'
export OPENAI_API_KEY=sk-...
ai-dev-effectiveness analyze REPO --judge openai --judge-model gpt-4o

# Local model via Ollama (privacy-preserving, uses your hardware)
pipx install 'git+https://github.com/denn-gubsky/ai-dev-effectiveness#egg=ai-dev-effectiveness[judge-ollama]'
ollama pull llama3.1:70b
ai-dev-effectiveness analyze REPO --judge ollama --judge-model llama3.1:70b

# Deterministic stub (CI tests, no network, LOC-based fake judgment)
ai-dev-effectiveness analyze REPO --judge stub
```

The API providers receive the diff inline in the user message rather than
running `git show` themselves, so they have less agentic context than
`claude-cli`. For most commits this is fine; for large cross-cutting
refactors `claude-cli` still produces meaningfully better estimates because
the bundled subagent can probe call sites via ast-index / Grep.

## Configuring specialist roles

This is the highest-leverage step for a credible top-down comparison. Two
paths:

**Auto-generate** (recommended for first runs):
```bash
ai-dev-effectiveness suggest-roles ~/work/your-repo
# → prints a YAML snippet ready to paste under your ai_dev.yaml `roles:` key
```

The bundled `roles-architect` subagent surveys your codebase, identifies
natural domain clusters, and emits person-month estimates calibrated to the
language families it observes. Takes 60-180 seconds. Edit the result freely.

**Manual**: see the schema and PM-estimation rubric in the
[main README](../README.md#configuring-specialist-roles), or copy
[`examples/robotics-case-study/ai_dev.yaml`](../examples/robotics-case-study/ai_dev.yaml)
as a template if your project is multi-domain.

## When trailers are missing

Real-world git histories rarely have an AI co-author trailer on every commit:

- GitHub squash-merges strip Co-Authored-By trailers from individual branch
  commits.
- Some Claude Code workflows commit via shell after the AI helps draft
  without invoking the trailer-adding flow.
- Projects often started before the trailer convention existed.

If you know your project was AI-paired but trailer-based detection
under-reports it, use `--assume-untagged`:

```bash
ai-dev-effectiveness analyze ~/work/your-repo \
    --assume-untagged "Claude Opus" \
    --judge claude-cli
```

Every untagged non-merge commit is then attributed to the named agent.
Merge commits stay un-attributed (they don't represent direct authorship).

You can persist this in `ai_dev.yaml`:

```yaml
agents:
  assume_untagged: "Claude Opus"
```

Run `ai-dev-effectiveness list-agents` to see valid names.

## Interpreting results

### The headline numbers

After running, open the HTML report or look at the JSON:

```json
{
  "headline": { "n_commits": 64, "n_ai_assisted": 54, ... },
  "judge_summary": {
    "total_human_hours": 395.3,
    "total_ai_hours":    185.8,
    "multiplier":        2.13
  }
}
```

The three multipliers (top-down PM, bottom-up formula hours, AI judge hours)
should agree within 2× for the result to be credible. The report flags any
estimator that disagrees by more than that.

### Sanity checks

| Check | How |
|---|---|
| Commit count matches `git log --oneline \| wc -l` | Headline `n_commits` |
| Date range matches project history | Headline `first_commit` / `last_commit` |
| AI-assisted count matches `git log --grep="Co-Authored-By" \| grep -c commit` | Headline `n_ai_assisted` |
| Per-commit median effort is 4-16 hours | JSON `weekly[i].traditional_hours / weekly[i].commits` |
| Top-down and bottom-up multipliers agree within 2× | Three-way reconciliation chart in the report |

### Red flags

- **Bottom-up multiplier > 10×**: per-LOC rates are too high, or large bulk
  commits inflated the estimate. Lower the rates in
  `effort.cpp_hours_per_loc` etc. or tighten `effort.max_hours_per_commit`.
- **Bottom-up multiplier < 1×**: rates too low, or your project has many
  small config-only commits. Raise the per-language `base_hours`.
- **Top-down and bottom-up differ by > 3×**: re-examine the role estimates
  and effort constants — one is miscalibrated. The judge's per-stratum
  estimates often pinpoint which stratum is the outlier.
- **Judge multiplier ≈ 1×**: the judge thinks AI gave no speedup. Either
  it's right (the project is mostly mechanical / AI-resistant work), or the
  rubric anchors in
  `~/dev-effectiveness/.claude/skills/effort-estimation/SKILL.md` need tuning.

## Caching

The judge caches per-commit results at:

```
~/dev-effectiveness/your-repo/.ai-dev-effectiveness-cache/judge/<provider>/<model>/<prompt-hash>/<sha>.json
```

The cache is location-stable: changing `--out-dir` doesn't invalidate it,
re-running the same analysis is instant. The cache invalidates when the
bundled SKILL.md, agent.md, or judge logic version changes.

Clear the cache for one target:

```bash
rm -rf ~/dev-effectiveness/your-repo/.ai-dev-effectiveness-cache
```

## Multi-developer projects

If your project has multiple humans:

```yaml
project:
  team_size: 3
  # ...
```

The `actual_person_months` baseline becomes `team_size × project_months`. AI
co-author detection still works per-commit regardless of human count.

## Branch-specific analysis

The library currently analyzes the current branch. If you want to analyze
just one branch's history, check it out first:

```bash
( cd ~/work/your-repo && git checkout some-branch ) && \
  ai-dev-effectiveness analyze ~/work/your-repo
( cd ~/work/your-repo && git checkout main )
```

A `--branch` flag is on the roadmap for v0.3.

## Multi-target side-by-side

Run on as many repos as you like; each gets its own subfolder:

```bash
cd ~/dev-effectiveness
for repo in ~/work/repo-a ~/work/repo-b ~/work/repo-c; do
    ai-dev-effectiveness analyze "$repo"
done
ls
# → repo-a/  repo-b/  repo-c/
```

## Troubleshooting

### "claude not found on PATH"

Install [Claude Code](https://claude.ai/code) and `claude --version` should
work in a new terminal.

### "ast-index build exited 2: error: unrecognized subcommand 'build'"

You have an older version of the analyzer that called `ast-index build`
instead of `ast-index rebuild`. Upgrade with `pipx upgrade
ai-dev-effectiveness` (or `pipx install --force git+...`).

### Judge returns 0 hours / "Judge failed: ValueError"

Either the judge subagent isn't installed, or claude isn't loading it. Run
`ai-dev-effectiveness init-judge --force` to reinstall, then re-run.

### Detection rate looks too low

This usually reflects reality (squash-merges strip trailers). Use
`--assume-untagged "Claude Opus"` to attribute untagged non-merge commits
to a known agent. See [When trailers are missing](#when-trailers-are-missing)
above.

## File checklist

After a run, your workspace should look like this:

```
~/dev-effectiveness/
├── .claude/
│   ├── agents/effort-judge.md
│   ├── agents/roles-architect.md
│   ├── skills/effort-estimation/SKILL.md
│   └── settings.recommended.json
├── ai_dev.yaml                              # if you ran init-config
├── your-repo/
│   ├── effectiveness-report.html
│   ├── effectiveness-report.json            # if you used --format json or all
│   └── .ai-dev-effectiveness-cache/
└── another-repo/
    └── ...
```
