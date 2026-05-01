/*
 * crypto_keys.yar — YARA rules for raw cryptographic key material.
 *
 * Run via: image-yara-scan.sh
 * Scope:   recovered corpus (extracted/carved files only).
 *
 * Confidence:
 *   high   — format is unambiguous; false positives rare in practice
 *   medium — pattern is right but context matters
 *   low    — heuristic; many false positives expected
 */

rule bip32_extended_key
{
    meta:
        description = "BIP32 extended public or private key (xpub/xprv/zpub/zprv)"
        url         = "https://github.com/bitcoin/bips/blob/master/bip-0032.mediawiki"
        score       = 92
        confidence  = "high"

    strings:
        // BIP32 mainnet private
        $xprv = /xprv[1-9A-HJ-NP-Za-km-z]{107}/
        // BIP32 mainnet public
        $xpub = /xpub[1-9A-HJ-NP-Za-km-z]{107}/
        // BIP84 (P2WPKH) mainnet private
        $zprv = /zprv[1-9A-HJ-NP-Za-km-z]{107}/
        // BIP84 (P2WPKH) mainnet public
        $zpub = /zpub[1-9A-HJ-NP-Za-km-z]{107}/
        // BIP49 (P2SH-P2WPKH) mainnet
        $yprv = /yprv[1-9A-HJ-NP-Za-km-z]{107}/
        $ypub = /ypub[1-9A-HJ-NP-Za-km-z]{107}/

    condition:
        any of them
}

rule wif_private_key
{
    meta:
        description = "Bitcoin WIF-encoded private key (uncompressed: 5..., compressed: K... or L...)"
        url         = "https://en.bitcoin.it/wiki/Wallet_import_format"
        score       = 88
        confidence  = "medium"
        notes       = "WIF is Base58Check encoded; length + prefix make false positives unlikely in text."

    strings:
        // Uncompressed WIF: starts with 5, 51 chars total
        $wif_u = /\b5[HJK][1-9A-HJ-NP-Za-km-z]{49}\b/
        // Compressed WIF: starts with K or L, 52 chars total
        $wif_c = /\b[KL][1-9A-HJ-NP-Za-km-z]{51}\b/

    condition:
        any of them
}

rule armored_private_key
{
    meta:
        description = "PEM-armored EC or RSA private key (may be used for HD wallet or exchange API)"
        url         = "https://en.wikipedia.org/wiki/Privacy-Enhanced_Mail"
        score       = 60
        confidence  = "medium"
        notes       = "Generic private key. Relevant if used for Bitcoin signing or exchange API auth."

    strings:
        $ec_priv  = "-----BEGIN EC PRIVATE KEY-----"       ascii
        $rsa_priv = "-----BEGIN RSA PRIVATE KEY-----"      ascii
        $priv     = "-----BEGIN PRIVATE KEY-----"          ascii

    condition:
        any of them
}

rule mnemonic_wordlist_file
{
    meta:
        description = "Text file containing multiple BIP39 seed words (stored recovery phrase)"
        url         = "https://github.com/bitcoin/bips/blob/master/bip-0039.mediawiki"
        score       = 55
        confidence  = "low"
        notes       = "Low-precision: uses rare BIP39 words. Follow up with image-pdf-extract.sh."

    strings:
        // Words chosen for low overlap with common English prose
        $w01 = "abandon"  fullword ascii wide nocase
        $w02 = "absurd"   fullword ascii wide nocase
        $w03 = "acoustic" fullword ascii wide nocase
        $w04 = "seminar"  fullword ascii wide nocase
        $w05 = "siren"    fullword ascii wide nocase
        $w06 = "slogan"   fullword ascii wide nocase
        $w07 = "vivid"    fullword ascii wide nocase
        $w08 = "volcano"  fullword ascii wide nocase
        $w09 = "vague"    fullword ascii wide nocase
        $w10 = "unveil"   fullword ascii wide nocase
        $w11 = "warfare"  fullword ascii wide nocase
        $w12 = "zebra"    fullword ascii wide nocase

    condition:
        filesize < 50KB and 4 of them
}
