"""Canonical character and storage-key contract shared by the backend.

The order in this module is the order used by both the printable form scanner and
the paperless drawing flow.  Keep storage keys ASCII-only: Firestore document IDs,
the browser renderer and the PDF generator all consume these exact base keys.
"""

from __future__ import annotations

import re
from typing import Final


LOWERCASE_CHARACTERS: Final[tuple[str, ...]] = tuple(
    "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
)
UPPERCASE_CHARACTERS: Final[tuple[str, ...]] = tuple(
    "ABCÇDEFGĞHIİJKLMNOÖPQRSŞTUÜVWXYZ"
)
DIGIT_CHARACTERS: Final[tuple[str, ...]] = tuple("0123456789")
SYMBOL_CHARACTERS: Final[tuple[str, ...]] = tuple(
    ".,:;?!-_\"'()[]{}/\\|+*=<>%^~@$€₺&#"
)

ALLOWED_VARIATION_COUNTS: Final[frozenset[int]] = frozenset({1, 2, 3, 5, 10})

# These names are the established app.py / engine.js storage contract.  The
# Turkish aliases intentionally distinguish I (ii) from İ (i), and ı (ii) from
# i (i), while the kucuk_/buyuk_ prefix keeps the two cases separate.
TURKISH_SAFE_MAP: Final[dict[str, str]] = {
    "ç": "cc",
    "ğ": "gg",
    "ı": "ii",
    "ö": "oo",
    "ş": "ss",
    "ü": "uu",
    "Ç": "cc",
    "Ğ": "gg",
    "I": "ii",
    "İ": "i",
    "Ö": "oo",
    "Ş": "ss",
    "Ü": "uu",
}

SYMBOL_SAFE_MAP: Final[dict[str, str]] = {
    ".": "nokta",
    ",": "virgul",
    ":": "ikiknokta",
    ";": "noktalivirgul",
    "?": "soru",
    "!": "unlem",
    "-": "tire",
    "_": "alt_tire",
    '"': "tirnak",
    "'": "tektirnak",
    "(": "parantezac",
    ")": "parantezkapama",
    "[": "koseli_ac",
    "]": "koseli_kapa",
    "{": "suslu_ac",
    "}": "suslu_kapa",
    "/": "slash",
    "\\": "backslas",
    "|": "pipe",
    "+": "arti",
    "*": "carpi",
    "=": "esit",
    "<": "kucuktur",
    ">": "buyuktur",
    "%": "yuzde",
    "^": "sapka",
    "~": "yaklasik",
    "@": "at",
    "$": "dolar",
    "€": "euro",
    "₺": "tl",
    "&": "ampersand",
    "#": "diyez",
}

CHARACTERS: Final[tuple[str, ...]] = (
    LOWERCASE_CHARACTERS
    + UPPERCASE_CHARACTERS
    + DIGIT_CHARACTERS
    + SYMBOL_CHARACTERS
)


def validate_variation_count(value: object) -> int:
    """Return a supported variation count or raise a user-facing ValueError."""

    if isinstance(value, bool):
        raise ValueError("Varyasyon sayısı 1, 2, 3, 5 veya 10 olmalıdır.")

    if isinstance(value, int):
        count = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        count = int(value.strip())
    else:
        raise ValueError("Varyasyon sayısı 1, 2, 3, 5 veya 10 olmalıdır.")

    if count not in ALLOWED_VARIATION_COUNTS:
        raise ValueError("Varyasyon sayısı 1, 2, 3, 5 veya 10 olmalıdır.")
    return count


def base_key_for_character(character: str, *, allow_space: bool = False) -> str:
    """Map one supported character to its canonical Firestore base key."""

    if not isinstance(character, str) or len(character) != 1:
        raise ValueError("Tek bir karakter bekleniyor.")
    if character in LOWERCASE_CHARACTERS:
        return f"kucuk_{TURKISH_SAFE_MAP.get(character, character)}"
    if character in UPPERCASE_CHARACTERS:
        safe = TURKISH_SAFE_MAP.get(character, character.lower())
        return f"buyuk_{safe}"
    if character in DIGIT_CHARACTERS:
        return f"rakam_{character}"
    if character in SYMBOL_SAFE_MAP:
        return f"ozel_{SYMBOL_SAFE_MAP[character]}"
    if allow_space and character == " ":
        return "ozel_bosluk"
    raise ValueError(f"Desteklenmeyen karakter: {character!r}")


CHARACTER_MANIFEST: Final[tuple[tuple[str, str], ...]] = tuple(
    (character, base_key_for_character(character)) for character in CHARACTERS
)
BASE_KEYS: Final[tuple[str, ...]] = tuple(key for _, key in CHARACTER_MANIFEST)
BASE_KEY_SET: Final[frozenset[str]] = frozenset(BASE_KEYS)


def variation_keys(variation_count: object) -> tuple[str, ...]:
    """Expand canonical base keys in paper-form order for the given variation."""

    count = validate_variation_count(variation_count)
    return tuple(
        f"{base_key}_{variation}"
        for base_key in BASE_KEYS
        for variation in range(1, count + 1)
    )


def variation_key_set(variation_count: object) -> frozenset[str]:
    return frozenset(variation_keys(variation_count))


# Fail immediately during development if someone accidentally changes the SaaS
# contract or introduces colliding aliases.
assert len(LOWERCASE_CHARACTERS) == 32
assert len(UPPERCASE_CHARACTERS) == 32
assert len(DIGIT_CHARACTERS) == 10
assert len(SYMBOL_CHARACTERS) == 33
assert len(CHARACTER_MANIFEST) == 107
assert len(BASE_KEY_SET) == len(CHARACTER_MANIFEST)
