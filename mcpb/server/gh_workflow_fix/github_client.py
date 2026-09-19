"""Async GitHub REST client for workflow content, issues and rerun."""
from __future__ import annotations

import base64
from pathlib import Path

import httpx


class GitHubAPI:
    def __init__(self, *, http: httpx.AsyncClient, token: str, repo: str,
                 data_dir: Path, marker: str, gh_api_base: str):
        self._http = http
        self.repo = repo
        self.marker = marker
        self._api = gh_api_base
        self._token = token
        self._workflows_dir = data_dir / "workflows"
        self._workflows_dir.mkdir(parents=True, exist_ok=True)

    def _repo_url(self, path: str) -> str:
        return f"{self._api}/repos/{self.repo}/{path}"

    def _auth(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._token}"}

    async def workflow_yaml(self, path: str, ref: str) -> str | None:
        r = await self._http.get(
            self._repo_url(f"contents/{path}"),
            params={"ref": ref},
            headers=self._auth(),
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        safe_name = path.rsplit("/", 1)[-1].replace(".", "_")
        (self._workflows_dir / f"{safe_name}.yml").write_text(content)
        return content

    async def open_issue(self, title: str, body: str) -> str | None:
        r = await self._http.post(
            self._repo_url("issues"),
            json={"title": title, "body": body},
            headers={**self._auth(), "X-GitHub-Api-Version": "2022-11-28"},
        )
        r.raise_for_status()
        return r.json().get("html_url")

    async def update_issue(self, issue_number: int, body: str) -> None:
        r = await self._http.patch(
            self._repo_url(f"issues/{issue_number}"),
            json={"body": body},
            headers={**self._auth(), "X-GitHub-Api-Version": "2022-11-28"},
        )
        r.raise_for_status()

    async def find_issue(self, title: str) -> int | None:
        r = await self._http.get(self._repo_url("issues"),
                                 params={"state": "open", "per_page": 50},
                                 headers={**self._auth(), "X-GitHub-Api-Version": "2022-11-28"})
        r.raise_for_status()
        for item in r.json():
            if item.get("title") == title:
                return int(item["number"])
        return None

    async def rerun_failed_jobs(self, run_id: int) -> bool:
        r = await self._http.post(self._repo_url(f"actions/runs/{run_id}/rerun-failed-jobs"),
                                  headers=self._auth())
        return r.status_code in (202, 204)

    async def latest_sha(self, branch: str) -> str | None:
        r = await self._http.get(self._repo_url("commits"),
                                 params={"sha": branch, "per_page": 1},
                                 headers=self._auth())
        r.raise_for_status()
        items = r.json()
        return items[0]["sha"] if items else None