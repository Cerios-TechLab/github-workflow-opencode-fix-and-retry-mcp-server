"""GitHub Workflow Fix-and-Retry MCP Server — MCPB entry point.

This is a self-contained entry point for the MCPB bundle (mcpb/server/).
It mirrors `python -m gh_workflow_fix.mcp` but runs from the bundled
directory so Smithery can execute it without the package being installed.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the bundled src/ is on the path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gh_workflow_fix.config import load_config
from gh_workflow_fix.db import Database
from gh_workflow_fix.mcp import create_mcp


def main() -> None:
    cfg = load_config()
    db = Database(cfg.data_dir / "state.db")
    mcp = create_mcp(db=db, cfg=cfg)
    mcp.run()


if __name__ == "__main__":
    main()