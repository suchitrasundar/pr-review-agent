"""Webhook server: GitHub sends pull_request events here; we review in the background."""
import hashlib
import hmac
import logging
import os

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

load_dotenv()

from agent import review_pull_request  # noqa: E402  (needs env loaded first)
from github_client import GitHubApp, GitHubRepo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("server")

WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
app_auth = GitHubApp(os.environ["GITHUB_APP_ID"], os.environ["GITHUB_PRIVATE_KEY"])
TRIGGER_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}

app = FastAPI(title="PR Review Agent")


def verify_signature(body: bytes, signature: str | None) -> None:
    if not signature:
        raise HTTPException(401, "Missing signature")
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Bad signature")


async def run_review(installation_id: int, owner: str, repo: str, number: int) -> None:
    gh = None
    try:
        token = await app_auth.installation_token(installation_id)
        gh = GitHubRepo(token, owner, repo)
        log.info("Reviewing %s/%s#%d", owner, repo, number)
        review = await review_pull_request(gh, number)
        await gh.create_review(number, **review)
        log.info("Posted review on %s/%s#%d (%d inline comments)", owner, repo, number, len(review["comments"]))
    except Exception:
        log.exception("Review failed for %s/%s#%d", owner, repo, number)
    finally:
        if gh:
            await gh.close()


@app.get("/")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    request: Request,
    background: BackgroundTasks,
    x_github_event: str = Header(None),
    x_hub_signature_256: str = Header(None),
):
    body = await request.body()
    verify_signature(body, x_hub_signature_256)
    payload = await request.json()

    if x_github_event == "ping":
        return {"msg": "pong"}
    if x_github_event != "pull_request":
        return {"msg": f"ignored event {x_github_event}"}

    action, pr = payload.get("action"), payload["pull_request"]
    if action not in TRIGGER_ACTIONS or pr.get("draft"):
        return {"msg": f"ignored action {action}"}

    owner, repo = payload["repository"]["full_name"].split("/")
    # Respond to GitHub immediately (it times out after 10s); review runs in background.
    background.add_task(run_review, payload["installation"]["id"], owner, repo, pr["number"])
    return {"msg": "review queued"}
