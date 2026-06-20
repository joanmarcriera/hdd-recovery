"""Unit tests for lib/serve_mapfile.py — ddrescue mapfile parsing + SVG (#18).
This logic was untested while embedded in image-serve.py; extracting it made it
testable. Offline, no fixtures beyond a temp mapfile."""
import tempfile
import unittest
from pathlib import Path

from _loader import load_module

mf = load_module("lib/serve_mapfile.py")


SAMPLE = """\
# Mapfile. Created by GNU ddrescue version 1.27
# Command line: ddrescue /dev/sdd disk.img disk.map
# Start time:   2026-05-01 10:00:00
# Current time: 2026-05-01 12:00:00
# current_pos  current_status  current_pass
0x00001000     ?               1
#      pos        size  status
0x00000000  0x00001000  +
0x00001000  0x00000200  ?
0x00001200  0x00010000  -
"""


class TestParseMapfile(unittest.TestCase):
    def _parse(self, text):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "disk.map"
            p.write_text(text)
            return mf.parse_mapfile(str(p))

    def test_meta_and_blocks(self):
        meta, blocks = self._parse(SAMPLE)
        self.assertEqual(meta["current_pos"], 0x1000)
        self.assertEqual(meta["current_status"], "?")
        self.assertEqual(meta["current_pass"], 1)
        self.assertEqual(meta["command_line"],
                         "ddrescue /dev/sdd disk.img disk.map")
        self.assertEqual(blocks, [
            (0x0, 0x1000, "+"),
            (0x1000, 0x200, "?"),
            (0x1200, 0x10000, "-"),
        ])

    def test_missing_file_is_empty(self):
        meta, blocks = mf.parse_mapfile("/nonexistent/x.map")
        self.assertEqual(blocks, [])
        self.assertFalse(meta["finished"])


class TestMapSvg(unittest.TestCase):
    def test_empty(self):
        svg, stats = mf.map_svg([])
        self.assertIn("No blocks", svg)
        self.assertEqual(stats, {})

    def test_renders_and_counts_bytes(self):
        blocks = [(0, 1024, "+"), (1024, 512, "?")]
        svg, stats = mf.map_svg(blocks)
        self.assertIn("<svg", svg)
        self.assertEqual(stats["total_size"], 1536)
        self.assertEqual(stats["bytes"]["+"], 1024)
        self.assertEqual(stats["bytes"]["?"], 512)

    def test_worst_status_wins_in_cell(self):
        # One huge good block and a tiny bad block at the start: the first cell
        # must render bad-sector colour (priority), not rescued.
        blocks = [(0, 10_000_000, "+"), (0, 512, "?")]
        svg, _ = mf.map_svg(blocks, cols=1, cell_w=1, cell_h=1)
        bad_colour = mf.MAP_STATUS["?"][0]
        self.assertIn(f'fill="{bad_colour}"', svg)


if __name__ == "__main__":
    unittest.main()
