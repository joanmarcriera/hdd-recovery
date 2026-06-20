"""Unit tests for lib/serve_ui.py (#18 extraction)."""
import unittest

from _loader import load_module

ui = load_module("lib/serve_ui.py")


class TestServeUi(unittest.TestCase):
    def test_escape_and_null_display(self):
        self.assertEqual(ui.h("<x>"), "&lt;x&gt;")
        self.assertIn("NULL", ui.h(None))

    def test_badge_status_classes(self):
        self.assertIn("badge ok", ui.badge("ok"))
        self.assertIn("badge partial", ui.badge("interrupted"))
        self.assertIn("badge failed", ui.badge("timeout"))
        self.assertIn("badge pending", ui.badge("unknown"))

    def test_table_html_escapes_and_truncates(self):
        out = ui.table_html(["c"], [["<unsafe>" * 80]], max_cell=10)
        self.assertIn("&lt;unsafe", out)
        self.assertIn("…", out)

    def test_page_contains_title_nav_and_footer(self):
        out = ui.page("Title", "<p>body</p>", db_name="/tmp/x.analysis.sqlite")
        self.assertIn("<title>Title", out)
        self.assertIn("/db?db=", out)
        self.assertIn("hdd-forensics", out)

    def test_human_size_units(self):
        self.assertEqual(ui.human_size(0), "—")
        self.assertIn("KB", ui.human_size(2048))
        self.assertIn("GB", ui.human_size(149 * 1024**3))


if __name__ == "__main__":
    unittest.main()
