import base64

import httpx

from gh_workflow_fix.github_client import GitHubAPI

BASE = "https://api.example.com"
YAML = "on:\n  push:\n# self-heal: true\n"
ENC = base64.b64encode(YAML.encode()).decode()


def _client(cfg, handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url=BASE)
    c = GitHubAPI(http=http, token=cfg.gh_token, repo=cfg.gh_repo,
                  data_dir=cfg.data_dir, marker=cfg.self_heal_marker,
                  gh_api_base=BASE)
    return c, http


async def test_workflow_yaml_decodes(cfg):
    def handler(request):
        assert request.headers["authorization"] == "Bearer t"
        assert request.url.path == "/repos/acme/app/contents/.github/workflows/ci.yml"
        assert request.url.params["ref"] == "sha1"
        return httpx.Response(200, json={"content": ENC, "encoding": "base64"})

    client, http = _client(cfg, handler)
    async with http:
        assert await client.workflow_yaml(".github/workflows/ci.yml", "sha1") == YAML


async def test_workflow_yaml_404_returns_none(cfg):
    def handler(request):
        return httpx.Response(404, json={"message": "Not Found"})

    client, http = _client(cfg, handler)
    async with http:
        assert await client.workflow_yaml(".github/workflows/ci.yml", "sha1") is None


async def test_open_issue(cfg):
    def handler(request):
        assert request.url.path == "/repos/acme/app/issues"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        body = request.read().decode()
        assert '"title"' in body and '"body"' in body
        return httpx.Response(201, json={"html_url": "https://github.com/acme/app/issues/1"})

    client, http = _client(cfg, handler)
    async with http:
        assert await client.open_issue("t", "b") == "https://github.com/acme/app/issues/1"


async def test_find_issue(cfg):
    def handler(request):
        assert request.url.path == "/repos/acme/app/issues"
        assert request.url.params["state"] == "open"
        items = [
            {"number": 2, "title": "anders"},
            {"number": 3, "title": "ghwf-fix t"},
        ]
        return httpx.Response(200, json=items)

    client, http = _client(cfg, handler)
    async with http:
        assert await client.find_issue("ghwf-fix t") == 3
        assert await client.find_issue("bestaat niet") is None


async def test_rerun_failed_jobs_accepts(cfg):
    def handler(request):
        assert request.url.path == "/repos/acme/app/actions/runs/10/rerun-failed-jobs"
        assert request.method == "POST"
        return httpx.Response(202, json={})

    client, http = _client(cfg, handler)
    async with http:
        assert await client.rerun_failed_jobs(10) is True


async def test_latest_sha(cfg):
    def handler(request):
        assert request.url.path == "/repos/acme/app/commits"
        assert request.url.params["sha"] == "main"
        return httpx.Response(200, json=[{"sha": "abc123"}])

    client, http = _client(cfg, handler)
    async with http:
        assert await client.latest_sha("main") == "abc123"