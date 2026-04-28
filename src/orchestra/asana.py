from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib import error, parse, request
import json
import os

from .config import AppConfig, AsanaConfig, ConfigError
from .models import AgentTask


ASANA_BASE_URL = "https://app.asana.com/api/1.0"


class AsanaError(RuntimeError):
    pass


class AsanaClient:
    def __init__(
        self,
        config: AsanaConfig,
        runner_id: str,
        token: str | None = None,
    ) -> None:
        self.config = config
        self.runner_id = runner_id
        self.token = token or os.environ.get(config.access_token_env)
        if not self.token:
            raise ConfigError(
                f"Asana token missing; set ${config.access_token_env} or use --dry-run"
            )

    def list_ready_tasks(self) -> list[AgentTask]:
        api_limit = min(max(self.config.task_limit * 10, 10), 100)
        response = self._request(
            "GET",
            f"/sections/{self.config.ready_section_gid}/tasks",
            params={
                "limit": str(api_limit),
                "opt_fields": "gid,name",
            },
        )
        tasks: list[AgentTask] = []
        for item in response.get("data", []):
            task = self.get_task(item["gid"])
            if is_claimable(task, self.runner_id):
                tasks.append(task)
            if len(tasks) >= self.config.task_limit:
                break
        return tasks

    def get_task(self, gid: str) -> AgentTask:
        response = self._request(
            "GET",
            f"/tasks/{gid}",
            params={
                "opt_fields": ",".join(
                    [
                        "gid",
                        "name",
                        "notes",
                        "permalink_url",
                        "completed",
                        "custom_fields",
                        "custom_fields.gid",
                        "custom_fields.name",
                        "custom_fields.display_value",
                        "custom_fields.text_value",
                        "custom_fields.enum_value.gid",
                        "custom_fields.enum_value.name",
                    ]
                )
            },
        )
        return parse_task(response["data"], self.config)

    def claim_task(
        self,
        task: AgentTask,
        run_id: str,
        branch: str,
        runner: str | None = None,
    ) -> AgentTask | None:
        fresh = self.get_task(task.gid)
        runner = runner or self.runner_id
        if not is_claimable(fresh, runner):
            return None

        self.update_custom_fields(
            task.gid,
            {
                self.config.fields.status: self._enum("status", "claimed"),
                self.config.fields.run_id: run_id,
                self.config.fields.branch_name: branch,
                self.config.fields.last_heartbeat: _now(),
                self.config.fields.runner: runner,
            },
        )
        verified = self.get_task(task.gid)
        if verified.run_id != run_id or verified.runner != runner:
            return None
        return verified

    def verify_claim(self, task_gid: str, run_id: str) -> bool:
        task = self.get_task(task_gid)
        return (
            task.run_id == run_id
            and task.runner == self.runner_id
            and task.status in {"claimed", "running", "verifying"}
        )

    def set_status(self, task_gid: str, status: str) -> None:
        self.update_custom_fields(
            task_gid,
            {
                self.config.fields.status: self._enum("status", status),
                self.config.fields.last_heartbeat: _now(),
            },
        )

    def set_pr_url(self, task_gid: str, pr_url: str) -> None:
        self.update_custom_fields(task_gid, {self.config.fields.pr_url: pr_url})

    def add_comment(self, task_gid: str, text: str) -> None:
        self._request("POST", f"/tasks/{task_gid}/stories", body={"data": {"text": text}})

    def move_to_section(self, task_gid: str, section_gid: str) -> None:
        self._request(
            "POST",
            f"/sections/{section_gid}/addTask",
            body={"data": {"task": task_gid}},
        )

    def update_custom_fields(self, task_gid: str, fields: dict[str, Any]) -> None:
        self._request(
            "PUT",
            f"/tasks/{task_gid}",
            body={"data": {"custom_fields": fields}},
        )

    def _enum(self, group: str, name: str) -> str:
        try:
            return self.config.enums[group][name]
        except KeyError as exc:
            raise ConfigError(f"Missing enum mapping for asana.enums.{group}.{name}") from exc

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = ASANA_BASE_URL + path
        if params:
            url += "?" + parse.urlencode(params)

        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url, method=method, headers=headers, data=data)
        try:
            with request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AsanaError(f"Asana API {method} {path} failed: {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise AsanaError(f"Asana API {method} {path} failed: {exc.reason}") from exc


class DryRunQueue:
    def __init__(
        self,
        config: AppConfig,
        runner_id: str,
        task: AgentTask | None = None,
    ) -> None:
        self.config = config
        self.runner_id = runner_id
        self.task = task or AgentTask(
            gid="dry-run-task",
            name="Dry run orchestration task",
            notes=(
                "Exercise the local orchestration flow without Asana writes, "
                "agent execution, git worktree creation, or PR creation."
            ),
            permalink_url="https://app.asana.com/0/dry-run/dry-run-task",
            repo=config.repo.slug,
            base_branch=config.repo.default_base_branch,
            preferred_agent=config.agents.default,
            status="queued",
            assigned_runner=runner_id,
            eligible=True,
        )
        self.comments: list[str] = []
        self.statuses: list[str] = []
        self.section_moves: list[str] = []

    def list_ready_tasks(self) -> list[AgentTask]:
        return [self.task] if is_claimable(self.task, self.runner_id) else []

    def claim_task(
        self,
        task: AgentTask,
        run_id: str,
        branch: str,
        runner: str | None = None,
    ) -> AgentTask:
        task.run_id = run_id
        task.status = "claimed"
        task.runner = runner or self.runner_id
        task.raw["branch"] = branch
        task.raw["runner"] = task.runner
        return task

    def verify_claim(self, task_gid: str, run_id: str) -> bool:
        return (
            self.task.gid == task_gid
            and self.task.run_id == run_id
            and self.task.runner == self.runner_id
        )

    def set_status(self, task_gid: str, status: str) -> None:
        self.statuses.append(status)
        self.task.status = status

    def set_pr_url(self, task_gid: str, pr_url: str) -> None:
        self.task.raw["pr_url"] = pr_url

    def add_comment(self, task_gid: str, text: str) -> None:
        self.comments.append(text)

    def move_to_section(self, task_gid: str, section_gid: str) -> None:
        self.section_moves.append(section_gid)


def parse_task(raw: dict[str, Any], config: AsanaConfig) -> AgentTask:
    fields = config.fields
    preferred_agent = _enum_name(raw, fields.preferred_agent, config, "preferred_agent")
    if preferred_agent is None:
        preferred_agent = _field_text(raw, fields.preferred_agent) or "either"

    status = _enum_name(raw, fields.status, config, "status")
    if status is None:
        status = _field_text(raw, fields.status)

    return AgentTask(
        gid=str(raw["gid"]),
        name=raw.get("name", ""),
        notes=raw.get("notes") or "",
        permalink_url=raw.get("permalink_url") or "",
        repo=_field_text(raw, fields.repo),
        base_branch=_field_text(raw, fields.base_branch),
        preferred_agent=_normalize_agent(preferred_agent),
        status=_normalize_optional(status),
        run_id=_field_text(raw, fields.run_id),
        runner=_field_text(raw, fields.runner),
        assigned_runner=_field_text(raw, fields.assigned_runner),
        eligible=_is_eligible(raw, config),
        raw=raw,
    )


def is_claimable(task: AgentTask, runner_id: str | None = None) -> bool:
    return (
        task.eligible
        and task.status == "queued"
        and not task.run_id
        and _assigned_to_runner(task, runner_id)
    )


def _custom_field(raw: dict[str, Any], gid: str) -> dict[str, Any] | None:
    for field in raw.get("custom_fields", []) or []:
        if str(field.get("gid")) == gid:
            return field
    return None


def _field_text(raw: dict[str, Any], gid: str | None) -> str | None:
    if not gid:
        return None
    field = _custom_field(raw, gid)
    if not field:
        return None
    for key in ("text_value", "display_value"):
        value = field.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    enum_value = field.get("enum_value") or {}
    if enum_value.get("name"):
        return str(enum_value["name"]).strip()
    return None


def _field_enum_gid(raw: dict[str, Any], gid: str) -> str | None:
    field = _custom_field(raw, gid)
    if not field:
        return None
    enum_value = field.get("enum_value") or {}
    if enum_value.get("gid"):
        return str(enum_value["gid"])
    return None


def _enum_name(
    raw: dict[str, Any],
    field_gid: str,
    config: AsanaConfig,
    group: str,
) -> str | None:
    enum_gid = _field_enum_gid(raw, field_gid)
    if enum_gid:
        for name, gid in config.enums.get(group, {}).items():
            if gid == enum_gid:
                return name
    return None


def _is_eligible(raw: dict[str, Any], config: AsanaConfig) -> bool:
    enum_gid = _field_enum_gid(raw, config.fields.agent_eligible)
    yes_gid = config.enums.get("agent_eligible", {}).get("yes")
    if yes_gid:
        return enum_gid == yes_gid
    value = _field_text(raw, config.fields.agent_eligible)
    return bool(value and value.lower() in {"yes", "true", "eligible"})


def _normalize_agent(value: str | None) -> str:
    normalized = _normalize_optional(value) or "either"
    if normalized not in {"codex", "claude", "either"}:
        return "either"
    return normalized


def _assigned_to_runner(task: AgentTask, runner_id: str | None) -> bool:
    if not task.assigned_runner:
        return True
    if not runner_id:
        return False
    return _normalize_runner(task.assigned_runner) == _normalize_runner(runner_id)


def _normalize_runner(value: str) -> str:
    return value.strip().lower()


def _normalize_optional(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).strip().lower().replace(" ", "_")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
