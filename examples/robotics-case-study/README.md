# Case study — anonymized robotics project

> Real numbers from a real production codebase, anonymized for public sharing.

## What this is

A real-world run of `ai-dev-effectiveness` on a production-grade ROS2 robotics
codebase: ~117K lines of code spanning CUDA-accelerated computer vision,
C++ real-time control, Python ML/prediction, depth sensing, and visualization,
all delivered by a **single developer co-programming with Claude** over
~10 months and 554 commits.

It's included here so you can see what the tool's output looks like on a real,
non-trivial project — and to give you a reference point when calibrating your
own analysis. The methodology and numbers are real; only project-identifying
specifics (manipulator make/model, camera vendor, deployment context) have
been replaced with generic descriptors.

## Files

- **[`coprogramming_effectiveness.ipynb`](coprogramming_effectiveness.ipynb)**
  — the analysis notebook. GitHub renders these natively with full Plotly
  interactivity; click through to view inline. To run locally:
  ```bash
  pip install jupyterlab
  jupyter lab coprogramming_effectiveness.ipynb
  ```
- **[`ai_dev.yaml`](ai_dev.yaml)** — the configuration that produced the
  analysis: domain regex patterns, specialist role definitions, effort
  constants. Use it as a starting point for your own ROS2 / multi-domain repo.

## Headline findings

| Metric | Traditional team (estimated) | Actual (1 dev + Claude) |
|--------|------------------------------|-------------------------|
| Person-months | ~38 PM | ~10 PM |
| Calendar months | ~13 | ~10 |
| Estimated cost | ~$680K | ~$178K |
| Codebase | ~117K LOC | (same) |
| Domain coverage | 4–5 specialists | 1 generalist + Claude |

**Productivity multiplier**: ~3.8× across both top-down (specialist roles) and
bottom-up (per-commit weekly effort) analyses. The two methods agreeing within
a small margin is what makes the multiplier defensible.

## How to read this

The notebook walks through ten sections:

1. Setup & git data extraction
2. Commit classification by domain
3. Weekly aggregation
4. Top-down analysis: codebase composition by package
5. Top-down analysis: traditional team estimate
6. Bottom-up analysis: weekly development velocity
7. Bottom-up analysis: weekly effort estimation
8. Claude tooling evolution (Web UI → Code CLI transition)
9. Comparison dashboard
10. Summary & key findings

## Caveats

- **LOC is not a quality metric.** This analysis quantifies *delivery
  throughput*, not code maintainability, test coverage, or correctness.
- **Effort estimates are calibrated against industry averages** for complex
  robotics software. Your domain may warrant different per-LOC rates — adjust
  in your own `ai_dev.yaml`.
- **Solo developers don't actually work 40-hour weeks.** The "actual hours"
  baseline assumes a steady 40h/week; real workload is variable.
- **Coordination overhead** (meetings, code review, onboarding) isn't included
  in the traditional team estimate — adding it would push the multiplier higher.
- **Third-party packages** (drivers for the manipulator and sensors) are
  excluded from the LOC counts since they weren't authored in the project.

## Reproducing this on your own repo

```bash
pipx install git+https://github.com/denn-gubsky/ai-dev-effectiveness

# 1. Pick a workspace (NOT one of your project repos).
mkdir -p ~/dev-effectiveness && cd ~/dev-effectiveness

# 2. One-time: install the bundled judge subagent into the workspace.
ai-dev-effectiveness init-judge

# 3. Drop a config and edit the `domains` and `roles` sections to match
#    your project. (Use this case study's ai_dev.yaml as a starting point.)
ai-dev-effectiveness init-config && $EDITOR ai_dev.yaml

# 4. Run the analysis. Reports land in ~/dev-effectiveness/<target_basename>/.
ai-dev-effectiveness analyze /path/to/your/repo --config ai_dev.yaml \
    --judge claude-cli
```

The analyzer is read-only with respect to your project — no `.claude/`, cache,
or report files are ever created inside it.
