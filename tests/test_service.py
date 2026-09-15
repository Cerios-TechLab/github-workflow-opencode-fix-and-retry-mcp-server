from dataclasses import replace

from gh_workflow_fix.db import Database
from gh_workflow_fix.models import ChainState, Event, utcnow_iso
from gh_workflow_fix.service import Service


class _FakeGithub:
    def __init__(self, yaml_text):
        self.yaml_text = yaml_text
        self.shas = {"main": "sha1"}
        self.issues = []

    async def workflow_yaml(self, path, ref):
        return self.yaml_text

    async def latest_sha(self, branch):
        return self.shas.get(branch)

    async def open_issue(self, title, body):
        self.issues.append(title)
        return "https://github.com/acme/app/issues/1"

    async def update_issue(self, number, body):
        pass

    async def find_issue(self, title):
        return None


class _FakeFixer:
    def __init__(self):
        self.calls = []

    def run_fix_pod(self, chain, sha_before):
        self.calls.append((chain.id, sha_before))


def _service(cfg, tmp_path, yaml_text="on:\n  push:\n# self-heal: true\n"):
    db = Database(tmp_path / "state.db")
    gh = _FakeGithub(yaml_text)
    fixer = _FakeFixer()
    svc = Service(db, gh, fixer, cfg)
    return svc, db, gh, fixer


def failing_event(delivery="d1", path=".github/workflows/ci.yml", conclusion="failure"):
    return Event(delivery_id=delivery, run_id=10, workflow_path=path,
                 head_branch="main", head_sha="sha1", action="completed",
                 conclusion=conclusion, handled="")


async def test_new_failure_starts_chain_in_schedule(cfg, tmp_path):
    svc, db, gh, _fixer = _service(cfg, tmp_path)
    await svc.handle_webhook(failing_event())
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    assert chain is not None
    assert chain.attempt == 1
    assert chain.next_retry_at is not None
    assert gh.issues == []  # auto=true → geen issue nu


async def test_auto_false_logs_issue_immediately(cfg, tmp_path):
    cfg = replace(cfg, gh_oc_auto=False)
    svc, db, gh, _fixer = _service(cfg, tmp_path)
    await svc.handle_webhook(failing_event())
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    assert chain.state == ChainState.EXHAUSTED.value
    assert gh.issues


async def test_no_marker_ignored(cfg, tmp_path):
    svc, db, _gh, _fixer = _service(cfg, tmp_path, yaml_text="name: x\non: push\n")
    handled = await svc.handle_webhook(failing_event())
    assert handled == "ignored"
    assert db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main") is None


async def test_tick_runs_due_fix(cfg, tmp_path):
    svc, db, _gh, fixer = _service(cfg, tmp_path)
    await svc.handle_webhook(failing_event())
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    assert chain.next_retry_at is not None
    chain.next_retry_at = utcnow_iso()
    db.update_chain(chain)
    await svc.tick()
    assert fixer.calls == [(chain.id, "sha1")]


async def test_success_event_marks_done(cfg, tmp_path):
    svc, db, _gh, _fixer = _service(cfg, tmp_path)
    await svc.handle_webhook(failing_event())
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    chain.next_retry_at = None
    chain.state = ChainState.WAITING.value
    db.update_chain(chain)
    await svc.handle_webhook(Event(delivery_id="d2", run_id=11,
                                   workflow_path=".github/workflows/ci.yml",
                                   head_branch="main", head_sha="sha2",
                                   action="completed", conclusion="success", handled=""))
    chain = db.get_chain(chain.id)
    assert chain.state == ChainState.DONE.value
    assert chain.next_retry_at is None


async def test_failing_event_on_active_chain_reschedules(cfg, tmp_path):
    svc, db, gh, _fixer = _service(cfg, tmp_path)
    await svc.handle_webhook(failing_event())
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    await svc.handle_webhook(failing_event(delivery="d2", conclusion="timed_out"))
    chain = db.get_chain(chain.id)
    assert chain.state == ChainState.RUNNING.value
    assert chain.next_retry_at is not None
    assert gh.issues == []  # geen dubbel issue


async def test_wrong_repo_ignored(cfg, tmp_path):
    svc, db, _gh, _fixer = _service(cfg, tmp_path)
    handled = await svc.handle_webhook(failing_event(), repo="other/org")
    assert handled == "ignored_repo"
    assert db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main") is None