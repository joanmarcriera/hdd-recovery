"""Unit tests for tui/executor.build_command / command_display — the argv the
TUI hands to create_subprocess_exec for every stage, including the ddrescue
passes. Pure: nothing is launched, no devices, no textual import."""
import shlex
import sys
import unittest
from pathlib import Path

from _loader import REPO_ROOT, isolate_tui_paths

_TUI = str(REPO_ROOT / "tui")
if _TUI not in sys.path:
    sys.path.insert(0, _TUI)

with isolate_tui_paths():
    import executor  # noqa: E402
    import state  # noqa: E402
    from stages import STAGES  # noqa: E402

STAGE = {s.key: s for s in STAGES}


def _disk(root="/data", basename="disk1", conf=True, mapfile=True):
    root = Path(root)
    return state.DiskInfo(
        conf_path=root / "jobs" / f"{basename}.conf" if conf else None,
        basename=basename, job_name=basename, source_model="",
        source_serial="", source_dev="/dev/sdz",
        image_path=root / "images" / f"{basename}.img",
        map_path=root / "logs" / f"{basename}.map" if mapfile else None,
        db_path=root / "db" / f"{basename}.img.analysis.sqlite",
        export_root=root / "exports" / basename,
    )


class TestBuildCommand(unittest.TestCase):
    def test_ddrescue_first_pass_argv(self):
        d = _disk()
        cmd = executor.build_command(d, STAGE["ddrescue-first"])
        self.assertEqual(cmd, [str(executor.BIN_DIR / "ddrescue-run.sh"),
                               str(d.conf_path), "first", "--run"])

    def test_preview_stage_never_carries_run_flag(self):
        cmd = executor.build_command(_disk(), STAGE["ddrescue-preview"])
        self.assertNotIn("--run", cmd)
        self.assertEqual(cmd[-1], "plan")

    def test_empty_flag_value_drops_flag_too(self):
        # init-db: ["{image}", "--map", "{mapfile}"] with no mapfile known
        d = _disk(mapfile=False)
        cmd = executor.build_command(d, STAGE["init-db"])
        self.assertEqual(cmd[1:], [str(d.image_path)])
        self.assertNotIn("--map", cmd)
        d = _disk(mapfile=True)
        self.assertEqual(executor.build_command(d, STAGE["init-db"])[1:],
                         [str(d.image_path), "--map", str(d.map_path)])

    def test_no_stage_ever_produces_an_empty_argument(self):
        for d in (_disk(), _disk(conf=False, mapfile=False)):
            for s in STAGES:
                cmd = executor.build_command(d, s)
                self.assertNotIn("", cmd, s.key)

    def test_manual_stage_has_no_command(self):
        self.assertEqual(executor.build_command(_disk(), STAGE["identify-source"]), [])
        self.assertEqual(executor.command_display(_disk(), STAGE["identify-source"]),
                         "(manual step — no command)")

    def test_ddrescue_without_conf_fails_closed(self):
        # With no job config the {conf} slot is dropped. ddrescue-run.sh then
        # sees the phase name as its config path and refuses ("job config not
        # found"); make sure no device path or extra argument slides in.
        cmd = executor.build_command(_disk(conf=False), STAGE["ddrescue-first"])
        self.assertEqual(cmd[1:], ["first", "--run"])
        self.assertFalse(any(a.startswith("/dev/") for a in cmd))

    def test_hostile_paths_stay_single_arguments(self):
        # Paths come from IMAGE_ROOT/DB_ROOT and job-conf BASENAME; spaces or
        # shell metacharacters must stay inside one argv element, and the
        # displayed command must round-trip through a shell parser unchanged.
        d = _disk(root="/data/my images; rm -rf ~", basename="d$(id)")
        for key in ("ddrescue-first", "init-db", "structure-scan"):
            cmd = executor.build_command(d, STAGE[key])
            self.assertEqual(shlex.split(executor.command_display(d, STAGE[key])),
                             cmd, key)
        cmd = executor.build_command(d, STAGE["structure-scan"])
        self.assertEqual(cmd[1:], [str(d.db_path)])

    def test_ddrescue_stages_auto_confirm_only_ddrescue_ask(self):
        # auto_stdin answers ddrescue's --ask prompt; it must only be set on
        # stages that actually exec ddrescue-run.sh with --run.
        for s in STAGES:
            if s.auto_stdin:
                self.assertEqual(s.script, "ddrescue-run.sh", s.key)
                self.assertIn("--run", s.args_template, s.key)


if __name__ == "__main__":
    unittest.main()
