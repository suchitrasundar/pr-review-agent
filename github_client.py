"""Minimal async GitHub client: GitHub App auth + the few PR endpoints the agent needs."""
import re
import time

import httpx
import jwt

API = "https://api.github.com"


class GitHubApp:
    """Authenticates as a GitHub App and mints short-lived installation tokens."""

    def __init__(self, app_id: str, private_key: str):
        self.app_id = app_id
        # Hosting dashboards often store multi-line keys with literal "\n".
        self.private_key = private_key.replace("\\n", "\n")

    def _app_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 540, "iss": self.app_id}
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def installation_token(self, installation_id: int) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{API}/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {self._app_jwt()}",
                    "Accept": "application/vnd.github+json",
                },
            )
            r.raise_for_status()
            return r.json()["token"]


class GitHubRepo:
    """Calls against one repository, using any token (installation token or PAT)."""

    def __init__(self, token: str, owner: str, repo: str):
        self.owner, self.repo = owner, repo
        self.client = httpx.AsyncClient(
            base_url=f"{API}/repos/{owner}/{repo}",
            timeout=60,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def close(self):
        await self.client.aclose()

    async def get_pr(self, number: int) -> dict:
        r = await self.client.get(f"/pulls/{number}")
        r.raise_for_status()
        return r.json()

    async def get_pr_files(self, number: int) -> list[dict]:
        files, page = [], 1
        while True:
            r = await self.client.get(
                f"/pulls/{number}/files", params={"per_page": 100, "page": page}
            )
            r.raise_for_status()
            batch = r.json()
            files.extend(batch)
            if len(batch) < 100 or page >= 30:  # GitHub caps at 3000 files
                return files
            page += 1

    async def get_file(self, path: str, ref: str) -> str | None:
        r = await self.client.get(
            f"/contents/{path}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text

    async def create_review(
        self, number: int, commit_id: str, body: str, comments: list[dict], event: str = "COMMENT"
    ) -> dict:
        r = await self.client.post(
            f"/pulls/{number}/reviews",
            json={"commit_id": commit_id, "body": body, "event": event, "comments": comments},
        )
        r.raise_for_status()
        return r.json()
