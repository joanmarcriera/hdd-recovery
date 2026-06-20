"""Unit tests for the shared async subprocess watchdog."""
import asyncio
import os
import signal
import sys
import time
import unittest

from _loader import load_module

runs = load_module("lib/runs.py")
wd = load_module("lib/watchdog.py")


def _run(coro):
    return asyncio.run(coro)


class TestWatchdog(unittest.TestCase):
    def test_normal_exit(self):
        result = _run(wd.run_command_async(["true"]))

        self.assertEqual(result.rc, 0)
        self.assertFalse(result.timed_out)

    def test_wall_timeout_kills_promptly(self):
        t0 = time.time()
        events = []

        result = _run(
            wd.run_command_async(
                ["sleep", "30"],
                wall_timeout=1,
                log_event=events.append,
            )
        )

        self.assertEqual(result.rc, wd.TIMEOUT_RC)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.timeout_kind, "wall")
        self.assertLess(time.time() - t0, 6)
        self.assertTrue(any("TIMEOUT after 1s" in e for e in events))

    def test_idle_timeout_after_output(self):
        outputs = []
        script = (
            "import time\n"
            "print('READY', flush=True)\n"
            "time.sleep(30)\n"
        )

        result = _run(
            wd.run_command_async(
                [sys.executable, "-c", script],
                idle_timeout=1,
                on_output=outputs.append,
            )
        )

        self.assertEqual(result.rc, wd.TIMEOUT_RC)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.timeout_kind, "idle")
        self.assertIn("READY", "".join(outputs))

    def test_progress_timeout_ignores_stdout_chatter(self):
        script = (
            "import time\n"
            "end = time.time() + 30\n"
            "while time.time() < end:\n"
            "    print('still noisy', flush=True)\n"
            "    time.sleep(0.1)\n"
        )

        result = _run(
            wd.run_command_async(
                [sys.executable, "-c", script],
                progress_timeout=1,
                progress_interval=0.1,
                progress_probe=lambda: 0,
            )
        )

        self.assertEqual(result.rc, wd.TIMEOUT_RC)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.timeout_kind, "progress")

    def test_progress_advance_resets_deadline(self):
        values = {"n": 0}
        seen = []

        def probe():
            values["n"] += 1
            return values["n"]

        result = _run(
            wd.run_command_async(
                [sys.executable, "-c", "import time; time.sleep(0.8)"],
                progress_timeout=0.3,
                progress_interval=0.1,
                progress_probe=probe,
                on_progress=seen.append,
            )
        )

        self.assertEqual(result.rc, 0)
        self.assertFalse(result.timed_out)
        self.assertGreaterEqual(len(seen), 2)

    def test_timeout_kills_child_process_group(self):
        outputs = []
        script = (
            "import subprocess, time\n"
            "child = subprocess.Popen(['sleep', '30'])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(30)\n"
        )

        result = _run(
            wd.run_command_async(
                [sys.executable, "-c", script],
                wall_timeout=1,
                on_output=outputs.append,
            )
        )

        self.assertEqual(result.rc, wd.TIMEOUT_RC)
        child_pid = int("".join(outputs).strip().splitlines()[0])
        try:
            for _ in range(20):
                if not runs.pid_alive(child_pid, markers=("sleep",)):
                    break
                time.sleep(0.1)
            self.assertFalse(runs.pid_alive(child_pid, markers=("sleep",)))
        finally:
            if runs.pid_alive(child_pid, markers=("sleep",)):
                os.kill(child_pid, signal.SIGKILL)

    def test_cancelled_run_kills_process_group(self):
        async def scenario():
            seen = {}

            def on_start(process):
                seen["pid"] = process.pid

            task = asyncio.create_task(
                wd.run_command_async(["sleep", "30"], on_start=on_start)
            )
            for _ in range(20):
                if "pid" in seen:
                    break
                await asyncio.sleep(0.05)
            self.assertIn("pid", seen)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return seen["pid"]

        pid = _run(scenario())
        try:
            for _ in range(20):
                if not runs.pid_alive(pid, markers=("sleep",)):
                    break
                time.sleep(0.1)
            self.assertFalse(runs.pid_alive(pid, markers=("sleep",)))
        finally:
            if runs.pid_alive(pid, markers=("sleep",)):
                os.kill(pid, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
