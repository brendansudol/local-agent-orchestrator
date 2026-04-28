from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from orchestra.config import parse_config
from orchestra.models import CommandResult, RunContext
from orchestra.orchestrator import Orchestrator
from tests.test_config import sample_config


class OrchestratorTests(unittest.TestCase):
    def test_dry_run_writes_artifacts_and_completes(self) -> None:
        with TemporaryDirectory() as tmp:
            config = parse_config(sample_config(Path(tmp) / "runs"))
            outcome = Orchestrator(config, dry_run=True).run_loop(once=True)

            self.assertTrue(outcome.handled)
            self.assertTrue(outcome.ok)
            self.assertIsNotNone(outcome.logs_dir)
            assert outcome.logs_dir is not None
            self.assertTrue((outcome.logs_dir / "prompt.md").exists())
            self.assertTrue((outcome.logs_dir / "agent_summary.md").exists())
            self.assertTrue((outcome.logs_dir / "verification.json").exists())
            self.assertTrue((outcome.logs_dir / "review_result.json").exists())

    def test_success_comment_includes_captured_agent_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            config = parse_config(sample_config(Path(tmp) / "runs"))
            orchestrator = Orchestrator(config, dry_run=True)
            orchestrator.runner = SummaryWritingRunner()

            outcome = orchestrator.run_loop(once=True)

            self.assertTrue(outcome.ok)
            self.assertIsNotNone(outcome.logs_dir)
            assert outcome.logs_dir is not None
            summary = (outcome.logs_dir / "agent_summary.md").read_text(encoding="utf-8")
            comment = orchestrator.queue.comments[-1]
            self.assertIn("## Agent Summary", summary)
            self.assertIn("## Agent Summary", comment)
            self.assertIn("src/orchestra/prompts.py", comment)
            self.assertNotIn("raw stdout", comment)


class SummaryWritingRunner:
    def run(
        self,
        agent_name: str,
        prompt: str,
        context: RunContext,
        *,
        review: bool = False,
        label: str | None = None,
    ) -> CommandResult:
        if review:
            return CommandResult(
                name=f"{label}:{agent_name}",
                command=["review"],
                returncode=0,
                stdout='{"verdict":"ok","findings":[]}\n',
            )

        (context.logs_dir / "final.md").write_text(
            (
                "## Agent Summary\n\n"
                "- Files changed: src/orchestra/prompts.py\n"
                "- Implementation summary: Added summary prompt requirements.\n"
                "- Tests run and results: unit tests passed.\n"
            ),
            encoding="utf-8",
        )
        return CommandResult(
            name=f"{label}:{agent_name}",
            command=["implement"],
            returncode=0,
            stdout="raw stdout that should not be posted",
        )


if __name__ == "__main__":
    unittest.main()
