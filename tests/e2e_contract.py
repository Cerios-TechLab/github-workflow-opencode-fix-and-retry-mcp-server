"""Contract-smoke-test voor de HTTP-laag — zonder echte server of netwerk.

Draait de Starlette-app in-process via httpx.ASGITransport en bewijst dat de
HTTP-contracten intact zijn:

- create_app retourneert een Starlette-instantie
- POST /webhook/github bestaat en verifieert de HMAC-signature
- GET /healthz retourneert JSON met ok: true
- de tick_loop-seam bestaat, is async en is aan te roepen

De daadwerkelijke E2E met een echte GitHub-webhook gebeurt op de box na
installatie (zie deploy/README.md).
"""
import asyncio
import hashlib
import hmac
import inspect
import json

import httpx
from starlette.applications import Starlette

from gh_workflow_fix.db import Database
from gh_workflow_fix.serve import create_app, tick_loop
from gh_workflow_fix.service import Service


class _FakeGithub:
    async def workflow_yaml(self, path, ref):
        return "on:\n  push:\n# self-heal: true\n"

    async def latest_sha(self, branch):
        return "sha1"

    async def open_issue(self, title, body):
        return "https://github.com/acme/app/issues/1"

    async def update_issue(self, number, body):
        pass

    async def find_issue(self, title):
        return None

    async def rerun_failed_jobs(self, run_id):
        return True


class _FakeFixer:
    def run_fix_pod(self, chain, sha_before):
        pass


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload() -> dict:
    return {
        "action": "completed",
        "repository": {"full_name": "acme/app"},
        "workflow_run": {
            "id": 10,
            "name": "ci",
            "path": ".github/workflows/ci.yml@main",
            "head_branch": "main",
            "head_sha": "sha1",
            "conclusion": "failure",
        },
    }


def _make_app(cfg, tmp_path):
    db = Database(tmp_path / "state.db")
    return create_app(db=db, github=_FakeGithub(), fixer=_FakeFixer(), cfg=cfg)


def test_create_app_returns_starlette(cfg, tmp_path):
    app = _make_app(cfg, tmp_path)
    assert isinstance(app, Starlette)


async def test_webhook_route_exists_and_hmac_verified(cfg, tmp_path):
    app = _make_app(cfg, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # zonder signature -> 401
        r = await client.post(
            "/webhook/github",
            json={"x": 1},
            headers={"X-GitHub-Event": "workflow_run", "X-GitHub-Delivery": "d1"},
        )
        assert r.status_code == 401

        # verkeerde signature -> 401
        data = json.dumps(_payload()).encode()
        r = await client.post(
            "/webhook/github",
            content=data,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": "d1",
                "X-Hub-Signature-256": _sign("wrong", data),
            },
        )
        assert r.status_code == 401

        # geldige HMAC -> 200 met ok: true
        r = await client.post(
            "/webhook/github",
            content=data,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": "d1",
                "X-Hub-Signature-256": _sign(cfg.webhook_secret, data),
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True


async def test_healthz_returns_ok(cfg, tmp_path):
    app = _make_app(cfg, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True


def test_tick_loop_seam_exists_and_is_async():
    assert inspect.iscoroutinefunction(tick_loop)


async def test_tick_loop_seam_runs_and_stops(cfg, tmp_path):
    db = Database(tmp_path / "state.db")
    svc = Service(db, _FakeGithub(), _FakeFixer(), cfg)
    stop = asyncio.Event()
    task = asyncio.create_task(tick_loop(svc, interval_s=0.01, stop_event=stop))
    await asyncio.sleep(0.05)
    stop.set()
    await task