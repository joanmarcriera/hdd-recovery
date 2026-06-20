"""Unit tests for lib/encrypted.py — encrypted-container detection helpers.
Offline, no fixtures, no source media: all inputs are synthetic bytes."""
import os
import struct
import unittest

from _loader import load_module

enc = load_module("lib/encrypted.py")


def _zip_header(encrypted: bool) -> bytes:
    flag = 0x0001 if encrypted else 0x0000
    return b"PK\x03\x04" + struct.pack("<HH", 20, flag) + b"\x00" * 20


class TestClassifyHeader(unittest.TestCase):
    def test_luks(self):
        d = enc.classify_header(b"LUKS\xba\xbe\x00\x01" + b"\x00" * 64)
        self.assertEqual(d.kind, "luks")
        self.assertGreaterEqual(d.confidence, 95)

    def test_bitlocker(self):
        d = enc.classify_header(b"\xeb\x52\x90-FVE-FS-" + b"\x00" * 64)
        self.assertEqual(d.kind, "bitlocker")

    def test_kdbx(self):
        d = enc.classify_header(b"\x03\xd9\xa2\x9a" + b"\x00" * 64)
        self.assertEqual(d.kind, "kdbx")

    def test_pgp_private_key(self):
        d = enc.classify_header(b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n")
        self.assertEqual(d.kind, "pgp-privkey")

    def test_pgp_message(self):
        d = enc.classify_header(b"-----BEGIN PGP MESSAGE-----\n")
        self.assertEqual(d.kind, "pgp-message")

    def test_encrypted_zip(self):
        d = enc.classify_header(_zip_header(encrypted=True))
        self.assertEqual(d.kind, "zip-encrypted")

    def test_plain_zip_is_not_flagged(self):
        self.assertIsNone(enc.classify_header(_zip_header(encrypted=False)))

    def test_7z_and_rar(self):
        self.assertEqual(enc.classify_header(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 4).kind,
                         "7z-archive")
        self.assertEqual(enc.classify_header(b"Rar!\x1a\x07\x00" + b"\x00" * 4).kind,
                         "rar-archive")

    def test_unknown(self):
        self.assertIsNone(enc.classify_header(b"\x00" * 64))
        self.assertIsNone(enc.classify_header(b"PK"))  # too short


class TestClassify(unittest.TestCase):
    def test_signature_beats_extension(self):
        d = enc.classify("vault.bin", b"LUKS\xba\xbe\x00\x02" + b"\x00" * 64)
        self.assertEqual(d.kind, "luks")

    def test_veracrypt_extension_with_high_entropy(self):
        blob = os.urandom(1024)
        d = enc.classify("backup.hc", blob, size=200 * 1024 * 1024)
        self.assertEqual(d.kind, "veracrypt")
        self.assertGreaterEqual(d.confidence, 60)

    def test_named_container_low_entropy_is_downgraded(self):
        d = enc.classify("notes.hc", b"hello world " * 30, size=4096)
        self.assertEqual(d.kind, "veracrypt")
        self.assertLessEqual(d.confidence, 30)

    def test_no_hint_no_signature(self):
        self.assertIsNone(enc.classify("readme.txt", b"just text\n", size=10))


class TestEntropy(unittest.TestCase):
    def test_zero_entropy(self):
        self.assertEqual(enc.shannon_entropy(b"\x00" * 4096), 0.0)

    def test_random_high_entropy(self):
        self.assertGreater(enc.shannon_entropy(os.urandom(4096)), 7.5)

    def test_empty(self):
        self.assertEqual(enc.shannon_entropy(b""), 0.0)


class TestPartitionParsing(unittest.TestCase):
    def _mbr(self, entries: list[tuple[int, int, int]]) -> bytes:
        # entries: list of (type_byte, start_lba, num_sectors)
        buf = bytearray(512)
        for i, (ptype, start, count) in enumerate(entries):
            off = 446 + i * 16
            buf[off + 4] = ptype
            struct.pack_into("<II", buf, off + 8, start, count)
        buf[510:512] = b"\x55\xaa"
        return bytes(buf)

    def test_mbr_single_partition(self):
        mbr = self._mbr([(0x83, 2048, 1000)])
        parts = enc.parse_mbr_partitions(mbr)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].start_offset, 2048 * 512)
        self.assertEqual(parts[0].size, 1000 * 512)
        self.assertEqual(parts[0].type_hint, "mbr:0x83")

    def test_mbr_requires_boot_signature(self):
        mbr = bytearray(self._mbr([(0x83, 2048, 1000)]))
        mbr[510:512] = b"\x00\x00"
        self.assertEqual(enc.parse_mbr_partitions(bytes(mbr)), [])

    def test_protective_mbr_detected(self):
        mbr = self._mbr([(0xEE, 1, 0xFFFFFFFF)])
        self.assertTrue(enc.is_protective_mbr(mbr))
        self.assertFalse(enc.is_protective_mbr(self._mbr([(0x83, 2048, 1000)])))

    def test_gpt_single_partition(self):
        header = bytearray(92)
        header[0:8] = b"EFI PART"
        struct.pack_into("<I", header, 80, 1)    # num entries
        struct.pack_into("<I", header, 84, 128)  # entry size
        entry = bytearray(128)
        entry[0:16] = b"\x11" * 16               # non-zero type GUID
        struct.pack_into("<QQ", entry, 32, 2048, 4047)  # first/last LBA
        parts = enc.parse_gpt_partitions(bytes(header), bytes(entry))
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].start_offset, 2048 * 512)
        self.assertEqual(parts[0].size, 2000 * 512)


if __name__ == "__main__":
    unittest.main()
