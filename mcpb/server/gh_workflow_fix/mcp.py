"""FastMCP stdio server for chain control and health tools."""
from __future__ import annotations

from collections import Counter

from fastmcp import FastMCP

from gh_workflow_fix.config import Config, load_config
from gh_workflow_fix.db import Database
from gh_workflow_fix.models import Chain, ChainState, utcnow_iso
from gh_workflow_fix.service import Service


def _chain_to_dict(chain: Chain) -> dict:
    return {
        "id": chain.id,
        "repo": chain.repo,
        "workflow_path": chain.workflow_path,
        "workflow_name": chain.workflow_name,
        "head_branch": chain.head_branch,
        "run_id": chain.run_id,
        "attempt": chain.attempt,
        "state": chain.state,
        "next_retry_at": chain.next_retry_at,
        "issue_url": chain.issue_url,
        "last_error": chain.last_error,
        "created_at": chain.created_at,
        "updated_at": chain.updated_at,
    }


def create_mcp(db: Database, cfg: Config) -> FastMCP:
    mcp = FastMCP(
        name="gh-workflow-fix",
        instructions="Monitor and auto-fix failing GitHub Actions workflows",
    )

    service = Service(db, github=None, fixer=None, cfg=cfg)

    @mcp.tool()
    async def list_chains(state: str | None = None) -> list[dict]:
        """List fix-and-retry chains, optionally filtered by state."""
        chains = db.list_chains(state=state)
        return [_chain_to_dict(c) for c in chains]

    @mcp.tool()
    async def get_chain(chain_id: int) -> dict:
        """Get a single chain by ID, or error if not found."""
        chain = db.get_chain(chain_id)
        if chain is None:
            return {"error": "not_found"}
        return _chain_to_dict(chain)

    @mcp.tool()
    async def retry_now(chain_id: int) -> dict:
        """Schedule a chain for immediate retry."""
        chain = db.get_chain(chain_id)
        if chain is None:
            return {"ok": False, "reason": "not_found"}
        now = utcnow_iso()
        chain.next_retry_at = now
        chain.updated_at = now
        db.update_chain(chain)
        return {"ok": True}

    @mcp.tool()
    async def pause_chain(chain_id: int) -> dict:
        """Pause a chain (only if running, waiting, or fix_error)."""
        chain = db.get_chain(chain_id)
        if chain is None:
            return {"ok": False, "reason": "not_found"}
        pauseable = {
            ChainState.RUNNING.value,
            ChainState.WAITING.value,
            ChainState.FIX_ERROR.value,
        }
        if chain.state not in pauseable:
            return {"ok": False, "reason": f"cannot pause chain in state {chain.state}"}
        chain.state = ChainState.PAUSED.value
        chain.updated_at = utcnow_iso()
        db.update_chain(chain)
        return {"ok": True}

    @mcp.tool()
    async def resume_chain(chain_id: int) -> dict:
        """Resume a paused chain."""
        chain = db.get_chain(chain_id)
        if chain is None:
            return {"ok": False, "reason": "not_found"}
        if chain.state != ChainState.PAUSED.value:
            return {"ok": False, "reason": f"cannot resume chain in state {chain.state}"}
        chain.state = ChainState.RUNNING.value
        chain.updated_at = utcnow_iso()
        db.update_chain(chain)
        return {"ok": True}

    @mcp.tool()
    async def create_issue_now(chain_id: int) -> dict:
        """Create a GitHub issue for a chain (requires GitHub client)."""
        chain = db.get_chain(chain_id)
        if chain is None:
            return {"ok": False, "reason": "not_found"}
        try:
            await service._maybe_create_issue(chain)
            return {"ok": True, "issue_url": chain.issue_url}
        except AttributeError:
            return {"ok": False, "reason": "no_github_client"}

    @mcp.tool()
    async def set_config(key: str, value: str) -> dict:
        """Set a configuration override (gh_oc_auto or retry_delays_min)."""
        if key == "gh_oc_auto":
            if value.strip().lower() not in ("true", "false", "0", "1"):
                return {"ok": False, "reason": "invalid_value"}
            db.set_override("gh_oc_auto", value)
            return {"ok": True}
        if key == "retry_delays_min":
            parts = [p.strip() for p in value.split(",") if p.strip()]
            try:
                delays = [int(p) for p in parts]
            except ValueError:
                return {"ok": False, "reason": "invalid_value"}
            if len(delays) < 1:
                return {"ok": False, "reason": "invalid_value"}
            db.set_override("retry_delays_min", value)
            return {"ok": True}
        return {"ok": False, "reason": "invalid_key"}

    @mcp.tool()
    async def health() -> dict:
        """Health check with DB status and chain counts."""
        chains = db.list_chains()
        state_counts = dict(Counter(c.state for c in chains))
        return {
            "ok": True,
            "db": str(db.path),
            "tick": cfg.scheduler_tick_s,
            "latest_event": db.latest_event_time(),
            "chains": state_counts,
        }

    return mcp


def main() -> None:
    """Start the MCP server over stdio (``python -m gh_workflow_fix.mcp``)."""
    cfg = load_config()
    db = Database(cfg.data_dir / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    mcp.run()


if __name__ == "__main__":
    main()
