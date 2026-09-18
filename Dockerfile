# Glama-build spec: baseImage debian:trixie-slim, buildSteps ["uv sync"],
# cmdArguments ["mcp-proxy", "--", ".venv/bin/python", "-m", "gh_workflow_fix.mcp"]
FROM debian:trixie-slim

# uv installeren (Glama's runtime heeft dit al; hier expliciit voor eigen hosting).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --break-system-packages --no-cache-dir uv

WORKDIR /app

COPY . /app/

# Build-step: uv sync installeert het package in /app/.venv
RUN uv sync

# Entrypoint: mcp-proxy exposeert de stdio-MCP-server over streamable HTTP.
# Glama's runtime voorziet mcp-proxy; hier geïnstalleerd voor eigen hosting.
CMD ["mcp-proxy", "--", ".venv/bin/python", "-m", "gh_workflow_fix.mcp"]