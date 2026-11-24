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
