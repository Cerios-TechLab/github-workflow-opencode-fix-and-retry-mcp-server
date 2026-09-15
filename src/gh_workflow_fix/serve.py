"""Starlette daemon: webhook-endpoint, HMAC-verificatie, scheduler-tick-loop."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from gh_workflow_fix.config import Config, load_config
from gh_workflow_fix.db import Database
from gh_workflow_fix.fixer import Fixer
from gh_workflow_fix.github_client import GitHubAPI
from gh_workflow_fix.models import Event
from gh_workflow_fix.opencode_runner import OpenCodeRunner
from gh_workflow_fix.service import Service

log = logging.getLogger("gh_workflow_fix.serve")

_REQUIRED_RUN_FIELDS = ("id", "path", "head_branch", "head_sha", "conclusion", "name")


# ---------------------------------------------------------------------------
# tick-loop (module-level, testable seam)
# ---------------------------------------------------------------------------
async def tick_loop(
    service: Service, *, interval_s: float, stop_event: asyncio.Event
) -> None:
    while not stop_event.is_set():
        try:
            await service.tick()
        except Exception:
            log.exception("tick-loop fout — draait door")
        await asyncio.sleep(interval_s)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _build_event(
    payload: dict[str, Any], delivery_id: str
) -> tuple[Event | None, str | None]:
    """Return (Event, repository_full_name | None) or (None, reason)."""
    action = payload.get("action")
    wr = payload.get("workflow_run")
    if not isinstance(wr, dict):
        return None, "missing workflow_run"
    missing = [f for f in _REQUIRED_RUN_FIELDS if f not in wr]
    if missing:
        return None, f"missing workflow_run field(s): {', '.join(missing)}"
    if action != "completed":
        return None, f"unsupported action: {action}"

    path = wr["path"]
    # strip @branch suffix
    path = path.rsplit("@", 1)[0]

    event = Event(
        delivery_id=delivery_id,
        run_id=wr["id"],
        workflow_path=path,
        head_branch=wr["head_branch"],
        head_sha=wr["head_sha"],
        action=action,
        conclusion=wr["conclusion"],
        handled="",
    )
    repo = None
    repo_obj = payload.get("repository")
    if isinstance(repo_obj, dict):
        repo = repo_obj.get("full_name")
    return event, repo


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def _webhook_route(service: Service, secret: str):
    async def _handler(request: Request) -> JSONResponse:
        body = await request.body()
        sig = request.headers.get("X-Hub-Signature-256")
        if not _verify_signature(secret, body, sig):
            return JSONResponse({"ok": False}, status_code=401)

        gh_event = (request.headers.get("X-GitHub-Event") or "").lower()
        delivery_id = request.headers.get("X-GitHub-Delivery", "")

        if gh_event != "workflow_run":
            return JSONResponse({"ok": False, "reason": "unsupported_event"})

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "reason": "invalid_json"}, status_code=400)

        event, repo = _build_event(payload, delivery_id)
        if event is None:
            return JSONResponse({"ok": False, "reason": repo})

        handled = await service.handle_webhook(event, repo=repo)
        return JSONResponse({"ok": True, "handled": handled})

    return _handler


def _healthz_route(db_path: str, tick_s: int):
    async def _handler(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "db": db_path, "tick": tick_s})

    return _handler


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(
    *, db: Database, github: GitHubAPI, fixer: Fixer, cfg: Config
) -> Starlette:
    service = Service(db, github, fixer, cfg)
    stop_event = asyncio.Event()

    @asynccontextmanager
    async def _lifespan(app: Starlette):
        task = asyncio.create_task(
            tick_loop(service, interval_s=cfg.scheduler_tick_s, stop_event=stop_event)
        )
        app.state.tick_task = task
        try:
            yield
        finally:
            stop_event.set()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = Starlette(
        routes=[
            Route(
                "/webhook/github",
                _webhook_route(service, cfg.webhook_secret),
                methods=["POST"],
            ),
            Route(
                "/healthz",
                _healthz_route(str(cfg.data_dir / "state.db"), cfg.scheduler_tick_s),
            ),
        ],
        lifespan=_lifespan,
    )
    return app


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    db = Database(cfg.data_dir / "state.db")
    github = GitHubAPI(
        http=httpx.AsyncClient(base_url=cfg.gh_api_base),
        token=cfg.gh_token,
        repo=cfg.gh_repo,
        data_dir=cfg.data_dir,
        marker=cfg.self_heal_marker,
        gh_api_base=cfg.gh_api_base,
    )
    runner = OpenCodeRunner(cfg)
    fixer = Fixer(db, github, runner, cfg)
    app = create_app(db=db, github=github, fixer=fixer, cfg=cfg)
    uvicorn.run(app, host=cfg.webhook_host, port=cfg.webhook_port)
