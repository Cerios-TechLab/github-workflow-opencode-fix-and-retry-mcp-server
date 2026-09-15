# Design: GitHub workflow fix-and-retry MCP server

**Datum:** 2026-09-15
**Status:** goedgekeurd
**Repo:** `Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server`

## Doel

Een MCP-server voor de Techlab-demo-server die gefaalde GitHub Actions
workflows van één repository volgt en gemarkeerde workflows tot 3 keer
probeert te fixen. Elke poging voert een lokale OpenCode-fix-agent uit
(analyseer + fix + push naar `main`), gevolgd door een rerun. Tussen de
pogingen geldt een oplopende wachttijd: 15, 30 en 60 minuten na elke
voorgaande poging.

Afhankelijk van `GH_OC_AUTO` wordt een faal geboekt als issue in `GH_REPO`:
meteen bij de eerste detectie (`false`) of pas nadat de derde poging faalde
(`true`).

## Aanpak

Zelfstandig pakket (aanpak A): eigen code voor daemon, fix-runner,
MCP-server en SQLite-staat. Geen hergebruik van `/root/opencode-fix`
(behalve de pattern van `opencode run --auto`); volgt de structuur van
`quality-coach-mcp` (fastmcp + pyproject/hatchling + pytest).

## Architectuur

Eén Python-pakket `gh_workflow_fix` met drie entry-points die één module-set
en één SQLite-DB delen:

| Component | Rol |
|---|---|
| **daemon** (`serve.py`) | Starlette-app, luistert op `0.0.0.0:18080`. Route `POST /webhook/github` ontvangt `workflow_run`-events, verifieert `X-Hub-Signature-256` (HMAC/SHA-256 met `WEBHOOK_SECRET`), schrijft gewogen events weg. Draait daarnaast de asyncio **scheduler-loop** die vervallen `next_retry_at`-deadlines ophaalt en de fix-runner start. |
| **fix-runner** (`fixer.py`) | Voert per retry uit: shallow `git clone` (SSH) van `GH_REPO` naar een werk-map in `DATA_DIR/work/`; genereert een briefing (run-URL, workflow-naam, sha, poging); draait `opencode run --auto --title "ghwf-fix-<chain_id>-<attempt>"`; leest exit-code en resultaat. Als de branch-sha na afloop niet is gewijzigd, triggert hij `POST /repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs`. |
| **MCP-server** (`mcp.py`) | FastMCP (stdio). Tools voor controle en inzicht over de gedeelde staat. |
| **SQLite** (`state.db` in `DATA_DIR`) | Tabellen `chains`, `retries`, `events`. Source of truth; deadlines worden herberekend na restart. |

## Dataflow (één fix-and-retry cyclus)

1. GitHub stuurt `workflow_run`-event met `action=completed`,
   `workflow_run.conclusion=failure` → daemon.
   Uit het payload komen `workflow_run.path` (workflow-bestand), `.name`,
   `.head_branch`, `.head_sha`, `.id`, `repository.full_name`.
2. Daemon haalt het workflow-YAML op (`GET /repos/{owner}/{repo}/contents/<path>`
   met ref=`head_sha`; `@branch`-suffix van `path` strippen) en checkt de
   marker `# self-heal: true`. Niet gemarkeerd of niet ophaalbaar (behalve
   loggen met `marker_check=error`) → geen keten.
3. Koppeling: chain-key is `(workflow_path, head_branch)`, hooguit één
   actieve keten per combinatie. Als er een keten in `running`/`waiting`/
   `fix_error` staat voor die combinatie, dan is dit nieuwe failing event
   een **voortzetting** → attempt verhogen. Anders een nieuwe keten
   (`attempt=0`, `state=running`).
4. `GH_OC_AUTO=false` → **meteen** issue aanmaken in `GH_REPO` (titels en
   body met run-link, zie issues sectie).
5. Retry #1 plannen op `next_retry_at = now + 15 min`.
6. Scheduler-deadline bereikt → fix-runner starten; bij starten `attempt=1`.
   Uitkomst: nieuwe commit gepusht (workflow triggert vanzelf) of
   `rerun-failed-jobs` aangeroepen. `state=waiting`.
7. Volgend `completed`-event voor dezelfde workflow+branch:
   - `conclusion=success` → keten klaar, `state=done`.
   - `conclusion=failure` → afhankelijk van attempt:
     - attempt 1 faalt → retry #2 op `+30 min` na dit event.
     - attempt 2 faalt → retry #3 op `+60 min` na dit event.
     - attempt 3 faalt (drie keer geprobeerd) → `state=exhausted`;
       bij `GH_OC_AUTO=true` nu pas het issue aanmaken.

Wachttijden worden gemeten vanaf het failing `completed`-event van de
voorgaande poging en zijn overschrijfbaar via `RETRY_DELAYS_MIN` (default
`15,30,60`).

## Configuratie (env vars)

