"""Unit tests for tui/screens/wizard._render_conf — the ddrescue job config the
TUI wizard writes. That file is later `source`d by bash (as root) in
bin/ddrescue-run.sh and tui/config.load_job_conf, so every value must come back
literally and none may execute. Offline: textual is stubbed (CI does not
install it), the conf is only sourced by bash in a temp dir, no devices."""
import sys
import tempfile
import types
import unittest
from pathlib import Path

from _loader import REPO_ROOT, isolate_tui_paths, load_module

_TUI = str(REPO_ROOT / "tui")
if _TUI not in sys.path:
    sys.path.insert(0, _TUI)


def _stub_textual():
    """Install minimal stand-ins for the textual/rich names wizard.py imports
    at module level; _render_conf itself uses none of them."""
    class _Any:
        def __init__(self, *a, **k):
            pass

    names = {
        "rich": [], "rich.markup": ["escape"],
        "textual": [], "textual.app": ["ComposeResult"],
        "textual.binding": ["Binding"],
        "textual.containers": ["Horizontal", "Vertical"],
        "textual.screen": ["Screen"],
        "textual.widgets": ["Button", "Footer", "Header", "Input", "Label",
                            "ListItem", "ListView", "Static"],
    }
    for mod, attrs in names.items():
        if mod in sys.modules:
            continue
        m = types.ModuleType(mod)
        for a in attrs:
            setattr(m, a, (lambda s: s) if a == "escape" else type(a, (_Any,), {}))
        sys.modules[mod] = m


try:
    import textual  # noqa: F401
except ImportError:
    _stub_textual()

with isolate_tui_paths():
    wizard = load_module("tui/screens/wizard.py")
    import config  # noqa: E402  (tui/config.py)

BY_PATH = "/dev/disk/by-path/pci-0000:00:17.0-ata-3"


def _render(**over):
    kw = dict(job_name="Old laptop", date_tag="20261101", source_dev="/dev/sdd",
              source_by_path=BY_PATH, source_model="WDC WD5000",
              source_serial="WD-123", source_size_bytes=500107862016,
              basename="old_laptop_sdd")
    kw.update(over)
    return wizard._render_conf(**kw)


class TestRenderConf(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)

    def _source(self, text):
        """Write the conf to the temp dir and source it with bash via the
        TUI's own loader. Returns (values, temp_dir_listing_after)."""
        p = self.tmp / "job.conf"
        p.write_text(text)
        vals = config.load_job_conf(p)
        return vals, sorted(x.name for x in self.tmp.iterdir())

    def test_plain_values_round_trip(self):
        vals, _ = self._source(_render())
        self.assertEqual(vals["JOB_NAME"], "Old laptop")
        self.assertEqual(vals["SOURCE_DEV"], "/dev/sdd")
        self.assertEqual(vals["SOURCE_MODEL"], "WDC WD5000")
        self.assertEqual(vals["SOURCE_SERIAL"], "WD-123")
        self.assertEqual(vals["SOURCE_SIZE_BYTES"], "500107862016")
        self.assertEqual(vals["BASENAME"], "old_laptop_sdd")
        self.assertEqual(vals["IMAGE_FILE"],
                         "/mnt/recovery16tb/recovery/images/old_laptop_sdd.img")
        self.assertEqual(vals["MAP_FILE"],
                         "/mnt/recovery16tb/recovery/logs/old_laptop_sdd.map")

    def test_by_path_is_the_link_name_ddrescue_run_expects(self):
        # bin/ddrescue-run.sh checks -L "/dev/disk/by-path/$SOURCE_BY_PATH",
        # so the conf must hold the link name, not the absolute path.
        vals, _ = self._source(_render())
        self.assertEqual(vals["SOURCE_BY_PATH"], "pci-0000:00:17.0-ata-3")
        self.assertEqual("/dev/disk/by-path/" + vals["SOURCE_BY_PATH"], BY_PATH)

    def test_hostile_values_are_literal_and_never_execute(self):
        # JOB_NAME is free operator text; model/serial come from the disk's
        # own firmware via lsblk — i.e. from the evidence being recovered.
        t = self.tmp
        hostile = {
            "job_name": f'x"; touch {t}/PWNED_JOB; echo "',
            "source_model": f"$(touch {t}/PWNED_MODEL)",
            "source_serial": f"`touch {t}/PWNED_SERIAL`'\\",
        }
        text = _render(**hostile)
        vals, listing = self._source(text)
        self.assertEqual(listing, ["job.conf"], "conf executed code when sourced")
        self.assertEqual(vals["JOB_NAME"], hostile["job_name"])
        self.assertEqual(vals["SOURCE_MODEL"], hostile["source_model"])
        self.assertEqual(vals["SOURCE_SERIAL"], hostile["source_serial"])
        # and the rest of the conf is unaffected
        self.assertEqual(vals["SOURCE_DEV"], "/dev/sdd")
        self.assertEqual(vals["BASENAME"], "old_laptop_sdd")

    def test_empty_size_and_model(self):
        vals, _ = self._source(_render(source_size_bytes=0, source_model=""))
        self.assertEqual(vals["SOURCE_MODEL"], "")
        self.assertEqual(vals["SOURCE_SIZE_BYTES"], "0")


class TestLoadJobConf(unittest.TestCase):
    def test_missing_conf_yields_empty_values(self):
        vals = config.load_job_conf(Path("/nonexistent/job.conf"))
        self.assertEqual(vals.get("SOURCE_DEV"), "")
        self.assertEqual(vals.get("BASENAME"), "")


if __name__ == "__main__":
    unittest.main()
