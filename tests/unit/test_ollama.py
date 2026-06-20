"""Unit tests for tui/ollama.py — Ollama availability probing (#14).
Offline: the HTTP opener is injected, so no network and no Ollama needed."""
import io
import json
import unittest

from _loader import load_module

ol = load_module("tui/ollama.py")


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _ok_opener(models):
    def opener(req, timeout=0):
        return FakeResp(json.dumps({"models": models}).encode())
    return opener


def _fail_opener(exc):
    def opener(req, timeout=0):
        raise exc
    return opener


class TestParse(unittest.TestCase):
    def test_dedup_and_strip(self):
        urls = ol.parse_ollama_urls(" http://a:11434/ , http://b:11434 , http://a:11434 ")
        self.assertEqual(urls, ["http://a:11434", "http://b:11434"])

    def test_empty(self):
        self.assertEqual(ol.parse_ollama_urls(""), [])


class TestProbe(unittest.TestCase):
    def test_reachable_reports_model_count(self):
        s = ol.probe_host("http://h:11434",
                          opener=_ok_opener([{"name": "llava:7b"}, {"name": "x"}]))
        self.assertTrue(s.ok)
        self.assertIn("2 model(s)", s.detail)

    def test_unreachable_reports_error(self):
        s = ol.probe_host("http://h:11434",
                          opener=_fail_opener(ConnectionRefusedError("refused")))
        self.assertFalse(s.ok)
        self.assertTrue(s.detail)

    def test_probe_hosts_and_any_reachable(self):
        statuses = [
            ol.HostStatus("http://a", True, "1 model(s)"),
            ol.HostStatus("http://b", False, "timeout"),
        ]
        self.assertTrue(ol.any_reachable(statuses))
        self.assertEqual(ol.summarize(statuses), "1/2 host(s) reachable")

    def test_none_reachable(self):
        statuses = ol.probe_hosts(
            "http://a:11434,http://b:11434",
            opener=_fail_opener(TimeoutError("timed out")))
        self.assertEqual(len(statuses), 2)
        self.assertFalse(ol.any_reachable(statuses))


if __name__ == "__main__":
    unittest.main()
