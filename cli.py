"""Test the agent locally on any PR, without setting up the GitHub App.

    python cli.py owner/repo#123          # dry run: prints the review
    python cli.py owner/repo#123 --post   # actually posts it to the PR

Needs ANTHROPIC_API_KEY and GITHUB_TOKEN (a personal access token) in .env.
"""
import argparse
import asyncio
import os
import re

from dotenv import load_dotenv

load_dotenv()

from agent import pretty, review_pull_request  # noqa: E402
from github_client import GitHubRepo  # noqa: E402


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("pr", help="owner/repo#number or a PR URL")
    p.add_argument("--post", action="store_true", help="Post the review to GitHub")
    args = p.parse_args()

    m = re.search(r"([\w.-]+)/([\w.-]+)(?:#|/pull/)(\d+)", args.pr)
    if not m:
        raise SystemExit("Use owner/repo#123 or https://github.com/owner/repo/pull/123")
    owner, repo, number = m.group(1), m.group(2), int(m.group(3))

    gh = GitHubRepo(os.environ["GITHUB_TOKEN"], owner, repo)
    try:
        review = await review_pull_request(gh, number)
        print(pretty(review))
        if args.post:
            await gh.create_review(number, **review)
            print(f"\n✅ Posted review to {owner}/{repo}#{number}")
    finally:
        await gh.close()


if __name__ == "__main__":
    asyncio.run(main())
