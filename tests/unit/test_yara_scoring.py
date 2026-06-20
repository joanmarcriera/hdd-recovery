"""Pins config/yara/scoring.conf to the historical YARA rule scores (#21), so a
typo in the externalized score map can't silently change wallet detection."""
import unittest

from _loader import REPO_ROOT

SCORING_CONF = REPO_ROOT / "config" / "yara" / "scoring.conf"

# The scores that were hardcoded in image-yara-scan.sh before externalization.
EXPECTED = {
    "ethereum_keystore": 85,
    "electrum_wallet_json": 80,
    "metamask_encrypted_vault": 85,
    "bitcoin_core_wallet_dat": 75,
    "exodus_wallet_passphrase_hint": 70,
    "trust_wallet_keystore": 65,
    "bip32_extended_key": 92,
    "wif_private_key": 88,
    "armored_private_key": 60,
    "mnemonic_wordlist_file": 55,
}


def _parse(path):
    """Same key=value parsing the script uses."""
    rules, default = {}, None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        try:
            iv = int(v)
        except ValueError:
            continue
        if k == "default":
            default = iv
        else:
            rules[k] = iv
    return rules, default


class TestYaraScoringConf(unittest.TestCase):
    def test_conf_matches_historical_scores(self):
        rules, default = _parse(SCORING_CONF)
        self.assertEqual(rules, EXPECTED)
        self.assertEqual(default, 50)


if __name__ == "__main__":
    unittest.main()
