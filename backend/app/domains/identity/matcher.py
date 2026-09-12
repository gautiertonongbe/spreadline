"""Product identity resolution.

Decides whether two marketplace listings are the same sellable product, and says
how it knows. The output is a confidence, a method, the evidence behind it and
the conflicts it had to tolerate (spec §7).

Two invariants are enforced here rather than left to callers:

* Title similarity alone can never reach the high-confidence band. A high
  similarity between "Sony WH-1000XM5" and "Sony WH-1000XM4" is 0.93, and acting
  on it buys the wrong headphones.
* A blocking variation conflict (pack count, capacity, size, model, accessory,
  condition, generation) rejects the match no matter how strong the identifier
  evidence looked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.money import ratio
from app.domains.identity.normalization import (
    normalize_brand,
    normalize_gtin,
    normalize_mpn,
    normalize_text,
)
from app.domains.identity.similarity import BACKEND as SIMILARITY_BACKEND
from app.domains.identity.similarity import title_similarity
from app.domains.identity.variation import (
    BLOCKING_DIMENSIONS,
    VariationComparison,
    VariationProfile,
    build_profile,
    compare_profiles,
)
from app.models.enums import IdentifierType, MatchMethod, MatchStatus


@dataclass(frozen=True)
class MatchingPolicy:
    """Every threshold in the identity engine, in one configurable object."""

    base_confidence: dict[MatchMethod, Decimal] = field(
        default_factory=lambda: {
            MatchMethod.GTIN: Decimal("0.97"),
            MatchMethod.UPC: Decimal("0.97"),
            MatchMethod.EAN: Decimal("0.97"),
            MatchMethod.MPN: Decimal("0.88"),
            MatchMethod.BRAND_MODEL: Decimal("0.80"),
            MatchMethod.ATTRIBUTES: Decimal("0.72"),
            MatchMethod.TITLE_SIMILARITY: Decimal("0.60"),
            MatchMethod.AI_ASSISTED: Decimal("0.75"),
            MatchMethod.MANUAL: Decimal("1.00"),
        }
    )
    #: Hard ceiling for a match resting on title text alone.
    title_only_ceiling: Decimal = Decimal("0.60")
    #: Ceiling applied once a blocking conflict is present.
    blocking_conflict_ceiling: Decimal = Decimal("0.30")
    soft_conflict_penalty: Decimal = Decimal("0.10")
    unknown_dimension_penalty: Decimal = Decimal("0.03")
    max_unknown_penalty: Decimal = Decimal("0.12")
    agreement_bonus: Decimal = Decimal("0.01")
    max_agreement_bonus: Decimal = Decimal("0.04")
    confirmed_threshold: Decimal = Decimal("0.90")
    probable_threshold: Decimal = Decimal("0.75")
    ambiguous_threshold: Decimal = Decimal("0.50")
    #: Minimum title similarity before attribute agreement is allowed to stand in
    #: for an identifier.
    attribute_match_min_similarity: Decimal = Decimal("0.70")
    title_similarity_floor: Decimal = Decimal("0.80")


DEFAULT_POLICY = MatchingPolicy()


@dataclass
class MatchCandidate:
    """A listing reduced to everything identity resolution needs."""

    listing_id: str | None
    marketplace: str
    title: str
    brand: str | None = None
    model: str | None = None
    condition: str | None = "new"
    pack_count: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    #: identifier type -> raw values
    identifiers: dict[str, list[str]] = field(default_factory=dict)
    provider: str | None = None

    def normalized_gtins(self) -> set[str]:
        values: set[str] = set()
        for kind in (
            IdentifierType.GTIN,
            IdentifierType.UPC,
            IdentifierType.EAN,
            IdentifierType.ISBN,
        ):
            for raw in self.identifiers.get(kind.value, []):
                normalized = normalize_gtin(raw)
                if normalized:
                    values.add(normalized)
        return values

    def normalized_mpns(self) -> set[str]:
        values: set[str] = set()
        for kind in (IdentifierType.MPN, IdentifierType.MODEL):
            for raw in self.identifiers.get(kind.value, []):
                normalized = normalize_mpn(raw)
                if normalized:
                    values.add(normalized)
        if self.model:
            normalized = normalize_mpn(self.model)
            if normalized:
                values.add(normalized)
        return values

    def profile(self) -> VariationProfile:
        return build_profile(
            self.title,
            self.attributes,
            condition=self.condition,
            pack_count=self.pack_count,
        )


@dataclass
class MatchResult:
    confidence: Decimal
    method: MatchMethod
    status: MatchStatus
    evidence: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    variation_passed: bool
    title_similarity: float
    summary: str
    #: Set when the match is rejected outright, naming the reason.
    rejection_reason: str | None = None

    @property
    def is_usable(self) -> bool:
        """Whether downstream engines may treat these as the same product."""
        return self.status in {MatchStatus.CONFIRMED, MatchStatus.PROBABLE}

    def as_dict(self) -> dict[str, Any]:
        return {
            "confidence": float(self.confidence),
            "method": self.method.value,
            "status": self.status.value,
            "evidence": self.evidence,
            "conflicts": self.conflicts,
            "variation_passed": self.variation_passed,
            "title_similarity": self.title_similarity,
            "summary": self.summary,
            "rejection_reason": self.rejection_reason,
            "similarity_backend": SIMILARITY_BACKEND,
        }


def _evidence(kind: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "detail": detail, **extra}


def _select_method(
    source: MatchCandidate, target: MatchCandidate, similarity: float, policy: MatchingPolicy
) -> tuple[MatchMethod | None, list[dict[str, Any]], str | None]:
    """Walk the identifier ladder, strongest rung first (spec §7)."""
    evidence: list[dict[str, Any]] = []

    source_gtins, target_gtins = source.normalized_gtins(), target.normalized_gtins()
    if source_gtins and target_gtins:
        shared = source_gtins & target_gtins
        if shared:
            evidence.append(
                _evidence("gtin", f"Exact GTIN match on {sorted(shared)[0]}", values=sorted(shared))
            )
            return MatchMethod.GTIN, evidence, None
        # Both sides carry a valid GTIN and they disagree. That is not weak
        # evidence, it is positive evidence of two different products.
        return (
            None,
            [
                _evidence(
                    "gtin_conflict",
                    "Both listings carry valid but different GTINs "
                    f"({sorted(source_gtins)[0]} vs {sorted(target_gtins)[0]})",
                )
            ],
            "conflicting GTINs",
        )

    source_mpns, target_mpns = source.normalized_mpns(), target.normalized_mpns()
    shared_mpn = source_mpns & target_mpns
    source_brand, target_brand = normalize_brand(source.brand), normalize_brand(target.brand)
    brands_agree = bool(source_brand and target_brand and source_brand == target_brand)

    if shared_mpn:
        evidence.append(
            _evidence("mpn", f"Manufacturer part number match on {sorted(shared_mpn)[0]}")
        )
        if brands_agree:
            evidence.append(_evidence("brand", f"Brand match on '{source_brand}'"))
        return MatchMethod.MPN, evidence, None

    if brands_agree and source.model and target.model:
        if normalize_text(source.model) == normalize_text(target.model):
            evidence.append(_evidence("brand", f"Brand match on '{source_brand}'"))
            evidence.append(_evidence("model", f"Model match on '{source.model}'"))
            return MatchMethod.BRAND_MODEL, evidence, None

    if brands_agree and Decimal(str(similarity)) >= policy.attribute_match_min_similarity:
        evidence.append(_evidence("brand", f"Brand match on '{source_brand}'"))
        evidence.append(
            _evidence("attributes", f"Structured attributes and title agree ({similarity:.0%})")
        )
        return MatchMethod.ATTRIBUTES, evidence, None

    if Decimal(str(similarity)) >= policy.title_similarity_floor:
        evidence.append(
            _evidence(
                "title",
                f"Title similarity {similarity:.0%} (identifiers unavailable on both sides)",
                similarity=similarity,
            )
        )
        return MatchMethod.TITLE_SIMILARITY, evidence, None

    return None, evidence, "no identifier, brand/model or title evidence"


def match_listings(
    source: MatchCandidate,
    target: MatchCandidate,
    *,
    policy: MatchingPolicy = DEFAULT_POLICY,
) -> MatchResult:
    """Resolve whether two listings are the same sellable product."""
    similarity = title_similarity(source.title, target.title)
    comparison = compare_profiles(source.profile(), target.profile())

    method, evidence, rejection = _select_method(source, target, similarity, policy)

    if method is None:
        # A blocking variation conflict is a more informative reason than the
        # absence of identifier evidence, so it leads the explanation.
        blocking = comparison.blocking_conflicts
        reason = blocking[0].message if blocking else rejection
        for conflict in blocking:
            evidence.append(_evidence("variation_conflict", conflict.message, blocking=True))
        return MatchResult(
            confidence=Decimal("0"),
            method=MatchMethod.TITLE_SIMILARITY,
            status=MatchStatus.REJECTED,
            evidence=evidence,
            conflicts=[c.as_dict() for c in comparison.conflicts],
            variation_passed=comparison.passed,
            title_similarity=similarity,
            summary=f"No match: {reason}",
            rejection_reason=reason,
        )

    confidence = policy.base_confidence[method]
    confidence, adjustments = _apply_variation_adjustments(
        confidence, comparison, policy, method=method
    )
    evidence.extend(adjustments)

    if method is MatchMethod.TITLE_SIMILARITY:
        confidence = min(confidence, policy.title_only_ceiling)

    confidence = max(Decimal("0"), min(Decimal("0.99"), confidence))
    status = _status_for(confidence, method, comparison, policy)
    rejection_reason = None
    if status is MatchStatus.REJECTED and comparison.blocking_conflicts:
        rejection_reason = "; ".join(c.message for c in comparison.blocking_conflicts)

    return MatchResult(
        confidence=ratio(confidence),
        method=method,
        status=status,
        evidence=evidence,
        conflicts=[c.as_dict() for c in comparison.conflicts],
        variation_passed=comparison.passed,
        title_similarity=similarity,
        summary=_summarize(confidence, method, status, comparison),
        rejection_reason=rejection_reason,
    )


#: Methods that rest on an authoritative identifier. For these, an unstated
#: variation dimension is not a gap in the evidence: distinct pack counts, sizes
#: and capacities carry distinct GTINs by definition, so the identifier already
#: answers the question the title left open.
_IDENTIFIER_TIER = frozenset(
    {MatchMethod.GTIN, MatchMethod.UPC, MatchMethod.EAN, MatchMethod.MANUAL}
)


def _apply_variation_adjustments(
    confidence: Decimal,
    comparison: VariationComparison,
    policy: MatchingPolicy,
    *,
    method: MatchMethod,
) -> tuple[Decimal, list[dict[str, Any]]]:
    notes: list[dict[str, Any]] = []
    identifier_backed = method in _IDENTIFIER_TIER

    if comparison.blocking_conflicts:
        confidence = min(confidence, policy.blocking_conflict_ceiling)
        for conflict in comparison.blocking_conflicts:
            notes.append(_evidence("variation_conflict", conflict.message, blocking=True))

    soft_conflicts = [c for c in comparison.conflicts if not c.is_blocking]
    for conflict in soft_conflicts:
        confidence -= policy.soft_conflict_penalty
        notes.append(_evidence("variation_conflict", conflict.message, blocking=False))

    unknown_blocking = [c for c in comparison.unknowns if c.dimension in BLOCKING_DIMENSIONS]
    if unknown_blocking and identifier_backed:
        notes.append(
            _evidence(
                "variation_unknown",
                "Unstated variation dimensions ("
                + ", ".join(c.dimension.value for c in unknown_blocking)
                + ") carry no penalty: the identifier match already distinguishes variants.",
                penalty=0.0,
            )
        )
    elif unknown_blocking:
        penalty = min(
            policy.max_unknown_penalty,
            policy.unknown_dimension_penalty * len(unknown_blocking),
        )
        confidence -= penalty
        notes.append(
            _evidence(
                "variation_unknown",
                "Unverified variation dimensions: "
                + ", ".join(c.dimension.value for c in unknown_blocking),
                penalty=float(penalty),
            )
        )

    agreeing_blocking = [c for c in comparison.agreements if c.dimension in BLOCKING_DIMENSIONS]
    if agreeing_blocking and not comparison.conflicts and not identifier_backed:
        bonus = min(policy.max_agreement_bonus, policy.agreement_bonus * len(agreeing_blocking))
        confidence += bonus
        notes.append(
            _evidence(
                "variation_check",
                "Variation check passed on "
                + ", ".join(c.dimension.value for c in agreeing_blocking),
                bonus=float(bonus),
            )
        )
    return confidence, notes


def _status_for(
    confidence: Decimal,
    method: MatchMethod,
    comparison: VariationComparison,
    policy: MatchingPolicy,
) -> MatchStatus:
    if comparison.blocking_conflicts:
        return MatchStatus.REJECTED
    if confidence >= policy.confirmed_threshold:
        # Belt and braces: even if a future policy raised the title ceiling, a
        # title-only match must not be presented as confirmed.
        return (
            MatchStatus.PROBABLE
            if method is MatchMethod.TITLE_SIMILARITY
            else MatchStatus.CONFIRMED
        )
    if confidence >= policy.probable_threshold:
        return MatchStatus.PROBABLE
    if confidence >= policy.ambiguous_threshold:
        return MatchStatus.AMBIGUOUS
    return MatchStatus.REJECTED


def _summarize(
    confidence: Decimal, method: MatchMethod, status: MatchStatus, comparison: VariationComparison
) -> str:
    label = {
        MatchMethod.GTIN: "exact GTIN",
        MatchMethod.UPC: "exact UPC",
        MatchMethod.EAN: "exact EAN",
        MatchMethod.MPN: "manufacturer part number",
        MatchMethod.BRAND_MODEL: "brand and model",
        MatchMethod.ATTRIBUTES: "structured attributes",
        MatchMethod.TITLE_SIMILARITY: "title similarity only",
        MatchMethod.AI_ASSISTED: "AI-assisted review",
        MatchMethod.MANUAL: "manual confirmation",
    }[method]
    if status is MatchStatus.REJECTED and comparison.blocking_conflicts:
        return f"Rejected on {comparison.blocking_conflicts[0].dimension.value} conflict."
    variation = "variation check passed" if comparison.passed else "variation conflicts present"
    return f"{confidence:.0%} via {label}; {variation}."
