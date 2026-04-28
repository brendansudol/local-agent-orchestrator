from pathlib import Path
import unittest

from orchestra.config import parse_config


class ConfigTests(unittest.TestCase):
    def test_parse_minimal_config(self) -> None:
        config = parse_config(sample_config(Path("/tmp/work")), base_dir=Path("/tmp"))

        self.assertEqual(config.repo.slug, "example")
        self.assertEqual(config.agents.default, "codex")
        self.assertIn("claude", config.agents.commands)
        self.assertEqual(config.verification.commands, ["python3 -m unittest discover"])


def sample_config(worktree_root: Path) -> dict:
    return {
        "asana": {
            "access_token_env": "ASANA_ACCESS_TOKEN",
            "project_gid": "project",
            "ready_section_gid": "ready",
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
        },
        "agents": {
            "default": "codex",
            "repair_rounds": 1,
            "review": True,
            "codex": {
                "command": ["codex", "exec", "-C", "{worktree}", "-"],
                "prompt_mode": "stdin",
                "review_command": ["codex", "exec", "-C", "{worktree}", "-"],
                "review_prompt_mode": "stdin",
            },
            "claude": {
                "command": ["claude", "-p", "{prompt}"],
                "prompt_mode": "arg",
                "review_command": ["claude", "-p", "{prompt}"],
                "review_prompt_mode": "arg",
            },
        },
        "verification": {"commands": ["python3 -m unittest discover"]},
        "pr": {"enabled": False},
    }


if __name__ == "__main__":
    unittest.main()
