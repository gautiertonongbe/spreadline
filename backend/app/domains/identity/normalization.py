"""Identifier and text normalisation.

Everything the matcher compares passes through here first, so that "0-12345-
67890-5", "012345678905" and "12345678905" are recognised as one identifier and
an invalid check digit is caught before it can produce a confident wrong match.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.models.enums import IdentifierType

_NON_DIGIT = re.compile(r"\D+")
_WHITESPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")

#: Lengths of the GTIN family. Anything else is not a GTIN.
_GTIN_LENGTHS = {8, 12, 13, 14}


@dataclass(frozen=True)
class NormalizedIdentifier:
    identifier_type: IdentifierType
    raw: str
    normalized: str | None
    is_valid: bool
    note: str | None = None


def gtin_check_digit(digits: str) -> int:
    """Modulo-10 check digit over the payload (everything but the last digit).

    Weights alternate 3/1 from the rightmost payload digit, which is the same
    rule for GTIN-8/12/13/14 once the payload is read right to left.
    """
    total = 0
    for index, char in enumerate(reversed(digits)):
        weight = 3 if index % 2 == 0 else 1
        total += int(char) * weight
    return (10 - (total % 10)) % 10


def is_valid_gtin(value: str) -> bool:
    digits = _NON_DIGIT.sub("", value or "")
    if len(digits) not in _GTIN_LENGTHS:
        return False
    return gtin_check_digit(digits[:-1]) == int(digits[-1])


def normalize_gtin(value: str | None) -> str | None:
    """Return the GTIN-14 form of a UPC-A/EAN-13/GTIN-8/14, or None.

    Zero-padding to 14 is what makes a UPC-12 and its EAN-13 equivalent compare
    equal, which is the single most common reason two marketplaces appear to
    disagree about a product that is in fact identical.
    """
    if not value:
        return None
    digits = _NON_DIGIT.sub("", value)
    if len(digits) not in _GTIN_LENGTHS or not is_valid_gtin(digits):
        return None
    return digits.zfill(14)


def normalize_asin(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().upper()
    return candidate if re.fullmatch(r"[A-Z0-9]{10}", candidate) else None


def normalize_mpn(value: str | None) -> str | None:
    """Uppercase and strip separators.

    Manufacturer part numbers are written as "WH-1000XM5", "WH1000XM5" and
    "wh 1000 xm5" by different catalogues for the same part.
    """
    if not value:
        return None
    candidate = re.sub(r"[\s\-_/.]+", "", value).upper()
    return candidate if len(candidate) >= 3 else None


def normalize_text(value: str | None) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _WHITESPACE.sub(" ", _PUNCT.sub(" ", stripped.lower())).strip()


def normalize_brand(value: str | None) -> str | None:
    """Normalise a brand, dropping corporate suffixes that vary by catalogue."""
    text = normalize_text(value)
    if not text:
        return None
    text = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|gmbh|sa|sas|company)\b", "", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def tokenize(value: str | None) -> list[str]:
    text = normalize_text(value)
    return [token for token in text.split(" ") if token]


def normalize_identifier(identifier_type: str, value: str) -> NormalizedIdentifier:
    """Normalise and validate one identifier claim."""
    try:
        kind = IdentifierType(identifier_type)
    except ValueError:
        return NormalizedIdentifier(
            IdentifierType.SKU, value, value.strip().upper() or None, False, "unknown type"
        )

    if kind in {IdentifierType.GTIN, IdentifierType.UPC, IdentifierType.EAN, IdentifierType.ISBN}:
        normalized = normalize_gtin(value)
        if normalized is None:
            digits = _NON_DIGIT.sub("", value or "")
            note = (
                "wrong length for a GTIN"
                if len(digits) not in _GTIN_LENGTHS
                else "check digit failed"
            )
            return NormalizedIdentifier(kind, value, None, False, note)
        return NormalizedIdentifier(kind, value, normalized, True, None)

    if kind is IdentifierType.ASIN:
        normalized = normalize_asin(value)
        return NormalizedIdentifier(
            kind, value, normalized, normalized is not None, None if normalized else "not an ASIN"
        )

    if kind in {IdentifierType.MPN, IdentifierType.MODEL}:
        normalized = normalize_mpn(value)
        return NormalizedIdentifier(
            kind, value, normalized, normalized is not None, None if normalized else "too short"
        )

    cleaned = (value or "").strip().upper() or None
    return NormalizedIdentifier(kind, value, cleaned, cleaned is not None, None)
