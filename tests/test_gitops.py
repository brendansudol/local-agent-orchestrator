from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from orchestra.config import parse_config
from orchestra.gitops import commit_changes, git_diff, has_worktree_changes, run_command
from orchestra.models import AgentTask, RunContext
from tests.test_config import sample_config


class GitOpsTests(unittest.TestCase):
    def test_run_command_times_out(self) -> None:
        result = run_command(
            "sleep",
            ["python3", "-c", "import time; time.sleep(2)"],
            timeout_seconds=0.1,
        )

        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out", result.stderr)

    def test_git_diff_includes_staged_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp))
            (repo / "tracked.txt").write_text("old\n", encoding="utf-8")
            run_command("git add", ["git", "add", "tracked.txt"], cwd=repo).raise_for_status()
            run_command("git commit", ["git", "commit", "-m", "base"], cwd=repo).raise_for_status()
            (repo / "tracked.txt").write_text("new\n", encoding="utf-8")
            run_command("git add change", ["git", "add", "tracked.txt"], cwd=repo).raise_for_status()
            (repo / "created.txt").write_text("created\n", encoding="utf-8")

            context = context_for(repo)
            diff = git_diff(context)

            self.assertTrue(has_worktree_changes(context))
            self.assertIn("## git diff --cached --binary", diff)
            self.assertIn("+new", diff)
            self.assertIn("## untracked files", diff)
            self.assertIn("+created", diff)

    def test_has_worktree_changes_is_false_for_clean_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp))
            (repo / "tracked.txt").write_text("old\n", encoding="utf-8")
            run_command("git add", ["git", "add", "tracked.txt"], cwd=repo).raise_for_status()
            run_command("git commit", ["git", "commit", "-m", "base"], cwd=repo).raise_for_status()

            self.assertFalse(has_worktree_changes(context_for(repo)))

    def test_commit_changes_stages_and_commits(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "repo")
            (repo / "tracked.txt").write_text("old\n", encoding="utf-8")
            run_command("git add", ["git", "add", "tracked.txt"], cwd=repo).raise_for_status()
            run_command("git commit", ["git", "commit", "-m", "base"], cwd=repo).raise_for_status()
            (repo / "tracked.txt").write_text("new\n", encoding="utf-8")
            (repo / "created.txt").write_text("created\n", encoding="utf-8")
            config = parse_config(sample_config(Path(tmp) / "runs"))

            commit_hash = commit_changes(config, context_for(repo))

            self.assertTrue(commit_hash)
            self.assertFalse(has_worktree_changes(context_for(repo)))
            log = run_command("git log", ["git", "log", "-1", "--pretty=%s"], cwd=repo)
            self.assertEqual(log.stdout.strip(), "Agent changes for Asana task 1")


def init_repo(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    run_command("git init", ["git", "init"], cwd=repo).raise_for_status()
    run_command("git config name", ["git", "config", "user.name", "Test"], cwd=repo).raise_for_status()
    run_command(
        "git config email",
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
    ).raise_for_status()
    return repo


def context_for(repo: Path) -> RunContext:
    task = AgentTask(gid="1", name="Task", notes="", eligible=True)
    return RunContext(
        task=task,
        run_id="run",
        branch="branch",
        base_branch="main",
        repo_path=repo,
        run_root=repo,
        worktree=repo,
        logs_dir=repo,
        implementer="codex",
    )


if __name__ == "__main__":
    unittest.main()
