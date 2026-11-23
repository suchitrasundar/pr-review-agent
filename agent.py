"""The review agent: Claude + two tools (read_file, submit_review) in a bounded loop."""
import json
import logging
import os
from fnmatch import fnmatch

from anthropic import AsyncAnthropic

from github_client import GitHubRepo, annotate_patch

log = logging.getLogger("agent")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
MAX_STEPS = int(os.getenv("MAX_AGENT_STEPS", "12"))
MAX_DIFF_CHARS = int(os.getenv("MAX_DIFF_CHARS", "150000"))
MAX_FILE_CHARS = 60_000
IGNORE = [p.strip() for p in os.getenv(
    "IGNORE_PATTERNS", "*.lock,package-lock.json,*.min.js,*.svg,*.png,*.jpg,dist/*,vendor/*"
).split(",") if p.strip()]

SYSTEM_PROMPT = """You are a senior software engineer reviewing a GitHub pull request.

Focus on what matters, in this order:
1. Bugs and incorrect logic, including edge cases and error handling
2. Security issues (injection, secrets, authz, unsafe deserialization, etc.)
3. Performance problems and resource leaks
4. Readability and maintainability issues that will genuinely cost the team later

Rules:
- Be specific and actionable. Suggest the fix, with a short code snippet when useful.
- Do NOT nitpick formatting or style a linter would catch. Do NOT praise trivial things.
- Only comment on lines that appear in the diff. Each diff line is prefixed with its
  line number in the new file; use that number for `line`.
- If you need surrounding code to judge a change, call read_file (at most a few times).
- If the PR looks good, say so briefly and leave few or no inline comments.
- Finish by calling submit_review exactly once."""

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the full contents of a file in the repo at the PR's head commit. "
                       "Use to understand context around a change (callers, types, config).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Repo-relative path"}},
            "required": ["path"],
        },
    },
    {
        "name": "submit_review",
        "description": "Submit the final review. Call exactly once, at the end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Markdown overview: what the PR does, overall assessment, key risks.",
                },
                "verdict": {
                    "type": "string",
                    "enum": ["approve", "comment", "request_changes"],
                },
                "comments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "line": {"type": "integer", "description": "New-file line number from the diff"},
                            "severity": {"type": "string", "enum": ["critical", "warning", "suggestion"]},
                            "body": {"type": "string", "description": "Markdown comment"},
                        },
                        "required": ["path", "line", "severity", "body"],
                    },
                },
            },
            "required": ["summary", "verdict", "comments"],
        },
    },
]

SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "suggestion": "💡"}


def build_diff(files: list[dict]) -> tuple[str, dict[str, set[int]], list[str]]:
    """Return (annotated diff text, commentable lines per file, skipped file names)."""
    parts, valid, skipped, used = [], {}, [], 0
    for f in files:
        name = f["filename"]
        if any(fnmatch(name, p) for p in IGNORE) or not f.get("patch"):
            skipped.append(name)
            continue
        annotated, lines = annotate_patch(f["patch"])
        block = f"### {name} ({f['status']}, +{f['additions']}/-{f['deletions']})\n```diff\n{annotated}\n```\n"
        if used + len(block) > MAX_DIFF_CHARS:
            skipped.append(name)
            continue
        parts.append(block)
        valid[name] = lines
        used += len(block)
    return "\n".join(parts), valid, skipped


async def review_pull_request(gh: GitHubRepo, number: int) -> dict:
    """Run the agent on a PR. Returns a dict ready for GitHubRepo.create_review()."""
    pr = await gh.get_pr(number)
    head_sha = pr["head"]["sha"]
    files = await gh.get_pr_files(number)
    diff, valid_lines, skipped = build_diff(files)

    if not diff:
        return {"commit_id": head_sha, "event": "COMMENT", "comments": [],
                "body": "🤖 Nothing reviewable in this PR (only ignored, binary or very large files)."}

    user_msg = (
        f"Repository: {gh.owner}/{gh.repo}\n"
        f"PR #{number}: {pr['title']}\n"
        f"Author: {pr['user']['login']}\n"
        f"Base: {pr['base']['ref']} ← Head: {pr['head']['ref']}\n\n"
        f"Description:\n{pr.get('body') or '(none)'}\n\n"
        f"Changed files ({len(files)}):\n\n{diff}"
    )
    if skipped:
        user_msg += f"\n\nNot shown (ignored/binary/too large): {', '.join(skipped[:50])}"

    client = AsyncAnthropic()
    messages = [{"role": "user", "content": user_msg}]
    result = None

    for step in range(MAX_STEPS):
        resp = await client.messages.create(
            model=MODEL, max_tokens=8000, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text")
            result = {"summary": text or "Review complete.", "verdict": "comment", "comments": []}
            break

        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            if block.name == "submit_review":
                result = block.input
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": "Submitted."})
            elif block.name == "read_file":
                path = block.input.get("path", "")
                log.info("step %d: read_file %s", step, path)
                content = await gh.get_file(path, head_sha)
                if content is None:
                    content = f"File not found: {path}"
                elif len(content) > MAX_FILE_CHARS:
                    content = content[:MAX_FILE_CHARS] + "\n... [truncated]"
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
            else:
                tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                     "content": "Unknown tool", "is_error": True})
        if result is not None:
            break
        messages.append({"role": "user", "content": tool_results})

    if result is None:
        result = {"summary": "Review hit the step limit before finishing.", "verdict": "comment", "comments": []}

    return to_github_review(result, head_sha, valid_lines)
