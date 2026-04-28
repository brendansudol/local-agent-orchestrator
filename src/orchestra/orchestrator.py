from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import socket
import time

from .agents import AgentRunner, select_implementer, select_reviewer
from .asana import AsanaClient, DryRunQueue
from .config import AppConfig
from .gitops import (
    commit_changes,
    create_pr,
    git_diff,
    has_worktree_changes,
    make_branch,
    make_run_id,
    prepare_worktree,
    safe_fragment,
    write_json,
    write_patch,
    write_text,
)
from .models import AgentTask, CommandResult, ReviewVerdict, RunContext, VerificationResult
from .prompts import build_implementation_prompt, build_repair_prompt, build_review_prompt
from .review import ReviewParseError, parse_review_verdict
from .verification import run_verification


@dataclass(slots=True)
class RunOutcome:
    handled: bool
    ok: bool
    message: str
    logs_dir: Path | None = None


class Orchestrator:
    def __init__(
        self,
        config: AppConfig,
        *,
        dry_run: bool = False,
        dry_task: AgentTask | None = None,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self.queue = DryRunQueue(config, dry_task) if dry_run else AsanaClient(config.asana)
        self.runner = AgentRunner(config)

    def run_loop(self, *, once: bool = False, interval_seconds: int = 60) -> RunOutcome:
        last = RunOutcome(False, True, "No runs attempted")
        while True:
            last = self.run_once()
            if once:
                return last
            time.sleep(interval_seconds)

    def run_once(self) -> RunOutcome:
        tasks = self.queue.list_ready_tasks()
        if not tasks:
            return RunOutcome(False, True, "No eligible queued tasks found")

        task = tasks[0]
        run_id = make_run_id(task.gid)
        branch = make_branch(task.gid, run_id)
        runner_id = socket.gethostname()

        claimed = self.queue.claim_task(task, run_id, branch, runner_id)
        if claimed is None:
            return RunOutcome(False, True, f"Task {task.gid} was already claimed")

        context = self._build_context(claimed, run_id, branch)
        try:
            return self._run_claimed_task(context)
        except Exception as exc:
            self._safe_mark_blocked(context.task.gid, f"Agent run failed: {exc}")
            write_text(context.logs_dir / "error.txt", str(exc) + "\n")
            return RunOutcome(True, False, f"Task {context.task.gid} failed: {exc}", context.logs_dir)

    def _run_claimed_task(self, context: RunContext) -> RunOutcome:
        if not self.queue.verify_claim(context.task.gid, context.run_id):
            return RunOutcome(
                True,
                False,
                f"Task {context.task.gid} claim could not be verified",
                context.logs_dir,
            )
        if self.config.asana.running_section_gid:
            self.queue.move_to_section(context.task.gid, self.config.asana.running_section_gid)

        prepare_worktree(self.config, context)
        self.queue.set_status(context.task.gid, "running")
        self.queue.add_comment(
            context.task.gid,
            f"Agent run {context.run_id} started on {socket.gethostname()}.\n"
            f"Branch: {context.branch}",
        )

        write_json(context.logs_dir / "task.json", asdict(context.task))
        write_json(
            context.logs_dir / "context.json",
            {
                "run_id": context.run_id,
                "branch": context.branch,
                "base_branch": context.base_branch,
                "repo_path": str(context.repo_path),
                "worktree": str(context.worktree),
                "implementer": context.implementer,
                "reviewer": context.reviewer,
                "dry_run": context.dry_run,
            },
        )

        prompt = build_implementation_prompt(context.task, context)
        write_text(context.logs_dir / "prompt.md", prompt)
        implementation = self.runner.run(
            context.implementer,
            prompt,
            context,
            label="implement",
        )
        self._write_command_result(context, implementation, "implement_result")
        if not implementation.ok:
            write_patch(context, timeout_seconds=self.config.repo.git_timeout_seconds)
            return self._block(
                context,
                "Implementation agent failed before verification.",
                implementation,
            )

        self.queue.set_status(context.task.gid, "verifying")
        verification = run_verification(self.config, context)
        self._write_verification(context, verification, "verification")

        repair_count = 0
        while not verification.ok and repair_count < self.config.agents.repair_rounds:
            repair_count += 1
            repair_prompt = build_repair_prompt(context.task, context, verification)
            write_text(context.logs_dir / f"repair_{repair_count}_prompt.md", repair_prompt)
            repair = self.runner.run(
                context.implementer,
                repair_prompt,
                context,
                label=f"repair_{repair_count}",
            )
            self._write_command_result(context, repair, f"repair_{repair_count}_result")
            if not repair.ok:
                break
            verification = run_verification(self.config, context)
            self._write_verification(context, verification, f"verification_after_repair_{repair_count}")

        write_patch(context, timeout_seconds=self.config.repo.git_timeout_seconds)
        if not verification.ok:
            return self._block(context, "Verification failed.", verification)
        if not has_worktree_changes(
            context,
            timeout_seconds=self.config.repo.git_timeout_seconds,
        ):
            return self._block(
                context,
                "Agent completed without producing any git changes.",
                CommandResult(
                    name="git status",
                    command=["git", "-C", str(context.worktree), "status", "--porcelain"],
                    returncode=1,
                    stdout="No worktree changes found.\n",
                ),
            )

        review_result: CommandResult | None = None
        review_verdict: ReviewVerdict | None = None
        if context.reviewer:
            diff = git_diff(context, timeout_seconds=self.config.repo.git_timeout_seconds)
            review_prompt = build_review_prompt(context.task, context, diff)
            write_text(context.logs_dir / "review_prompt.md", review_prompt)
            review_result = self.runner.run(
                context.reviewer,
                review_prompt,
                context,
                review=True,
                label="review",
            )
            self._write_command_result(context, review_result, "review_result")
            if not review_result.ok:
                return self._block(context, "Review agent failed.", review_result)
            try:
                review_verdict = parse_review_verdict(
                    review_result.stdout + "\n" + review_result.stderr
                )
            except ReviewParseError as exc:
                return self._block(
                    context,
                    f"Review verdict could not be parsed: {exc}",
                    review_result,
                )
            write_json(context.logs_dir / "review_verdict.json", review_verdict.to_dict())
            if not review_verdict.ok:
                return self._block(
                    context,
                    "Review verdict blocked the run.",
                    review_result,
                )

        commit_hash = commit_changes(self.config, context)
        if commit_hash:
            write_text(context.logs_dir / "commit.txt", commit_hash + "\n")
        pr_url = create_pr(self.config, context)
        if pr_url:
            self.queue.set_pr_url(context.task.gid, pr_url)

        self.queue.set_status(context.task.gid, "review")
        self.queue.move_to_section(context.task.gid, self.config.asana.review_section_gid)
        self.queue.add_comment(
            context.task.gid,
            self._success_summary(
                context,
                verification,
                review_result,
                review_verdict,
                commit_hash,
                pr_url,
            ),
        )
        return RunOutcome(
            True,
            True,
            f"Task {context.task.gid} completed and is ready for human review",
            context.logs_dir,
        )

    def _build_context(self, task: AgentTask, run_id: str, branch: str) -> RunContext:
        base_branch = task.base_branch or self.config.repo.default_base_branch
        implementer = select_implementer(self.config, task.preferred_agent)
        reviewer = select_reviewer(self.config, implementer)
        run_root = (
            self.config.repo.worktree_root
            / safe_fragment(task.gid)
            / safe_fragment(run_id)
        )
        return RunContext(
            task=task,
            run_id=run_id,
            branch=branch,
            base_branch=base_branch,
            repo_path=self.config.repo.path,
            run_root=run_root,
            worktree=run_root / "repo",
            logs_dir=run_root / "logs",
            implementer=implementer,
            reviewer=reviewer,
            dry_run=self.dry_run,
        )

    def _block(
        self,
        context: RunContext,
        reason: str,
        result: CommandResult | VerificationResult,
    ) -> RunOutcome:
        self.queue.set_status(context.task.gid, "blocked")
        self.queue.move_to_section(context.task.gid, self.config.asana.blocked_section_gid)
        self.queue.add_comment(context.task.gid, self._failure_summary(context, reason, result))
        return RunOutcome(True, False, f"Task {context.task.gid} blocked: {reason}", context.logs_dir)

    def _safe_mark_blocked(self, task_gid: str, message: str) -> None:
        try:
            self.queue.set_status(task_gid, "blocked")
            self.queue.move_to_section(task_gid, self.config.asana.blocked_section_gid)
            self.queue.add_comment(task_gid, message)
        except Exception:
            pass

    def _write_command_result(
        self,
        context: RunContext,
        result: CommandResult,
        name: str,
    ) -> None:
        write_json(context.logs_dir / f"{name}.json", result.to_dict())
        write_text(context.logs_dir / f"{name}.stdout", result.stdout)
        write_text(context.logs_dir / f"{name}.stderr", result.stderr)

    def _write_verification(
        self,
        context: RunContext,
        result: VerificationResult,
        name: str,
    ) -> None:
        write_json(context.logs_dir / f"{name}.json", result.to_dict())
        write_text(context.logs_dir / f"{name}.log", result.combined_output())

    def _success_summary(
        self,
        context: RunContext,
        verification: VerificationResult,
        review: CommandResult | None,
        review_verdict: ReviewVerdict | None,
        commit_hash: str | None,
        pr_url: str | None,
    ) -> str:
        lines = [
            f"Agent run {context.run_id} completed.",
            f"Implementer: {context.implementer}",
            f"Branch: {context.branch}",
            f"Verification: {'passed' if verification.ok else 'failed'}",
        ]
        if review:
            lines.append(f"Reviewer: {context.reviewer} exited {review.returncode}")
        if review_verdict:
            lines.append(f"Review verdict: {review_verdict.verdict}")
        if commit_hash:
            lines.append(f"Commit: {commit_hash}")
        if pr_url:
            lines.append(f"PR: {pr_url}")
        else:
            lines.append("PR: not created by configuration")
        lines.append(f"Logs: {context.logs_dir}")
        return "\n".join(lines)

    def _failure_summary(
        self,
        context: RunContext,
        reason: str,
        result: CommandResult | VerificationResult,
    ) -> str:
        lines = [
            f"Agent run {context.run_id} is blocked.",
            f"Reason: {reason}",
            f"Branch: {context.branch}",
            f"Logs: {context.logs_dir}",
        ]
        if isinstance(result, CommandResult):
            lines.append(f"Command: {' '.join(result.command)}")
            lines.append(f"Exit code: {result.returncode}")
            if result.timed_out:
                lines.append("Timed out: yes")
            output = (result.stdout + "\n" + result.stderr).strip()
            if output:
                lines.append("Command output:")
                lines.append(output[-4000:])
        else:
            lines.append("Verification output:")
            lines.append(result.combined_output()[-4000:])
        return "\n".join(lines)
