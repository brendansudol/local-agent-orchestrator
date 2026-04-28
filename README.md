# Local Orchestra

Local Orchestra is a small, deterministic worker daemon for the workflow sketched in
`spec.md`: Asana is the queue, git worktrees provide isolation, and Claude Code or
Codex run as bounded CLI workers.

The initial version is intentionally conservative:

- one Asana task at a time
- one configured repository
- one isolated git worktree per run
- no auto-merge
- PR creation disabled by default
- dry-run mode for checking config and prompt generation without Asana writes

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp config.example.toml config.toml
```

Set your Asana token:

```bash
export ASANA_ACCESS_TOKEN="..."
```

Run a local dry run first:

```bash
orchestra run --config config.toml --once --dry-run
```

Run against Asana after the config is filled in:

```bash
orchestra run --config config.toml --once
```

## Asana Setup

Create one project with sections like:

- `Ready for agent`
- `Claimed / running`
- `Needs human review`
- `Blocked`
- `Done`

Create custom fields matching `config.example.toml`:

- `Agent eligible`
- `Preferred agent`
- `Repo`
- `Base branch`
- `Agent status`
- `Run id`
- `Branch name`
- `PR URL`
- `Last heartbeat`
- `Runner`

The orchestrator claims tasks by moving `Agent status` from `queued` to `claimed`,
writing a unique `Run id`, and then re-reading the task to verify ownership.

## Workflow

For each eligible task, the worker:

1. claims the task in Asana
2. creates a branch and git worktree from the configured base branch
3. writes run artifacts under `worktree_root/<task_gid>/<run_id>/logs`
4. builds a strict implementation prompt from the task
5. runs the selected CLI agent
6. runs configured verification commands
7. optionally runs one repair round
8. optionally runs cross-agent review
9. optionally creates a draft PR
10. updates Asana with the result

## Tests

```bash
python3 -m unittest discover
```
