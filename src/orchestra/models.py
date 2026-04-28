from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AgentTask:
    gid: str
    name: str
    notes: str
    permalink_url: str = ""
    repo: str | None = None
    base_branch: str | None = None
    preferred_agent: str = "either"
    status: str | None = None
    run_id: str | None = None
    runner: str | None = None
    assigned_runner: str | None = None
    eligible: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunContext:
    task: AgentTask
    run_id: str
    branch: str
    base_branch: str
    repo_path: Path
    run_root: Path
    worktree: Path
    logs_dir: Path
    implementer: str
    reviewer: str | None = None
    dry_run: bool = False


@dataclass(slots=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def raise_for_status(self) -> None:
        if self.returncode != 0:
            raise RuntimeError(
                f"{self.name} failed with exit code {self.returncode}\n"
                f"command: {' '.join(self.command)}\n"
                f"stdout:\n{self.stdout}\n"
                f"stderr:\n{self.stderr}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": round(self.duration_seconds, 3),
            "timed_out": self.timed_out,
        }


@dataclass(slots=True)
class VerificationResult:
    commands: list[CommandResult]

    @property
    def ok(self) -> bool:
        return all(command.ok for command in self.commands)

    def combined_output(self) -> str:
        parts: list[str] = []
        for command in self.commands:
            parts.append(f"$ {' '.join(command.command)}")
            if command.stdout:
                parts.append(command.stdout)
            if command.stderr:
                parts.append(command.stderr)
            parts.append(f"exit code: {command.returncode}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "commands": [command.to_dict() for command in self.commands],
        }


@dataclass(slots=True)
class ReviewVerdict:
    verdict: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.verdict == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "findings": self.findings,
            "raw": self.raw,
        }
