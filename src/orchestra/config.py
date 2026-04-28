from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tomllib


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AsanaFields:
    agent_eligible: str
    preferred_agent: str
    repo: str
    base_branch: str
    status: str
    run_id: str
    branch_name: str
    pr_url: str
    last_heartbeat: str
    runner: str
    assigned_runner: str | None = None


@dataclass(frozen=True, slots=True)
class AsanaConfig:
    access_token_env: str
    project_gid: str
    ready_section_gid: str
    running_section_gid: str | None
    review_section_gid: str
    blocked_section_gid: str
    done_section_gid: str
    task_limit: int
    fields: AsanaFields
    enums: dict[str, dict[str, str]]


@dataclass(frozen=True, slots=True)
class RepoConfig:
    slug: str
    path: Path
    remote: str
    default_base_branch: str
    worktree_root: Path
    git_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class AgentCommandConfig:
    command: list[str]
    prompt_mode: str
    timeout_seconds: float
    review_command: list[str]
    review_prompt_mode: str
    review_timeout_seconds: float

    def command_for(self, review: bool = False) -> tuple[list[str], str, float]:
        if review:
            return self.review_command, self.review_prompt_mode, self.review_timeout_seconds
        return self.command, self.prompt_mode, self.timeout_seconds


@dataclass(frozen=True, slots=True)
class AgentsConfig:
    default: str
    repair_rounds: int
    review: bool
    commands: dict[str, AgentCommandConfig] = field(default_factory=dict)

    def get(self, name: str) -> AgentCommandConfig:
        try:
            return self.commands[name]
        except KeyError as exc:
            known = ", ".join(sorted(self.commands)) or "none"
            raise ConfigError(f"Unknown agent {name!r}; configured agents: {known}") from exc


@dataclass(frozen=True, slots=True)
class VerificationConfig:
    commands: list[str]
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class PrConfig:
    enabled: bool
    command: str
    push: bool = True
    timeout_seconds: float = 120.0
    commit_message: str = "Agent changes for Asana task {task_gid}"


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    id: str | None = None


@dataclass(frozen=True, slots=True)
class AppConfig:
    asana: AsanaConfig
    repo: RepoConfig
    agents: AgentsConfig
    verification: VerificationConfig
    pr: PrConfig
    runner: RunnerConfig


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser()
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    return parse_config(data, base_dir=config_path.parent)


def parse_config(data: dict[str, Any], base_dir: Path | None = None) -> AppConfig:
    base_dir = base_dir or Path.cwd()
    asana_data = _required_table(data, "asana")
    try:
        fields = AsanaFields(**_required_table(asana_data, "fields"))
    except TypeError as exc:
        raise ConfigError(f"Invalid [asana.fields] table: {exc}") from exc

    repo_data = _required_table(data, "repo")
    repo_path = _expand_path(_required(repo_data, "path"), base_dir)
    worktree_root = _expand_path(_required(repo_data, "worktree_root"), base_dir)

    agents_data = _required_table(data, "agents")
    commands: dict[str, AgentCommandConfig] = {}
    for name, value in agents_data.items():
        if not isinstance(value, dict):
            continue
        prompt_mode = value.get("prompt_mode", "stdin")
        review_prompt_mode = value.get("review_prompt_mode", prompt_mode)
        timeout_seconds = _positive_float(
            value.get("timeout_seconds", agents_data.get("timeout_seconds", 3600)),
            f"agents.{name}.timeout_seconds",
        )
        _validate_prompt_mode(name, prompt_mode)
        _validate_prompt_mode(name, review_prompt_mode)
        commands[name] = AgentCommandConfig(
            command=_string_list(value.get("command", []), f"agents.{name}.command"),
            prompt_mode=prompt_mode,
            timeout_seconds=timeout_seconds,
            review_command=_string_list(
                value.get("review_command", value.get("command", [])),
                f"agents.{name}.review_command",
            ),
            review_prompt_mode=review_prompt_mode,
            review_timeout_seconds=_positive_float(
                value.get(
                    "review_timeout_seconds",
                    value.get("timeout_seconds", agents_data.get("review_timeout_seconds", 1800)),
                ),
                f"agents.{name}.review_timeout_seconds",
            ),
        )

    if not commands:
        raise ConfigError("At least one [agents.<name>] table is required")

    default_agent = agents_data.get("default", "codex")
    if default_agent not in commands:
        raise ConfigError(f"agents.default {default_agent!r} is not configured")

    verification_data = data.get("verification", {})
    pr_data = data.get("pr", {})
    runner_data = data.get("runner", {})
    if not isinstance(runner_data, dict):
        raise ConfigError("[runner] must be a table")

    return AppConfig(
        asana=AsanaConfig(
            access_token_env=asana_data.get("access_token_env", "ASANA_ACCESS_TOKEN"),
            project_gid=_required(asana_data, "project_gid"),
            ready_section_gid=_required(asana_data, "ready_section_gid"),
            running_section_gid=asana_data.get("running_section_gid"),
            review_section_gid=_required(asana_data, "review_section_gid"),
            blocked_section_gid=_required(asana_data, "blocked_section_gid"),
            done_section_gid=_required(asana_data, "done_section_gid"),
            task_limit=int(asana_data.get("task_limit", 1)),
            fields=fields,
            enums=_normalize_enums(asana_data.get("enums", {})),
        ),
        repo=RepoConfig(
            slug=_required(repo_data, "slug"),
            path=repo_path,
            remote=repo_data.get("remote", "origin"),
            default_base_branch=repo_data.get("default_base_branch", "main"),
            worktree_root=worktree_root,
            git_timeout_seconds=_positive_float(
                repo_data.get("git_timeout_seconds", 120),
                "repo.git_timeout_seconds",
            ),
        ),
        agents=AgentsConfig(
            default=default_agent,
            repair_rounds=int(agents_data.get("repair_rounds", 1)),
            review=bool(agents_data.get("review", True)),
            commands=commands,
        ),
        verification=VerificationConfig(
            commands=_string_list(verification_data.get("commands", []), "verification.commands"),
            timeout_seconds=_positive_float(
                verification_data.get("timeout_seconds", 900),
                "verification.timeout_seconds",
            ),
        ),
        pr=PrConfig(
            enabled=bool(pr_data.get("enabled", False)),
            command=pr_data.get(
                "command",
                "gh pr create --fill --draft --base {base_branch} --head {branch}",
            ),
            push=bool(pr_data.get("push", True)),
            timeout_seconds=_positive_float(pr_data.get("timeout_seconds", 120), "pr.timeout_seconds"),
            commit_message=pr_data.get(
                "commit_message",
                "Agent changes for Asana task {task_gid}",
            ),
        ),
        runner=RunnerConfig(id=_optional_non_empty_string(runner_data.get("id"), "runner.id")),
    )


def _required_table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"Missing required [{key}] table")
    return value


def _required(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Missing required config value: {key}")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    return value


def _expand_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _validate_prompt_mode(agent_name: str, prompt_mode: str) -> None:
    if prompt_mode not in {"stdin", "arg"}:
        raise ConfigError(
            f"agents.{agent_name}.prompt_mode must be either 'stdin' or 'arg'"
        )


def _positive_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive number") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be a positive number")
    return parsed


def _optional_non_empty_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _normalize_enums(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise ConfigError("asana.enums must be a table")
    normalized: dict[str, dict[str, str]] = {}
    for group, mapping in value.items():
        if not isinstance(mapping, dict):
            raise ConfigError(f"asana.enums.{group} must be a table")
        normalized[group] = {
            str(name).lower(): str(gid) for name, gid in mapping.items()
        }
    return normalized
