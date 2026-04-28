Inspiration: https://openai.com/index/open-source-codex-orchestration-symphony/

prompt: please sketch out what a local implementation would look like for someone using this at work where they have access to claude code and codex (but not app server) and can use asana and asana mcp for task management

response:

I’d build this as a **local worker daemon** with Asana as the queue, git worktrees as isolation, and Claude Code/Codex as interchangeable command-line workers.

```text
Asana project / tasks
        |
        v
local orchestrator daemon
  - polls/claims tasks
  - creates git worktree
  - builds task prompt
  - launches Claude Code or Codex
  - runs tests/checks
  - opens PR or leaves patch
  - updates Asana
        |
        v
isolated local worktree per task
        |
        +--> claude -p ...
        +--> codex exec ...
```

The important design choice: **do not make the model responsible for queue ownership.** Let a deterministic script claim tasks, create branches, run tests, and update status. Let the agents work only inside the checked-out repo.

## 1. Use Asana as the control plane

Create one Asana project, maybe called `Agent Queue`, with sections like:

```text
Ready for agent
Claimed / running
Needs human review
Blocked
Done
```

Add custom fields such as:

```text
Agent eligible: yes/no
Preferred agent: claude | codex | either
Repo: repo slug or URL
Base branch: main, develop, etc.
Risk level: low | medium | high
Agent status: queued | claimed | running | verifying | review | blocked | done
Run id
Branch name
PR URL
Last heartbeat
```

Asana’s MCP server can let AI clients access and manage Asana tasks/projects through compatible MCP clients, and Asana’s current MCP V2 setup uses OAuth with a registered MCP app. Use the **V2** Asana MCP flow rather than building on the old beta endpoint, because Asana documents the V1 beta MCP server as deprecated with a shutdown date of **May 11, 2026**. ([Asana Docs][1])

For the orchestrator itself, I would usually use the normal Asana API or a small MCP client library for deterministic reads/writes. For the agents, use Asana MCP when they need task context or when you want Claude/Codex to summarize/update work. Asana’s docs say tool availability should be discovered with `tools/list`, which is a good reason not to hard-code MCP tool names too aggressively. ([Asana Docs][1])

## 2. Configure the local agent clients

Claude Code is the cleaner Asana-MCP client here. Asana documents native Claude Code setup with:

```bash
claude mcp add --transport http \
  --client-id "$ASANA_CLIENT_ID" \
  --client-secret \
  --callback-port 8080 \
  asana https://mcp.asana.com/v2/mcp
```

Asana notes that Claude Code stores the client secret securely in the system keychain, with non-sensitive config in user/project config files. ([Asana Docs][2])

Codex can also be local: OpenAI describes Codex CLI as a local terminal coding agent that can read, change, and run code on your machine in the selected directory. ([OpenAI Developers][3]) Without app-server, the key mode is `codex exec`, which OpenAI documents for scripted/non-interactive runs such as CI-style jobs. ([OpenAI Developers][4])

For Codex + Asana MCP, Asana’s docs show a `mcp-remote` configuration in `~/.codex/config.toml`, but they also warn that `mcp-remote` is community-maintained, experimental, and not officially supported by Asana. I’d use Claude Code for Asana-MCP-heavy steps and keep Codex focused on repo work unless your workplace is comfortable reviewing and approving the `mcp-remote` path. ([Asana Docs][2])

## 3. Replace app-server with bounded CLI jobs

Since you do **not** have Codex app-server, don’t try to emulate Symphony’s long-lived JSON-RPC agent session. Instead, make each agent run a bounded job.

For Codex:

```bash
codex exec \
  -C "$WORKTREE" \
  --sandbox workspace-write \
  --ask-for-approval never \
  --json \
  --output-last-message "$RUN_DIR/final.md" \
  - < "$RUN_DIR/prompt.md"
```

