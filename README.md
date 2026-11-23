# 🤖 PR Review Agent

A GitHub App that reviews every pull request with Claude. It reads the diff, pulls in
full files when it needs context, and posts a review with inline comments
(🔴 critical / 🟠 warning / 💡 suggestion).

```
GitHub PR opened/updated ──webhook──▶ FastAPI /webhook ──▶ Agent loop (Claude)
                                                            ├─ read_file(path)
                                                            └─ submit_review(...)
                                       ◀── review + inline comments posted to PR
```

| File | What it does |
|---|---|
| `main.py` | Webhook server: verifies GitHub signature, queues reviews |
| `agent.py` | Prompt, tools, agent loop, converts output to a GitHub review |
| `github_client.py` | GitHub App auth + PR API calls + diff line-numbering |
| `cli.py` | Run a review on any PR from your terminal (for testing) |

---

## Step 1 — Run it locally on a real PR (5 min)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

In `.env`, set:
- `ANTHROPIC_API_KEY`: from https://console.anthropic.com
- `GITHUB_TOKEN`: GitHub → Settings → Developer settings → Fine-grained tokens →
  give it access to a repo with **Pull requests: Read & write** and **Contents: Read**.

```bash
python cli.py your-name/your-repo#12          # prints the review (dry run)
python cli.py your-name/your-repo#12 --post   # posts it to the PR
```

Tweak `SYSTEM_PROMPT` in `agent.py` until you like the reviews.

## Step 2 — Push to GitHub

```bash
git init && git add . && git commit -m "PR review agent"
git branch -M main
git remote add origin https://github.com/your-name/pr-review-agent.git
git push -u origin main
```
(`.env` and `*.pem` are git-ignored, so keep it that way.)

## Step 3 — Deploy to Render

1. https://render.com → **New → Blueprint** → pick your repo (it reads `render.yaml`).
   *Or* **New → Web Service** with build `pip install -r requirements.txt` and start
   `uvicorn main:app --host 0.0.0.0 --port $PORT`.
2. Add environment variables: `ANTHROPIC_API_KEY` for now. You'll add the GitHub ones in Step 4.
3. Deploy, then copy your URL, e.g. `https://pr-review-agent.onrender.com`.
   Visiting it should show `{"status":"ok"}` once all env vars are set.

> Use a paid instance (or another always-on host). Free instances sleep, and GitHub
> gives up on a webhook after 10 seconds. Railway, Fly.io and Cloud Run work too:
> use the `Dockerfile`.

## Step 4 — Create the GitHub App

GitHub → Settings → Developer settings → **GitHub Apps → New GitHub App**

| Field | Value |
|---|---|
| Name | anything, e.g. `my-ai-reviewer` |
| Homepage URL | your Render URL |
| Webhook URL | `https://YOUR-APP.onrender.com/webhook` |
| Webhook secret | a long random string (`openssl rand -hex 32`) |
| **Permissions → Repository** | Pull requests: **Read & write** · Contents: **Read-only** · Metadata: Read-only |
| **Subscribe to events** | ✅ Pull request |
| Where can it be installed | Only on this account |

Create it, then:
1. Copy the **App ID** at the top of the page.
2. Scroll down → **Generate a private key**. A `.pem` file downloads.
3. Back in Render → Environment, add:
   - `GITHUB_APP_ID` = the App ID
   - `GITHUB_WEBHOOK_SECRET` = the secret you chose
   - `GITHUB_PRIVATE_KEY` = paste the full contents of the `.pem`
4. Save. Render redeploys.

## Step 5 — Install and test

1. In your GitHub App page → **Install App** → choose the repos.
2. Open a PR (or push to one). Within about 30–90 seconds a review appears.
3. Not working? Check the App → **Advanced → Recent Deliveries** (shows the webhook response),
   and Render → **Logs**.

---

## Tests

```bash
python tests/test_smoke.py   # diff parsing, webhook signatures, App JWT, agent loop (mocked)
```

## Customizing

- **Review style:** edit `SYSTEM_PROMPT` in `agent.py` (add your team's conventions, language-specific rules).
- **Model:** `ANTHROPIC_MODEL` (Sonnet is a good cost/quality balance; Opus for deeper reviews).
- **Skip files:** `IGNORE_PATTERNS` (comma-separated globs).
- **Let it approve/block:** `ALLOW_VERDICTS=true` (off by default so the bot never blocks merges).
- **Cost control:** `MAX_DIFF_CHARS` and `MAX_AGENT_STEPS` cap tokens per review. Also set a
  monthly spend limit in the Anthropic console.

## Ideas for v2

- Only review when someone comments `/review` (subscribe to *Issue comment* events)
- Reply to follow-up questions in review threads
- Run the test suite / linter and feed results to the agent as another tool
- A `.ai-review.yml` in each repo for per-repo rules
- A queue (Redis + worker) for busy orgs instead of in-process background tasks
