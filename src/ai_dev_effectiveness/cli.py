"""click-based CLI for ai-dev-effectiveness."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__, agent_detector, analyze
from . import config as _config
from . import judge as _judge


@click.group()
@click.version_option(__version__)
def main() -> None:
    """ai-dev-effectiveness — measure AI co-programming effectiveness on any git repo."""


@main.command(name="analyze")
@click.argument("target", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False),
              default=None, help="Path to ai_dev.yaml (optional).")
@click.option("--out-dir", "out_dir", type=click.Path(file_okay=False),
              default=None,
              help="Where to write the report and cache. Defaults to "
                   "$PWD/<target_basename>/ when target != $PWD, else $PWD. "
                   "NOTHING is ever written inside the target.")
@click.option("--workspace", "workspace", type=click.Path(file_okay=False),
              default=None,
              help="Analyzer workspace where the bundled judge agent lives. "
                   "Defaults to $PWD. Run `init-judge` here once before "
                   "using --judge claude-cli.")
@click.option("--format", "fmt", type=click.Choice(["html", "json", "all"]), default="html",
              help="Output format.")
@click.option("--judge", "judge_provider", type=click.Choice(
    ["claude-cli", "anthropic-api", "openai", "ollama", "stub"]),
    default=None, help="Enable AI judge with the given provider.")
@click.option("--judge-all", is_flag=True, help="Judge every commit instead of stratified sampling.")
@click.option("--judge-dry-run", is_flag=True, help="Show what the judge would do without running it.")
@click.option("--judge-model", default=None, help="Override the judge model name.")
@click.option("--no-ast-index", is_flag=True,
              help="Skip the `ast-index rebuild` step before the judge runs.")
@click.option("--assume-untagged", "assume_untagged", default=None,
              metavar="AGENT_NAME",
              help="Attribute every untagged non-merge commit to AGENT_NAME "
                   "(e.g. 'Claude Opus'). Use when you know the work was AI-"
                   "assisted but the trailer is missing (squash-merges strip "
                   "trailers; pre-trailer-convention commits look manual). "
                   "Run `list-agents` to see valid names.")
def analyze_cmd(target: str, config_path: str | None, out_dir: str | None,
                workspace: str | None, fmt: str,
                judge_provider: str | None, judge_all: bool, judge_dry_run: bool,
                judge_model: str | None, no_ast_index: bool,
                assume_untagged: str | None) -> None:
    """Analyze the git repo at TARGET (defaults to current directory).

    The analyzer is read-only with respect to TARGET — no files are created
    or modified inside it. Reports, the judge cache, and the bundled effort-
    judge agent all live in your local analyzer workspace ($PWD by default).
    """
    cfg = _config.load(Path(config_path)) if config_path else _config.Config()

    # CLI flags override config.
    if judge_provider is not None:
        cfg.judge.enabled = True
        cfg.judge.provider = judge_provider
    if judge_all:
        cfg.judge.judge_all = True
    if judge_model is not None:
        cfg.judge.model = judge_model
    if assume_untagged is not None:
        cfg.agents.assume_untagged = assume_untagged

    if judge_dry_run:
        _run_judge_dry_run(target, cfg, workspace, out_dir)
        return

    if no_ast_index:
        # Mark via env; ast_index.build checks this before running.
        import os
        os.environ["AI_DEV_EFFECTIVENESS_NO_AST_INDEX"] = "1"

    result = analyze(repo=target, config=cfg, workspace=workspace, out_dir=out_dir)

    click.echo(f"Workspace:      {result.workspace}")
    click.echo(f"Output dir:     {result.out_dir}")

    if fmt in ("html", "all"):
        path = result.default_out_path("html")
        result.to_html(path)
        click.echo(f"HTML written:   {path}")

    if fmt in ("json", "all"):
        path = result.default_out_path("json")
        result.to_json(path)
        click.echo(f"JSON written:   {path}")

    h = result.metrics.headline
    click.echo("")
    click.echo(f"Commits:        {h['n_commits']:,}  ({h['n_ai_assisted']:,} AI-assisted)")
    click.echo(f"Authors:        {h['n_authors']}")
    click.echo(f"LOC (current):  {h['loc_total']:,}")
    if h.get("project_months"):
        click.echo(f"Project span:   {h['project_months']:.1f} months")
    if not result.metrics.by_agent.empty:
        click.echo("")
        click.echo("Top AI agents detected:")
        for _, row in result.metrics.by_agent.head(5).iterrows():
            click.echo(f"  {row['agent']:<25} {row['commits']:>5} commits")


@main.command(name="init-judge")
@click.argument("workspace", type=click.Path(file_okay=False), default=".")
@click.option("--force", is_flag=True, help="Overwrite existing files.")
def init_judge_cmd(workspace: str, force: bool) -> None:
    """Install the bundled effort-judge subagent into WORKSPACE/.claude/.

    WORKSPACE defaults to the current directory and should be a folder
    DEDICATED to running analyses — NOT one of your project repos. The
    `analyze` command will look up `<workspace>/.claude/agents/effort-judge.md`
    when --judge claude-cli is used.

    Why a dedicated workspace? The bundled subagent is meant to never appear
    inside the project repos you're analyzing — it's the analyzer's tool, not
    a per-project config. Run this once in a folder like ~/dev-effectiveness/,
    then run analyses from that folder against any number of target repos.
    """
    workspace_path = Path(workspace).resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    claude_bin = _judge.claude_on_path()
    if claude_bin:
        click.echo(f"Found claude CLI at: {claude_bin}")
    else:
        click.echo("⚠️  `claude` not found on PATH. The claude-cli judge provider "
                   "won't work until you install Claude Code.", err=True)

    actions = _judge.install_judge_artifacts(workspace_path, force=force)
    click.echo("")
    click.echo(f"Workspace: {workspace_path}")
    for kind, action in actions.items():
        click.echo(f"  [{kind}]  {action}")

    click.echo("")
    click.echo("Next steps:")
    click.echo(f"  1. Review {workspace_path}/.claude/agents/effort-judge.md and "
               f"{workspace_path}/.claude/skills/effort-estimation/SKILL.md.")
    click.echo("  2. Optionally add the ast-index MCP from "
               ".claude/settings.recommended.json to your settings.json.")
    click.echo("  3. From this directory, run:")
    click.echo("       ai-dev-effectiveness analyze /path/to/some-target-repo --judge claude-cli")
    click.echo("     Reports will be written to ./<target_basename>/.")


def _run_judge_dry_run(target: str, cfg, workspace: str | None, out_dir: str | None) -> None:
    """Print what the judge would do without actually invoking it."""
    from . import _resolve_out_dir, agent_detector, ast_index, domain_classifier, git_extractor
    repo_path = Path(target).resolve()
    while not (repo_path / ".git").exists() and repo_path != repo_path.parent:
        repo_path = repo_path.parent

    workspace_path = Path(workspace).resolve() if workspace else Path.cwd().resolve()
    out_dir_path = _resolve_out_dir(out_dir, workspace_path, repo_path)
    agent_path = workspace_path / ".claude/agents/effort-judge.md"

    commits = git_extractor.extract_commits(repo_path)
    commits = agent_detector.detect_agents(commits, agent_detector.load_builtin_registry())
    patterns = cfg.domain_patterns() or domain_classifier.autoderive_patterns(repo_path, commits)
    commits = domain_classifier.classify(commits, patterns)

    judge_cfg = _judge.JudgeConfig(
        provider=cfg.judge.provider, model=cfg.judge.model,
        sample_size=cfg.judge.sample_size, judge_all=cfg.judge.judge_all,
        skip_below_loc=cfg.judge.skip_below_loc,
    )
    sampled = _judge.stratified_sample(
        commits, judge_cfg.sample_size, judge_cfg.skip_below_loc, judge_cfg.judge_all,
    )

    click.echo(f"Judge dry-run for target:  {repo_path}")
    click.echo(f"  workspace:           {workspace_path}")
    click.echo(f"  out dir (reports):   {out_dir_path}")
    click.echo(f"  cache (judgments):   {out_dir_path / '.ai-dev-effectiveness-cache'}")
    click.echo(f"  judge agent path:    {agent_path}  ({'EXISTS' if agent_path.exists() else 'MISSING — run `init-judge`'})")
    click.echo(f"  ast-index:           {'available' if ast_index.is_installed() else 'NOT installed (judge will run without symbol lookups)'}")
    click.echo(f"  provider:            {cfg.judge.provider}")
    click.echo(f"  model:               {cfg.judge.model}")
    click.echo(f"  total commits:       {len(commits):,}")
    click.echo(f"  eligible:            {(commits['insertions']+commits['deletions'] >= cfg.judge.skip_below_loc).sum():,}")
    click.echo(f"  sampled:             {len(sampled):,}")
    click.echo(f"  judge_all:           {cfg.judge.judge_all}")
    if cfg.judge.provider == "claude-cli":
        click.echo(f"  est. wall-time:      {len(sampled) * 10:.0f}s "
                   f"({len(sampled) * 10 / 60:.1f} minutes) "
                   f"@ ~10s per claude --print invocation")
        click.echo("  cost:                uses your Claude subscription quota; no USD charge.")
    else:
        click.echo(f"  cost:                see `--judge {cfg.judge.provider}` docs; metered against an API key.")
    click.echo("")
    click.echo("To proceed, run without --judge-dry-run.")


@main.command(name="init-config")
@click.option("--out", "out_path", type=click.Path(dir_okay=False),
              default="ai_dev.yaml", help="Where to write the config.")
@click.option("--force", is_flag=True, help="Overwrite if file exists.")
def init_config_cmd(out_path: str, force: bool) -> None:
    """Drop an example ai_dev.yaml into the current directory."""
    path = Path(out_path)
    if path.exists() and not force:
        click.echo(f"Refusing to overwrite {path} (use --force).", err=True)
        sys.exit(1)
    path.write_text(_config.example_config_text())
    click.echo(f"Wrote {path}")
    click.echo("Edit it, then run: ai-dev-effectiveness analyze .")


@main.command(name="list-agents")
def list_agents_cmd() -> None:
    """Print the built-in AI-agent signature registry."""
    registry = agent_detector.load_builtin_registry()
    click.echo(f"{len(registry)} signatures in built-in registry:")
    click.echo("")
    for sig in registry:
        click.echo(f"  {sig.name:<22} [{sig.vendor:<12}] {sig.kind:<16} {sig.pattern}")


@main.command(name="validate-config")
@click.argument("config_path", type=click.Path(exists=True, dir_okay=False))
def validate_config_cmd(config_path: str) -> None:
    """Schema-check an ai_dev.yaml without running the analysis."""
    try:
        cfg = _config.load(Path(config_path))
        click.echo(f"OK — {len(cfg.domains)} domains, {len(cfg.roles)} roles, "
                   f"{len(cfg.agents.extend)} agent extensions.")
    except Exception as e:
        click.echo(f"INVALID: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
