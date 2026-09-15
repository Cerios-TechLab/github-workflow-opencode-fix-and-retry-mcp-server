"""Orkestreert één fix-poging: lock, runner, resultaat, chain-update."""
from __future__ import annotations

from gh_workflow_fix.models import Chain, ChainState, Retry, utcnow_iso


class Fixer:
    def __init__(self, db, github, runner, cfg):
        self.db = db
        self.github = github
        self.runner = runner
        self.cfg = cfg

    def run_fix_pod(self, chain: Chain, sha_before: str) -> None:
        if not self.db.acquire_fix_lock(chain.id):
            return
        try:
            chain.state = ChainState.RUNNING.value
            chain.updated_at = utcnow_iso()
            self.db.update_chain(chain)
            self.db.insert_retry(Retry(chain_id=chain.id, attempt=chain.attempt,
                                       started_at=utcnow_iso(), outcome="started"))
            res = self.runner.run_fix(chain_id=chain.id, attempt=chain.attempt,
                                      repo=chain.repo, branch=chain.head_branch,
                                      workflow_path=chain.workflow_path,
                                      marker=self.cfg.self_heal_marker, sha=sha_before)
            self._apply_result(chain, res)
        finally:
            self.db.release_fix_lock()

    def _apply_result(self, chain: Chain, res) -> None:
        if not res.ok:
            self._finish_retry(chain, "opencode_failed", res.notes)
            chain.state = ChainState.FIX_ERROR.value
            chain.last_error = res.notes
            self._commit(chain)
            return
        if res.sha_after == res.sha_before:
            if not self.github.rerun_failed_jobs(chain.run_id):
                self._finish_retry(chain, "rerun_failed", res.notes)
                chain.state = ChainState.FIX_ERROR.value
                chain.last_error = "rerun_failed_jobs niet geaccepteerd"
                self._commit(chain)
                return
            self._finish_retry(chain, "fix_ok_no_change", "")  # geen push; rerun gezet
        else:
            self._finish_retry(chain, "fix_ok", res.notes)
        chain.state = ChainState.WAITING.value
        chain.next_retry_at = None
        self._commit(chain)

    def _finish_retry(self, chain: Chain, outcome: str, notes: str) -> None:
        retries = self.db.list_retries(chain.id)
        last = retries[-1] if retries else None
        if last and last.outcome == "started":
            last = Retry(chain_id=last.chain_id, attempt=last.attempt,
                         started_at=last.started_at, finished_at=utcnow_iso(),
                         outcome=outcome, sha_before=last.sha_before,
                         sha_after=last.sha_after, notes=notes, id=last.id)
            self.db.update_retry(last)

    def _commit(self, chain: Chain) -> None:
        chain.updated_at = utcnow_iso()
        self.db.update_chain(chain)