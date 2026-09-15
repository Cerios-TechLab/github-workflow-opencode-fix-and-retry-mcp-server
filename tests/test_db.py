from gh_workflow_fix.db import Database
from gh_workflow_fix.models import Chain, ChainState, Event, Retry, utcnow_iso


def _mk_chain():
    return Chain(
        repo="acme/app",
        workflow_path=".github/workflows/ci.yml",
        workflow_name="ci",
        head_branch="main",
        run_id=100,
        attempt=1,
        state=ChainState.RUNNING.value,
        next_retry_at=utcnow_iso(),
    )


def test_chain_crud_and_active_lookup(tmp_path):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain())
    assert c.id is not None
    assert db.get_chain(c.id) == c
    active = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    assert active == c
    c.state = ChainState.DONE.value
    db.update_chain(c)
    assert db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main") is None


def test_paused_state_counts_as_active(tmp_path):
    db = Database(tmp_path / "state.db")
    c = _mk_chain()
    c.state = ChainState.PAUSED.value
    db.insert_chain(c)
    assert db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main") == c


def test_due_chains_threshold(tmp_path):
    db = Database(tmp_path / "state.db")
    now = utcnow_iso()
    c = _mk_chain()
    c.next_retry_at = now
    db.insert_chain(c)
    assert len(db.due_chains(now)) == 1
    future = _mk_chain()
    future.workflow_path = ".github/workflows/future.yml"
    future.next_retry_at = "2999-01-01T00:00:00+00:00"
    db.insert_chain(future)
    assert len(db.due_chains(now)) == 1
    assert len(db.due_chains("2999-01-01T00:00:00+00:00")) == 2


def test_list_chains_state_filter(tmp_path):
    db = Database(tmp_path / "state.db")
    db.insert_chain(_mk_chain())
    done = _mk_chain()
    done.workflow_path = ".github/workflows/other.yml"
    done.state = ChainState.DONE.value
    db.insert_chain(done)
    assert len(db.list_chains(state=ChainState.RUNNING.value)) == 1
    assert len(db.list_chains()) == 2


def test_retries_and_events(tmp_path):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain())
    db.insert_retry(Retry(chain_id=c.id, attempt=1, started_at=utcnow_iso(), outcome="fix_ok"))
    assert len(db.list_retries(c.id)) == 1
    assert db.list_retries(999) == []
    db.insert_event(Event(delivery_id="d1", run_id=100, workflow_path="p", head_branch="main",
                          head_sha="abc", action="completed", conclusion="failure",
                          handled="new_chain"))
    assert db.latest_event_time() is not None


def test_overrides_and_fix_lock(tmp_path):
    db = Database(tmp_path / "state.db")
    db.set_override("gh_oc_auto", "false")
    assert db.get_override("gh_oc_auto") == "false"
    db.set_override("gh_oc_auto", "true")
    assert db.get_override("gh_oc_auto") == "true"
    c1 = db.insert_chain(_mk_chain())
    c2 = _mk_chain()
    c2.workflow_path = ".github/workflows/two.yml"
    c2 = db.insert_chain(c2)
    assert db.acquire_fix_lock(c1.id) is True
    assert db.acquire_fix_lock(c2.id) is False
    db.release_fix_lock()
    assert db.acquire_fix_lock(c2.id) is True
    db.release_fix_lock()