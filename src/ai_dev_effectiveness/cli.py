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
@click.argument("repo", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False),
              default=None, help="Path to ai_dev.yaml (optional).")
@click.option("--out", "out_path", type=click.Path(dir_okay=False), default=None,
              help="Output file. Defaults to effectiveness-report.<format>.")
@click.option("--format", "fmt", type=click.Choice(["html", "json", "all"]), default="html",
              help="Output format.")
@click.option("--judge", "judge_provider", type=click.Choice(
    ["claude-cli", "anthropic-api", "openai", "ollama", "stub"]),
    default=None, help="Enable AI judge with the given provider.")
@click.option("--judge-all", is_flag=True, help="Judge every commit instead of stratified sampling.")
@click.option("--judge-dry-run", is_flag=True, help="Show what the judge would do without running it.")
@click.option("--judge-model", default=None, help="Override the judge model name.")
def analyze_cmd(repo: str, config_path: str | None, out_path: str | None, fmt: str,
                judge_provider: str | None, judge_all: bool, judge_dry_run: bool,
                judge_model: str | None) -> None:
    """Run the analysis on REPO (defaults to current directory)."""
    cfg = _config.load(Path(config_path)) if config_path else _config.Config()

    # CLI flags override config.
    if judge_provider is not None:
        cfg.judge.enabled = True
        cfg.judge.provider = judge_provider
    if judge_all:
        cfg.judge.judge_all = True
    if judge_model is not None:
        cfg.judge.model = judge_model

    if judge_dry_run:
        _run_judge_dry_run(repo, cfg)
        return

    result = analyze(repo=repo, config=cfg)

    if fmt in ("html", "all"):
        path = Path(out_path) if (out_path and fmt == "html") else Path("effectiveness-report.html")
        result.to_html(path)
        click.echo(f"HTML written to {path}")

    if fmt in ("json", "all"):
        path = Path(out_path) if (out_path and fmt == "json") else Path("effectiveness-report.json")
        result.to_json(path)
        click.echo(f"JSON written to {path}")

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
@click.argument("repo", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--force", is_flag=True, help="Overwrite existing files.")
def init_judge_cmd(repo: str, force: bool) -> None:
    """Install the bundled effort-judge subagent + skill into REPO/.claude/."""
    repo_path = Path(repo).resolve()

    claude_bin = _judge.claude_on_path()
    if claude_bin:
        click.echo(f"Found claude CLI at: {claude_bin}")
    else:
        click.echo("⚠️  `claude` not found on PATH. The claude-cli judge provider "
                   "won't work until you install Claude Code.", err=True)

    actions = _judge.install_judge_artifacts(repo_path, force=force)
    click.echo("")
    for kind, action in actions.items():
        click.echo(f"  [{kind}]  {action}")

    click.echo("")
    click.echo("Next steps:")
    click.echo("  1. Review .claude/agents/effort-judge.md and "
               ".claude/skills/effort-estimation/SKILL.md.")
    click.echo("  2. Optionally add the ast-index MCP from "
               ".claude/settings.recommended.json to your settings.json.")
    click.echo("  3. Run: ai-dev-effectiveness analyze . --judge claude-cli")


def _run_judge_dry_run(repo: str, cfg) -> None:
    """Print what the judge would do without actually invoking it."""
    from . import agent_detector, domain_classifier, git_extractor
    repo_path = Path(repo).resolve()
    while not (repo_path / ".git").exists() and repo_path != repo_path.parent:
        repo_path = repo_path.parent

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

    click.echo(f"Judge dry-run for {repo_path}")
    click.echo(f"  provider:        {cfg.judge.provider}")
    click.echo(f"  model:           {cfg.judge.model}")
    click.echo(f"  total commits:   {len(commits):,}")
    click.echo(f"  eligible:        {(commits['insertions']+commits['deletions'] >= cfg.judge.skip_below_loc).sum():,}")
    click.echo(f"  sampled:         {len(sampled):,}")
    click.echo(f"  judge_all:       {cfg.judge.judge_all}")
    if cfg.judge.provider == "claude-cli":
        click.echo(f"  est. wall-time:  {len(sampled) * 10:.0f}s "
                   f"({len(sampled) * 10 / 60:.1f} minutes) "
                   f"@ ~10s per claude --print invocation")
        click.echo("  cost:            uses your Claude subscription quota; no USD charge.")
    else:
        click.echo("  cost:            see `ai-dev-effectiveness analyze --judge {provider}` "
                   "documentation; this provider is metered against an API key.")
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
