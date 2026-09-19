"""State models for the fix-and-retry chains."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ChainState(str, enum.Enum):
    RUNNING = "running"      # scheduled or fix in progress
    WAITING = "waiting"      # fix pushed/rerun; waiting for next run event
    FIX_ERROR = "fix_error"  # fix runner failed; next retry scheduled
    PAUSED = "paused"
    DONE = "done"
    EXHAUSTED = "exhausted"


class EventHandled(str, enum.Enum):
    NEW_CHAIN = "new_chain"
    CONTINUED = "continued"
    DONE = "done"
    EXHAUSTED = "exhausted"
    IGNORED = "ignored"
    IGNORED_PAUSED = "ignored_paused"
    IGNORED_STALE = "ignored_stale"
    IGNORED_REPO = "ignored_repo"
    ERROR = "error"


@dataclass
class Chain:
    repo: str
    workflow_path: str
    workflow_name: str
    head_branch: str
    run_id: int
    attempt: int
    state: str
    next_retry_at: str | None = None
    issue_url: str | None = None
    last_error: str | None = None
    id: int | None = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)


@dataclass
class Retry:
    chain_id: int
    attempt: int
    started_at: str
    outcome: str
    finished_at: str | None = None
    sha_before: str | None = None
    sha_after: str | None = None
    notes: str = ""
    id: int | None = None


@dataclass
class Event:
    delivery_id: str
    run_id: int
    workflow_path: str
    head_branch: str
    head_sha: str
    action: str
    conclusion: str
    handled: str
    workflow_name: str = ""
    created_at: str = field(default_factory=utcnow_iso)
    id: int | None = None