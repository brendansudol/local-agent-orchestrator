from __future__ import annotations

from .config import AppConfig
from .gitops import format_command, write_text
from .gitops import run_command
from .models import CommandResult, RunContext


def select_implementer(config: AppConfig, preferred_agent: str | None) -> str:
    preferred = (preferred_agent or "either").lower()
    if preferred != "either" and preferred in config.agents.commands:
        return preferred
    return config.agents.default


def select_reviewer(config: AppConfig, implementer: str) -> str | None:
    if not config.agents.review:
        return None
    if implementer == "codex" and "claude" in config.agents.commands:
        return "claude"
    if implementer == "claude" and "codex" in config.agents.commands:
        return "codex"

    for candidate in sorted(config.agents.commands):
        if candidate != implementer:
            return candidate
    return None


class AgentRunner:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def run(
        self,
        agent_name: str,
        prompt: str,
        context: RunContext,
        *,
        review: bool = False,
        label: str | None = None,
    ) -> CommandResult:
        agent_config = self.config.agents.get(agent_name)
        command_template, prompt_mode = agent_config.command_for(review=review)
        label = label or ("review" if review else "implement")

        command = self._render_command(command_template, prompt, context, prompt_mode)
        display_command = self._render_command(
            command_template,
            "<prompt>",
            context,
            prompt_mode,
        )
        input_text = prompt if prompt_mode == "stdin" else None

        write_text(
            context.logs_dir / f"{label}_{agent_name}_command.txt",
            " ".join(display_command) + "\n",
        )

        if context.dry_run:
            return CommandResult(
                name=f"{label}:{agent_name}",
                command=display_command,
                returncode=0,
                stdout=f"dry run: skipped {label} agent {agent_name}\n",
            )

        return run_command(
            f"{label}:{agent_name}",
            command,
            cwd=context.worktree,
            input_text=input_text,
        )

    def _render_command(
        self,
        command_template: list[str],
        prompt: str,
        context: RunContext,
        prompt_mode: str,
    ) -> list[str]:
        variables = {
            "worktree": str(context.worktree),
            "run_dir": str(context.run_root),
            "logs_dir": str(context.logs_dir),
            "final_message": str(context.logs_dir / "final.md"),
            "prompt": prompt,
            "task_gid": context.task.gid,
            "run_id": context.run_id,
            "branch": context.branch,
            "base_branch": context.base_branch,
        }
        rendered = format_command(command_template, variables)
        if prompt_mode == "arg" and "{prompt}" not in " ".join(command_template):
            rendered.append(prompt)
        return rendered

