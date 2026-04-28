from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .asana import parse_task
from .config import ConfigError, load_config
from .models import AgentTask
from .orchestrator import Orchestrator


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_command(args)
    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestra",
        description="Run local Asana-driven orchestration for Codex and Claude Code.",
    )
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="poll and run queued Asana tasks")
    run.add_argument("--config", default="config.toml", help="path to config TOML")
    run.add_argument("--once", action="store_true", help="run one polling cycle and exit")
    run.add_argument("--dry-run", action="store_true", help="skip Asana writes, git worktrees, agents, and PRs")
    run.add_argument("--task-json", help="optional local task JSON for --dry-run")
    run.add_argument("--interval", type=int, default=60, help="poll interval in seconds")
    return parser


def run_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        dry_task = load_task_json(Path(args.task_json), config) if args.task_json else None
        orchestrator = Orchestrator(config, dry_run=args.dry_run, dry_task=dry_task)
        outcome = orchestrator.run_loop(once=args.once, interval_seconds=args.interval)
    except (ConfigError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(outcome.message)
    if outcome.logs_dir:
        print(f"logs: {outcome.logs_dir}")
    return 0 if outcome.ok else 1


def load_task_json(path: Path, config) -> AgentTask:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], dict):
        raw = raw["data"]
    if not isinstance(raw, dict):
        raise ConfigError("--task-json must contain a JSON object")

    if raw.get("custom_fields"):
        task = parse_task(raw, config.asana)
        task.eligible = True
        task.status = "queued"
        return task

    return AgentTask(
        gid=str(raw.get("gid", "dry-run-task")),
        name=str(raw.get("name", "Dry run orchestration task")),
        notes=str(raw.get("notes", raw.get("description", ""))),
        permalink_url=str(raw.get("permalink_url", "")),
        repo=raw.get("repo") or config.repo.slug,
        base_branch=raw.get("base_branch") or config.repo.default_base_branch,
        preferred_agent=str(raw.get("preferred_agent", config.agents.default)),
        status="queued",
        assigned_runner=raw.get("assigned_runner"),
        eligible=True,
        raw=raw,
    )


if __name__ == "__main__":
    raise SystemExit(main())
