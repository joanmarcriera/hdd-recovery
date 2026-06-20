"""Unit tests for TUI monitor activity and stuckness classification."""
import unittest

from _loader import load_module

activity = load_module("tui/activity.py")


class TestActivityClassification(unittest.TestCase):
    def test_active_from_cpu(self):
        judgment = activity.classify_activity(
            activity.ActivitySample(cpu_pct=3.2, last_progress_age_s=500),
            active_cpu_pct=1.0,
        )

        self.assertEqual(judgment.state, "active")
        self.assertEqual(judgment.source, "cpu 3%")

    def test_active_from_io(self):
        judgment = activity.classify_activity(
            activity.ActivitySample(io_mib_s=0.25, last_progress_age_s=500),
            active_io_mib_s=0.1,
        )

        self.assertEqual(judgment.state, "active")
        self.assertEqual(judgment.source, "io 0.2 MB/s")

    def test_idle_from_stale_progress_below_stuck_threshold(self):
        judgment = activity.classify_activity(
            activity.ActivitySample(last_progress_age_s=600),
            recent_progress_s=120,
            stuck_s=3600,
        )

        self.assertEqual(judgment.state, "idle")
        self.assertEqual(judgment.label, "idle 10m 00s")
        self.assertEqual(judgment.source, "last progress")

    def test_probably_stuck_from_old_progress(self):
        judgment = activity.classify_activity(
            activity.ActivitySample(last_progress_age_s=4000),
            stuck_s=3600,
        )

        self.assertEqual(judgment.state, "probably stuck")
        self.assertEqual(judgment.source, "no progress 1h 06m")

    def test_no_output_when_no_progress_timestamp(self):
        judgment = activity.classify_activity(
            activity.ActivitySample(started_age_s=300),
            stuck_s=3600,
        )

        self.assertEqual(judgment.state, "no output")
        self.assertEqual(judgment.label, "no output 5m 00s")
        self.assertEqual(judgment.source, "started")


class TestProcessActivitySampler(unittest.TestCase):
    def test_process_activity_delta(self):
        counters = [
            activity.ProcessCounters(cpu_ticks=100, io_bytes=1_048_576),
            activity.ProcessCounters(cpu_ticks=150, io_bytes=3_145_728),
        ]
        times = [10.0, 12.0]

        def reader(_pid):
            return counters.pop(0)

        def time_fn():
            return times.pop(0)

        sampler = activity.ProcessActivitySampler(reader=reader, time_fn=time_fn, hz=100)
        first = sampler.sample(123)
        second = sampler.sample(123)

        self.assertEqual(first.cpu_pct, 0.0)
        self.assertAlmostEqual(second.cpu_pct, 25.0)
        self.assertAlmostEqual(second.io_mib_s, 1.0)


if __name__ == "__main__":
    unittest.main()
