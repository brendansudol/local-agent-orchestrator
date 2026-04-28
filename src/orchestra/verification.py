from __future__ import annotations

import shlex

from .config import AppConfig
from .gitops import run_command
from .models import CommandResult, RunContext, VerificationResult


def run_verification(config: AppConfig, context: RunContext) -> VerificationResult:
    if not config.verification.commands:
        return VerificationResult(commands=[])

    results: list[CommandResult] = []
    for raw_command in config.verification.commands:
        command = shlex.split(raw_command)
        if context.dry_run:
            results.append(
                CommandResult(
                    name=f"verify:{raw_command}",
                    command=command,
                    returncode=0,
                    stdout="dry run: skipped verification command\n",
                )
            )
            continue

        result = run_command(
            f"verify:{raw_command}",
            command,
            cwd=context.worktree,
        )
        results.append(result)
        if not result.ok:
            break

    return VerificationResult(commands=results)

