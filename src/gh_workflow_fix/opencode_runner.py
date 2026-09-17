"""Lokale opencode fix-sessie: clone, briefing, run, sha-vergelijking."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FixResult:
    sha_before: str | None
    sha_after: str | None
    exit_code: int
    notes: str = ""
    ok: bool = False


class CmdResult(tuple):
    """Tuple-variant om subprocess-achtige resultaten te modelleren (testbaar)."""

    def __new__(cls, returncode: int, stdout: str):
        return tuple.__new__(cls, (returncode, stdout))

    @property
    def returncode(self) -> int:
        return self[0]

    @property
    def stdout(self) -> str:
        return self[1]


class OpenCodeRunner:
    def __init__(self, cfg):
        self.cfg = cfg

    # -- testbare seams ------------------------------------------------------
    def _git(self, args: list[str], cwd: Path) -> CmdResult:
        return self._run(["git", *args], cwd=cwd)

    def _opencode(self, args: list[str], cwd: Path) -> CmdResult:
        env = {**os.environ, "GITHUB_TOKEN": self.cfg.gh_token, "GH_TOKEN": self.cfg.gh_token}
        return self._run([str(self.cfg.opencode_bin), *args], cwd=cwd, env=env,
                         timeout=self.cfg.fix_timeout_s)

    def _run(self, cmd: list[str], *, cwd: Path, env=None, timeout=None) -> CmdResult:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True,
                              text=True, timeout=timeout, check=False)
        return CmdResult(proc.returncode, proc.stdout.strip())

    # -- conversatie ---------------------------------------------------------
    def run_fix(self, *, chain_id: int, attempt: int, repo: str, branch: str,
                workflow_path: str, marker: str, sha: str) -> FixResult:
        wd = self.cfg.data_dir / "worktrees" / f"fix-{chain_id}-{attempt}"
        wd.mkdir(parents=True, exist_ok=True)
        for args in (
            ["init", "-q"],
            ["remote", "add", "origin", f"git@github.com:{repo}.git"],
            ["fetch", "-q", "origin", branch],
            ["checkout", "-q", "--detach", f"origin/{branch}"],
        ):
            res = self._git(args, wd)
            if res.returncode != 0:
                return FixResult(sha_before=sha, sha_after=None, exit_code=res.returncode,
                                 notes=f"git {' '.join(args)} exit {res.returncode}", ok=False)
        (wd / "briefing.md").write_text(
            f"# Fix-opdracht\n\nRepareer de failing GitHub Actions workflow:\n"
            f"- bestand: {workflow_path}\n- branch: {branch}\n- sha: {sha}\n"
            f"- status-marker: {marker}\n\nPus de fix naar dezelfde branch.\n"
        )
        try:
            args = [
                "run", "--auto", "--title", f"ghwf-fix-{chain_id}-{attempt}",
                "--project", str(wd),
            ]
            if self.cfg.opencode_model:
                args += ["--model", self.cfg.opencode_model]
            res = self._opencode(args + [
                "--message",
                f"fix failing GitHub Actions workflow {workflow_path} (sha {sha}); marker '{marker}'",
            ], wd)
        except subprocess.TimeoutExpired:
            return FixResult(sha_before=sha, sha_after=None, exit_code=-1,
                             notes="opencode timeout", ok=False)
        if res.returncode != 0:
            return FixResult(sha_before=sha, sha_after=None, exit_code=res.returncode,
                             notes=f"opencode exit {res.returncode}", ok=False)
        remote = self._git(["ls-remote", "origin", branch], wd).stdout
        sha_after = remote.splitlines()[0].split("\t")[0] if remote else sha
        return FixResult(sha_before=sha, sha_after=sha_after, exit_code=0, notes="", ok=True)
