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
- timeout limits for agent, review, verification, git, and PR commands

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
writing a unique `Run id`, and then re-reading the task to verify ownership. If
`running_section_gid` is configured, claimed tasks are also moved out of the ready
section for clearer board state.

## Workflow

For each eligible task, the worker:

1. claims the task in Asana
2. creates a branch and git worktree from the configured base branch
3. writes run artifacts under `worktree_root/<task_gid>/<run_id>/logs`
4. builds a strict implementation prompt from the task
5. runs the selected CLI agent
6. runs configured verification commands
7. optionally runs one repair round
8. blocks if no git changes were produced
9. optionally runs cross-agent review and requires a machine-readable JSON verdict
10. commits verified changes on the task branch
11. optionally pushes and creates a draft PR
12. updates Asana with the result

Review agents must return JSON:

```json
{
  "verdict": "ok",
  "findings": []
}
```

A `blocked` verdict leaves the Asana task in the blocked section with the run logs
attached in the comment.

## Tests

```bash
python3 -m unittest discover
```