Codex documents `codex exec` as the non-interactive command, supports setting the workspace root with `-C/--cd`, JSON event output, final-message output, sandbox policy, and stdin prompt input. It also has an approval flag; avoid the dangerous bypass/yolo mode except inside a hardened sandbox or VM. ([OpenAI Developers][5])

For Claude Code:

```bash
claude -p \
  --output-format json \
  --max-turns 20 \
  --permission-mode acceptEdits \
  --append-system-prompt-file "$PROMPTS/work-rules.md" \
  "$(cat "$RUN_DIR/prompt.md")" \
  > "$RUN_DIR/claude.json"
```

Claude Code documents `-p/--print` for non-interactive output, JSON output, max-turn limits, JSON-schema output, permission modes, MCP config loading, and system prompt append files. ([Claude][6])

For review-only steps, run the reviewer in a more restrictive mode:

```bash
codex exec -C "$WORKTREE" --sandbox read-only --json - < "$RUN_DIR/review_prompt.md"
```

or:

```bash
claude -p \
  --permission-mode plan \
  --output-format json \
  "$(cat "$RUN_DIR/review_prompt.md")"
```

## 4. Local orchestrator loop

A minimal Python/Node orchestrator can be very small:

```python
while True:
    tasks = asana.find_tasks(
        project="Agent Queue",
        section="Ready for agent",
        filters={"Agent eligible": True},
        limit=3,
    )

    for task in tasks:
        if not asana.claim_task(task.gid, runner=HOSTNAME):
            continue

        try:
            run_id = new_run_id(task)
            worktree = create_worktree(task, run_id)
            prompt = build_prompt_from_asana_task(task, worktree)

            asana.comment(task.gid, f"Agent run {run_id} started on {HOSTNAME}.")
            asana.update_fields(task.gid, {"Agent status": "running"})

            result = run_agent(
                preferred_agent=task.preferred_agent,
                worktree=worktree,
                prompt=prompt,
                run_id=run_id,
            )

            test_result = run_verification(worktree)

            if test_result.failed:
                repair_prompt = build_repair_prompt(task, result, test_result)
                result = run_agent(task.preferred_agent, worktree, repair_prompt, run_id)
                test_result = run_verification(worktree)

            review = run_cross_agent_review(worktree, implementer=task.preferred_agent)

            if test_result.passed and review.ok:
                pr_url = create_pr(worktree, task)
                asana.update_fields(task.gid, {
                    "Agent status": "review",
                    "PR URL": pr_url,
                })
                asana.comment(task.gid, summarize_run(result, test_result, review))
            else:
                asana.update_fields(task.gid, {"Agent status": "blocked"})
                asana.comment(task.gid, summarize_failure(result, test_result, review))

        except Exception as e:
            asana.update_fields(task.gid, {"Agent status": "blocked"})
            asana.comment(task.gid, f"Agent run failed: {e}")

    sleep(60)
```

The claim step should be conservative. For example, only claim tasks that are still in `Ready for agent`, have `Agent status = queued`, and have no active `Run id`. Immediately set `Agent status = claimed`, write the hostname, and add a heartbeat timestamp. If two local workers are running, this prevents duplicate work.

## 5. Worktree and branch layout

For each Asana task:

```bash
TASK_ID="120123456789"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$TASK_ID"
BRANCH="agent/asana-$TASK_ID-$RUN_ID"
WORKTREE="$HOME/agent-work/$TASK_ID/$RUN_ID/repo"
RUN_DIR="$HOME/agent-work/$TASK_ID/$RUN_ID/logs"

mkdir -p "$RUN_DIR"

git -C "$REPO_CACHE" fetch origin
git -C "$REPO_CACHE" worktree add "$WORKTREE" -b "$BRANCH" "origin/$BASE_BRANCH"
```

Keep per-run artifacts:

```text
logs/
  task.json
  prompt.md
  agent.jsonl
  final.md
  test.log
  review.md
  patch.diff
  summary.md
```