| Var | Verplicht | Default | Beschrijving |
|---|---|---|---|
| `GH_TOKEN` | ja | — | GitHub PAT (`repo`-scope) voor `GH_REPO` |
| `GH_REPO` | ja | — | `owner/repo` dat wordt gemonitord (één repo, demo-scope) |
| `GH_OC_AUTO` | ja | — | `true`/`false`; logt issue direct of pas na 3e falen |
| `WEBHOOK_SECRET` | ja | — | HMAC-secret; ook nodig bij webhook-configuratie |
| `SELF_HEAL_MARKER` | nee | `# self-heal: true` | comment-markettekst in het workflow-YAML |
| `RETRY_DELAYS_MIN` | nee | `15,30,60` | wachttijden per poging (minuten, aflopend) |
| `DATA_DIR` | nee | `/var/lib/gh-workflow-fix` | SQLite + werk-maps |
| `OPENCODE_BIN` | nee | `/root/.opencode/bin/opencode` | pad naar opencode-binary |
| `WEBHOOK_HOST` / `WEBHOOK_PORT` | nee | `0.0.0.0` / `18080` | bind-adres daemon |

## Marker

Overal in `.github/workflows/*.yml` een regel zoals:

```yaml
# self-heal: true
```

Alleen workflows mét deze marker krijgen een fix-and-retry keten.

## MCP-tools

- `list_chains(state=None)` — ketens (+ attempt, state, timestamps, issue-URL)
- `get_chain(chain_id)` — detail incl. retries en events
- `retry_now(chain_id)` — wachttijd overslaan, fix nu starten
- `pause_chain(chain_id)` / `resume_chain(chain_id)`
- `create_issue_now(chain_id)` — issue forceren ongeacht `GH_OC_AUTO`
- `set_config(gh_oc_auto=None, retry_delays_min=None)` — runtime-overschrijven
  (persistent in DB)
- `health()` — token-validatie, laatste webhook-delivery, scheduler-lag

## Issues (als nieuw issue in GH_REPO)

Een nieuw issue in de issues-tab van `GH_REPO` (geen kanban-project).
Titel/body worden gegenereerd uit de chain (repo, workflow, run-URL, poging,
timestamps). Voor `GH_OC_AUTO=false` ontstaat het issue bij `attempt=0` en
wordt de keten voortgezet; na een succes wordt het issue eventueel bijgewerkt
met een slot-commentaar (`closed` blíjft aan de gebruiker).

## Foutafhandeling / edge cases

- **HMAC mismatch** → 401, geen state-wijziging.
- **Non-relevant events** (`requested`, `in_progress`, `success` zonder
  actieve keten) → ack, niks opslaan (behalve `events`-log bij failure).
- **YAML niet ophaalbaar** → behandeld als niet-gemarkeerd; wel loggen met
  `marker_check=error`.
- **Fix-runner faalt** (clone-fout, opencode non-zero) → attempt telt mee,
  `state=fix_error`, volgende retry volgens schema; na 3× `fix_error` →
  `exhausted` (issue bij `GH_OC_AUTO=true`).
- **GitHub 4xx/rate-limit** → backoff binnen de deadline (0/1/2/5 min),
  daarna de retry overslaan en doorplannen.
- **Daemon-restart** → deadlines uit `next_retry_at` in SQLite (source of
  truth), geen verloren werk.
- **Eén actieve fix tegelijk** over de hele server (DB-lock), zoals de
  bestaande opencode-fix-conventie; andere ketens wachten.

## Testen & kwaliteit

- `pytest` + `ruff`; tests in het pakket met een gefakete GitHub-client en
  gefakete opencode-runner (geen netwerk/opencode-aanroepen).
- Smoke-test: daemon starten, lokaal nep-webhook POSTEN met geldige HMAC;
  niet-gemarkeerde run doet niks, gemarkeerde run start een keten.
- Geschreven per kwaliteitspoort van de box (zie `/AGENTS.md`): CLI-verificatie,
  geen LSP-claims.

## Uitlevering op de Techlab-box

1. Code pushen naar `main` van de repo (SSH als `Steavy`).
2. `.venv` aanmaken; systemd-unit `gh-workflow-fix-daemon.service`;
   MCP-server registreren in de opencode-config.
3. Poort `18080/tcp` openen in firewalld →
   `http://techlab5.mooo.com:18080/webhook/github` configureren als
   repository-webhook met event `workflow_run`, secret=`WEBHOOK_SECRET`,
   content-type `application/json`.
4. End-to-end verificatie met een marker-test-workflow (bewust faillende job)
   in één van de test-repo's; controleren dat 1e poging via opencode een fix
   produceert en de cyclus doorloopt.

## Scope-beperkingen

- Eén `GH_REPO` per server; wisselen wordt niet ondersteund (demo-scope).
- Fixes worden geleverd door de lokale opencode-agent (tip: gebruik briefings
  uit bestaande `/root/opencode-fix`-conventie); de server regelt clone, state,
  scheduling en het issue-loggen.