"""Deploy the MCPB bundle to Smithery with a complete serverCard.

==========================================================
MCPB bundle method — what works for publishing to Smithery
==========================================================

1) Pack locally with the official MCPB CLI:
       npx -y @anthropic-ai/mcpb pack <mcpb-dir> dist/server.mcpb

2) Manifest MUST be MCPB schema v0.4 and MUST NOT carry `inputSchema` or
   `input_schema` on tools — both are rejected as "Unrecognized key(s) in
   object" by the local `mcpb pack` validator. Tool input schemas are
   carried via the deploy payload's serverCard instead (see step 3).

3) Deploy via the Smithery REST API:
       PUT  /servers/{qualifiedName}             # create stub if 404
       PUT  /servers/{qualifiedName}/releases   # multipart upload

   Multipart parts:
     - payload (JSON):
         {
           "type": "stdio",
           "runtime": "python",
           "serverCard": {
             "serverInfo": { "name": <manifest.name>, "version": <manifest.version> },
             "description": <manifest.description>,
             "tools": [ {"name", "description", "inputSchema"} ... ],   # from server-card.json
             "resources": [],
             "prompts": []
           }
         }
     - bundle (file): the packed dist/server.mcpb

   Returns 202 + { deploymentId, status: "SUCCESS", mcpUrl:
   https://{name}--{ns}.run.tools }.

4) API requires an Authorization: Bearer <SMITHERY_API_KEY> header. On
   this box the key lives in /root/backups/.env (export it per-shell,
   e.g. `set -a; . /root/backups/.env; set +a`).

5) A User-Agent header is required (Cloudflare blocks default
   urllib/python-requests UA). This script sends a browser UA.

==========================================================
Usage:
    SMITHERY_API_KEY=... python scripts/smithery-deploy.py \
        [-n org/name] [-b server.mcpb]
==========================================================
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://api.smithery.ai"
REPO_ROOT = Path(__file__).resolve().parents[1]


def build_bundle(bundle_path: Path) -> None:
    """Pack mcpb/ into an MCPB bundle using the official MCPB CLI."""
    result = subprocess.run(
        ["npx", "-y", "@anthropic-ai/mcpb", "pack", str(REPO_ROOT / "mcpb"), str(bundle_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit("mcpb pack failed")
    print(result.stdout.strip().splitlines()[-8:])


def load_tools(card_path: Path) -> list[dict]:
    """Collect tool definitions (name, description, inputSchema) from
    server-card.json. Falls back to a generic empty schema when the card
    omits one."""
    card = json.loads(card_path.read_text(encoding="utf-8"))
    tools = []
    for tool in card["tools"]:
        schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
        tools.append(
            {"name": tool["name"], "description": tool["description"], "inputSchema": schema}
        )
    return tools


def build_payload(manifest: dict, tools: list[dict]) -> dict:
    return {
        "type": "stdio",
        "runtime": "python",
        "serverCard": {
            "serverInfo": {"name": manifest["name"], "version": manifest["version"]},
            "description": manifest.get("description", ""),
            "tools": tools,
            "resources": [],
            "prompts": [],
        },
    }


def api_request(method: str, url: str, api_key: str, body=None, headers=None) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        method=method,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


def ensure_server(api_key: str, qualified_name: str) -> None:
    status, _body = api_request("GET", f"{API_BASE}/servers/{qualified_name}", api_key)
    if status == 200:
        return
    if status != 404:
        raise SystemExit(f"Failed to check server ({status}): {_body}")
    status, body = api_request(
        "PUT", f"{API_BASE}/servers/{qualified_name}", api_key,
        body=b"{}", headers={"Content-Type": "application/json"},
    )
    if status not in (200, 201):
        raise SystemExit(f"Failed to create server ({status}): {body}")
    print(f"Created server {qualified_name}")


def deploy_release(api_key: str, qualified_name: str, bundle_path: Path, payload: dict) -> dict:
    boundary = "MCPB-Deploy-Boundary"
    fields = []
    files = []

    payload_bytes = json.dumps(payload).encode("utf-8")
    fields.append(
        (b'Content-Disposition: form-data; name="payload"\r\n\r\n', payload_bytes)
    )
    bundle_bytes = bundle_path.read_bytes()
    files.append(
        (
            b'Content-Disposition: form-data; name="bundle"; filename="server.mcpb"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n",
            bundle_bytes,
        )
    )

    parts = []
    for header, data in fields:
        parts.append(b"--" + boundary.encode() + b"\r\n" + header + data + b"\r\n")
    for header, data in files:
        parts.append(b"--" + boundary.encode() + b"\r\n" + header + data + b"\r\n")
    parts.append(b"--" + boundary.encode() + b"--\r\n")
    body = b"".join(parts)

    status, response = api_request(
        "PUT", f"{API_BASE}/servers/{qualified_name}/releases", api_key,
        body=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if status != 202:
        raise SystemExit(f"Deployment failed: {status} {response}")
    return json.loads(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--name", default="djsteavy/github-workflow-opencode-fix-and-retry-mcp-server")
    parser.add_argument("-b", "--bundle", default="dist/server.mcpb")
    args = parser.parse_args()

    api_key = os.environ.get("SMITHERY_API_KEY")
    if not api_key:
        raise SystemExit("SMITHERY_API_KEY not set")

    bundle_path = REPO_ROOT / args.bundle
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    build_bundle(bundle_path)

    manifest = json.loads(
        subprocess.run(
            ["unzip", "-p", str(bundle_path), "manifest.json"], capture_output=True, text=True, check=True
        ).stdout
    )
    tools = load_tools(REPO_ROOT / "server-card.json")
    payload = build_payload(manifest, tools)

    print(f"Publishing {args.name} (stdio) to Smithery Registry...")
    ensure_server(api_key, args.name)
    result = deploy_release(api_key, args.name, bundle_path, payload)
    print(f"Release {result.get('deploymentId')} accepted")
    if result.get("mcpUrl"):
        print(f"  MCP URL: {result['mcpUrl']}")
    print(f"  Server: https://smithery.ai/servers/{args.name}")


if __name__ == "__main__":
    main()