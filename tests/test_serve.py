import asyncio
import hashlib
import hmac
import json

import httpx
import pytest

from gh_workflow_fix.db import Database
from gh_workflow_fix.models import Event, utcnow_iso
from gh_workflow_fix.serve import create_app, tick_loop
from gh_workflow_fix.service import Service


class _FakeGithub:
    def __init__(self):
        self.issues = []

    async def workflow_yaml(self, path, ref):
        return "on:\n  push:\n# self-heal: true\n"

    async def latest_sha(self, branch):
        return "sha1"

    async def open_issue(self, title, body):
        self.issues.append(title)
        return "https://github.com/acme/app/issues/1"

    async def update_issue(self, number, body):
        pass

    async def find_issue(self, title):
        return None

    async def rerun_failed_jobs(self, run_id):
        return True


class _FakeFixer:
    def __init__(self):
        self.calls = []

    def run_fix_pod(self, chain, sha_before):
        self.calls.append((chain.id, sha_before))


def _sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _event():
    return Event(
        delivery_id="d1",
        run_id=10,
        workflow_path=".github/workflows/ci.yml",
        head_branch="main",
        head_sha="sha1",
        action="completed",
        conclusion="failure",
        handled="",
    )


@pytest.fixture
def app(cfg, tmp_path):
    db = Database(tmp_path / "state.db")
    gh = _FakeGithub()
    fixer = _FakeFixer()
    return create_app(db=db, github=gh, fixer=fixer, cfg=cfg)


def _payload(**overrides):
    body = {
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
    body.update(overrides)
    return body


async def _post(client, body, event="workflow_run", delivery="d1", secret="s3cret"):
    data = json.dumps(body).encode()
    return await client.post(
        "/webhook/github",
        content=data,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": _sign(secret, data),
            "Content-Type": "application/json",
        },
    )


async def test_webhook_hmac_required(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/webhook/github",
            json={"x": 1},
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": "d1",
            },
        )
        assert r.status_code == 401


async def test_webhook_bad_signature_401(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
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


async def test_webhook_completed_run_ok(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await _post(client, _payload())
        assert r.status_code == 200
        assert r.json() == {"ok": True, "handled": "new_chain"}


async def test_webhook_carries_workflow_name(cfg, tmp_path):
    db = Database(tmp_path / "state.db")
    gh = _FakeGithub()
    fixer = _FakeFixer()
    app = create_app(db=db, github=gh, fixer=fixer, cfg=cfg)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await _post(client, _payload())
        assert r.status_code == 200
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    assert chain is not None
    assert chain.workflow_name == "ci"


async def test_webhook_unsupported_event_reason(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await _post(client, _payload(), event="push")
        assert r.status_code == 200
        assert r.json()["ok"] is False


async def test_webhook_success_conclusion_marks_done(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await _post(
            client,
            _payload(
                workflow_run={
                    **_payload()["workflow_run"],
                    "conclusion": "success",
                }
            ),
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "handled": "ignored"}


async def test_healthz(app, tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["ok"] is True


async def test_tick_loop_runs_until_stop(cfg, tmp_path):
    db = Database(tmp_path / "state.db")
    gh = _FakeGithub()
    fixer = _FakeFixer()
    svc = Service(db, gh, fixer, cfg)
    await svc.handle_webhook(_event())
    chain = db.get_active_chain("acme/app", ".github/workflows/ci.yml", "main")
    chain.next_retry_at = utcnow_iso()
    db.update_chain(chain)

    stop = asyncio.Event()
    task = asyncio.create_task(tick_loop(svc, interval_s=0.01, stop_event=stop))
    for _ in range(500):
        if fixer.calls:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await task
    assert fixer.calls
