from pathlib import Path
import unittest

from orchestra.config import parse_config


class ConfigTests(unittest.TestCase):
    def test_parse_minimal_config(self) -> None:
        config = parse_config(sample_config(Path("/tmp/work")), base_dir=Path("/tmp"))

        self.assertEqual(config.repo.slug, "example")
        self.assertEqual(config.repo.git_timeout_seconds, 120)
        self.assertEqual(config.runner.id, "test-runner")
        self.assertEqual(config.agents.default, "codex")
        self.assertIn("claude", config.agents.commands)
        self.assertEqual(config.agents.commands["claude"].prompt_mode, "stdin")
        self.assertEqual(config.verification.commands, ["python3 -m unittest discover"])
        self.assertEqual(config.verification.timeout_seconds, 900)

    def test_runner_config_is_optional(self) -> None:
        data = sample_config(Path("/tmp/work"))
        data.pop("runner")
        data["asana"]["fields"].pop("assigned_runner")

        config = parse_config(data, base_dir=Path("/tmp"))

        self.assertIsNone(config.runner.id)
        self.assertIsNone(config.asana.fields.assigned_runner)


def sample_config(worktree_root: Path) -> dict:
    return {
        "asana": {
            "access_token_env": "ASANA_ACCESS_TOKEN",
            "project_gid": "project",
            "ready_section_gid": "ready",
            "running_section_gid": "running",
            "review_section_gid": "review",
            "blocked_section_gid": "blocked",
            "done_section_gid": "done",
            "task_limit": 1,
            "fields": {
                "agent_eligible": "eligible",
                "preferred_agent": "preferred",
                "repo": "repo",
                "base_branch": "base",
                "status": "status",
                "run_id": "run",
                "branch_name": "branch",
                "pr_url": "pr",
                "last_heartbeat": "heartbeat",
                "runner": "runner",
                "assigned_runner": "assigned_runner",
            },
            "enums": {
                "agent_eligible": {"yes": "yes", "no": "no"},
                "preferred_agent": {
                    "codex": "codex",
                    "claude": "claude",
                    "either": "either",
                },
                "status": {
                    "queued": "queued",
                    "claimed": "claimed",
                    "running": "running",
                    "verifying": "verifying",
                    "review": "review",
                    "blocked": "blocked",
                    "done": "done",
                },
            },
        },
        "repo": {
            "slug": "example",
            "path": "/tmp/example",
            "remote": "origin",
            "default_base_branch": "main",
            "worktree_root": str(worktree_root),
            "git_timeout_seconds": 120,
        },
        "runner": {"id": "test-runner"},
        "agents": {
            "default": "codex",
            "repair_rounds": 1,
            "review": True,
            "timeout_seconds": 3600,
            "review_timeout_seconds": 1800,
            "codex": {
                "command": ["codex", "exec", "-C", "{worktree}", "-"],
                "prompt_mode": "stdin",
                "timeout_seconds": 3600,
                "review_command": ["codex", "exec", "-C", "{worktree}", "-"],
                "review_prompt_mode": "stdin",
                "review_timeout_seconds": 1800,
            },
            "claude": {
                "command": ["claude", "-p"],
                "prompt_mode": "stdin",
                "timeout_seconds": 3600,
                "review_command": ["claude", "-p"],
                "review_prompt_mode": "stdin",
                "review_timeout_seconds": 1800,
            },
        },
        "verification": {
            "commands": ["python3 -m unittest discover"],
            "timeout_seconds": 900,
        },
        "pr": {
            "enabled": False,
            "timeout_seconds": 120,
            "commit_message": "Agent changes for Asana task {task_gid}",
        },
    }


if __name__ == "__main__":
    unittest.main()
