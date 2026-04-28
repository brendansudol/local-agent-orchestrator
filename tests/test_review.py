import unittest

from orchestra.review import ReviewParseError, parse_review_verdict


class ReviewParsingTests(unittest.TestCase):
    def test_parse_direct_json_verdict(self) -> None:
        verdict = parse_review_verdict('{"verdict":"ok","findings":[]}')

        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.findings, [])

    def test_parse_wrapped_json_verdict(self) -> None:
        output = '{"result":"```json\\n{\\"verdict\\":\\"blocked\\",\\"findings\\":[\\"bad\\"]}\\n```"}'

        verdict = parse_review_verdict(output)

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.findings, [{"message": "bad"}])

    def test_parse_missing_verdict_raises(self) -> None:
        with self.assertRaises(ReviewParseError):
            parse_review_verdict("looks fine")


if __name__ == "__main__":
    unittest.main()

