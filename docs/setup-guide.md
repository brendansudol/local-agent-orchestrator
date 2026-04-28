# Setup Guide

This guide walks through setting up Local Orchestra on a developer laptop. The
intended workflow is:

```text
Asana task -> local worker -> git worktree -> Codex/Claude CLI -> tests -> review -> commit -> optional PR
```

The worker is conservative by default: it processes one task at a time, keeps work
in an isolated git worktree, blocks no-op runs, requires machine-readable review
results, and leaves PR creation disabled until you opt in.

## 1. Prerequisites

Install or verify these tools:

```bash
python3 --version
git --version
```

Install at least one supported coding agent CLI:

```bash
codex --version
claude --version
```

The example config includes both. If you only use one of them, either remove the
other `[agents.<name>]` table from `config.toml` or set `review = false` so the
worker does not try to launch a missing reviewer.

If you want the worker to create PRs, also install and authenticate GitHub CLI:

```bash
gh --version
gh auth status
```

You also need:

- an Asana personal access token or other bearer token with access to the queue project
- local checkout of the repository the agents will modify
- push permission to that repository if PR creation is enabled
- enough local disk space under `worktree_root` for per-task worktrees and logs

## 2. Install Local Orchestra

From this repository:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp config.example.toml config.toml
```

`config.toml` is ignored by git because it contains local paths and Asana IDs.

Set your Asana token:

```bash
export ASANA_ACCESS_TOKEN="..."
```

For repeated use, put that export in your shell profile or your local process
manager. Do not commit tokens.

## 3. Create the Asana Queue

Create one Asana project, for example `Agent Queue`.

Create these sections:

- `Ready for agent`
- `Claimed / running`
- `Needs human review`
- `Blocked`
- `Done`

Create these custom fields:

| Field | Suggested Type | Values |
| --- | --- | --- |
| `Agent eligible` | single-select | `yes`, `no` |
| `Preferred agent` | single-select | `codex`, `claude`, `either` |
| `Repo` | text | repo slug, for example `example-service` |
| `Base branch` | text | `main`, `develop`, etc. |
| `Agent status` | single-select | `queued`, `claimed`, `running`, `verifying`, `review`, `blocked`, `done` |
| `Run id` | text | written by worker |
| `Branch name` | text | written by worker |
| `PR URL` | text | written by worker |
| `Last heartbeat` | text | written by worker |
| `Runner` | text | written by worker |
| `Assigned runner` | text or single-select | teammate/worker IDs, for example `brendan` |

Only tasks with these values are claimable:

- `Agent eligible = yes`
- `Agent status = queued`
- empty `Run id`
- empty `Assigned runner` or `Assigned runner` equal to this worker's `runner.id`

That lets multiple local workers poll the same project with low risk of duplicate
work. Use empty `Assigned runner` for a shared pool task, or set it to a specific
teammate's runner ID to route the task to that teammate. The worker writes
`claimed`, `Run id`, `Branch name`, `Last heartbeat`, and `Runner`, then re-reads
the task to verify ownership.

## 4. Fill In Asana IDs

`config.toml` needs Asana gids for sections, custom fields, and enum options.

The project gid is usually visible in the Asana project URL. To fetch section IDs:

```bash
curl -sS \
  -H "Authorization: Bearer $ASANA_ACCESS_TOKEN" \
  "https://app.asana.com/api/1.0/projects/<project_gid>/sections" \
  | python3 -m json.tool
```

To fetch custom field and enum option IDs:

```bash
curl -sS \
  -H "Authorization: Bearer $ASANA_ACCESS_TOKEN" \
  "https://app.asana.com/api/1.0/projects/<project_gid>/custom_field_settings?opt_fields=custom_field.gid,custom_field.name,custom_field.enum_options.gid,custom_field.enum_options.name" \
  | python3 -m json.tool
```

Copy those IDs into these config sections:

```toml
[asana]
project_gid = "..."
ready_section_gid = "..."
running_section_gid = "..."
review_section_gid = "..."
blocked_section_gid = "..."
done_section_gid = "..."

[asana.fields]
agent_eligible = "..."
preferred_agent = "..."
repo = "..."
base_branch = "..."
status = "..."
run_id = "..."
branch_name = "..."
pr_url = "..."
last_heartbeat = "..."
runner = "..."
assigned_runner = "..."

