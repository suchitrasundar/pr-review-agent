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
