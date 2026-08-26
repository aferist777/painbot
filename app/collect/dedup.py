"""Near-duplicate detection: 64-bit simhash over word trigrams."""
import hashlib
import re
from typing import Iterable, Optional

from app.db.base import q

HASH_BITS = 64
MAX_DISTANCE = 3          # bits; 0..3 reads as "the same story reposted"
COMPARE_WINDOW = 3000     # recent items to check against

_word = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_STOP = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "to", "of",
    "in", "on", "for", "with", "my", "i", "it", "this", "that", "you", "we",
    "и", "в", "на", "с", "не", "что", "как", "это", "для", "по", "от", "у",
}


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _word.findall(text or "") if w.lower() not in _STOP]


def _shingles(tokens: list[str], n: int = 3) -> Iterable[str]:
    if len(tokens) < n:
        yield " ".join(tokens)
        return
    for i in range(len(tokens) - n + 1):
        yield " ".join(tokens[i : i + n])


def simhash(text: str) -> str:
    vector = [0] * HASH_BITS
    for shingle in _shingles(_tokens(text)):
        # blake2b, not hash(): CPython randomises str hashing per process,
        # which would make stored fingerprints useless after a restart.
        digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "big")
        for bit in range(HASH_BITS):
            vector[bit] += 1 if (h >> bit) & 1 else -1
    value = 0
    for bit in range(HASH_BITS):
        if vector[bit] > 0:
            value |= 1 << bit
    return f"{value:016x}"


def distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def find_duplicate(fingerprint: str) -> Optional[int]:
    """Return the id of a near-identical earlier item, if any."""
    rows = q(
        "SELECT id, simhash FROM raw_items WHERE simhash IS NOT NULL "
        "ORDER BY id DESC LIMIT ?",
        COMPARE_WINDOW,
    )
    for row in rows:
        try:
            if distance(fingerprint, row["simhash"]) <= MAX_DISTANCE:
                return row["id"]
        except ValueError:
            continue
    return None
