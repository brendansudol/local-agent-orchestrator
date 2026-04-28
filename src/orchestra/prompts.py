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

At the end, provide:
1. files changed
2. summary of implementation
3. tests run and results
4. risks or follow-ups
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

At the end, summarize the fix and the verification you ran.
"""


def build_review_prompt(task: AgentTask, context: RunContext, diff: str) -> str:
    return f"""Review the implementation for Asana task: {task.name}
Task URL/id: {task.permalink_url or task.gid}
Working directory: {context.worktree}

Review the diff below without editing files. Focus on correctness, regressions, missing
tests, security issues, and whether the change satisfies the task. If there are no
blocking issues, say that clearly. If there are issues, list them with file paths and
line references where possible.

Diff:
{trim(diff, MAX_EMBEDDED_OUTPUT)}
"""


def trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return value[:limit] + f"\n\n[truncated {omitted} characters]\n"

