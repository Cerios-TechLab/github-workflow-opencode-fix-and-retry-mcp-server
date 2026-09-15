"""SQLite-persistentie: ketens, retries, events, overrides en fix-slot."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from gh_workflow_fix.models import Chain, ChainState, Event, Retry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    workflow_path TEXT NOT NULL,
    workflow_name TEXT NOT NULL,
    head_branch TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    state TEXT NOT NULL,
    next_retry_at TEXT,
    issue_url TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id INTEGER NOT NULL REFERENCES chains(id),
    attempt INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT NOT NULL,
    sha_before TEXT,
    sha_after TEXT,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    workflow_path TEXT NOT NULL,
    head_branch TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    action TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    handled TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS overrides (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_ACTIVE_STATES = tuple(
    s.value for s in ChainState if s not in (ChainState.DONE, ChainState.EXHAUSTED)
)


def _row_to_chain(row: sqlite3.Row) -> Chain:
    return Chain(
        id=row["id"], repo=row["repo"], workflow_path=row["workflow_path"],
        workflow_name=row["workflow_name"], head_branch=row["head_branch"],
        run_id=row["run_id"], attempt=row["attempt"], state=row["state"],
        next_retry_at=row["next_retry_at"], issue_url=row["issue_url"],
        last_error=row["last_error"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- chains --------------------------------------------------------------
    def insert_chain(self, chain: Chain) -> Chain:
        cur = self._conn.execute(
            "INSERT INTO chains (repo, workflow_path, workflow_name, head_branch, run_id,"
            " attempt, state, next_retry_at, issue_url, last_error, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (chain.repo, chain.workflow_path, chain.workflow_name, chain.head_branch,
             chain.run_id, chain.attempt, chain.state, chain.next_retry_at, chain.issue_url,
             chain.last_error, chain.created_at, chain.updated_at),
        )
        self._conn.commit()
        chain.id = cur.lastrowid
        return chain

    def get_chain(self, chain_id: int) -> Chain | None:
        row = self._conn.execute("SELECT * FROM chains WHERE id = ?", (chain_id,)).fetchone()
        return _row_to_chain(row) if row else None

    def get_active_chain(self, repo: str, workflow_path: str, head_branch: str) -> Chain | None:
        placeholders = ",".join("?" for _ in _ACTIVE_STATES)
        row = self._conn.execute(
            f"SELECT * FROM chains WHERE repo = ? AND workflow_path = ? AND head_branch = ?"
            f" AND state IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            (repo, workflow_path, head_branch, *_ACTIVE_STATES),
        ).fetchone()
        return _row_to_chain(row) if row else None

    def update_chain(self, chain: Chain) -> None:
        self._conn.execute(
            "UPDATE chains SET attempt=?, state=?, next_retry_at=?, issue_url=?,"
            " last_error=?, updated_at=? WHERE id=?",
            (chain.attempt, chain.state, chain.next_retry_at, chain.issue_url,
             chain.last_error, chain.updated_at, chain.id),
        )
        self._conn.commit()

    def list_chains(self, state: str | None = None) -> list[Chain]:
        if state is None:
            rows = self._conn.execute("SELECT * FROM chains ORDER BY id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM chains WHERE state = ? ORDER BY id", (state,)
            ).fetchall()
        return [_row_to_chain(r) for r in rows]

    def due_chains(self, now_iso: str) -> list[Chain]:
        rows = self._conn.execute(
            "SELECT * FROM chains WHERE state = ? AND next_retry_at IS NOT NULL"
            " AND next_retry_at <= ? ORDER BY next_retry_at",
            (ChainState.RUNNING.value, now_iso),
        ).fetchall()
        return [_row_to_chain(r) for r in rows]

    # -- retries -------------------------------------------------------------
    def insert_retry(self, retry: Retry) -> Retry:
        cur = self._conn.execute(
            "INSERT INTO retries (chain_id, attempt, started_at, finished_at, outcome,"
            " sha_before, sha_after, notes) VALUES (?,?,?,?,?,?,?,?)",
            (retry.chain_id, retry.attempt, retry.started_at, retry.finished_at,
             retry.outcome, retry.sha_before, retry.sha_after, retry.notes),
        )
        self._conn.commit()
        retry.id = cur.lastrowid
        return retry

    def list_retries(self, chain_id: int) -> list[Retry]:
        rows = self._conn.execute(
            "SELECT * FROM retries WHERE chain_id = ? ORDER BY id", (chain_id,)
        ).fetchall()
        return [
            Retry(id=r["id"], chain_id=r["chain_id"], attempt=r["attempt"],
                  started_at=r["started_at"], finished_at=r["finished_at"],
                  outcome=r["outcome"], sha_before=r["sha_before"],
                  sha_after=r["sha_after"], notes=r["notes"])
            for r in rows
        ]

    # -- events --------------------------------------------------------------
    def insert_event(self, event: Event) -> Event:
        cur = self._conn.execute(
            "INSERT INTO events (delivery_id, run_id, workflow_path, head_branch, head_sha,"
            " action, conclusion, handled, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (event.delivery_id, event.run_id, event.workflow_path, event.head_branch,
             event.head_sha, event.action, event.conclusion, event.handled,
             event.created_at),
        )
        self._conn.commit()
        event.id = cur.lastrowid
        return event

    def latest_event_time(self) -> str | None:
        row = self._conn.execute("SELECT MAX(created_at) AS t FROM events").fetchone()
        return row["t"] if row else None

    # -- config overrides ----------------------------------------------------
    def set_override(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO overrides (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_override(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM overrides WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def all_overrides(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._conn.execute("SELECT * FROM overrides")}

    # -- globale fix-slot (één actieve fix tegelijk) ------------------------
    def acquire_fix_lock(self, chain_id: int) -> bool:
        row = self._conn.execute("SELECT value FROM settings WHERE key = 'active_fix'").fetchone()
        if row and row["value"] != str(chain_id):
            return False
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES ('active_fix', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(chain_id),),
        )
        self._conn.commit()
        return True

    def release_fix_lock(self) -> None:
        self._conn.execute("DELETE FROM settings WHERE key = 'active_fix'")
        self._conn.commit()