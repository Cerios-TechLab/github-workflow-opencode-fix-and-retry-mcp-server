# GitHub Workflow Fix daemon — ops-doc

Deze map bevat de uitrol-bestanden voor de **GitHub Workflow Fix daemon** op de
box: een systemd-dienst die failing GitHub Actions workflows monitort, de fout
laat fixen door OpenCode en de workflow automatisch opnieuw laat draaien.

| Bestand | Doel |
|---|---|
| `gh-workflow-fix-daemon.service` | systemd-unit (daemon op poort 18080) |
| `install.sh` | interactief install-script (kopieert project, venv, env-file, unit) |
| `README.md` | deze ops-doc |

## Overzicht dienst

- **Webhook-endpoint**: `POST /webhook/github` — ontvangt GitHub
  `workflow_run`-events, verifieert de HMAC-signature (`X-Hub-Signature-256`).
- **Fix-and-retry-keten**: bij een failing run met `# self-heal: true` in de
  workflow-YAML wordt een "chain" gestart: wachten (retry-delays), fix draaien
  via OpenCode (`opencode run`), workflow-rerun, en bij succes `done` / bij
  herhaald falen `exhausted` (met GitHub-issue).
- **Scheduler**: een tick-loop (default elke 60 s) pikt due chains op.
- **Health**: `GET /healthz` → `{"ok": true, "db": ..., "tick": ...}`.
- **MCP-server**: `python -m gh_workflow_fix.mcp` (stdio) voor keten-controle
  en health-tools (zie "MCP-registratie" hieronder).

## Secrets

| Variabele | Verplicht | Toelichting |
|---|---|---|
| `GH_TOKEN` | ja | GitHub PAT met `repo`-scope (workflow-rerun, issues, YAML lezen) |
| `WEBHOOK_SECRET` | ja | Geheim voor HMAC-verificatie; **zelfde** waarde als bij de GitHub-webhook |

Beide staan in `/etc/gh-workflow-fix.env` (rechten 600, alleen root leesbaar).
Deze file wordt door de systemd-unit via `EnvironmentFile=` ingelezen.

## Poort

De daemon luistert op **TCP 18080** (default; `WEBHOOK_HOST`/`WEBHOOK_PORT` in
de env-file). Van buiten de box bereikbaar maken:

```bash
firewall-cmd --permanent --add-port=18080/tcp && firewall-cmd --reload
```

## Webhook-configuratie (GitHub)

1. GitHub-repo → **Settings → Webhooks → Add webhook**.
2. **Payload URL**: `http://techlab5.mooo.com:18080/webhook/github`
3. **Content type**: `application/json`
4. **Events**: selecteer "Let me select individual events" → **Workflow runs**
   (event `workflow_run`).
5. **Secret**: dezelfde waarde als `WEBHOOK_SECRET` in `/etc/gh-workflow-fix.env`.
6. **Active**: aan. Sla op; GitHub stuurt een test-ping (die wordt genegeerd,
   alleen `workflow_run`-events worden verwerkt).

## MCP-registratie (opencode.json)

Voeg in `/root/.config/opencode/opencode.json` toe:

```json
{
  "mcp": {
    "gh-workflow-fix": {
      "command": "/root/gh-workflow-fix/.venv/bin/python",
      "args": ["-m", "gh_workflow_fix.mcp"],
      "env": { "GH_TOKEN": "...", "GH_REPO": "acme/app", "GH_OC_AUTO": "true", "WEBHOOK_SECRET": "..." }
    }
  }
}
```

Beschikbare tools: `list_chains`, `get_chain`, `retry_now`, `pause_chain`,
`resume_chain`, `create_issue_now`, `set_config`, `health`.

## Handmatige installatiestappen

Gebruik het interactieve script (aanbevolen):

```bash
sudo bash deploy/install.sh
```

Het script kopieert het project naar `/root/gh-workflow-fix`, maakt de venv
(`python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'`), vraagt alle
env-variabelen op, schrijft `/etc/gh-workflow-fix.env`, installeert de
systemd-unit en start de daemon (`systemctl enable --now`).

Handmatig (zonder script):

```bash
# 1. project + venv
mkdir -p /root/gh-workflow-fix
rsync -a --exclude '.git' --exclude '.venv' ./ /root/gh-workflow-fix/
cd /root/gh-workflow-fix
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'

# 2. env-file (zie Secrets hierboven)
#    Let op: GH_OC_AUTO=false wordt aangeraden voor eerste tests.

# 3. systemd
cp deploy/gh-workflow-fix-daemon.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gh-workflow-fix-daemon

# 4. config-check
curl -s localhost:18080/healthz
```

Beheer:

```bash
systemctl status gh-workflow-fix-daemon
journalctl -u gh-workflow-fix-daemon -f
systemctl restart gh-workflow-fix-daemon
```

## E2E test-workflow voorbeeld

Maak `.github/workflows/e2e-marker.yml` in de doel-repo en run hem handmatig:

```yaml
name: e2e-marker
on:
  workflow_dispatch:

jobs:
  fail:
    runs-on: ubuntu-latest
    steps:
      - run: exit 1
# self-heal: true
```

Verwachting: de webhook start een chain, na de eerste retry-delay draait de
fixer, de workflow wordt gererund en eindigt uiteindelijk in `done` (of
`exhausted` na alle retries). Controleer met:

```bash
curl -s localhost:18080/healthz
# en via de MCP-tools: list_chains
```

Verwijder de test-workflow daarna weer. De contract-smoke-test zonder echte
server staat in `tests/e2e_contract.py` (draait de app in-process via
`httpx.ASGITransport`).