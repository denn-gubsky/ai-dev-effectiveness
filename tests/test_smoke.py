"""End-to-end smoke tests on synthesized repos — no network, no real `claude`."""
from __future__ import annotations

from ai_dev_effectiveness import analyze
from ai_dev_effectiveness.config import Config
from ai_dev_effectiveness.config import JudgeConfig as JudgeCfgPydantic
from tests.conftest import FakeCommit


def test_analyze_zero_config(git_fixture):
    """Bare-minimum: 3 commits, no config — returns a populated AnalysisResult."""
    repo = git_fixture(commits=[
        FakeCommit(files={"src/app.py": "x = 1\n"}, subject="initial commit"),
        FakeCommit(files={"src/app.py": "x = 2\n", "src/utils.py": "y = 3\n"},
                   subject="feat: add utils"),
        FakeCommit(files={"src/app.py": "x = 4\n"}, subject="fix: bump x"),
    ])
    result = analyze(repo=repo)
    assert result.metrics.headline["n_commits"] == 3
    assert result.metrics.headline["n_authors"] == 1
    assert result.metrics.headline["n_ai_assisted"] == 0


def test_detects_claude_signatures(git_fixture):
    """Co-Authored-By trailers for each Claude variant should be detected."""
    repo = git_fixture(commits=[
        FakeCommit(files={"a.py": "1\n"}, subject="opus work",
                   extra_trailers={"Co-Authored-By": "Claude Opus 4.6 <noreply@anthropic.com>"}),
        FakeCommit(files={"a.py": "2\n"}, subject="sonnet work",
                   extra_trailers={"Co-Authored-By": "Claude Sonnet 4.6 <noreply@anthropic.com>"}),
        FakeCommit(files={"a.py": "3\n"}, subject="opus 1m work",
                   extra_trailers={"Co-Authored-By": "Claude Opus 4.7 (1M context) <noreply@anthropic.com>"}),
        FakeCommit(files={"a.py": "4\n"}, subject="manual"),  # no trailer
    ])
    result = analyze(repo=repo)
    h = result.metrics.headline
    assert h["n_commits"] == 4
    assert h["n_ai_assisted"] == 3, "expected 3 commits with Claude trailers"

    by_agent = result.metrics.by_agent.set_index("agent")["commits"].to_dict()
    assert by_agent.get("Claude Opus", 0) == 2, "expected 2 Opus commits (4.6 + 4.7)"
    assert by_agent.get("Claude Sonnet", 0) == 1
    # The catch-all "Claude (other)" also matches because it's a superset regex.
    # That's fine: it's how downstream "by_vendor" rollups work.


def test_html_output_renders(git_fixture, tmp_path):
    repo = git_fixture(commits=[
        FakeCommit(files={"src/main.py": "print('hi')\n" * 40}, subject="initial"),
        FakeCommit(files={"src/main.py": "print('updated')\n" * 50}, subject="rewrite"),
    ])
    result = analyze(repo=repo)
    html = result.to_html()
    assert "<html" in html
    assert "AI Co-Programming Effectiveness" in html
    assert "<svg" in html or "Plotly" in html, "Plotly figures should be embedded"

    out = tmp_path / "report.html"
    result.to_html(out)
    assert out.exists() and out.stat().st_size > 10_000


def test_json_output_serializes(git_fixture):
    repo = git_fixture(commits=[
        FakeCommit(files={"a.py": "1\n"}, subject="x"),
        FakeCommit(files={"a.py": "2\n"}, subject="y",
                   extra_trailers={"Co-Authored-By": "Claude Sonnet <noreply@anthropic.com>"}),
    ])
    result = analyze(repo=repo)
    import json
    payload = json.loads(result.to_json())
    assert "headline" in payload
    assert "by_agent" in payload
    assert payload["headline"]["n_commits"] == 2
    assert payload["headline"]["n_ai_assisted"] == 1


def test_judge_with_stub_provider(git_fixture):
    """Stub judge runs without claude CLI / network and populates judge_summary."""
    commits = []
    for i in range(8):
        # Write a 30-line file each time to exceed the LOC threshold.
        commits.append(FakeCommit(
            files={"app.py": f"# rev {i}\n" + "x = 1\n" * 30},
            subject=f"rev {i}",
        ))
    repo = git_fixture(commits=commits)

    cfg = Config()
    cfg.judge = JudgeCfgPydantic(enabled=True, provider="stub", sample_size=3,
                                 skip_below_loc=5)
    result = analyze(repo=repo, config=cfg)
    js = result.metrics.judge_summary
    assert js is not None
    assert js["n_judged"] >= 1
    assert js["total_human_hours"] > 0
    assert js["judged_provider"] == "stub"


def test_builtin_registry_loads():
    """The shipped agents.yaml must parse and contain the four Claude entries."""
    from ai_dev_effectiveness import agent_detector
    registry = agent_detector.load_builtin_registry()
    names = {sig.name for sig in registry}
    assert "Claude Opus" in names
    assert "Claude Sonnet" in names
    assert "Claude Haiku" in names
    assert "Claude (other)" in names
    assert len(registry) >= 10, "expected at least 10 agents in the registry"


def test_init_judge_installs_artifacts(tmp_path):
    """init-judge copies bundled agent + skill into the user's repo."""
    from ai_dev_effectiveness import judge as J
    repo = tmp_path / "repo"
    repo.mkdir()
    actions = J.install_judge_artifacts(repo)
    assert (repo / ".claude/agents/effort-judge.md").exists()
    assert (repo / ".claude/skills/effort-estimation/SKILL.md").exists()
    assert (repo / ".claude/settings.recommended.json").exists()
    assert "agent" in actions
