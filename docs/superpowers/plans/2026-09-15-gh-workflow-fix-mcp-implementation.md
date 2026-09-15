# GitHub Workflow Fix-and-Retry MCP Server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bouw een zelfstandig MCP-server-pakket dat gefaalde, gemarkeerde GitHub Actions workflows van één repo tot 3 keer fix-and-retry't (lokale opencode-agent + rerun) met wachttijden 15/30/60 min, en fouten als GitHub-issue logt afhankelijk van `GH_OC_AUTO`.

**Architecture:** Eén Python-pakket `gh_workflow_fix` met een daemon (Starlette webhook + scheduler), een fix-runner (clone → opencode run → rerun), een FastMCP stdio-server voor controle, en een gedeelde SQLite-staat. De service-module bevat alle businesslogica en is volledig testbaar met fake GitHub-client en fake opencode-runner.

**Tech Stack:** Python ≥3.11, fastmcp≥2.0, httpx, starlette, uvicorn, pydantic, pyyaml, pytest + pytest-asyncio, ruff.

**Repo root:** `/tmp/github-workflow-opencode-fix-and-retry-mcp-server` (git clone van `git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git`). Werk altijd in deze map. Git-identiteit repo-lokaal is ingesteld (`Steavy` / `steavy@cerios-techlab.io`).

## Global Constraints

