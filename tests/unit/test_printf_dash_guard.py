"""Lint-style guard against a recurring bash bug.

`printf '- ...'` / `printf '--- ...'` makes bash parse the leading dash(es) as
options ("printf: - : invalid option"), which silently broke generate-report
(and the mapview/backup summaries) until the pipeline first ran them to
completion on a small image. The fix is `printf -- '...'`. This test fails if a
format string starting with '-' is passed to printf without the `--` guard.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# printf followed by a single/double-quoted format whose first char is '-',
# but NOT `printf --`.
_BAD = re.compile(r"""printf\s+(?!--\s)['"]-""")


class TestNoPrintfDashFormat(unittest.TestCase):
    def test_no_unguarded_dash_format(self):
        offenders = []
        for sh in sorted(REPO_ROOT.glob("bin/*.sh")) + sorted(REPO_ROOT.glob("lib/*.sh")):
            for n, line in enumerate(sh.read_text().splitlines(), 1):
                if _BAD.search(line):
                    offenders.append(f"{sh.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "printf with a dash-leading format needs `printf -- '...'`:\n"
            + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
