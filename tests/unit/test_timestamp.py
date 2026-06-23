"""Unit tests for lib/timestamp.py (shared utc_now helper, de-dup cycle 1)."""
import re
import unittest

from lib.timestamp import utc_now

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class TestUtcNow(unittest.TestCase):
    def test_matches_iso8601_second_precision_utc(self):
        # Must match the exact format produced by lib/common.sh::timestamp_utc
        # (no fractional seconds, trailing 'Z'), since both write the same DB.
        self.assertRegex(utc_now(), _ISO_Z)

    def test_returns_str(self):
        self.assertIsInstance(utc_now(), str)


if __name__ == "__main__":
    unittest.main()
