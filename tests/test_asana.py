import unittest
from pathlib import Path

from orchestra.asana import is_claimable, parse_task
from orchestra.config import parse_config
from tests.test_config import sample_config


class AsanaParsingTests(unittest.TestCase):
    def test_parse_task_custom_fields(self) -> None:
        config = parse_config(sample_config(Path("/tmp/work")))
        task = parse_task(
            {
                "gid": "120",
                "name": "Implement feature",
                "notes": "Ship the first version",
                "permalink_url": "https://app.asana.com/0/1/120",
                "custom_fields": [
                    field("eligible", enum_gid="yes", enum_name="Yes"),
                    field("preferred", enum_gid="claude", enum_name="Claude"),
                    field("repo", text="example"),
                    field("base", text="main"),
                    field("status", enum_gid="queued", enum_name="Queued"),
                    field("run", text=""),
                    field("runner", text=""),
                    field("assigned_runner", text="test-runner"),
                ],
            },
            config.asana,
        )

        self.assertTrue(task.eligible)
        self.assertEqual(task.preferred_agent, "claude")
        self.assertEqual(task.status, "queued")
        self.assertEqual(task.assigned_runner, "test-runner")
        self.assertTrue(is_claimable(task, "test-runner"))
        self.assertFalse(is_claimable(task, "someone-else"))

    def test_unassigned_task_is_claimable_by_any_runner(self) -> None:
        config = parse_config(sample_config(Path("/tmp/work")))
        task = parse_task(
            {
                "gid": "121",
                "name": "Implement feature",
                "notes": "",
                "custom_fields": [
                    field("eligible", enum_gid="yes", enum_name="Yes"),
                    field("preferred", enum_gid="codex", enum_name="Codex"),
                    field("repo", text="example"),
                    field("base", text="main"),
                    field("status", enum_gid="queued", enum_name="Queued"),
                    field("run", text=""),
                    field("runner", text=""),
                    field("assigned_runner", text=""),
                ],
            },
            config.asana,
        )

        self.assertTrue(is_claimable(task, "any-runner"))


def field(gid: str, text: str | None = None, enum_gid: str | None = None, enum_name: str | None = None):
    data = {"gid": gid, "display_value": text, "text_value": text}
    if enum_gid:
        data["enum_value"] = {"gid": enum_gid, "name": enum_name or enum_gid}
        data["display_value"] = enum_name or enum_gid
    return data


if __name__ == "__main__":
    unittest.main()
