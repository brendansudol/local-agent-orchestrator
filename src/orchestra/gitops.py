from __future__ import annotations

from datetime import datetime
from pathlib import Path
from string import Formatter
import json
import re
import shlex
import subprocess
import time
import uuid

from .config import AppConfig
from .models import CommandResult, RunContext


def make_run_id(task_gid: str, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{safe_fragment(task_gid)}-{uuid.uuid4().hex[:8]}"


def make_branch(task_gid: str, run_id: str) -> str:
    return f"agent/asana-{safe_fragment(task_gid)}-{safe_fragment(run_id)}"


def safe_fragment(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return clean or "item"


def prepare_worktree(config: AppConfig, context: RunContext) -> None:
    context.logs_dir.mkdir(parents=True, exist_ok=True)
    if context.dry_run:
        return

    if not context.repo_path.exists():
        raise FileNotFoundError(f"Configured repo path does not exist: {context.repo_path}")
    if context.worktree.exists():
        raise FileExistsError(f"Worktree already exists: {context.worktree}")

    run_command(
        "git fetch",
        ["git", "-C", str(context.repo_path), "fetch", config.repo.remote],
        timeout_seconds=config.repo.git_timeout_seconds,
    ).raise_for_status()
    run_command(
        "git worktree add",
        [
            "git",
            "-C",
            str(context.repo_path),
            "worktree",
            "add",
            str(context.worktree),
            "-b",
            context.branch,
            f"{config.repo.remote}/{context.base_branch}",
        ],
        timeout_seconds=config.repo.git_timeout_seconds,
    ).raise_for_status()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def git_status(context: RunContext, timeout_seconds: float = 120.0) -> str:
    if context.dry_run:
        return "Dry run: git status skipped."
    result = run_command(
        "git status",
        ["git", "-C", str(context.worktree), "status", "--porcelain"],
        timeout_seconds=timeout_seconds,
    )
    result.raise_for_status()
    return result.stdout


def has_worktree_changes(
    context: RunContext,
    timeout_seconds: float = 120.0,
) -> bool:
    if context.dry_run:
        return True
    return bool(git_status(context, timeout_seconds=timeout_seconds).strip())


def git_diff(context: RunContext, timeout_seconds: float = 120.0) -> str:
    if context.dry_run:
        return "Dry run: git diff skipped."

    status = git_status(context, timeout_seconds=timeout_seconds)
    unstaged = run_command(
        "git diff unstaged",
        ["git", "-C", str(context.worktree), "diff", "--binary"],
        timeout_seconds=timeout_seconds,
    )
    unstaged.raise_for_status()
    staged = run_command(
        "git diff staged",
        ["git", "-C", str(context.worktree), "diff", "--cached", "--binary"],
        timeout_seconds=timeout_seconds,
    )
    staged.raise_for_status()
    untracked = git_untracked_diff(context, timeout_seconds=timeout_seconds)
    return "\n".join(
        [
            "## git status --porcelain",
            status,
            "## git diff --binary",
            unstaged.stdout,
            unstaged.stderr,
            "## git diff --cached --binary",
            staged.stdout,
            staged.stderr,
            "## untracked files",
            untracked,
        ]
    )


def git_untracked_diff(context: RunContext, timeout_seconds: float = 120.0) -> str:
    result = run_command(
        "git ls-files untracked",
        ["git", "-C", str(context.worktree), "ls-files", "--others", "--exclude-standard"],
        timeout_seconds=timeout_seconds,
    )
    result.raise_for_status()
    parts: list[str] = []
    for relative_path in [line for line in result.stdout.splitlines() if line.strip()]:
        file_path = context.worktree / relative_path
        if not file_path.is_file():
            parts.append(f"Untracked non-file path: {relative_path}")
            continue
        diff = run_command(
            f"git diff untracked:{relative_path}",
            [
                "git",
                "-C",
                str(context.worktree),
                "diff",
                "--no-index",
                "--binary",
                "--",
                "/dev/null",
                relative_path,
            ],
            timeout_seconds=timeout_seconds,
        )
        if diff.returncode not in {0, 1}:
            diff.raise_for_status()
        parts.append(diff.stdout)
        if diff.stderr:
            parts.append(diff.stderr)
    return "\n".join(parts)


def write_patch(context: RunContext, timeout_seconds: float = 120.0) -> None:
    write_text(context.logs_dir / "patch.diff", git_diff(context, timeout_seconds=timeout_seconds))


def commit_changes(config: AppConfig, context: RunContext) -> str | None:
    if context.dry_run:
        return "dry-run-commit"
    if not has_worktree_changes(context, timeout_seconds=config.repo.git_timeout_seconds):
        raise RuntimeError("No worktree changes to commit")

    run_command(
        "git add",
        ["git", "-C", str(context.worktree), "add", "-A"],
        timeout_seconds=config.repo.git_timeout_seconds,
    ).raise_for_status()
    if not has_worktree_changes(context, timeout_seconds=config.repo.git_timeout_seconds):
        raise RuntimeError("No worktree changes to commit after staging")

    variables = _git_variables(context)
    message = config.pr.commit_message.format_map(variables)
    run_command(
        "git commit",
        ["git", "-C", str(context.worktree), "commit", "-m", message],
        timeout_seconds=config.repo.git_timeout_seconds,
    ).raise_for_status()
    result = run_command(
        "git rev-parse",
        ["git", "-C", str(context.worktree), "rev-parse", "--short", "HEAD"],
        timeout_seconds=config.repo.git_timeout_seconds,
    )
    result.raise_for_status()
    return result.stdout.strip()


def create_pr(config: AppConfig, context: RunContext) -> str | None:
    if not config.pr.enabled:
        return None
    if context.dry_run:
        return "dry-run-pr-url"

    if config.pr.push:
        run_command(
            "git push",
            [
                "git",
                "-C",
                str(context.worktree),
                "push",
                "-u",
                config.repo.remote,
                context.branch,
            ],
            timeout_seconds=config.pr.timeout_seconds,
        ).raise_for_status()

    variables = _git_variables(context)
    command = [part.format_map(variables) for part in shlex.split(config.pr.command)]
    result = run_command(
        "create PR",
        command,
        cwd=context.worktree,
        timeout_seconds=config.pr.timeout_seconds,
    )
    result.raise_for_status()
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[-1] if output else None


def command_uses_variable(command: list[str], variable: str) -> bool:
    wanted = "{" + variable + "}"
    return any(wanted in part for part in command)


def format_command(command: list[str], variables: dict[str, str]) -> list[str]:
    formatter = Formatter()
    rendered: list[str] = []
    for part in command:
        used = {field_name for _, field_name, _, _ in formatter.parse(part) if field_name}
        missing = used - variables.keys()
        if missing:
            joined = ", ".join(sorted(missing))
            raise KeyError(f"Missing command template variables: {joined}")
        rendered.append(part.format_map(variables))
    return rendered


def run_command(
    name: str,
    command: list[str],
    cwd: Path | None = None,
    input_text: str | None = None,
    timeout_seconds: float | None = None,
) -> CommandResult:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _normalize_timeout_output(exc.stdout)
        stderr = _normalize_timeout_output(exc.stderr)
        if timeout_seconds is not None:
            stderr = (stderr + "\n" if stderr else "") + (
                f"Command timed out after {timeout_seconds:g} seconds."
            )
        return CommandResult(
            name=name,
            command=command,
            returncode=124,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - start,
            timed_out=True,
        )
    return CommandResult(
        name=name,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=time.monotonic() - start,
    )


def _git_variables(context: RunContext) -> dict[str, str]:
    return {
        "base_branch": context.base_branch,
        "branch": context.branch,
        "worktree": str(context.worktree),
        "run_dir": str(context.run_root),
        "logs_dir": str(context.logs_dir),
        "task_gid": context.task.gid,
        "task_name": context.task.name,
        "run_id": context.run_id,
    }


def _normalize_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
