/*
 * wallets.yar — YARA rules for cryptocurrency wallet file formats.
 *
 * Run via: image-yara-scan.sh
 * Scope:   recovered corpus (not raw image — too slow for binary regex).
 *
 * Confidence levels:
 *   high   — very low false-positive rate; format is well-specified
 *   medium — occasional false positives; requires content review
 *   low    — heuristic only; use as a lead, not a conclusion
 */

rule ethereum_keystore
{
    meta:
        description  = "Ethereum Web3 Secret Storage JSON keystore"
        url          = "https://eips.ethereum.org/EIPS/eip-55"
        score        = 85
        confidence   = "high"

    strings:
        $crypto  = "\"crypto\""     ascii wide nocase
        $kdf     = "\"kdfparams\""  ascii wide nocase
        $cipher  = "\"ciphertext\"" ascii wide nocase
        $iv      = "\"iv\""         ascii wide nocase

    condition:
        filesize < 10KB and 3 of them
}

rule electrum_wallet_json
{
    meta:
        description = "Electrum wallet JSON (all versions)"
        url         = "https://electrum.org"
        score       = 80
        confidence  = "high"

    strings:
        $sv    = "\"seed_version\"" ascii wide
        $ks    = "\"keystore\""     ascii wide
        $wtype = "\"wallet_type\""  ascii wide

    condition:
        filesize < 10MB and all of them
}

rule metamask_encrypted_vault
{
    meta:
        description = "MetaMask encrypted vault (browser extension storage)"
        url         = "https://metamask.io"
        score       = 85
        confidence  = "high"

    strings:
        $data  = "\"data\""  ascii wide nocase
        $iv    = "\"iv\""    ascii wide nocase
        $salt  = "\"salt\""  ascii wide nocase
        $vault = "\"vault\"" ascii wide nocase

    condition:
        filesize < 1MB and (($vault and $data and $iv) or all of them)
}

rule bitcoin_core_wallet_dat
{
    meta:
        description = "Bitcoin Core wallet.dat — Berkeley DB B-tree key markers"
        url         = "https://bitcoin.org"
        score       = 75
        confidence  = "medium"
        notes       = "Matches BDB files with Bitcoin Core key names. Other BDB apps may also match."

    strings:
        // These are the B-tree record key names used by Bitcoin Core's CWalletDB.
        // mkey = master key, ckey = encrypted key, wkey = watch-only key.
        $mkey = "mkey" fullword ascii
        $ckey = "ckey" fullword ascii
        $wkey = "wkey" fullword ascii
        $pool = "pool" fullword ascii

    condition:
        filesize < 500MB and ($mkey or $ckey or $wkey) and $pool
}

rule exodus_wallet_passphrase_hint
{
    meta:
        description = "Exodus wallet passphrase hint or seed backup JSON"
        url         = "https://www.exodus.com"
        score       = 70
        confidence  = "medium"

    strings:
        $seed    = "\"seedPhrase\""    ascii wide nocase
        $pass    = "\"passphrase\""    ascii wide nocase
        $exodus  = "\"exodus\""        ascii wide nocase
        $entropy = "\"entropyHex\""    ascii wide nocase

    condition:
        filesize < 1MB and 2 of them
}

rule trust_wallet_keystore
{
    meta:
        description = "Trust Wallet / generic mobile wallet keystore"
        url         = "https://trustwallet.com"
        score       = 65
        confidence  = "medium"

    strings:
        $kdf     = "\"kdf\""        ascii wide nocase
        $mac     = "\"mac\""        ascii wide nocase
        $address = "\"address\""    ascii wide nocase
        $cipher  = "\"ciphertext\"" ascii wide nocase

    condition:
        filesize < 10KB and all of them
}
