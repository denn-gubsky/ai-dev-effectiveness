---
name: effort-estimation
description: Calibrate engineering effort estimates for git commits using a 5-tier rubric. Use whenever you need to translate a diff into an hours estimate for a senior engineer working with or without an AI coding assistant.
---

# Effort estimation rubric

You are estimating engineering effort for a SINGLE git commit. Two numbers per
commit:

- `human_hours` = senior engineer, no AI assistance, full design + implementation
  + testing + self-review.
- `ai_assisted_hours` = same engineer, same change, with a strong AI coding
  agent actively pair-programming. Roughly half human_hours for most changes;
  closer to human_hours when the bottleneck is design judgment rather than typing.

## TODO(dennis): tune these anchor ranges before v0.1 release

The numbers below are defensible industry-typical starting points. **You have
direct intuition about what real human-vs-AI hour ratios look like** for each
complexity tier from 13 months of co-programming on a real production codebase.
Adjust the ranges based on observed reality. This calibration is the single
most important quality lever for the judge.

| Tier              | Description                                                  | human_hours | ai_assisted_hours |
|-------------------|--------------------------------------------------------------|-------------|-------------------|
| `trivial`         | typo, formatting, single config tweak                        | 0.25 – 0.5  | 0.1 – 0.25        |
| `small`           | single-file bugfix or small isolated feature                 | 1 – 3       | 0.5 – 1.5         |
| `medium`          | new feature touching multiple files in one subsystem         | 4 – 10      | 2 – 5             |
| `large`           | substantial subsystem or non-trivial cross-file refactor     | 12 – 30     | 6 – 15            |
| `architectural`   | cross-cutting redesign or new top-level component            | 40 – 100    | 20 – 50           |

## Discounts to apply

- Mechanical/generated content: lockfiles, generated stubs, vendored
  dependencies, auto-formatter sweeps. Score these `trivial` regardless of LOC.
- Pure renames or moves with no logic changes: `trivial` to `small`.
- Translation work between two languages with the same logic: `small`.

## Premiums to apply

- Touches a function with many callers (use `mcp__ast-index__find_callers` or
  `grep` to check). Bump one tier higher than the raw diff suggests.
- Adds tests for previously-untested behavior (the hard part is figuring out
  what to test, not the typing). Bump half a tier.
- Touches concurrency, cryptography, parsers, or numerical methods. Bump one tier.

## Confidence

- `high`: you read the diff and at least one related file; the change is
  self-contained and unambiguous.
- `medium`: you read the diff; some uncertainty about blast radius but the
  estimate is in the right tier.
- `low`: binary, generated, mega-merge, or you couldn't open the file. Mark and move on.

## Common pitfalls

- LOC ≠ effort. A 500-LOC config sync is `trivial`; a 50-LOC algorithm rewrite
  is `medium` to `large`.
- Don't double-count: if you bump the tier for "many callers", don't ALSO add
  a multiplier on top.
- The `human_hours` estimate already includes design, implementation, testing,
  and self-review. Don't add testing overhead — it's baked in.
