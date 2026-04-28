import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from orchestra.agent_summary import PLACEHOLDER_SUMMARY, capture_agent_summary
from orchestra.models import CommandResult


class AgentSummaryTests(unittest.TestCase):
    def test_extracts_summary_from_final_message_file(self) -> None:
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            (logs_dir / "final.md").write_text(
                "Done.\n\n## Agent Summary\n\n- Files changed: src/app.py\n",
                encoding="utf-8",
            )

            summary = capture_agent_summary(logs_dir, command_result(stdout="ignored"))

            self.assertIn("## Agent Summary", summary)
            self.assertIn("- Files changed: src/app.py", summary)
            self.assertNotIn("ignored", summary)
            self.assertEqual(
                (logs_dir / "agent_summary.md").read_text(encoding="utf-8"),
                summary + "\n",
            )

    def test_extracts_summary_from_simple_stdout(self) -> None:
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)

            summary = capture_agent_summary(
                logs_dir,
                command_result(
                    stdout=(
                        "Changed src/app.py to validate inputs.\n"
                        "Tests run: python3 -m unittest discover passed.\n"
                    )
                ),
            )

            self.assertIn("## Agent Summary", summary)
            self.assertIn("Changed src/app.py", summary)
            self.assertIn("unittest discover passed", summary)

    def test_extracts_summary_from_json_stdout(self) -> None:
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            stdout = json.dumps(
                {
                    "result": (
                        "```markdown\n"
                        "## Agent Summary\n\n"
                        "- Files changed: src/orchestra/prompts.py\n"
                        "- Tests run and results: passed\n"
                        "```"
                    )
                }
            )

            summary = capture_agent_summary(logs_dir, command_result(stdout=stdout))

            self.assertIn("## Agent Summary", summary)
            self.assertIn("src/orchestra/prompts.py", summary)
            self.assertNotIn("```", summary)

    def test_writes_placeholder_when_no_summary_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            stdout = "\n".join(
                [
                    json.dumps({"type": "event", "delta": "working"}),
                    json.dumps({"type": "done", "usage": {"output_tokens": 10}}),
                ]
            )

            summary = capture_agent_summary(logs_dir, command_result(stdout=stdout))

            self.assertIn("## Agent Summary", summary)
            self.assertIn(PLACEHOLDER_SUMMARY, summary)
            self.assertNotIn('"type": "event"', summary)

    def test_preserves_existing_summary_when_requested(self) -> None:
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            first = capture_agent_summary(
                logs_dir,
                command_result(stdout="Implemented the requested change.\n"),
            )

            second = capture_agent_summary(
                logs_dir,
                command_result(stdout=""),
                preserve_existing=True,
            )

            self.assertEqual(second, first)
            self.assertEqual(
                (logs_dir / "agent_summary.md").read_text(encoding="utf-8"),
                first + "\n",
            )


def command_result(stdout: str = "") -> CommandResult:
    return CommandResult(
        name="implement:codex",
        command=["codex", "exec"],
        returncode=0,
        stdout=stdout,
    )


if __name__ == "__main__":
    unittest.main()
