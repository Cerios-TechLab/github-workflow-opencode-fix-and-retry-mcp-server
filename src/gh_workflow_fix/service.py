"""Kern-businesslogica: webhook-afhandeling, planning en issues."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from gh_workflow_fix.markers import parse_workflow_marker
from gh_workflow_fix.models import Chain, ChainState, Event, utcnow_iso


def _add_minutes(iso: str, minutes: int) -> str:
    """ISO-timestamp + `minutes` minuten, zelfde formaat als `utcnow_iso()`."""
    return (datetime.fromisoformat(iso) + timedelta(minutes=minutes)).isoformat(
        timespec="seconds"
    )


class Service:
    def __init__(self, db, github, fixer, cfg):
        self.db = db
        self.github = github
        self.fixer = fixer
        self.cfg = cfg

    # -- effectieve config (DB-override wint van env) -------------------------
    def effective_retry_delays(self) -> tuple[int, ...]:
        raw = self.db.get_override("retry_delays_min")
        if raw:
            try:
                parsed = tuple(int(p) for p in raw.split(",") if p.strip())
                if parsed:
                    return parsed
            except ValueError:
                pass
        return self.cfg.retry_delays_min

    def effective_gh_oc_auto(self) -> bool:
        raw = self.db.get_override("gh_oc_auto")
        if raw is not None:
            return raw.strip().lower() in ("1", "true")
        return self.cfg.gh_oc_auto

    def _delay_before(self, attempt: int) -> int:
        return self.effective_retry_delays()[attempt - 1]

    # -- webhook --------------------------------------------------------------
    async def handle_webhook(self, event: Event, repo: str | None = None) -> str:
        if repo is not None and repo != self.cfg.gh_repo:
            return "ignored_repo"

        if event.conclusion not in ("success", "failure", "timed_out"):
            event.handled = "ignored"
            self.db.insert_event(event)
            return "ignored"

        if event.conclusion == "success":
            return await self._handle_success(event)
        return await self._handle_failure(event)

    async def _handle_success(self, event: Event) -> str:
        chain = self.db.get_active_chain(
            self.cfg.gh_repo, event.workflow_path, event.head_branch
        )
        if chain is not None:
            chain.state = ChainState.DONE.value
            chain.next_retry_at = None
            self.db.update_chain(chain)
            event.handled = "done"
        else:
            event.handled = "ignored"
        self.db.insert_event(event)
        return event.handled

    async def _handle_failure(self, event: Event) -> str:
        chain = self.db.get_active_chain(
            self.cfg.gh_repo, event.workflow_path, event.head_branch
        )
        if chain is not None:
            now = utcnow_iso()
            candidate = _add_minutes(now, self._delay_before(chain.attempt))
            chain.next_retry_at = (
                candidate if chain.next_retry_at is None
                else min(chain.next_retry_at, candidate)
            )
            chain.state = ChainState.RUNNING.value
            self.db.update_chain(chain)
            event.handled = "continued"
            self.db.insert_event(event)
            return "continued"

        yaml = await self.github.workflow_yaml(event.workflow_path, event.head_sha)
        if yaml is None or not parse_workflow_marker(yaml, self.cfg.self_heal_marker):
            event.handled = "ignored"
            self.db.insert_event(event)
            return "ignored"

        now = utcnow_iso()
        chain = Chain(
            repo=self.cfg.gh_repo,
            workflow_path=event.workflow_path,
            workflow_name="",
            head_branch=event.head_branch,
            run_id=event.run_id,
            attempt=1,
            state=ChainState.RUNNING.value,
            next_retry_at=_add_minutes(now, self._delay_before(1)),
            created_at=now,
            updated_at=now,
        )
        self.db.insert_chain(chain)
        if not self.effective_gh_oc_auto():
            chain.state = ChainState.EXHAUSTED.value
            chain.next_retry_at = None
            self.db.update_chain(chain)
            await self._maybe_create_issue(chain)
            event.handled = "exhausted"
        else:
            event.handled = "new_chain"
        self.db.insert_event(event)
        return event.handled

    # -- scheduler ------------------------------------------------------------
    async def tick(self) -> None:
        for chain in self.db.due_chains(utcnow_iso()):
            sha = await self.github.latest_sha(chain.head_branch)
            if sha is None:
                chain.last_error = "branch_sha_none"
                self.db.update_chain(chain)
                continue
            await asyncio.to_thread(self.fixer.run_fix_pod, chain, sha)
            fresh = self.db.get_chain(chain.id)
            if fresh is not None and fresh.state == ChainState.FIX_ERROR.value:
                await self._schedule_or_exhaust(fresh)

    async def _schedule_or_exhaust(self, chain: Chain) -> None:
        if chain.attempt >= len(self.effective_retry_delays()):
            chain.state = ChainState.EXHAUSTED.value
            chain.next_retry_at = None
            await self._maybe_create_issue(chain)
        else:
            chain.attempt += 1
            chain.state = ChainState.RUNNING.value
            chain.next_retry_at = _add_minutes(
                utcnow_iso(), self._delay_before(chain.attempt)
            )
            chain.last_error = None
        self.db.update_chain(chain)

    # -- issues ---------------------------------------------------------------
    async def _maybe_create_issue(self, chain: Chain) -> None:
        title = self._issue_title(chain)
        existing = await self.github.find_issue(title)
        if existing is not None:
            chain.issue_url = (
                f"https://github.com/{self.cfg.gh_repo}/issues/{existing}"
            )
        else:
            url = await self.github.open_issue(title, self._issue_body(chain))
            if url:
                chain.issue_url = url
        self.db.update_chain(chain)

    def _issue_title(self, chain: Chain) -> str:
        return f"ghwf-fix {chain.workflow_path} ({chain.head_branch})"

    def _issue_body(self, chain: Chain) -> str:
        return (
            f"Workflow `{chain.workflow_path}` faalt op branch "
            f"`{chain.head_branch}` in repo `{chain.repo}`.\n\n"
            f"- poging: {chain.attempt}\n"
            f"- laatste fout: {chain.last_error or '-'}\n\n"
            f"Fixed? Push en re-run de workflow; anders opnieuw toewijzen."
        )