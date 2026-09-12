"""Title similarity.

RapidFuzz is the implementation. A pure-Python equivalent is kept behind the same
function so the identity engine and its tests remain runnable in an environment
where the extension is unavailable; the fallback is documented as approximate and
reports itself, because a silent change of scoring behaviour would quietly move
every match confidence in the system.
"""

from __future__ import annotations

from app.domains.identity.normalization import normalize_text, tokenize

try:  # pragma: no cover - import path depends on environment
    from rapidfuzz import fuzz as _fuzz

    BACKEND = "rapidfuzz"
except ImportError:  # pragma: no cover
    _fuzz = None
    BACKEND = "python-fallback"


def _token_set_ratio_fallback(left: str, right: str) -> float:
    """Jaccard-weighted token overlap, in the 0..100 range RapidFuzz uses."""
    left_tokens, right_tokens = set(tokenize(left)), set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = left_tokens & right_tokens
    union = left_tokens | right_tokens
    jaccard = len(intersection) / len(union)
    containment = len(intersection) / min(len(left_tokens), len(right_tokens))
    # Containment alone would call "Sony WH-1000XM5" and "Sony WH-1000XM5 case"
    # a perfect match; blending with Jaccard keeps extra tokens costly.
    return round(100 * (0.6 * containment + 0.4 * jaccard), 2)


def title_similarity(left: str | None, right: str | None) -> float:
    """0..1 similarity between two product titles."""
    if not left or not right:
        return 0.0
    left_norm, right_norm = normalize_text(left), normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if _fuzz is not None:
        # token_set_ratio ignores word order and duplicate tokens, which is the
        # right shape for retail titles ("Brand Model 2-Pack" vs "Model, Brand").
        return round(_fuzz.token_set_ratio(left_norm, right_norm) / 100, 4)
    return round(_token_set_ratio_fallback(left_norm, right_norm) / 100, 4)


def token_overlap(left: str | None, right: str | None) -> float:
    """Share of the shorter title's tokens present in the longer one."""
    left_tokens, right_tokens = set(tokenize(left)), set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return round(len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens)), 4)
