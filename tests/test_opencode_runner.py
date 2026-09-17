import dataclasses
import subprocess

from gh_workflow_fix.opencode_runner import CmdResult, OpenCodeRunner


def _ok(stdout=""):
    return CmdResult(returncode=0, stdout=stdout)


def _mk_runner(cfg, git, opencode, monkeypatch):
    runner = OpenCodeRunner(cfg)
    monkeypatch.setattr(runner, "_git", git)
    monkeypatch.setattr(runner, "_opencode", opencode)
    return runner


def test_fix_success_reports_ok(cfg, tmp_path, monkeypatch):
    def git(args, cwd):
        if args[0] == "ls-remote":
            return _ok("abc123\trefs/heads/main")
        return _ok()

    def opencode(args, cwd):
        assert cwd == tmp_path / "worktrees" / "fix-1-2"
        assert args[0] == "run" and "--auto" in args
        assert "--model" not in args
        return _ok()

    runner = _mk_runner(cfg, git, opencode, monkeypatch)
    res = runner.run_fix(chain_id=1, attempt=2, repo="acme/app", branch="main",
                         workflow_path=".github/workflows/ci.yml",
                         marker="# self-heal: true", sha="old")
    assert res.ok is True
    assert res.sha_before == "old"
    assert res.sha_after == "abc123"
    brief = (tmp_path / "worktrees" / "fix-1-2" / "briefing.md").read_text()
    assert "ci.yml" in brief


def test_fix_passes_model_flag_when_configured(cfg, tmp_path, monkeypatch):
    cfg = dataclasses.replace(cfg, opencode_model="opencode/big-pickle")

    def git(args, cwd):
        return _ok("old\trefs/heads/main")

    def opencode(args, cwd):
        assert "--model" in args
        assert args[args.index("--model") + 1] == "opencode/big-pickle"
        return _ok()

    runner = _mk_runner(cfg, git, opencode, monkeypatch)
    res = runner.run_fix(chain_id=1, attempt=1, repo="acme/app", branch="main",
                         workflow_path="p", marker="m", sha="old")
    assert res.ok is True


def test_opencode_failure_reports_not_ok(cfg, tmp_path, monkeypatch):
    def git(args, cwd):
        return _ok("old\trefs/heads/main")

    def opencode(args, cwd):
        return CmdResult(returncode=1, stdout="")

    runner = _mk_runner(cfg, git, opencode, monkeypatch)
    res = runner.run_fix(chain_id=1, attempt=1, repo="acme/app", branch="main",
                         workflow_path="p", marker="m", sha="old")
    assert res.ok is False
    assert res.sha_after is None
    assert res.exit_code == 1


def test_timeout_reports_not_ok(cfg, tmp_path, monkeypatch):
    def git(args, cwd):
        return _ok()

    def opencode(args, cwd):
        raise subprocess.TimeoutExpired(cmd="opencode", timeout=0)

    runner = _mk_runner(cfg, git, opencode, monkeypatch)
    res = runner.run_fix(chain_id=1, attempt=1, repo="acme/app", branch="main",
                         workflow_path="p", marker="m", sha="old")
    assert res.ok is False
    assert res.sha_after is None
    assert res.exit_code == -1
    assert "timeout" in res.notes.lower()