That gives you resumability without app-server. The durable state is the git branch, logs, and Asana task, not an agent session.

## 6. Prompt shape

The orchestrator should generate a strict prompt like:

```text
You are working on Asana task: {task_name}
Task URL/id: {task_gid}
Repository: {repo}
Base branch: {base_branch}
Working directory: {worktree}

Goal:
{task_description}

Acceptance criteria:
{acceptance_criteria}

Constraints:
- Work only inside this repository.
- Do not push directly to main or merge anything.
- Do not modify secrets, production config, or credentials.
- Prefer small, reviewable changes.
- Run relevant tests before finishing.
- If requirements are ambiguous, stop and write a concise blocking question.
- At the end, provide:
  1. files changed
  2. summary of implementation
  3. tests run and results
  4. risks or follow-ups
```

For repair loops, include the failing test output and ask the same agent to fix only the failure. For review loops, ask the other agent to inspect the diff without editing.

A strong pattern is:

```text
Implementer: Codex
Reviewer: Claude

or

Implementer: Claude
Reviewer: Codex
```

That catches more issues than having the same agent review itself.

## 7. Suggested workflow per task

```text
1. Poll Asana for eligible task.
2. Claim it.
3. Create branch/worktree.
4. Build prompt from task body, comments, repo metadata, and acceptance criteria.
5. Run implementer agent.
6. Run deterministic checks: format, lint, unit tests, typecheck.
7. If failing, run one or two repair rounds.
8. Run cross-agent review.
9. Create PR if checks pass.
10. Update Asana with summary, PR URL, test results, and status.
11. Move task to Needs human review.
```

I would avoid auto-merging. At work, the safe default is “agent opens a PR; human reviews and merges.”

## 8. Security defaults I’d use at work

Use **unique Asana MCP credentials per developer**, don’t commit them, rotate them, and monitor which local processes can access them; Asana explicitly recommends those precautions. ([Asana Docs][2])

For agents, avoid broad bypass modes. Codex documents a dangerous bypass flag that runs without approvals or sandboxing and says to use it only in externally hardened environments; its safety notes also recommend avoiding that combination unless inside a dedicated sandbox VM. ([OpenAI Developers][5]) Claude Code similarly exposes a dangerous skip-permissions flag, but for work automation I’d prefer explicit permission modes such as `plan`, `acceptEdits`, or `auto` depending on the step. ([Claude][6])

I’d also separate permissions:

```text
Orchestrator:
  - can update Asana
  - can create branches/PRs
  - can run tests
  - should be deterministic

Coding agent:
  - can edit repo worktree
  - can run local dev/test commands
  - ideally does not need Asana write access

Review agent:
  - read-only repo access
  - no Asana writes
```

## 9. Minimal viable version

The first useful version could be:

```text
One local daemon
One Asana project
One repo
One task at a time
Codex or Claude chosen by custom field
No auto-merge
PR creation only after tests pass
```

Then add concurrency, multiple repos, custom Asana rules, better review prompts, and richer logs.

The core idea is: **local Symphony-like orchestration is absolutely feasible without Codex app-server**. Treat Claude Code and Codex as CLI workers, make Asana the queue, make git worktrees the isolation layer, and keep the orchestration/state transitions outside the model.

[1]: https://developers.asana.com/docs/using-asanas-mcp-server "Using Asana's MCP Server"
[2]: https://developers.asana.com/docs/connecting-mcp-clients-to-asanas-v2-server "Connecting Coding Clients to Asana's V2 server"
[3]: https://developers.openai.com/codex/cli "CLI – Codex | OpenAI Developers"
[4]: https://developers.openai.com/codex/noninteractive "Non-interactive mode – Codex | OpenAI Developers"
[5]: https://developers.openai.com/codex/cli/reference "Command line options – Codex CLI | OpenAI Developers"
[6]: https://code.claude.com/docs/en/cli-reference "CLI reference - Claude Code Docs"
