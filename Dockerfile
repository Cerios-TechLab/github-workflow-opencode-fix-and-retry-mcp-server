FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --break-system-packages uv

COPY . /app
WORKDIR /app
RUN uv sync

CMD [".venv/bin/python", "-m", "gh_workflow_fix.mcp"]