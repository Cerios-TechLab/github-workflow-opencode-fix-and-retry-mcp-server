"""MCP smoke test: start the server on stdio and introspect tools/list.

Usage:
    python scripts/mcp-smoke.py [expected_tool_count]

Exits 0 only if the server answers an initialize + tools/list round-trip.

The gh-workflow-fix MCP server calls ``load_config()`` at startup, which
requires GH_TOKEN, GH_REPO, GH_OC_AUTO and WEBHOOK_SECRET. When those are not
present in the environment, harmless dummy values are injected so the smoke
test exercises the protocol layer without needing real credentials.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_DUMMY_ENV = {
    "GH_TOKEN": "smoke-token",
    "GH_REPO": "acme/app",
    "GH_OC_AUTO": "true",
    "WEBHOOK_SECRET": "smoke-secret",
}


def _send(writer, obj):
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    writer(payload + b"\n")


def main() -> int:
    expected = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    script_dir = Path(__file__).resolve().parent.parent

    env = {**os.environ, "PYTHONPATH": str(script_dir)}
    for key, value in _DUMMY_ENV.items():
        env.setdefault(key, value)
    env.setdefault("DATA_DIR", "/tmp/gh-workflow-fix-smoke")

    proc = subprocess.Popen(
        [sys.executable, "-m", "gh_workflow_fix.mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    responses = []
    lock = threading.Lock()

    def _reader():
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("id") is not None:
                with lock:
                    responses.append(msg)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()

    def _write(text):
        proc.stdin.write(text if isinstance(text, bytes) else text.encode("utf-8"))
        proc.stdin.write(b"\n")
        proc.stdin.flush()

    try:
        _send(_write, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-smoke", "version": "0.1.0"},
            },
        })
        _send(_write, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(_write, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

        deadline = 15.0
        start = time.monotonic()
        while time.monotonic() - start < deadline:
            with lock:
                done = {r.get("id") for r in responses}
            if 1 in done and 2 in done:
                break
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        else:
            print("timeout waiting for initialize + tools/list")
            return 1

        with lock:
            by_id = {r["id"]: r for r in responses}

        init = by_id.get(1)
        tools = by_id.get(2)
        if init is None or "error" in init or tools is None or "error" in tools:
            print("MCP error:", by_id)
            if proc.poll() is not None:
                print("stderr:", proc.stderr.read().decode("utf-8", "replace"))
            return 1

        names = sorted(t["name"] for t in tools.get("result", {}).get("tools", []))
        print(f"tools/list returned {len(names)} tools")
        for name in names:
            print(f"  {name}")

        if len(names) != expected:
            print(f"expected {expected} tools, got {len(names)}")
            return 1
        print("smoke test passed")
        return 0
    finally:
        proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except (TimeoutError, subprocess.TimeoutExpired):
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
