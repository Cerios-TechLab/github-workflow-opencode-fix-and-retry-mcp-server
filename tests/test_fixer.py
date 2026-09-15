from types import SimpleNamespace

from gh_workflow_fix.db import Database
from gh_workflow_fix.fixer import Fixer
from gh_workflow_fix.models import Chain, ChainState, utcnow_iso


def _chain():
    return Chain(repo="acme/app", workflow_path=".github/workflows/ci.yml",
                 workflow_name="ci", head_branch="main", run_id=10,
                 attempt=1, state=ChainState.RUNNING.value,
                 next_retry_at=utcnow_iso())


class _FakeRunner:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def run_fix(self, **kwargs):
        self.calls += 1
        self.chain_id = kwargs["chain_id"]
        return self.result


class _FakeGitHub:
    def __init__(self):
        self.reruns = []

    def rerun_failed_jobs(self, run_id):
        self.reruns.append(run_id)
        return True


def test_fix_ok_goes_waiting(cfg, tmp_path):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_chain())
    runner = _FakeRunner(SimpleNamespace(ok=True, sha_before="a", sha_after="b",
                                         exit_code=0, notes=""))
    gh = _FakeGitHub()
    Fixer(db, gh, runner, cfg).run_fix_pod(c, sha_before="a")
    assert db.get_chain(c.id).state == ChainState.WAITING.value
    assert db.get_chain(c.id).next_retry_at is None
    assert db.list_retries(c.id)[0].outcome == "fix_ok"


def test_no_change_reruns(cfg, tmp_path):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_chain())
    runner = _FakeRunner(SimpleNamespace(ok=True, sha_before="a", sha_after="a",
                                         exit_code=0, notes=""))
    gh = _FakeGitHub()
    Fixer(db, gh, runner, cfg).run_fix_pod(c, sha_before="a")
    assert gh.reruns == [10]
    assert db.get_chain(c.id).state == ChainState.WAITING.value


def test_opencode_failure_sets_fix_error(cfg, tmp_path):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_chain())
    runner = _FakeRunner(SimpleNamespace(ok=False, sha_before=None, sha_after=None,
                                         exit_code=1, notes="boom"))
    gh = _FakeGitHub()
    Fixer(db, gh, runner, cfg).run_fix_pod(c, sha_before="a")
    chain = db.get_chain(c.id)
    assert chain.state == ChainState.FIX_ERROR.value
    assert chain.last_error == "boom"
    assert db.list_retries(c.id)[0].outcome == "opencode_failed"