[asana.enums.agent_eligible]
yes = "..."
no = "..."
```

`running_section_gid` is optional. When present, claimed tasks are moved out of
the ready section so the board is easier for humans to scan.

`assigned_runner` is optional but recommended when more than one teammate uses
the same Asana board. If it is omitted, the worker treats all queued eligible
tasks as part of the shared pool.

## 5. Configure the Target Repository

Point `[repo]` at the local checkout that should be used as the worktree source:

```toml
[repo]
slug = "example-service"
path = "/absolute/path/to/example-service"
remote = "origin"
default_base_branch = "main"
worktree_root = "~/agent-work"
git_timeout_seconds = 120
```

Set a stable runner ID for this teammate or machine:

```toml
[runner]
id = "brendan"
```

If `[runner].id` is not set, the worker uses the machine hostname. A stable,
human-readable ID is better when multiple teammates share one board.

The current worker is intentionally one-repository-per-config. If you want to
orchestrate multiple repositories, run one worker process per repository with a
separate config file and Asana queue/filtering convention.

Before running the worker, make sure this checkout can fetch from the remote:

```bash
git -C /absolute/path/to/example-service fetch origin
```

The worker creates branches like:

```text
agent/asana-<task_gid>-<run_id>
```

and worktrees like:

```text
~/agent-work/<task_gid>/<run_id>/repo
~/agent-work/<task_gid>/<run_id>/logs
```

## 6. Configure Agents

The default config supports Codex and Claude Code. Prompts are sent over stdin so
long task descriptions, diffs, and test logs do not hit command-line argument
limits.

Codex implementation command:

```toml
[agents.codex]
command = [
  "codex", "exec",
  "-C", "{worktree}",
  "--sandbox", "workspace-write",
  "--ask-for-approval", "never",
  "--json",
  "--output-last-message", "{final_message}",
  "-"
]
prompt_mode = "stdin"
timeout_seconds = 3600
```

Claude implementation command:

```toml
[agents.claude]
command = [
  "claude", "-p",
  "--output-format", "json",
  "--max-turns", "20",
  "--permission-mode", "acceptEdits"
]
prompt_mode = "stdin"
timeout_seconds = 3600
```

Review commands should be more restrictive. The default Codex reviewer uses a
read-only sandbox, and the default Claude reviewer uses `plan` mode.

## 7. Configure Verification

Set commands that should pass before review and PR creation:

```toml
[verification]
commands = [
  "python3 -m unittest discover",
  "npm test"
]
timeout_seconds = 900
```

Commands run from the task worktree. They run in order and stop on the first
failure. If verification fails, the worker can run repair rounds according to:

```toml
[agents]
repair_rounds = 1
```

## 8. Configure PR Creation

PR creation is off by default:

```toml
[pr]
enabled = false
```

To enable it:

```toml
[pr]
enabled = true
command = "gh pr create --fill --draft --base {base_branch} --head {branch}"
push = true
timeout_seconds = 120
commit_message = "Agent changes for Asana task {task_gid}"
```

The worker commits after verification and review pass, then pushes and creates
the PR. It does not auto-merge.

Available template variables include:

- `{task_gid}`
- `{task_name}`
- `{run_id}`
- `{branch}`
- `{base_branch}`
- `{worktree}`
- `{run_dir}`
- `{logs_dir}`

## 9. Test With Dry Run

Dry run exercises the orchestration path without Asana writes, git worktree
creation, agent execution, or PR creation:

```bash
orchestra run --config config.toml --once --dry-run
```

You should see a successful dry-run message and a logs directory under
`worktree_root`.

You can also test prompt generation with a local task JSON:

```json
{
  "gid": "dry-123",
  "name": "Add health check endpoint",
  "notes": "Create a GET /health endpoint and add tests.",
  "repo": "example-service",
  "base_branch": "main",
  "preferred_agent": "codex"
}
```

Run it with:

```bash
orchestra run --config config.toml --once --dry-run --task-json task.json
```

## 10. Run One Real Task

Create or update one Asana task in the ready section:

- `Agent eligible = yes`
- `Preferred agent = codex`, `claude`, or `either`
- `Repo = <your configured repo slug>`
- `Base branch = main`
- `Agent status = queued`
- `Assigned runner` is empty or equals your `[runner].id`
- clear `Run id`

Then run:

```bash
orchestra run --config config.toml --once
```

If it succeeds, the task moves to review, and the Asana comment includes the
branch, commit, PR URL if enabled, and logs path.

If it blocks, inspect:

```text
<worktree_root>/<task_gid>/<run_id>/logs/
```

Important files include:

- `prompt.md`
- `implement_result.json`
- `verification.json`
- `verification.log`
- `patch.diff`
- `review_prompt.md`
- `review_result.json`
- `review_verdict.json`
- `commit.txt`
- `error.txt`

## 11. Run as a Polling Worker

After one-task runs work, run the worker continuously:

```bash
orchestra run --config config.toml --interval 60
```

For work use, run it under a process manager such as `launchd`, `systemd`, or a
terminal multiplexer. Keep one worker per repository until you are comfortable
with your Asana claim and review process.

## 12. Troubleshooting

`Asana token missing`

Set the environment variable configured by `asana.access_token_env`, usually:

```bash
export ASANA_ACCESS_TOKEN="..."
```

`No eligible queued tasks found`

Check that the task is in the ready section, `Agent eligible = yes`,
`Agent status = queued`, `Run id` is empty, and `Assigned runner` is empty or
matches this worker's `[runner].id`.

`Task was already claimed`

Another worker or earlier run wrote a `Run id`. Inspect the task's custom fields
and logs before clearing it.

`Agent completed without producing any git changes`

The agent exited successfully but left the worktree clean. The run is blocked so
an empty branch is not marked ready for review.

`Review verdict could not be parsed`

The reviewer must return a JSON object:

```json
{"verdict":"ok","findings":[]}
```

or:

```json
{"verdict":"blocked","findings":[{"title":"Issue","body":"Explanation"}]}
```

`Command timed out`

Increase the relevant timeout:

- `agents.<name>.timeout_seconds`
- `agents.<name>.review_timeout_seconds`
- `verification.timeout_seconds`
- `repo.git_timeout_seconds`
- `pr.timeout_seconds`

`gh pr create` fails

Run `gh auth status`, verify push access, and try the generated branch manually
from the worktree listed in the logs.

## 13. Operating Checklist

Before relying on the worker for real work:

- run `python3 -m unittest discover` in this repository
- run `orchestra run --config config.toml --once --dry-run`
- test one low-risk Asana task with PR creation disabled
- inspect the generated patch and logs
- enable PR creation only after the first real run behaves correctly
- keep auto-merge disabled
- rotate tokens according to your workplace policy
