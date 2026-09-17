"""Tests voor de FastMCP stdio-server tools."""
import json
import os
import subprocess
import sys

from gh_workflow_fix.db import Database
from gh_workflow_fix.mcp import create_mcp
from gh_workflow_fix.models import Chain, ChainState, utcnow_iso


def test_stdio_entrypoint_handshake(tmp_path):
    """`python -m gh_workflow_fix.mcp` moet een werkende stdio-server starten."""
    env = dict(os.environ)
    env.update(
        {
            "GH_TOKEN": "t",
            "GH_REPO": "acme/app",
            "GH_OC_AUTO": "true",
            "WEBHOOK_SECRET": "s3cret",
            "DATA_DIR": str(tmp_path),
            "FASTMCP_SHOW_SERVER_BANNER": "false",
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "gh_workflow_fix.mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    }
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    try:
        assert line, "geen init-response op stdout — start -m gh_workflow_fix.mcp geen server?"
        msg = json.loads(line)
        assert msg.get("id") == 1
        assert msg["result"]["serverInfo"]["name"] == "gh-workflow-fix"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _mk_chain(**overrides):
    defaults = {
        "repo": "acme/app",
        "workflow_path": ".github/workflows/ci.yml",
        "workflow_name": "ci",
        "head_branch": "main",
        "run_id": 100,
        "attempt": 1,
        "state": ChainState.RUNNING.value,
        "next_retry_at": utcnow_iso(),
    }
    defaults.update(overrides)
    return Chain(**defaults)


def _result_text(call_result):
    """Extract parsed JSON from a FastMCP ToolResult."""
    if call_result.content:
        return json.loads(call_result.content[0].text)
    # Empty list returns produce no content; structured_content wraps it.
    return call_result.structured_content["result"]


# -- list_chains ---------------------------------------------------------------


async def test_list_chains_empty(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("list_chains", {})
    data = _result_text(result)
    assert data == []


async def test_list_chains_with_data(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain())
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("list_chains", {})
    data = _result_text(result)
    assert len(data) == 1
    assert data[0]["id"] == c.id
    assert data[0]["workflow_path"] == ".github/workflows/ci.yml"
    assert data[0]["state"] == ChainState.RUNNING.value


async def test_list_chains_with_state_filter(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    db.insert_chain(_mk_chain())
    done = _mk_chain(workflow_path=".github/workflows/other.yml")
    done.state = ChainState.DONE.value
    db.insert_chain(done)
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("list_chains", {"state": "running"})
    data = _result_text(result)
    assert len(data) == 1
    assert data[0]["state"] == "running"


# -- get_chain -----------------------------------------------------------------


async def test_get_chain_found(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain())
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("get_chain", {"chain_id": c.id})
    data = _result_text(result)
    assert data["id"] == c.id
    assert data["repo"] == "acme/app"
    assert "created_at" in data
    assert "updated_at" in data


async def test_get_chain_not_found(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("get_chain", {"chain_id": 999})
    data = _result_text(result)
    assert data == {"error": "not_found"}


# -- retry_now -----------------------------------------------------------------


async def test_retry_now(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain(next_retry_at="2999-01-01T00:00:00+00:00"))
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("retry_now", {"chain_id": c.id})
    data = _result_text(result)
    assert data == {"ok": True}
    fresh = db.get_chain(c.id)
    # next_retry_at should be updated to approximately now (not 2999 anymore)
    assert fresh.next_retry_at is not None
    assert "2999" not in fresh.next_retry_at


async def test_retry_now_not_found(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("retry_now", {"chain_id": 999})
    data = _result_text(result)
    assert data == {"ok": False, "reason": "not_found"}


# -- pause_chain ---------------------------------------------------------------


async def test_pause_chain_running(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain(state=ChainState.RUNNING.value))
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("pause_chain", {"chain_id": c.id})
    data = _result_text(result)
    assert data == {"ok": True}
    assert db.get_chain(c.id).state == ChainState.PAUSED.value


async def test_pause_chain_waiting(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain(state=ChainState.WAITING.value))
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("pause_chain", {"chain_id": c.id})
    data = _result_text(result)
    assert data == {"ok": True}
    assert db.get_chain(c.id).state == ChainState.PAUSED.value


async def test_pause_chain_fix_error(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain(state=ChainState.FIX_ERROR.value))
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("pause_chain", {"chain_id": c.id})
    data = _result_text(result)
    assert data == {"ok": True}
    assert db.get_chain(c.id).state == ChainState.PAUSED.value


async def test_pause_chain_invalid_state(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain(state=ChainState.DONE.value))
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("pause_chain", {"chain_id": c.id})
    data = _result_text(result)
    assert data["ok"] is False
    assert "cannot pause" in data["reason"]
    assert db.get_chain(c.id).state == ChainState.DONE.value


async def test_pause_chain_not_found(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("pause_chain", {"chain_id": 999})
    data = _result_text(result)
    assert data == {"ok": False, "reason": "not_found"}


# -- resume_chain --------------------------------------------------------------


async def test_resume_chain(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain(state=ChainState.PAUSED.value))
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("resume_chain", {"chain_id": c.id})
    data = _result_text(result)
    assert data == {"ok": True}
    assert db.get_chain(c.id).state == ChainState.RUNNING.value


async def test_resume_chain_invalid_state(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain(state=ChainState.RUNNING.value))
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("resume_chain", {"chain_id": c.id})
    data = _result_text(result)
    assert data["ok"] is False
    assert "cannot resume" in data["reason"]


async def test_resume_chain_not_found(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("resume_chain", {"chain_id": 999})
    data = _result_text(result)
    assert data == {"ok": False, "reason": "not_found"}


# -- create_issue_now ----------------------------------------------------------


async def test_create_issue_now_no_github_client(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    c = db.insert_chain(_mk_chain())
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("create_issue_now", {"chain_id": c.id})
    data = _result_text(result)
    assert data == {"ok": False, "reason": "no_github_client"}


async def test_create_issue_now_not_found(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("create_issue_now", {"chain_id": 999})
    data = _result_text(result)
    assert data == {"ok": False, "reason": "not_found"}


# -- set_config ----------------------------------------------------------------


async def test_set_config_gh_oc_auto_true(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("set_config", {"key": "gh_oc_auto", "value": "true"})
    data = _result_text(result)
    assert data == {"ok": True}
    assert db.get_override("gh_oc_auto") == "true"


async def test_set_config_gh_oc_auto_false(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("set_config", {"key": "gh_oc_auto", "value": "false"})
    data = _result_text(result)
    assert data == {"ok": True}
    assert db.get_override("gh_oc_auto") == "false"


async def test_set_config_gh_oc_auto_zero_one(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    for val in ("0", "1"):
        result = await mcp.call_tool("set_config", {"key": "gh_oc_auto", "value": val})
        data = _result_text(result)
        assert data == {"ok": True}
        assert db.get_override("gh_oc_auto") == val


async def test_set_config_gh_oc_auto_invalid(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("set_config", {"key": "gh_oc_auto", "value": "maybe"})
    data = _result_text(result)
    assert data == {"ok": False, "reason": "invalid_value"}


async def test_set_config_retry_delays_valid(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool(
        "set_config", {"key": "retry_delays_min", "value": "5,10,20"}
    )
    data = _result_text(result)
    assert data == {"ok": True}
    assert db.get_override("retry_delays_min") == "5,10,20"


async def test_set_config_retry_delays_single(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool(
        "set_config", {"key": "retry_delays_min", "value": "30"}
    )
    data = _result_text(result)
    assert data == {"ok": True}
    assert db.get_override("retry_delays_min") == "30"


async def test_set_config_retry_delays_invalid_non_int(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool(
        "set_config", {"key": "retry_delays_min", "value": "abc"}
    )
    data = _result_text(result)
    assert data == {"ok": False, "reason": "invalid_value"}


async def test_set_config_retry_delays_empty(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool(
        "set_config", {"key": "retry_delays_min", "value": ""}
    )
    data = _result_text(result)
    assert data == {"ok": False, "reason": "invalid_value"}


async def test_set_config_invalid_key(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool(
        "set_config", {"key": "unknown_key", "value": "123"}
    )
    data = _result_text(result)
    assert data == {"ok": False, "reason": "invalid_key"}


# -- health --------------------------------------------------------------------


async def test_health_empty(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("health", {})
    data = _result_text(result)
    assert data["ok"] is True
    assert data["db"] == str(tmp_path / "state.db")
    assert data["tick"] == 60
    assert data["latest_event"] is None
    assert data["chains"] == {}


async def test_health_with_chains(tmp_path, cfg):
    db = Database(tmp_path / "state.db")
    db.insert_chain(_mk_chain())
    done = _mk_chain(workflow_path=".github/workflows/other.yml")
    done.state = ChainState.DONE.value
    db.insert_chain(done)
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("health", {})
    data = _result_text(result)
    assert data["ok"] is True
    assert data["chains"] == {"running": 1, "done": 1}
    assert data["tick"] == cfg.scheduler_tick_s


async def test_health_with_event(tmp_path, cfg):
    from gh_workflow_fix.models import Event

    db = Database(tmp_path / "state.db")
    db.insert_event(
        Event(
            delivery_id="d1",
            run_id=10,
            workflow_path=".github/workflows/ci.yml",
            head_branch="main",
            head_sha="abc",
            action="completed",
            conclusion="failure",
            handled="new_chain",
        )
    )
    mcp = create_mcp(db=db, cfg=cfg)
    result = await mcp.call_tool("health", {})
    data = _result_text(result)
    assert data["ok"] is True
    assert data["latest_event"] is not None
