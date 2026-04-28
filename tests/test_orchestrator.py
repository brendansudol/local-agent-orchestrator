from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from orchestra.config import parse_config
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
            self.assertTrue((outcome.logs_dir / "verification.json").exists())
            self.assertTrue((outcome.logs_dir / "review_result.json").exists())


if __name__ == "__main__":
    unittest.main()

