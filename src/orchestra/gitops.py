from __future__ import annotations

from datetime import datetime
from pathlib import Path
from string import Formatter
import json
import re
import shlex
import subprocess
import time

from .config import AppConfig
from .models import CommandResult, RunContext


def make_run_id(task_gid: str, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{safe_fragment(task_gid)}"


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


def git_diff(context: RunContext) -> str:
    if context.dry_run:
        return "Dry run: git diff skipped."
    result = run_command(
        "git diff",
        ["git", "-C", str(context.worktree), "diff", "--binary"],
    )
    return result.stdout + result.stderr


def write_patch(context: RunContext) -> None:
    write_text(context.logs_dir / "patch.diff", git_diff(context))


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
        ).raise_for_status()

    variables = {
        "base_branch": context.base_branch,
        "branch": context.branch,
        "worktree": str(context.worktree),
        "run_dir": str(context.run_root),
    }
    command = [part.format_map(variables) for part in shlex.split(config.pr.command)]
    result = run_command("create PR", command, cwd=context.worktree)
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
) -> CommandResult:
    start = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(
        name=name,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=time.monotonic() - start,
    )

