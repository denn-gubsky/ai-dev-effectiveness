"""click-based CLI for ai-dev-effectiveness."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__, agent_detector, analyze
from . import config as _config
from . import judge as _judge
from . import roles_architect as _roles_architect


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
@click.option("--format", "fmt",
              type=click.Choice(["html", "json", "both", "all"]), default="both",
              help="Output format. Default is `both` (html + json sidecar). "
                   "Use `html` or `json` to emit only one. `all` is an alias for `both` "
                   "(reserved for when pdf rendering ships).")
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
@click.option("--team", "team_description", default=None, metavar="LABEL",
              help='Label for the actual team that built the project '
                   '(e.g. "1 developer + Claude Code (Opus 4.7)"). Appears as '
                   "the bar label in the team-composition comparison chart. "
                   "If omitted, auto-derived from author count + detected AI agents.")
@click.option("--team-size", "team_size", default=None, type=int, metavar="N",
              help="Number of human developers on the project. Affects the "
                   "actual person-months baseline. Default 1.")
def analyze_cmd(target: str, config_path: str | None, out_dir: str | None,
                workspace: str | None, fmt: str,
                judge_provider: str | None, judge_all: bool, judge_dry_run: bool,
                judge_model: str | None, no_ast_index: bool,
                assume_untagged: str | None,
                team_description: str | None, team_size: int | None) -> None:
    """Analyze the git repo at TARGET (defaults to current directory).

    The analyzer is read-only with respect to TARGET — no files are created
    or modified inside it. Reports, the judge cache, and the bundled effort-
    judge agent all live in your local analyzer workspace ($PWD by default).

    If `<workspace>/ai_dev.yaml` exists and you don't pass --config, it's
    auto-loaded.
    """
    # Auto-pickup workspace config if --config wasn't passed.
    if config_path is None:
        ws = Path(workspace).resolve() if workspace else Path.cwd().resolve()
        candidate = ws / "ai_dev.yaml"
        if candidate.exists():
            config_path = str(candidate)
            click.echo(f"Using workspace config: {candidate}")
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
    if team_description is not None:
        cfg.project.team_description = team_description
    if team_size is not None:
        cfg.project.team_size = team_size

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

    if fmt in ("html", "both", "all"):
        path = result.default_out_path("html")
        result.to_html(path)
        click.echo(f"HTML written:   {path}")

    if fmt in ("json", "both", "all"):
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
    # Also install the roles-architect subagent so `suggest-roles` works
    # without a separate setup step.
    roles_dst = _roles_architect.install_roles_architect(workspace_path, force=force)
    actions["roles-architect"] = (
        f"{'WROTE' if force or not roles_dst.exists() else 'OK   '} {roles_dst}"
    )
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
    cache_root = _resolve_out_dir(None, workspace_path, repo_path)  # cache is location-stable
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
    click.echo(f"  cache (judgments):   {cache_root / '.ai-dev-effectiveness-cache'}")
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


@main.command(name="suggest-roles")
@click.argument("target", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--workspace", "workspace", type=click.Path(file_okay=False),
              default=None,
              help="Analyzer workspace where the bundled subagent lives. "
                   "Defaults to $PWD; auto-installs the agent if missing.")
@click.option("--out", "out_path", type=click.Path(dir_okay=False), default=None,
              help="Write the YAML snippet to this path instead of stdout.")
@click.option("--apply", "apply_to_config", is_flag=True,
              help="Write the suggested roles directly into "
                   "<workspace>/ai_dev.yaml, replacing any existing roles: "
                   "section. Creates the file (from the example template) "
                   "if it doesn't exist.")
@click.option("--model", "model", default="sonnet",
              help="Claude model name (default: sonnet).")
@click.option("--timeout", "timeout_sec", default=600, type=int,
              help="Subprocess timeout in seconds (default: 600 = 10 minutes). "
                   "Bump to 1800+ for very large repos (>500K LOC).")
def suggest_roles_cmd(target: str, workspace: str | None, out_path: str | None,
                      apply_to_config: bool, model: str, timeout_sec: int) -> None:
    """Survey TARGET and propose specialist roles for the top-down comparison.

    Runs the bundled `roles-architect` Claude Code subagent against TARGET (the
    target git repo, read-only). The subagent surveys the codebase structure,
    identifies natural domain clusters, and proposes a `roles:` list with
    person-month estimates per role. Output is YAML you can paste into your
    `ai_dev.yaml`.

    Uses your existing Claude subscription (no API key, no metered billing,
    no diffs leaving your machine — same as the AI judge).
    """
    target_path = Path(target).resolve()
    workspace_path = Path(workspace).resolve() if workspace else Path.cwd().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    if not _judge.claude_on_path():
        click.echo("ERROR: `claude` not found on PATH. Install Claude Code "
                   "(https://claude.ai/code).", err=True)
        sys.exit(1)

    click.echo(f"Surveying {target_path} via the roles-architect subagent...")
    click.echo("(60-90s for small repos; 5-10 minutes for 100K+ LOC; "
               f"bump --timeout if it hits the {timeout_sec}s limit)")
    click.echo("")

    try:
        suggestion = _roles_architect.suggest_roles(
            target=target_path, workspace=workspace_path,
            model=model, timeout_sec=timeout_sec,
        )
    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)

    yaml_snippet = _roles_architect.render_yaml(suggestion)

    if apply_to_config:
        cfg_path = workspace_path / "ai_dev.yaml"
        if not cfg_path.exists():
            cfg_path.write_text(_config.example_config_text())
            click.echo(f"Created {cfg_path} from the example template.")
        backup = _replace_roles_in_config(cfg_path, suggestion.roles)
        click.echo(f"Wrote {len(suggestion.roles)} roles into {cfg_path}.")
        if backup:
            click.echo(f"Original saved as {backup}.")
        click.echo("Re-run analyze and the new roles will be auto-loaded.")
    elif out_path:
        Path(out_path).write_text(yaml_snippet)
        click.echo(f"Wrote {len(suggestion.roles)} roles to {out_path} "
                   f"(elapsed {suggestion.elapsed_sec:.1f}s).")
    else:
        click.echo(f"# Generated by ai-dev-effectiveness suggest-roles in "
                   f"{suggestion.elapsed_sec:.1f}s.")
        click.echo("# Paste under your `ai_dev.yaml` `roles:` key, edit as needed.")
        click.echo("# Or rerun with --apply to write directly into "
                   "<workspace>/ai_dev.yaml.")
        click.echo("")
        click.echo(yaml_snippet)


def _replace_roles_in_config(cfg_path: Path, roles: list) -> Path | None:
    """Replace the `roles:` block in `cfg_path` with the suggested roles.

    Preserves all other YAML content. Saves a `.bak` backup if any roles
    section was overwritten. Returns the backup path or None.
    """
    import yaml as _yaml
    content = cfg_path.read_text()
    parsed = _yaml.safe_load(content) or {}

    backup_path: Path | None = None
    if "roles" in parsed and parsed["roles"]:
        backup_path = cfg_path.with_suffix(".yaml.bak")
        backup_path.write_text(content)

    parsed["roles"] = roles
    # yaml.dump preserves field ordering best-effort; we accept the rewrite.
    cfg_path.write_text(_yaml.safe_dump(parsed, sort_keys=False, width=120))
    return backup_path


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