- Python ≥ 3.11; dependency-vloeren: `fastmcp>=2.0`, `httpx>=0.27`, `starlette>=0.40`, `uvicorn>=0.30`, `pydantic>=2.0`, `pyyaml>=6.0`; dev: `pytest>=8.0`, `pytest-asyncio>=0.24`, `ruff>=0.5`.
- Marker default: `# self-heal: true`. Overschrijfbaar via `SELF_HEAL_MARKER`.
- Retry-wachttijden default `(15, 30, 60)` minuten, overschrijfbaar via `RETRY_DELAYS_MIN`. Wachttijd vóór poging N is `delays[N-1]`.
- Eén `GH_REPO` per server (`owner/repo`). Events van andere repos worden genegeerd.
- `GH_OC_AUTO` (`true`/`false`): `false` → issue bij eerste detectie (attempt 0); `true` → issue alleen na uitputting van 3 pogingen. Issues worden als gewoon GitHub-issue in `GH_REPO` aangemaakt.
- State-machine: `attempt` = het pogingsnummer dat als **volgende** wordt uitgevoerd (1..3). Nieuwe keten (failing `completed`-event, marker aanwezig, geen actieve keten): `attempt=1`, `next_retry_at=now+delay_before(1)`. Failing event op actieve keten (state `running` of `waiting`): `next_retry_at = min(existing, now+delay_before(attempt))`, state blijft `running` (er wordt niets extra's verbruikt). Eindigt een fix-poging `attempt` in `fix_error` of faalt de rerun (failing event terwijl `waiting`): als `attempt+1 <= len(delays)` → `attempt=attempt+1`, `next_retry_at=now+delay_before(attempt)`, state `running`; anders → `exhausted` (+ issue bij `gh_oc_auto=true`). `fix_ok` (nieuwe push) → state `waiting`, `next_retry_at=None` — wacht op volgend `completed`-event (success → `done`). `paused`: events en retries genegeerd.
- Kwaliteitspoort = CLI: `pytest` en `ruff check` groen. Geen LSP-claims.
- Git-push: na elke commit pushen naar `main` via SSH-URL `git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main` (identiteit `Steavy`). HTTPS-push faalt op deze box.

---

## File Structure

```
src/gh_workflow_fix/
  __init__.py          versie
  config.py            Config uit env vars
  models.py            Chain, Retry, Event, enums, utcnow_iso
  db.py                SQLite-persistentie (ketens, retries, events, overrides, fix-slot)
  markers.py           self-heal marker detectie in YAML
  github_client.py     async GitHub REST-client
  opencode_runner.py   lokale opencode fix-sessie (clone, briefing, run)
  fixer.py             orkestreert één fix-poging
  service.py           kern-businesslogica (webhook, scheduler, issues)
  serve.py             Starlette-daemon (webhook-route + scheduler-loop)
  mcp.py               FastMCP stdio-server (tools)
tests/                 conftest + test_* per module
deploy/                systemd-unit + install-script + ops-README
```

### Task 1: Project-skeleton + config

**Files:**
- Create: `pyproject.toml`
- Create: `src/gh_workflow_fix/__init__.py`
- Create: `src/gh_workflow_fix/config.py`
- Create: `tests/conftest.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config` dataclass met velden `gh_token: str`, `gh_repo: str`, `gh_oc_auto: bool`, `webhook_secret: str`, `self_heal_marker: str`, `retry_delays_min: tuple[int, ...]`, `data_dir: Path`, `opencode_bin: Path`, `webhook_host: str`, `webhook_port: int`, `fix_timeout_s: int`, `scheduler_tick_s: int` en methode `delay_before(attempt: int) -> int`; en functie `load_config(env: Mapping[str, str] | None = None) -> Config`.
- Produces: pytest-config via `pyproject.toml` (`asyncio_mode = "auto"`, `testpaths=["tests"]`).

- [ ] **Step 1: schrijf `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "gh-workflow-fix-mcp"
version = "0.1.0"
description = "MCP server: watch and fix-and-retry failing GitHub Actions workflows"
requires-python = ">=3.11"
license = "MIT"
dependencies = [
    "fastmcp>=2.0",
    "httpx>=0.27",
    "starlette>=0.40",
    "uvicorn>=0.30",
    "pydantic>=2.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.5",
]

[tool.hatch.build.targets.wheel]
packages = ["src/gh_workflow_fix"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: schrijf `src/gh_workflow_fix/__init__.py`**

```python
"""GitHub workflow fix-and-retry MCP server."""

__version__ = "0.1.0"
```

- [ ] **Step 3: schrijf de faalende test `tests/test_config.py`**

```python
from pathlib import Path

from gh_workflow_fix.config import load_config


def test_load_config_defaults():
    cfg = load_config(
        {
            "GH_TOKEN": "tok",
            "GH_REPO": "acme/app",
            "GH_OC_AUTO": "true",
            "WEBHOOK_SECRET": "s3cret",
        }
    )
    assert cfg.gh_repo == "acme/app"
    assert cfg.gh_oc_auto is True
    assert cfg.self_heal_marker == "# self-heal: true"
    assert cfg.retry_delays_min == (15, 30, 60)
    assert cfg.webhook_port == 18080


def test_load_config_overrides():
    cfg = load_config(
        {
            "GH_TOKEN": "tok",
            "GH_REPO": "acme/app",
            "GH_OC_AUTO": "false",
            "WEBHOOK_SECRET": "s3cret",
            "RETRY_DELAYS_MIN": "5,10",
            "DATA_DIR": "/tmp/data",
            "WEBHOOK_PORT": "9999",
            "OPENCODE_BIN": "/tmp/opencode",
        }
    )
    assert cfg.gh_oc_auto is False
    assert cfg.retry_delays_min == (5, 10)
    assert cfg.data_dir == Path("/tmp/data")
    assert cfg.webhook_port == 9999
    assert cfg.opencode_bin == Path("/tmp/opencode")


def test_missing_required_env_raises():
    try:
        load_config({})
    except ValueError as exc:
        assert "GH_TOKEN" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 4: `.venv` aanmaken en de test laten falen**

Run: `cd /tmp/github-workflow-opencode-fix-and-retry-mcp-server && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' -q && .venv/bin/pytest tests/test_config.py -q 2>&1 | tail -5`
Expected: pytest faalt met `ModuleNotFoundError` (pakket bestaat nog niet). De pip-install geeft mogelijk een foutmelding over ontbrekende `README`/`src` — daarom eerste de mappen aanmaken in Step 5; indien pip faalt: herhaal `pip install -e '.[dev]'` na Step 5/8.

- [ ] **Step 5: schrijf `src/gh_workflow_fix/config.py`**

```python
"""Configuratie uit omgevingsvariabelen."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MARKER = "# self-heal: true"
DEFAULT_DELAYS = (15, 30, 60)


@dataclass(frozen=True)
class Config:
    gh_token: str
    gh_repo: str
    gh_oc_auto: bool
    webhook_secret: str
    self_heal_marker: str = DEFAULT_MARKER
    retry_delays_min: tuple[int, ...] = DEFAULT_DELAYS
    data_dir: Path = field(default_factory=lambda: Path("/var/lib/gh-workflow-fix"))
    opencode_bin: Path = field(default_factory=lambda: Path("/root/.opencode/bin/opencode"))
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 18080
    fix_timeout_s: int = 1800
    scheduler_tick_s: int = 60

    def delay_before(self, attempt: int) -> int:
        """Wachttijd in minuten vóór poging `attempt` (1-based)."""
        return self.retry_delays_min[attempt - 1]


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _delays(value: str) -> tuple[int, ...]:
    return tuple(int(p) for p in value.split(",") if p.strip())


def load_config(env=None) -> Config:
    env = env if env is not None else os.environ

    def req(name: str) -> str:
        value = env.get(name)
        if not value:
            raise ValueError(f"Ontbrekende env-var {name}")
        return value

    delays = DEFAULT_DELAYS
    if "RETRY_DELAYS_MIN" in env and env["RETRY_DELAYS_MIN"].strip():
        delays = _delays(env["RETRY_DELAYS_MIN"])
        if len(delays) < 1:
            raise ValueError("RETRY_DELAYS_MIN moet minimaal 1 waarde bevatten")

    return Config(
        gh_token=req("GH_TOKEN"),
        gh_repo=req("GH_REPO"),
        gh_oc_auto=_bool(req("GH_OC_AUTO")),
        webhook_secret=req("WEBHOOK_SECRET"),
        self_heal_marker=env.get("SELF_HEAL_MARKER", DEFAULT_MARKER),
        retry_delays_min=delays,
        data_dir=Path(env.get("DATA_DIR", "/var/lib/gh-workflow-fix")),
        opencode_bin=Path(env.get("OPENCODE_BIN", "/root/.opencode/bin/opencode")),
        webhook_host=env.get("WEBHOOK_HOST", "0.0.0.0"),
        webhook_port=int(env.get("WEBHOOK_PORT", "18080")),
        fix_timeout_s=int(env.get("FIX_TIMEOUT_S", "1800")),
        scheduler_tick_s=int(env.get("SCHEDULER_TICK_S", "60")),
    )
```

- [ ] **Step 6: schrijf `tests/conftest.py`** (gedeelde fixtures)

```python
import pytest

from gh_workflow_fix.config import Config


@pytest.fixture
def cfg(tmp_path):
    return Config(
        gh_token="t",
        gh_repo="acme/app",
        gh_oc_auto=True,
        webhook_secret="s3cret",
        data_dir=tmp_path,
        opencode_bin=tmp_path / "opencode",
    )
```

- [ ] **Step 7: draai de test en zie dat hij groen is**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: 3 passed.

- [ ] **Step 8: draai ruff**

Run: `.venv/bin/ruff check src tests`
Expected: geen fouten.

- [ ] **Step 9: commit + push**

```bash
cd /tmp/github-workflow-opencode-fix-and-retry-mcp-server
git add pyproject.toml src/gh_workflow_fix/__init__.py src/gh_workflow_fix/config.py tests/conftest.py tests/test_config.py
git commit -m "feat: project skeleton en config (env-driven)"
git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main
```

---

### Task 2: Modellen + SQLite-laag

**Files:**
- Create: `src/gh_workflow_fix/models.py`
- Create: `src/gh_workflow_fix/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: niets (DB gebruikt alleen stdlib `sqlite3`).
- Produces:
  - `models.py`: `utcnow_iso() -> str` (ISO-8601 UTC, `timespec="seconds"`); `ChainState` enum (`running`, `waiting`, `fix_error`, `paused`, `done`, `exhausted`); dataclasses `Chain(id:int|None=None, repo:str, workflow_path:str, workflow_name:str, head_branch:str, run_id:int, attempt:int, state:str, next_retry_at:str|None=None, issue_url:str|None=None, last_error:str|None=None, created_at=utcnow, updated_at=utcnow)`, `Retry(chain_id:int, attempt:int, started_at:str, outcome:str, finished_at:str|None=None, sha_before:str|None=None, sha_after:str|None=None, notes:str="", id:int|None=None)`, `Event(delivery_id, run_id, workflow_path, head_branch, head_sha, action, conclusion, handled, created_at=utcnow, id=None)`.
  - `db.py`: `Database(path: Path)` met `insert_chain(chain)->Chain`, `get_chain(id)->Chain|None`, `get_active_chain(repo, workflow_path, head_branch)->Chain|None`, `update_chain(chain)`, `list_chains(state=None)->list[Chain]`, `due_chains(now_iso)->list[Chain]`, `insert_retry(retry)->Retry`, `list_retries(chain_id)->list[Retry]`, `insert_event(event)->Event`, `latest_event_time()->str|None`, `set_override(key,value)`, `get_override(key)->str|None`, `all_overrides()->dict`, `acquire_fix_lock(chain_id)->bool`, `release_fix_lock()`.

- [ ] **Step 1: schrijf `src/gh_workflow_fix/models.py`**

```python
"""Staatmodellen voor de fix-and-retry ketens."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ChainState(str, enum.Enum):
    RUNNING = "running"      # gepland of fix wordt uitgevoerd
    WAITING = "waiting"      # fix gepusht/rerun; wacht op volgend run-event
    FIX_ERROR = "fix_error"  # fix-runner faalde; volgende retry gepland
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
    created_at: str = field(default_factory=utcnow_iso)
    id: int | None = None
```

- [ ] **Step 2: schrijf de faalende test `tests/test_db.py`**

```python
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
    assert len(db.due_chains(now)) == 1  # future nog niet due
    assert len(db.due_chains("2999-01-01T00:00:00+00:00")) == 2  # beide due


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
```

- [ ] **Step 3: draai de test en zie dat hij faalt**

Run: `.venv/bin/pytest tests/test_db.py -q 2>&1 | tail -5`
Expected: FAIL met `ModuleNotFoundError: gh_workflow_fix.db`.

- [ ] **Step 4: schrijf `src/gh_workflow_fix/db.py`**

```python
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

    # ── chains ──────────────────────────────────────────────────────
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

    # ── retries ─────────────────────────────────────────────────────
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

    # ── events ──────────────────────────────────────────────────────
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

    # ── config overrides ────────────────────────────────────────────
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

    # ── globale fix-slot (één actieve fix tegelijk) ────────────────
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
```

- [ ] **Step 5: draai de test en zie dat hij groen is**

Run: `.venv/bin/pytest tests/test_db.py -q`
Expected: 6 passed.

- [ ] **Step 6: ruff**

Run: `.venv/bin/ruff check src tests`
Expected: geen fouten.

- [ ] **Step 7: commit + push**

```bash
git add src/gh_workflow_fix/models.py src/gh_workflow_fix/db.py tests/test_db.py
git commit -m "feat: modellen en SQLite-persistentie voor fix-and-retry ketens"
git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main
```

---

### Task 3: self-heal-marker detectie

**Files:**
- Create: `src/gh_workflow_fix/markers.py`
- Test: `tests/test_markers.py`

**Interfaces:**
- Consumes: niets.
- Produces: `parse_workflow_marker(yaml_text: str, marker: str) -> bool` — waar als een `on:`-trigger-blok (werkstroomdefinitie) de marker-comment bevat; `has_marker_in_branch(yaml_text: str, branch: str) -> bool` — altijd False (paren van marker + branch worden door de service beschouwd; hier alleen de parser).

- [ ] **Step 1: schrijf de faalende test `tests/test_markers.py`**

```python
from gh_workflow_fix.markers import has_marker_in_branch, parse_workflow_marker

MARKER = "# self-heal: true"
OTHER = "# self-heal: false"
RAW = """
name: CI

on:
  push:
    branches: [main]

# self-heal: true
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""


def test_marker_found_in_raw():
    assert parse_workflow_marker(RAW, MARKER) is True


def test_marker_missing():
    assert parse_workflow_marker(RAW, OTHER) is False


def test_other_marker_ignored():
    tagged = RAW.replace("# self-heal: true", "# other: true")
    assert parse_workflow_marker(tagged, MARKER) is False


def test_empty_text():
    assert parse_workflow_marker("", MARKER) is False
    assert parse_workflow_marker("# no workflow here", MARKER) is False


def test_branch_paren_helper_always_false():
    assert has_marker_in_branch(RAW, "main") is False
```

- [ ] **Step 2: draai en zie dat hij faalt**

Run: `.venv/bin/pytest tests/test_markers.py -q 2>&1 | tail -3`
Expected: FAIL met `ModuleNotFoundError`.

- [ ] **Step 3: schrijf `src/gh_workflow_fix/markers.py`**

```python
"""Detectie van de self-heal marker in workflow-YAML."""
from __future__ import annotations


def parse_workflow_marker(yaml_text: str, marker: str) -> bool:
    """True als de opgegeven marker-comment in een werkstroomdefinitie staat."""
    needle = marker.strip()
    return any(line.strip() == needle for line in yaml_text.splitlines())


def has_marker_in_branch(yaml_text: str, branch: str) -> bool:
    """Reservering: paren van marker + branch bij specifieke triggers (P2)."""
    return False
```

- [ ] **Step 4: draai en zie dat hij groen is**

Run: `.venv/bin/pytest tests/test_markers.py -q`
Expected: 5 passed.

- [ ] **Step 5: ruff + commit + push**

```bash
.venv/bin/ruff check src tests
git add src/gh_workflow_fix/markers.py tests/test_markers.py
git commit -m "feat: self-heal marker detectie in workflow-YAML"
git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main
```

---

### Task 4: GitHub REST-client

**Files:**
- Create: `src/gh_workflow_fix/github_client.py`
- Test: `tests/test_github_client.py`

**Interfaces:**
- Consumes: `config.Config` (token, repo).
- Produces: `GitHubAPI(httpx.AsyncClient, token, repo, data_dir: Path, marker: str)` met:
  - `workflow_yaml(path: str, ref: str) -> str | None` (None bij 404)
  - `open_issue(title, body) -> str | None` (issue URL; en bij `GH_API_BASE` override al dan niet)
  - `update_issue(issue_number: int, body: str) -> None`
  - `find_issue(title) -> int | None` (laatste open issue met die titel)
  - `rerun_failed_jobs(run_id: int) -> bool` (`POST …/rerun-failed-jobs`)
  - `latest_sha(branch: str) -> str | None` (`GET /repos/…/commits?sha=<branch>&per_page=1`)
  - `check_rate_limit() -> tuple[int, int]`

**Test-benadering:** HTTP-responses via `MockTransport`-pattern; config met `GH_API_BASE` naar testserver.

Config-uitbreiding in `config.py` (Task 1): voeg veld `gh_api_base: str = "https://api.github.com"` toe + env-var `GH_API_BASE`. (Tiny edit van `config.py` + regressie `tests/test_config.py`.)

- [ ] **Step 1: breid `config.py` + `tests/test_config.py` uit** — voeg `gh_api_base: str = "https://api.github.com"` en env mapping toe. Draai `pytest tests/test_config.py -q` → 3 passed, plus eventueel nieuwe assert in `test_load_config_defaults` (`cfg.gh_api_base == "https://api.github.com"`).
- [ ] **Step 2: schrijf de faalende test `tests/test_github_client.py`**

```python
import base64

import httpx
import pytest

from gh_workflow_fix.github_client import GitHubAPI

BASE = "https://api.example.com"
YAML = "on:\n  push:\n# self-heal: true\n"
ENC = base64.b64encode(YAML.encode()).decode()


def _client(cfg, handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url=BASE)
    c = GitHubAPI(http=http, token=cfg.gh_token, repo=cfg.gh_repo,
                  data_dir=cfg.data_dir, marker=cfg.self_heal_marker,
                  gh_api_base=BASE)
    return c, http


async def test_workflow_yaml_decodes(cfg):
    def handler(request):
        assert request.headers["authorization"] == "Bearer t"
        assert request.url.path == "/repos/acme/app/contents/.github/workflows/ci.yml"
        assert request.url.params["ref"] == "sha1"
        return httpx.Response(200, json={"content": ENC, "encoding": "base64"})

    client, http = _client(cfg, handler)
    async with http:
        assert await client.workflow_yaml(".github/workflows/ci.yml", "sha1") == YAML


async def test_workflow_yaml_404_returns_none(cfg):
    def handler(request):
        return httpx.Response(404, json={"message": "Not Found"})

    client, http = _client(cfg, handler)
    async with http:
        assert await client.workflow_yaml(".github/workflows/ci.yml", "sha1") is None


async def test_open_issue(cfg):
    def handler(request):
        assert request.url.path == "/repos/acme/app/issues"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        body = request.read().decode()
        assert '"title"' in body and '"body"' in body
        return httpx.Response(201, json={"html_url": "https://github.com/acme/app/issues/1"})

    client, http = _client(cfg, handler)
    async with http:
        assert await client.open_issue("t", "b") == "https://github.com/acme/app/issues/1"


async def test_find_issue(cfg):
    def handler(request):
        assert request.url.path == "/repos/acme/app/issues"
        assert request.url.params["state"] == "open"
        items = [
            {"number": 2, "title": "anders"},
            {"number": 3, "title": "ghwf-fix t"},
        ]
        return httpx.Response(200, json=items)

    client, http = _client(cfg, handler)
    async with http:
        assert await client.find_issue("ghwf-fix t") == 3
        assert await client.find_issue("bestaat niet") is None


async def test_rerun_failed_jobs_accepts(cfg):
    def handler(request):
        assert request.url.path == "/repos/acme/app/actions/runs/10/rerun-failed-jobs"
        assert request.method == "POST"
        return httpx.Response(202, json={})

    client, http = _client(cfg, handler)
    async with http:
        assert await client.rerun_failed_jobs(10) is True


async def test_latest_sha(cfg):
    def handler(request):
        assert request.url.path == "/repos/acme/app/commits"
        assert request.url.params["sha"] == "main"
        return httpx.Response(200, json=[{"sha": "abc123"}])

    client, http = _client(cfg, handler)
    async with http:
        assert await client.latest_sha("main") == "abc123"
```

- [ ] **Step 3: draai en zie dat hij consistent faalt** (`ModuleNotFoundError: gh_workflow_fix.github_client`)
- [ ] **Step 4: schrijf `src/gh_workflow_fix/github_client.py`**:

```python
"""Async GitHub REST-client voor workflow-content, issues en rerun."""
from __future__ import annotations

import base64
from pathlib import Path

import httpx


class GitHubAPI:
    def __init__(self, *, http: httpx.AsyncClient, token: str, repo: str,
                 data_dir: Path, marker: str, gh_api_base: str):
        self._http = http
        self.repo = repo
        self.marker = marker
        self._api = gh_api_base
        self._token = token
        self._workflows_dir = data_dir / "workflows"
        self._workflows_dir.mkdir(parents=True, exist_ok=True)

    def _repo_url(self, path: str) -> str:
        return f"{self._api}/repos/{self.repo}/{path}"

    def _auth(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._token}"}

    async def workflow_yaml(self, path: str, ref: str) -> str | None:
        r = await self._http.get(
            self._repo_url(f"contents/{path}"),
            params={"ref": ref},
            headers={**self._auth(), "accept": "application/vnd.github.raw"},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        safe_name = path.rsplit("/", 1)[-1].replace(".", "_")
        (self._workflows_dir / f"{safe_name}.yml").write_text(content)
        return content

    async def open_issue(self, title: str, body: str) -> str | None:
        r = await self._http.post(
            self._repo_url("issues"),
            json={"title": title, "body": body},
            headers={**self._auth(), "X-GitHub-Api-Version": "2022-11-28"},
        )
        r.raise_for_status()
        return r.json().get("html_url")

    async def update_issue(self, issue_number: int, body: str) -> None:
        r = await self._http.patch(
            self._repo_url(f"issues/{issue_number}"),
            json={"body": body},
            headers={**self._auth(), "X-GitHub-Api-Version": "2022-11-28"},
        )
        r.raise_for_status()

    async def find_issue(self, title: str) -> int | None:
        r = await self._http.get(self._repo_url("issues"),
                                 params={"state": "open", "per_page": 50},
                                 headers={**self._auth(), "X-GitHub-Api-Version": "2022-11-28"})
        r.raise_for_status()
        for item in r.json():
            if item.get("title") == title:
                return int(item["number"])
        return None

    async def rerun_failed_jobs(self, run_id: int) -> bool:
        r = await self._http.post(self._repo_url(f"actions/runs/{run_id}/rerun-failed-jobs"),
                                  headers=self._auth())
        return r.status_code in (202, 204)

    async def latest_sha(self, branch: str) -> str | None:
        r = await self._http.get(self._repo_url("commits"),
                                 params={"sha": branch, "per_page": 1},
                                 headers=self._auth())
        r.raise_for_status()
        items = r.json()
        return items[0]["sha"] if items else None
```

- [ ] **Step 5: draai de test en zie dat hij groen is**
- [ ] **Step 6: ruff + commit + push**

```bash
.venv/bin/ruff check src tests
git add src/gh_workflow_fix/config.py src/gh_workflow_fix/github_client.py tests/test_config.py tests/test_github_client.py
git commit -m "feat: async GitHub REST-client voor workflow-content, issues en rerun"
git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main
```

---

### Task 5: OpenCode fix-runner

**Files:**
- Create: `src/gh_workflow_fix/opencode_runner.py`
- Test: `tests/test_opencode_runner.py`

**Interfaces:**
- Consumes: `Config` (opencode_bin, fix_timeout_s, gh_token, data_dir).
- Produces: `OpenCodeRunner` met:
  - `run_fix(chain_id: int, attempt: int, repo: str, branch: str, workflow_path: str, marker: str, sha: str) -> FixResult`
  - `FixResult` (dataclass): `sha_before`, `sha_after` (None bij opencode-falen/timeout), `exit_code: int` (-1 bij timeout), `notes: str`, `ok: bool`.
  - `CmdResult` (NamedTuple): `returncode: int`, `stdout: str`.
  - Testable seams: `_git(args: list[str], cwd: Path) -> CmdResult` en `_opencode(args: list[str], cwd: Path) -> CmdResult`, beide monkeypatch-able instance-methoden.

**Uitvoeringscontract (`run_fix`):**
1. Werkmap `{cfg.data_dir}/worktrees/fix-{chain_id}-{attempt}` aanmaken.
2. `_git(["init", "-q"], wd)`, `_git(["remote", "add", "origin", f"git@github.com:{repo}.git"], wd)`, `_git(["fetch", "-q", "origin", branch], wd)`, `_git(["checkout", "-q", "--detach", f"origin/{branch}"], wd)`. Git-exit != 0 per stap → `FixResult(ok=False, notes="git: <args> exit <code>", exit_code=code)`.
3. `briefing.md` schrijven in wd: minimal instructie met werkstroom, sha, marker (met-aangesloten brief-patroon).
4. `_opencode(["run", "--auto", "--title", f"ghwf-fix-{chain_id}-{attempt}", "--project", str(wd), "--message", f"fix failing GitHub Actions workflow {workflow_path} (sha {sha}); marker '{marker}'"], wd)` met `env={**os.environ, "GITHUB_TOKEN": cfg.gh_token, "GH_TOKEN": cfg.gh_token}`, `timeout=cfg.fix_timeout_s`.
   - `subprocess.TimeoutExpired` → `FixResult(ok=False, sha_after=None, exit_code=-1, notes="opencode timeout")`.
   - `returncode != 0` → `FixResult(ok=False, sha_after=None, exit_code=rc, notes="opencode exit <rc>")`.
5. `ok=True`: lees `_git(["ls-remote", "origin", branch], wd).stdout` → eerste token van eerste regel = `sha_after` (kan gelijk zijn aan `sha_before`; Fixer beslist over rerun). `FixResult(sha_before=sha, sha_after=..., exit_code=0, notes="", ok=True)`.

- [ ] **Step 1: schrijf de faalende test `tests/test_opencode_runner.py`**

```python
import subprocess

from gh_workflow_fix.opencode_runner import CmdResult, FixResult, OpenCodeRunner


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
```

- [ ] **Step 2: draai en zie dat hij faalt**
- [ ] **Step 3: schrijf `src/gh_workflow_fix/opencode_runner.py`**

```python
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
                              text=True, timeout=timeout)
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
            res = self._opencode([
                "run", "--auto", "--title", f"ghwf-fix-{chain_id}-{attempt}",
                "--project", str(wd),
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
```

- [ ] **Step 4: draai en zie dat hij groen is**
- [ ] **Step 5: ruff + commit + push**

```bash
.venv/bin/ruff check src tests
git add src/gh_workflow_fix/opencode_runner.py tests/test_opencode_runner.py
git commit -m "feat: lokale opencode fix-runner met git clone en sha-vergelijking"
git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main
```

---

### Task 6: Fixer (orkestreert één fix-poging)

**Files:**
- Create: `src/gh_workflow_fix/fixer.py`
- Test: `tests/test_fixer.py`

**Interfaces:**
- Consumes: `Database`, `GitHubAPI`, `OpenCodeRunner`.
- Produces: `Fixer(db: Database, github: GitHubAPI, runner: OpenCodeRunner, cfg: Config)` met `run_fix_pod(chain: Chain, sha_before: str) -> None` — voert de fix uit, werkt de keten en retries bij, en logt `fix_ok` / `fix_failed` (→ `fix_error`) / `rerun_failed` naar het retries-overzicht.

**Uitvoeringscontract:**
1. `fix_lock`-acquire; indien mislukt → skip (al bezet).
2. Start `chain.state = running`, `chain.updated_at = utcnow_iso()`.
3. Log `Retry(chain_id, attempt, started_at, outcome="started")`.
4. Roep `runner.run_fix(...)` aan.
5. Uitkomst:
   - `runner.ok == False` → `outcome = "opencode_failed"`, `chain.state = fix_error`, `chain.last_error = notes`, plan volgende poging via `schedule_next` (service) of `exhausted`.
   - `sha_after == sha_before` → `outcome = "no_change"`, probeer `github.rerun_failed_jobs(chain.run_id)`; ok → `chain.state = waiting`; niet ok → `outcome = "rerun_failed"`, `chain.state = fix_error`.
   - `sha_after != sha_before` → `outcome = "fix_ok"`, `chain.state = waiting`.
6. Bij `waiting`: `chain.next_retry_at = None` (geen deadline tot volgend falend event).
7. `release_fix_lock()` altijd via `try/finally`.

- [ ] **Step 1: schrijf de faalende test `tests/test_fixer.py`**

```python
import pytest

from gh_workflow_fix.db import Database
from gh_workflow_fix.fixer import Fixer
from gh_workflow_fix.models import Chain, ChainState, utcnow_iso


def _chain(repo="acme/app"):
    return Chain(repo=repo, workflow_path=".github/workflows/ci.yml",
                 workflow_name="ci", head_branch="main", run_id=10,
                 attempt=1, state=ChainState.RUNNING.value,
                 next_retry_at=utcnow_iso())


class _FakeRunner:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def run_fix(self, chain_id, attempt, repo, branch, workflow_path, marker, sha):
        self.calls += 1
        self.chain_id = chain_id
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
```

Gebruik in tests `from types import SimpleNamespace`.

- [ ] **Step 2: draai en zie dat hij faalt**
- [ ] **Step 3: schrijf `src/gh_workflow_fix/fixer.py`** — structuur:

```python
class Fixer:
    def __init__(self, db, github, runner, cfg):
        ...

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
```

- [ ] **Step 4: draai en zie dat hij groen is**
- [ ] **Step 5: ruff + commit + push**

```bash
.venv/bin/ruff check src tests
git add src/gh_workflow_fix/fixer.py tests/test_fixer.py
git commit -m "feat: fixer orkestreert fix, rerun en fix_error-overgangen"
git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main
```

---

### Task 7: Service — kern-businesslogica

**Files:**
- Create: `src/gh_workflow_fix/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `Database`, `GitHubAPI`, `Fixer`, `Config`.
- Produces: `Service(db, github, fixer, cfg)`:
  - `handle_webhook(event: Event) -> str /* handled */`: repo-check → conclusie in {`failure`, `timed_out`} · {`success`} → marker-check → keten-upsert + planning; doet issue-creatie op basis van `effective_gh_oc_auto()`.
  - `tick() -> None`: `due_chains` (state `running`, `next_retry_at <= now`) → voor elke keten `state=running` bevestigen en `fixer.run_fix_pod(chain, sha_before=github.latest_sha(branch))`; na afloop `chain.state` herlezen en bij `fix_error` → `_schedule_or_exhaust(chain)`.
  - `_schedule_or_exhaust(chain) -> None`: `attempt+1 <= len(effective_retry_delays())` → `attempt=attempt+1`, `next_retry_at=now+delay_before(attempt)`, state `running`; anders state `exhausted` + `_maybe_create_issue(chain)` + foutmelding.
  - Helpers: `_maybe_create_issue(chain)`; `_decide_kind(event, chain_state) -> handled-waarde`; `_mark_chain_and_event(...)`.
  - `effective_retry_delays() -> tuple[int, ...]` en `effective_gh_oc_auto() -> bool`: DB-override (`retry_delays_min`, `gh_oc_auto`) wint van env.

**Businessregels (uit design) — samenvatting:**
- Failing `completed`-event (`failure`/`timed_out`), geen actieve keten:
  - marker niet gevonden (`workflow_yaml(...)` → None of marker mist) → event `ignored`, geen keten.
  - marker gevonden:
    - `gh_oc_auto=false` → issue meteen aanmaken, keten `exhausted`, event `exhausted`.
    - `gh_oc_auto=true` → nieuwe `Chain(attempt=1, state=running, next_retry_at=now+delay_before(1))`, event `new_chain`.
- Failing `completed`-event op actieve keten (state `running`/`waiting`): `next_retry_at = min(existing, now+delay_before(attempt))`, event `continued`. Let op: probeer GEEN nieuw issue (al dan niet na exhaust) — issue-hond eerst de bestaande keten afhandelen.
- `success` `completed`-event op actieve keten → state `done`, `next_retry_at=None`, event `done`.
- `tick()`: keteels met `next_retry_at <= now` → state `running`; `run_fix_pod`; na terugkeer laadt keten opnieuw; `fix_error` → `_schedule_or_exhaust`; `waiting` blijft.

- [ ] **Step 1: schrijf de faalende test `tests/test_service.py`** — redelijk compleet (fake github en fixer):

```python
from dataclasses import replace

from gh_workflow_fix.db import Database
from gh_workflow_fix.models import Chain, ChainState, Event, utcnow_iso
from gh_workflow_fix.service import Service


class _FakeGithub:
    def __init__(self):
        self.shas = {"main": "sha1"}
        self.issues = []

    def workflow_yaml(self, path, ref):
        return "on:\n  push:\n# self-heal: true\n"

    def latest_sha(self, branch):
        return self.shas.get(branch)

    def open_issue(self, title, body):
        self.issues.append(title)
        return "https://github.com/acme/app/issues/1"

    def update_issue(self, number, body):
        pass

    def find_issue(self, title):
        return None


class _FakeFixer:
    def __init__(self):
        self.calls = []

    def run_fix_pod(self, chain, sha_before):
        self.calls.append((chain.id, sha_before))


def _service(cfg, tmp_path):
    db = Database(tmp_path / "state.db")
    gh = _FakeGithub()
    fixer = _FakeFixer()
    svc = Service(db, gh, fixer, cfg)
    return svc, db, gh, fixer


def failing_event(delivery="d1", path=".github/workflows/ci.yml", conclusion="failure"):
    return Event(delivery_id=delivery, run_id=10, workflow_path=path,
                 head_branch="main", head_sha="sha1", action="completed",
                 conclusion=conclusion, handled="")


def test_new_failure_starts_chain_in_schedule(cfg, tmp_path):
    svc, db, gh, fixer = _service(cfg, tmp_path)
    svc.handle_webhook(failing_event())
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    assert chain is not None
    assert chain.attempt == 1
    assert chain.next_retry_at is not None
    assert gh.issues == []  # auto=true → geen issue nu


def test_auto_false_logs_issue_immediately(cfg, tmp_path):
    cfg = replace(cfg, gh_oc_auto=False)
    svc, db, gh, fixer = _service(cfg, tmp_path)
    svc.handle_webhook(failing_event())
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    assert chain.state == ChainState.EXHAUSTED.value
    assert gh.issues


def test_tick_runs_due_fix(cfg, tmp_path):
    svc, db, gh, fixer = _service(cfg, tmp_path)
    svc.handle_webhook(failing_event())
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    assert chain.next_retry_at is not None
    chain.next_retry_at = utcnow_iso()
    db.update_chain(chain)
    svc.tick()
    assert fixer.calls == [(chain.id, "sha1")]


def test_success_event_marks_done(cfg, tmp_path):
    svc, db, gh, fixer = _service(cfg, tmp_path)
    svc.handle_webhook(failing_event())
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    chain.next_retry_at = None
    chain.state = ChainState.WAITING.value
    db.update_chain(chain)
    svc.handle_webhook(Event(delivery_id="d2", run_id=11,
                             workflow_path=".github/workflows/ci.yml",
                             head_branch="main", head_sha="sha2",
                             action="completed", conclusion="success", handled=""))
    chain = db.get_chain(chain.id)
    assert chain.state == ChainState.DONE.value
    assert chain.next_retry_at is None
```

 (De `Chain`-import is nodig in `test_success_event_marks_done`; laat hem in de imports staan. Eventueel `event`-logica: gebruik `db.latest_event_time()` om te bevestigen dat events worden weggeschreven.)

- [ ] **Step 2: draai en zie dat hij faalt (rood)**
- [ ] **Step 3: schrijf `src/gh_workflow_fix/service.py`**
- [ ] **Step 4: draai en zie dat hij groen is (groen)**
- [ ] **Step 5: ruff + commit + push**

```bash
.venv/bin/ruff check src tests
git add src/gh_workflow_fix/service.py tests/test_service.py
git commit -m "feat: kern-service met webhook-afhandeling, planning en issues"
git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main
```

---

### Task 8: Daemon (Starlette + scheduler)

**Files:**
- Create: `src/gh_workflow_fix/serve.py`
- Test: `tests/test_serve.py`
- Create: `src/gh_workflow_fix/__main__.py`

**Interfaces:**
- Consumes: `Service`, `Config`, `Database`, `GitHubAPI`, `Fixer`, `OpenCodeRunner`.
- Produces:
  - `create_app(*, db: Database, github: GitHubAPI, fixer: Fixer, cfg: Config) -> Starlette`: routes `POST /webhook/github` (HMAC-verificatie via `X-Hub-Signature-256`, schending → 401) en `GET /healthz`; start stoppen van de asynchrone tick-loop via lifespan.
  - `serve.py` `main()`: `load_config()`, db openen, github/fixer opbouwen, uvicorn-run (`host`, `port`).
  - `__main__.py`: `from gh_workflow_fix.serve import main; main()`.

**Webhook-contract:**
- Verifieer `X-Hub-Signature-256` d.m.v. HMAC-SHA256 (hex) over de ruwe body (prefix `sha256=`). Onjuist of ontbrekend → `401`.
- Parse body als dict; `delivery_id` uit header `X-GitHub-Delivery`; `X-GitHub-Event` = event-type (normalizeer naar lower).
- Verwerk uitsluitend event-type `workflow_run` met `action == "completed"`; `workflow_run.path` is `path@branch` → strip de `@branch`-suffix (controle: suffix komt overeen met `head_branch`, anders suffix afknippen op laatste `@`). Conclusie `success` → `done`-afhandeling in service; conclusie `failure`/`timed_out` → keten-logica; alle andere conclusies → event met `handled="ignored"`.
- Response bij verwerkte webhook `{"ok": true}`; bij niet te verwerken (verkeerde event-type, ontbrekende velden) → `{"ok": false, "reason": "..."}`.

- [ ] **Step 1: schrijf de faalende test `tests/test_serve.py`** (gebruik httpx AsyncClient met `ASGITransport` op `create_app`):

```python
import hashlib
import hmac

import httpx
import pytest

from gh_workflow_fix.serve import create_app
from gh_workflow_fix.db import Database


def _sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def app(cfg, tmp_path):
    db = Database(tmp_path / "state.db")
    return create_app(db=db, cfg=cfg)


async def test_webhook_hmac_required(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.post("/webhook/github", json={"x": 1}, headers={
            "X-GitHub-Event": "workflow_run",
            "X-GitHub-Delivery": "d1",
        })
        assert r.status_code == 401


async def test_webhook_completed_run(app):
    body = json.dumps({
        "action": "completed",
        "workflow_run": {
            "id": 10,
            "name": "ci",
            "path": ".github/workflows/ci.yml@main",
            "head_branch": "main",
            "head_sha": "sha1",
            "conclusion": "failure",
        },
    }).encode()
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-GitHub-Delivery": "d1",
        "X-Hub-Signature-256": _sign("s3cret", body),
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.post("/webhook/github", content=body, headers=headers)
        assert r.status_code == 200
```

- [ ] **Step 2: draai en zie dat hij faalt**
- [ ] **Step 3: schrijf `src/gh_workflow_fix/serve.py`** — structuur:

```python
def create_app(*, db, github, fixer, cfg) -> Starlette:
    service = Service(db, github, fixer, cfg)
    ...
    return app
```

Webhook-body → Event; start van de achtergrondtick-loop via starlette-lifespan: `app.state.tick_task = asyncio.create_task(_tick_loop(...))` bij startup, cancel bij shutdown; `_tick_loop` roept elke `cfg.scheduler_tick_s` seconden `service.tick()` aan.

- [ ] **Step 4: draai en zie dat hij groen is**
- [ ] **Step 5: ruff + commit + push**

```bash
.venv/bin/ruff check src tests
git add src/gh_workflow_fix/serve.py src/gh_workflow_fix/__main__.py tests/test_serve.py
git commit -m "feat: Starlette-daemon met webhook, HMAC en scheduler-loop"
git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main
```

---

### Task 9: MCP-server (FastMCP stdio)

**Files:**
- Create: `src/gh_workflow_fix/mcp.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `Service`, `Database`, `Config`, `GitHubAPI`.
- Produces: `create_mcp(service: Service, cfg: Config) -> FastMCP`; tools:
  - `list_chains(state: str | None = None) -> list[dict]` (id, workflow_path, head_branch, attempt, state, next_retry_at, issue_url)
  - `get_chain(chain_id: int) -> dict`
  - `retry_now(chain_id: int) -> dict` (activeert `next_retry_at = now`)
  - `pause_chain(chain_id: int) -> dict`
  - `resume_chain(chain_id: int) -> dict` (behoudt oude `next_retry_at`)
  - `create_issue_now(chain_id: int) -> dict` (maakt issue + logt event `issue_manual`)
  - `set_config(key: str, value: str) -> dict` (overrides: `gh_oc_auto`, `retry_delays_min`)
  - `health() -> dict` (db-path, scheduler-tick, laatste event-tijd, aantal ketens per state)

**MCP-projecttitel:** `project="gh-workflow-fix"`, `name="gh-workflow-fix"`, beschrijving voor register.

- [ ] **Step 1: schrijf de faalende test `tests/test_mcp.py`** — standaard: `mcp.call_tool("health")`, `mcp.call_tool("list_chains")` etc. met fake service.
- [ ] **Step 2: draai en zie dat hij faalt**
- [ ] **Step 3: schrijf `src/gh_workflow_fix/mcp.py`**
- [ ] **Step 4: draai en zie dat hij groen is**
- [ ] **Step 5: ruff + commit + push**

```bash
.venv/bin/ruff check src tests
git add src/gh_workflow_fix/mcp.py tests/test_mcp.py
git commit -m "feat: FastMCP stdio-server met keten-controle en health-tools"
git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main
```

---

### Task 10: Uitrol op de box — systemd + MCP-registratie + end-to-end

**Files:**
- Create: `deploy/gh-workflow-fix-daemon.service` (systemd-unit: `User=root`, `WorkingDirectory=/root/gh-workflow-fix`, `ExecStart=/root/gh-workflow-fix/.venv/bin/python -m gh_workflow_fix`, `EnvironmentFile=/etc/gh-workflow-fix.env`)
- Create: `deploy/install.sh` (maakt `/root/gh-workflow-fix`, venv-install `-e .`, schrijft `/etc/gh-workflow-fix.env` uit interactieve prompts, `systemctl daemon-reload`, enable+start)
- Create: `deploy/README.md` (ops-doc: secrets, poort, webhook-config, MCP-registratie, tests)
- Test: `tests/e2e_contract.py` (nginx-free smoke: hier alleen contract — daadwerkelijke E2E op de box na installatie)

**Uitrolstappen (handmatig, in de box):**
1. Kopieer pakket naar `/root/gh-workflow-fix` (rsync local kopie) en `pip install -e '.[dev]'`.
2. `/etc/gh-workflow-fix.env` vullen: zie `deploy/README.md`. (Let op: `GH_OC_AUTO=false` wordt aangeraden voor eerste tests.)
3. systemd-unit installeren + `systemctl enable --now gh-workflow-fix-daemon`.
4. Config check: `curl -s localhost:18080/healthz`.
5. firewalld openen: `firewall-cmd --permanent --add-port=18080/tcp && firewall-cmd --reload`.
6. GitHub-webhook aanmaken (repo-settings → Webhooks → `http://techlab5.mooo.com:18080/webhook/github`, `application/json`, event `workflow_run`, secret = hetzelfde als `WEBHOOK_SECRET`).
7. E2E: make (of direct) een test-workflow `.github/workflows/e2e-marker.yml` met failing `exit 1` job + `# self-heal: true`; run handmatig; verwacht: keten actief, na 15 min fix, rerun, uiteindelijk `done` of `exhausted`. Daarna test-workflow verwijderen.

**MCP-registratie (in opencode-config):** voeg MCP-server toe in `/root/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "gh-workflow-fix": {
      "command": "/root/gh-workflow-fix/.venv/bin/python",
      "args": ["-m", "gh_workflow_fix.mcp"],
      "env": { "GH_TOKEN": "...", "GH_REPO": "acme/app", "GH_OC_AUTO": "true", "WEBHOOK_SECRET": "..." }
    }
  }
}
```

- [ ] **Step 1: schrijf `deploy/` bestanden** (unit, install.sh, README.md, e2e_contract.py)
- [ ] **Step 2: ruff op tests + commit + push**

```bash
.venv/bin/ruff check src tests deploy
git add deploy/ tests/e2e_contract.py
git commit -m "docs(ops): systemd-unit, install-script en ops-README voor uitrol"
git push git@github.com:Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server.git main
```

- [ ] **Step 3: voer install.sh uit op de box en start de daemon.** *Dit is handmatig en buiten het automatische proces — meld aan de gebruiker, niet zelf uitvoeren.*

---

## Verificatie per taak

Elke taak eindigt met `pytest` groen én `ruff check src tests` zonder output. Commits per taak, direct gepusht naar `main` via SSH-URL. Geen LSP-opclaims — CLI-output is het bewijs.

## Post-implementatie review

- [ ] Design-paragraaf "Edge cases en mitigaties" tegen de code getoetst.
- [ ] Gearchiveerd: `docs/superpowers/plans/` blijft naast `specs/` staan.
- [ ] Klaar voor production-run: gedrag bij real-webhook-runs bevestigd aan de hand van `docs/superpowers/specs/2026-09-15-github-workflow-fix-retry-mcp-design.md` §"Edge cases en mitigaties".