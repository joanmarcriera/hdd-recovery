"""Shared BIP39 seed phrase scanning helpers."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from pathlib import Path
from typing import Iterable


DEFAULT_WORDLIST_PATHS = [
    "/usr/local/share/bip39-english.txt",
    "/root/hdd-recovery/config/bip39-english.txt",
    str(Path(__file__).resolve().parents[1] / "config" / "bip39-english.txt"),
]


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class Match:
    words: list[str]
    start_token: int
    end_token: int
    start_char: int
    end_char: int

    @property
    def run_len(self) -> int:
        return len(self.words)

    @property
    def sequence(self) -> str:
        return " ".join(self.words)


def load_wordlist(wordlist_path: str | None = None) -> set[str]:
    candidates = ([wordlist_path] if wordlist_path else []) + DEFAULT_WORDLIST_PATHS
    for path in candidates:
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                words = {line.strip().lower() for line in f if line.strip()}
            if len(words) >= 2000:
                return words
    return set()


def tokenize_text(text: str) -> list[Token]:
    return [
        Token(match.group(0).lower(), match.start(), match.end())
        for match in re.finditer(r"[a-z]+", text.lower())
    ]


def scan_text(
    text: str,
    min_words: int = 6,
    wordlist: Iterable[str] | None = None,
) -> list[Match]:
    bip39 = set(wordlist) if wordlist is not None else load_wordlist()
    if not bip39:
        return []

    tokens = tokenize_text(text)
    matches: list[Match] = []
    current_words: list[str] = []
    current_start_token = 0
    current_start_char = 0

    def flush(end_index: int, end_char: int) -> None:
        if len(current_words) >= min_words:
            matches.append(
                Match(
                    words=list(current_words),
                    start_token=current_start_token,
                    end_token=end_index,
                    start_char=current_start_char,
                    end_char=end_char,
                )
            )

    for idx, token in enumerate(tokens):
        if token.value in bip39:
            if not current_words:
                current_start_token = idx
                current_start_char = token.start
            current_words.append(token.value)
        else:
            flush(idx, token.start)
            current_words = []

    if current_words:
        flush(len(tokens), tokens[-1].end if tokens else 0)

    return matches


def best_match(
    text: str,
    min_words: int = 1,
    wordlist: Iterable[str] | None = None,
) -> Match | None:
    matches = scan_text(text, min_words=min_words, wordlist=wordlist)
    if not matches:
        return None
    return max(matches, key=lambda match: match.run_len)
