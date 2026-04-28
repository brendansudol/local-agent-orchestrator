from __future__ import annotations

from .models import AgentTask, RunContext, VerificationResult


MAX_EMBEDDED_OUTPUT = 60000


def build_implementation_prompt(task: AgentTask, context: RunContext) -> str:
    return f"""You are working on Asana task: {task.name}
Task URL/id: {task.permalink_url or task.gid}
Repository: {task.repo or context.repo_path.name}
Base branch: {context.base_branch}
Working directory: {context.worktree}

Goal:
{task.notes or "(No description provided.)"}

Constraints:
- Work only inside this repository.
- Do not push directly to main or merge anything.
- Do not modify secrets, production config, or credentials.
- Prefer small, reviewable changes.
- Follow the repository's existing style and helper APIs.
- Run relevant tests before finishing if the task requires local validation.
- If requirements are ambiguous, stop and write a concise blocking question.

At the very end, provide a concise Markdown section exactly named:
## Agent Summary

This is an external implementation summary for humans and Asana. Do not include
private reasoning, hidden chain-of-thought, raw logs, or raw JSON event streams.
Include:
- Files changed
- Implementation summary
- Key decisions / tradeoffs
- Tests run and results
- Risks or follow-ups
"""


def build_repair_prompt(
    task: AgentTask,
    context: RunContext,
    verification: VerificationResult,
) -> str:
    output = trim(verification.combined_output(), MAX_EMBEDDED_OUTPUT)
    return f"""The previous implementation for Asana task {task.gid} did not pass verification.

Working directory: {context.worktree}

Fix only the failure shown below. Keep the change focused and do not broaden scope.

Verification output:
{output}

At the very end, provide a concise Markdown section exactly named:
## Agent Summary

This is an external repair summary for humans and Asana. Do not include private
reasoning, hidden chain-of-thought, raw logs, or raw JSON event streams.
Include:
- Files changed
- Implementation summary
- Key decisions / tradeoffs
- Tests run and results
- Risks or follow-ups
"""


def build_review_prompt(task: AgentTask, context: RunContext, diff: str) -> str:
    return f"""Review the implementation for Asana task: {task.name}
Task URL/id: {task.permalink_url or task.gid}
Working directory: {context.worktree}

Review the diff below without editing files. Focus on correctness, regressions, missing
tests, security issues, and whether the change satisfies the task. If there are no
blocking issues, return verdict "ok". If there are blocking issues, return verdict
"blocked" and include findings with file paths and line references where possible.

Return only a JSON object in this exact shape:
{{
  "verdict": "ok" | "blocked",
  "findings": [
    {{
      "title": "short issue title",
      "body": "one paragraph explanation",
      "file": "path/to/file.py",
      "line": 123
    }}
  ]
}}

Diff:
{trim(diff, MAX_EMBEDDED_OUTPUT)}
"""


def trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return value[:limit] + f"\n\n[truncated {omitted} characters]\n"
