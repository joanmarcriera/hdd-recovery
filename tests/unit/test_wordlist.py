"""Unit tests for lib/wordlist.py — targeted wordlist generation.
Offline, no fixtures: inputs are synthetic (feature_file, value) rows."""
import unittest

from _loader import load_module

wl = load_module("lib/wordlist.py")


class TestDeriveCandidates(unittest.TestCase):
    def test_email_local_and_parts(self):
        c = wl.derive_candidates("email.txt", "john.doe@personalsite.org")
        self.assertIn("john.doe", c)
        self.assertIn("john", c)
        self.assertIn("doe", c)
        self.assertIn("johndoe", c)       # collapsed
        self.assertIn("personalsite", c)  # non-common domain label

    def test_common_mail_domain_dropped(self):
        c = wl.derive_candidates("email.txt", "alice@gmail.com")
        self.assertIn("alice", c)
        self.assertNotIn("gmail", c)

    def test_domain_feature(self):
        c = wl.derive_candidates("domain.txt", "shop.myhomelab.net")
        self.assertIn("myhomelab", c)

    def test_two_part_tld(self):
        c = wl.derive_candidates("url.txt", "www.example.co.uk")
        self.assertIn("example", c)

    def test_generic_feature_keeps_token(self):
        c = wl.derive_candidates("winpe_screen_names.txt", "Bitcoin_Bob")
        self.assertIn("Bitcoin_Bob", c)
        self.assertIn("Bitcoin", c)
        self.assertIn("Bob", c)

    def test_empty(self):
        self.assertEqual(wl.derive_candidates("email.txt", ""), [])


class TestBuildWordlist(unittest.TestCase):
    def test_dedup_and_length_filter(self):
        rows = [
            ("email.txt", "john.doe@site.org"),
            ("email.txt", "john.doe@site.org"),   # duplicate row
            ("domain.txt", "a.bc"),               # label too short -> dropped
        ]
        out = wl.build_wordlist(rows, min_len=3)
        # de-duplicated, first occurrence preserved
        self.assertEqual(len(out), len(set(t.lower() for t in out)))
        self.assertIn("john", out)
        self.assertNotIn("bc", out)  # below min_len

    def test_case_insensitive_dedup(self):
        rows = [("names.txt", "Satoshi"), ("names.txt", "satoshi")]
        out = wl.build_wordlist(rows)
        self.assertEqual([t.lower() for t in out].count("satoshi"), 1)


class TestMergeWithBase(unittest.TestCase):
    def test_targeted_first_then_base_without_dupes(self):
        merged = list(wl.merge_with_base(
            ["alice", "password"], iter(["password\n", "123456\n", "alice\n"])))
        self.assertEqual(merged[0], "alice")
        self.assertEqual(merged[1], "password")
        # "password" and "alice" from base are skipped; "123456" kept
        self.assertEqual(merged.count("password"), 1)
        self.assertIn("123456", merged)

    def test_max_total_cap(self):
        merged = list(wl.merge_with_base(
            ["a", "b", "c"], iter(["d\n", "e\n"]), max_total=2))
        self.assertEqual(merged, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
