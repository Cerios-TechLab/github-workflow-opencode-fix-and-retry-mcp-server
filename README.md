# GitHub Workflow Fix-and-Retry MCP Server

Monitor failing GitHub Actions workflows, auto-fix them with [OpenCode](https://opencode.ai),
and retry the run — driven by a `workflow_run` webhook, coordinated through an MCP server.

When a workflow that opts in via a `# self-heal: true` marker in its YAML fails, a
"fix-and-retry chain" is started: wait for the configured retry delays, run OpenCode
on a worktree of the failing branch to produce a fix, push it, re-run the workflow,
and on repeated failure open a GitHub issue. The MCP server surfaces the chains
(`list_chains`, `get_chain`, `pause_chain`, `resume_chain`, `retry_now`,
`create_issue_now`, `set_config`, `health`) to any MCP client.

## Architecture

```
GitHub Actions (workflow_run event)
        │  POST /webhook/github  (HMAC-SHA256 verified)
        ▼
Starlette webhook daemon  ──►  Service (chain state machine)
        │                            │
        │                            ├─► db (SQLite: chains / retries / events)
        │                            ├─► GitHub client (open issue, rerun)
        │                            └─► OpenCodeRunner (clone → fix → push)
        ▼
MCP server (stdio)  ──►  chain control tools for AI clients
```

Two runnable entry points:

- **Daemon**: `python -m gh_workflow_fix` — Starlette webhook server on port `18080`
  (configurable) plus a scheduler tick that picks up due chains.
- **MCP server**: `python -m gh_workflow_fix.mcp` — stdio MCP server with the chain
  control tools.

## Installation

```bash
pip install gh-workflow-fix-mcp
```

Requires Python >= 3.11 and [OpenCode](https://opencode.ai) installed for the fix
runner (the binary path is set with `OPENCODE_BIN`; it must be able to push to the
repository of the failing workflow).

## Configuration

All settings come from environment variables. Only `GH_TOKEN`, `GH_REPO`,
`GH_OC_AUTO` and `WEBHOOK_SECRET` are required.

| Variable | Required | Description | Default |
|---|---|---|---|
| `GH_TOKEN` | yes | GitHub PAT with `repo` scope (rerun, issues, read workflow YAML) | — |
| `GH_REPO` | yes | Repository to monitor, `owner/repo` | — |
| `GH_OC_AUTO` | yes | Automatically run OpenCode fixes (`true`/`false`) | — |
| `WEBHOOK_SECRET` | yes | Secret for HMAC verification (same value as the GitHub webhook) | — |
| `SELF_HEAL_MARKER` | no | Marker comment that opts a workflow into self-healing | `# self-heal: true` |
| `RETRY_DELAYS_MIN` | no | Comma-separated delays (minutes) before attempts 1, 2, 3… | `15,30,60` |
| `DATA_DIR` | no | Where DB and worktrees live | `/var/lib/gh-workflow-fix` |
| `OPENCODE_BIN` | no | Path to the `opencode` binary | `opencode` (via `PATH`) |
| `OPENCODE_MODEL` | no | Model to pass to `opencode run --model` | unset |
| `GH_API_BASE` | no | GitHub API base URL (for GHES) | `https://api.github.com` |
| `WEBHOOK_HOST` / `WEBHOOK_PORT` | no | Bind address of the daemon | `0.0.0.0` / `18080` |
| `FIX_TIMEOUT_S` | no | Timeout for a single OpenCode fix run | `1800` |
| `SCHEDULER_TICK_S` | no | Scheduler tick interval in seconds | `60` |

## Opt-in marker

A workflow only becomes a fix candidate when its YAML contains the marker
(default `# self-heal: true`) as a comment. For example:

```yaml
name: CI
# self-heal: true
on:
  workflow_dispatch:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
```

## Webhook setup (GitHub)

1. Repository → **Settings → Webhooks → Add webhook**.
2. **Payload URL**: `https://your-host:18080/webhook/github`.
3. **Content type**: `application/json`.
4. **Events**: "Let me select individual events" → **Workflow runs** (`workflow_run`).
5. **Secret**: the same value as `WEBHOOK_SECRET`.
6. Save; GitHub sends a test ping which is ignored — only `workflow_run` events are
   processed.

Only failing runs of opt-in workflows create chains; successful runs are recorded
in the events table and ignored.

## Running the daemon

```bash
export GH_TOKEN=... GH_REPO=owner/repo GH_OC_AUTO=true WEBHOOK_SECRET=...
python -m gh_workflow_fix
```

The daemon exposes `POST /webhook/github` (HMAC verified) and `GET /healthz`
(`{"ok": true, "db": ..., "tick": ...}`).

## MCP client setup

Add to your MCP configuration, e.g. for OpenCode (`opencode.json`):

```json
{
  "mcp": {
    "gh-workflow-fix": {
      "type": "local",
      "command": ["python", "-m", "gh_workflow_fix.mcp"],
      "enabled": true
    }
  }
}
```

The MCP server reads the same environment as the daemon. Run it locally on the same
machine as the daemon (they share `DATA_DIR`/`state.db`).

### MCP tools

| Tool | Description |
|---|---|
| `list_chains` | List fix-and-retry chains, optionally filtered by state |
| `get_chain` | Get a single chain by ID |
| `retry_now` | Schedule a chain for immediate retry |
| `pause_chain` | Pause a chain (only if running, waiting, or fix_error) |
| `resume_chain` | Resume a paused chain |
| `create_issue_now` | Create a GitHub issue for a chain |
| `set_config` | Set a config override (`gh_oc_auto` or `retry_delays_min`) |
| `health` | Health check with DB status and chain counts |

## Chain states

`running` → fix attempt on a worktree → either `waiting` (fix pushed / rerun set,
waiting for the next run event) or `fix_error` (fix runner failed, next retry
scheduled) → after the retry delays are exhausted: `done` or `exhausted`.
`exhausted` chains have a GitHub issue opened.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## License

[MIT](LICENSE)