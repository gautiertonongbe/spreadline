"""Variation detection.

The expensive mistake in cross-market sourcing is not a missed opportunity, it is
buying a 1-pack against the price of a 6-pack, a 128 GB against a 256 GB, or a
phone case against the phone. This module extracts the attributes that define a
distinct sellable variant and reports precisely where two candidates disagree.

Three outcomes per dimension, and the third is the point:

    AGREE     both sides known and equal
    CONFLICT  both sides known and different
    UNKNOWN   at least one side is silent

UNKNOWN never counts as agreement. An unknown pack count on a listing whose
counterpart says "6-pack" is a reason to lower confidence, not to proceed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.domains.identity.normalization import normalize_text

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

COLORS = {
    "black",
    "white",
    "silver",
    "gray",
    "grey",
    "blue",
    "navy",
    "red",
    "green",
    "yellow",
    "pink",
    "purple",
    "gold",
    "rose gold",
    "beige",
    "brown",
    "orange",
    "teal",
    "graphite",
    "midnight",
    "starlight",
    "ivory",
    "cream",
    "charcoal",
    "clear",
    "transparent",
    "multicolor",
}

#: Phrases that mean "this listing is an accessory for the product", which is the
#: highest-frequency false match in retail catalogues.
ACCESSORY_MARKERS = (
    # "X for Y" constructions
    "case for",
    "cover for",
    "replacement for",
    "compatible with",
    "fits ",
    "for use with",
    "charger for",
    "cable for",
    "stand for",
    "mount for",
    "skin for",
    "sleeve for",
    "adapter for",
    "strap for",
    "band for",
    # Accessory noun phrases. Bare "case" is deliberately excluded because
    # "case of 12" is a pack size, not an accessory.
    "carrying case",
    "carry case",
    "hard case",
    "hard shell case",
    "protective case",
    "storage case",
    "travel case",
    "case cover",
    "screen protector",
    "tempered glass",
    "charging cable",
    "charging dock",
    "replacement band",
    "replacement strap",
    "replacement filter",
    "wall mount",
    "car mount",
    "dust cover",
    "lens cap",
    "ear tips",
    "ear pads",
    "carrying pouch",
)

BUNDLE_MARKERS = ("bundle", "kit with", "includes", "with case", "plus ", " + ")

CONDITION_MARKERS = {
    "renewed": "renewed",
    "refurbished": "renewed",
    "refurb": "renewed",
    "pre owned": "used",
    "preowned": "used",
    "open box": "open_box",
    "used": "used",
    "like new": "used",
}

REGIONAL_MARKERS = (
    "international version",
    "eu version",
    "uk version",
    "japan version",
    "china version",
    "import",
    "uk plug",
    "eu plug",
)

# --------------------------------------------------------------------------
# Unit handling
# --------------------------------------------------------------------------

#: unit -> (dimension, factor to the dimension's base unit)
UNITS: dict[str, tuple[str, Decimal]] = {
    # mass, base gram
    "mg": ("mass", Decimal("0.001")),
    "g": ("mass", Decimal("1")),
    "gram": ("mass", Decimal("1")),
    "grams": ("mass", Decimal("1")),
    "kg": ("mass", Decimal("1000")),
    "oz": ("mass", Decimal("28.349523")),
    "ounce": ("mass", Decimal("28.349523")),
    "ounces": ("mass", Decimal("28.349523")),
    "lb": ("mass", Decimal("453.59237")),
    "lbs": ("mass", Decimal("453.59237")),
    "pound": ("mass", Decimal("453.59237")),
    # volume, base millilitre
    "ml": ("volume", Decimal("1")),
    "l": ("volume", Decimal("1000")),
    "liter": ("volume", Decimal("1000")),
    "litre": ("volume", Decimal("1000")),
    "fl oz": ("volume", Decimal("29.573530")),
    "floz": ("volume", Decimal("29.573530")),
    "gal": ("volume", Decimal("3785.4118")),
    "qt": ("volume", Decimal("946.35295")),
    "pt": ("volume", Decimal("473.17648")),
    # digital capacity, base gigabyte
    "mb": ("capacity", Decimal("0.0009765625")),
    "gb": ("capacity", Decimal("1")),
    "tb": ("capacity", Decimal("1024")),
    # length, base millimetre
    "mm": ("length", Decimal("1")),
    "cm": ("length", Decimal("10")),
    "m": ("length", Decimal("1000")),
    "in": ("length", Decimal("25.4")),
    "inch": ("length", Decimal("25.4")),
    "inches": ("length", Decimal("25.4")),
    "ft": ("length", Decimal("304.8")),
}

_UNIT_ALTERNATION = "|".join(sorted((re.escape(unit) for unit in UNITS), key=len, reverse=True))
_MEASURE_RE = re.compile(rf"(?<![\w.])(\d+(?:\.\d+)?)\s*({_UNIT_ALTERNATION})(?![a-z])")

_PACK_PATTERNS = (
    re.compile(r"(\d+)\s*[-\s]?(?:pack|pk|count|ct|pieces|pcs|pc)\b"),
    re.compile(r"\bpack\s+of\s+(\d+)\b"),
    re.compile(r"\bset\s+of\s+(\d+)\b"),
    re.compile(r"\bcase\s+of\s+(\d+)\b"),
    re.compile(r"\bbox\s+of\s+(\d+)\b"),
    re.compile(r"(?:^|\s)x\s*(\d{1,3})(?:\s|$)"),
)

_GENERATION_PATTERNS = (
    re.compile(r"\b(\d+)(?:st|nd|rd|th)\s+gen(?:eration)?\b"),
    re.compile(r"\bgen(?:eration)?\s+(\d+)\b"),
    re.compile(r"\bseries\s+(\d+)\b"),
    re.compile(r"\bv(\d+)\b"),
)

#: Model-ish tokens: 4+ chars mixing letters and digits, hyphens allowed.
#: Matched against a hyphen-preserving normalisation, because stripping the
#: hyphen first turns "WH-1000XM5" into "wh" + "1000xm5" and loses the model.
_MODEL_TOKEN_RE = re.compile(
    r"(?<![a-z0-9-])(?=[a-z0-9]*[a-z])(?=[a-z0-9-]*\d)[a-z0-9]+(?:-[a-z0-9]+)*(?![a-z0-9-])"
)

#: Tokens that mix letters and digits but describe a measure, a pack or a spec
#: rather than a model ("92oz", "6pack", "4k", "5ghz").
_MODEL_STOPWORD_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:" + _UNIT_ALTERNATION.replace(r"\ ", "") + r"|k|w|v|hz|ghz|mhz|"
    r"pack|pk|ct|count|pc|pcs|piece|pieces|x|p|fps|bit|core|way|inch)$"
)


def normalize_for_model(value: str | None) -> str:
    """Lowercase and collapse whitespace while preserving hyphens."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^\w\s-]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


class VariationDimension(StrEnum):
    PACK_COUNT = "pack_count"
    SIZE = "size"
    CAPACITY = "capacity"
    COLOR = "color"
    MODEL = "model"
    GENERATION = "generation"
    CONDITION = "condition"
    BUNDLE = "bundle"
    ACCESSORY = "accessory"
    REGION = "region"


class ComparisonOutcome(StrEnum):
    AGREE = "agree"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


#: A disagreement on these means the two listings are not the same sellable unit,
#: whatever the titles say. They cap match confidence outright.
BLOCKING_DIMENSIONS = frozenset(
    {
        VariationDimension.PACK_COUNT,
        VariationDimension.CAPACITY,
        VariationDimension.SIZE,
        VariationDimension.MODEL,
        VariationDimension.ACCESSORY,
        VariationDimension.CONDITION,
        VariationDimension.GENERATION,
    }
)


@dataclass(frozen=True)
class Measure:
    value: Decimal
    unit: str
    dimension: str

    @property
    def base_value(self) -> Decimal:
        return self.value * UNITS[self.unit][1]

    def __str__(self) -> str:
        return f"{self.value.normalize()} {self.unit}"


@dataclass
class VariationProfile:
    """What a listing says about which variant it is."""

    pack_count: int | None = None
    size: Measure | None = None
    capacity: Measure | None = None
    color: str | None = None
    model: str | None = None
    generation: int | None = None
    condition_class: str | None = None
    is_bundle: bool | None = None
    is_accessory: bool | None = None
    region_note: str | None = None
    evidence: dict[str, str] = field(default_factory=dict)

    def get(self, dimension: VariationDimension) -> Any:
        return {
            VariationDimension.PACK_COUNT: self.pack_count,
            VariationDimension.SIZE: self.size,
            VariationDimension.CAPACITY: self.capacity,
            VariationDimension.COLOR: self.color,
            VariationDimension.MODEL: self.model,
            VariationDimension.GENERATION: self.generation,
            VariationDimension.CONDITION: self.condition_class,
            VariationDimension.BUNDLE: self.is_bundle,
            VariationDimension.ACCESSORY: self.is_accessory,
            VariationDimension.REGION: self.region_note,
        }[dimension]


@dataclass(frozen=True)
class DimensionComparison:
    dimension: VariationDimension
    outcome: ComparisonOutcome
    left: str | None
    right: str | None
    message: str
    is_blocking: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "outcome": self.outcome.value,
            "left": self.left,
            "right": self.right,
            "message": self.message,
            "blocking": self.is_blocking,
        }


@dataclass(frozen=True)
class VariationComparison:
    comparisons: tuple[DimensionComparison, ...]

    @property
    def conflicts(self) -> tuple[DimensionComparison, ...]:
        return tuple(c for c in self.comparisons if c.outcome is ComparisonOutcome.CONFLICT)

    @property
    def blocking_conflicts(self) -> tuple[DimensionComparison, ...]:
        return tuple(c for c in self.conflicts if c.is_blocking)

    @property
    def unknowns(self) -> tuple[DimensionComparison, ...]:
        return tuple(c for c in self.comparisons if c.outcome is ComparisonOutcome.UNKNOWN)

    @property
    def agreements(self) -> tuple[DimensionComparison, ...]:
        return tuple(c for c in self.comparisons if c.outcome is ComparisonOutcome.AGREE)

    @property
    def passed(self) -> bool:
        return not self.conflicts

    def as_dicts(self) -> list[dict[str, Any]]:
        return [c.as_dict() for c in self.comparisons]


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def _first_measure(text: str, dimensions: set[str]) -> Measure | None:
    for match in _MEASURE_RE.finditer(text):
        raw_value, unit = match.group(1), match.group(2)
        dimension, _ = UNITS[unit]
        if dimension in dimensions:
            return Measure(Decimal(raw_value), unit, dimension)
    return None


def _extract_pack_count(text: str) -> tuple[int | None, str | None]:
    for pattern in _PACK_PATTERNS:
        match = pattern.search(text)
        if match:
            count = int(match.group(1))
            # Guard against matching a model number such as "x100" or a year.
            if 1 <= count <= 500:
                return count, match.group(0).strip()
    return None, None


def _extract_generation(text: str) -> int | None:
    for pattern in _GENERATION_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def _extract_color(text: str, attributes: dict[str, Any]) -> str | None:
    for key in ("color", "colour", "color_name"):
        value = attributes.get(key)
        if value:
            return normalize_text(str(value)) or None
    # Multi-word colours first so "rose gold" does not resolve to "gold".
    for color in sorted(COLORS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(color)}\b", text):
            return color
    return None


def _extract_model(hyphenated_text: str, attributes: dict[str, Any]) -> str | None:
    for key in ("model", "model_number", "mpn", "part_number"):
        value = attributes.get(key)
        if value:
            return re.sub(r"[\s\-_]", "", str(value)).lower() or None
    candidates = [
        token
        for token in _MODEL_TOKEN_RE.findall(hyphenated_text)
        if len(token.replace("-", "")) >= 4 and not _MODEL_STOPWORD_RE.match(token)
    ]
    if not candidates:
        return None
    # The longest mixed token is the most model-like ("wh-1000xm5" over "5ghz").
    best = max(candidates, key=lambda token: len(token.replace("-", "")))
    # Hyphens are dropped for storage and comparison so "WH-1000XM5" and
    # "WH1000XM5" resolve to the same model.
    return re.sub(r"-", "", best)


def _extract_condition(text: str, declared: str | None) -> str | None:
    if declared and declared not in {"new", "unknown"}:
        return "used" if declared.startswith("used") else declared
    for marker, value in CONDITION_MARKERS.items():
        if marker in text:
            return value
    return "new" if declared == "new" else None


def build_profile(
    title: str | None,
    attributes: dict[str, Any] | None = None,
    *,
    condition: str | None = None,
    pack_count: int | None = None,
) -> VariationProfile:
    """Extract the variant-defining attributes of a listing.

    Structured attributes win over title text wherever both exist: a provider
    field is a claim, a title is a marketing string.
    """
    attributes = {str(k).lower(): v for k, v in (attributes or {}).items()}
    text = normalize_text(title)
    profile = VariationProfile()

    declared_pack = pack_count or attributes.get("pack_count") or attributes.get("count")
    if declared_pack is not None:
        try:
            profile.pack_count = int(declared_pack)
            profile.evidence["pack_count"] = f"attribute: {declared_pack}"
        except (TypeError, ValueError):
            pass
    if profile.pack_count is None:
        extracted, evidence = _extract_pack_count(text)
        if extracted is not None:
            profile.pack_count = extracted
            profile.evidence["pack_count"] = f"title: {evidence}"

    size_attr = attributes.get("size") or attributes.get("volume") or attributes.get("weight")
    size = _first_measure(normalize_text(str(size_attr)) if size_attr else "", {"mass", "volume"})
    if size is None:
        size = _first_measure(text, {"mass", "volume"})
        if size is not None:
            profile.evidence["size"] = f"title: {size}"
    else:
        profile.evidence["size"] = f"attribute: {size}"
    profile.size = size

    capacity_attr = attributes.get("capacity") or attributes.get("storage")
    capacity = _first_measure(
        normalize_text(str(capacity_attr)) if capacity_attr else "", {"capacity"}
    )
    if capacity is None:
        capacity = _first_measure(text, {"capacity"})
        if capacity is not None:
            profile.evidence["capacity"] = f"title: {capacity}"
    else:
        profile.evidence["capacity"] = f"attribute: {capacity}"
    profile.capacity = capacity

    profile.color = _extract_color(text, attributes)
    if profile.color:
        profile.evidence["color"] = profile.color

    profile.model = _extract_model(normalize_for_model(title), attributes)
    if profile.model:
        profile.evidence["model"] = profile.model

    profile.generation = _extract_generation(text)
    if profile.generation is not None:
        profile.evidence["generation"] = str(profile.generation)

    profile.condition_class = _extract_condition(text, condition)
    profile.is_accessory = any(marker in text for marker in ACCESSORY_MARKERS)
    profile.is_bundle = any(marker in text for marker in BUNDLE_MARKERS)
    region = next((marker for marker in REGIONAL_MARKERS if marker in text), None)
    profile.region_note = region
    return profile


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

#: Measures from different catalogues round differently (16 fl oz vs 473 ml).
MEASURE_TOLERANCE = Decimal("0.02")


def _compare_measure(
    dimension: VariationDimension, left: Measure | None, right: Measure | None
) -> DimensionComparison:
    label = dimension.value
    if left is None or right is None:
        known = "left" if left is not None else "right" if right is not None else None
        if known is None:
            message = f"Neither listing states a {label}."
        else:
            stated = left if left is not None else right
            message = f"Only one listing states a {label} ({stated})."
        return DimensionComparison(
            dimension,
            ComparisonOutcome.UNKNOWN,
            str(left) if left else None,
            str(right) if right else None,
            message,
            is_blocking=False,
        )
    if left.dimension != right.dimension:
        return DimensionComparison(
            dimension,
            ComparisonOutcome.CONFLICT,
            str(left),
            str(right),
            f"{label} measured in different dimensions ({left.dimension} vs {right.dimension}).",
            is_blocking=dimension in BLOCKING_DIMENSIONS,
        )
    larger = max(left.base_value, right.base_value)
    if larger == 0:
        delta = Decimal("0")
    else:
        delta = abs(left.base_value - right.base_value) / larger
    if delta <= MEASURE_TOLERANCE:
        return DimensionComparison(
            dimension,
            ComparisonOutcome.AGREE,
            str(left),
            str(right),
            f"{label} matches ({left} vs {right}).",
        )
    return DimensionComparison(
        dimension,
        ComparisonOutcome.CONFLICT,
        str(left),
        str(right),
        f"{label} differs: {left} vs {right}.",
        is_blocking=dimension in BLOCKING_DIMENSIONS,
    )


def _compare_scalar(
    dimension: VariationDimension,
    left: Any,
    right: Any,
    *,
    label: str | None = None,
) -> DimensionComparison:
    name = label or dimension.value.replace("_", " ")
    if left is None or right is None:
        stated = left if left is not None else right
        message = (
            f"Neither listing states a {name}."
            if stated is None
            else f"Only one listing states a {name} ({stated})."
        )
        return DimensionComparison(
            dimension,
            ComparisonOutcome.UNKNOWN,
            None if left is None else str(left),
            None if right is None else str(right),
            message,
        )
    if left == right:
        return DimensionComparison(
            dimension, ComparisonOutcome.AGREE, str(left), str(right), f"{name} matches ({left})."
        )
    return DimensionComparison(
        dimension,
        ComparisonOutcome.CONFLICT,
        str(left),
        str(right),
        f"{name} differs: {left} vs {right}.",
        is_blocking=dimension in BLOCKING_DIMENSIONS,
    )


def _compare_pack_count(left: int | None, right: int | None) -> DimensionComparison:
    """Pack count, with one deliberate asymmetry.

    A listing that declares a multipack and a counterpart that declares nothing
    is treated as a blocking conflict, not as unknown. Comparing a stated 6-pack
    against an unlabelled listing is the single most common way to book a 6x
    profit that does not exist, and "the other side probably means one unit" is a
    guess the platform is not entitled to make.
    """
    if left is not None and right is not None:
        if left == right:
            return DimensionComparison(
                VariationDimension.PACK_COUNT,
                ComparisonOutcome.AGREE,
                str(left),
                str(right),
                f"Pack count matches ({left}).",
            )
        return DimensionComparison(
            VariationDimension.PACK_COUNT,
            ComparisonOutcome.CONFLICT,
            str(left),
            str(right),
            f"Pack count differs: {left} vs {right}.",
            is_blocking=True,
        )
    declared = left if left is not None else right
    if declared is not None and declared > 1:
        side = "source" if left is not None else "target"
        return DimensionComparison(
            VariationDimension.PACK_COUNT,
            ComparisonOutcome.CONFLICT,
            None if left is None else str(left),
            None if right is None else str(right),
            f"The {side} listing is a {declared}-pack and the other states no pack count.",
            is_blocking=True,
        )
    return DimensionComparison(
        VariationDimension.PACK_COUNT,
        ComparisonOutcome.UNKNOWN,
        None if left is None else str(left),
        None if right is None else str(right),
        "Pack count is stated on at most one listing."
        if declared is not None
        else "Neither listing states a pack count.",
    )


def _compare_model(left: str | None, right: str | None) -> DimensionComparison:
    if left is None or right is None:
        return _compare_scalar(VariationDimension.MODEL, left, right)
    if left == right:
        return DimensionComparison(
            VariationDimension.MODEL,
            ComparisonOutcome.AGREE,
            left,
            right,
            f"Model matches ({left}).",
        )
    # One catalogue routinely writes a longer model string than the other
    # ("wh1000xm5" vs "wh1000xm5b"); containment is agreement, not conflict.
    if left in right or right in left:
        return DimensionComparison(
            VariationDimension.MODEL,
            ComparisonOutcome.AGREE,
            left,
            right,
            f"Model is compatible ({left} / {right}).",
        )
    return DimensionComparison(
        VariationDimension.MODEL,
        ComparisonOutcome.CONFLICT,
        left,
        right,
        f"Model differs: {left} vs {right}.",
        is_blocking=True,
    )


def compare_profiles(left: VariationProfile, right: VariationProfile) -> VariationComparison:
    """Compare two variation profiles dimension by dimension."""
    comparisons: list[DimensionComparison] = [
        _compare_pack_count(left.pack_count, right.pack_count),
        _compare_measure(VariationDimension.SIZE, left.size, right.size),
        _compare_measure(VariationDimension.CAPACITY, left.capacity, right.capacity),
        _compare_scalar(VariationDimension.COLOR, left.color, right.color),
        _compare_model(left.model, right.model),
        _compare_scalar(VariationDimension.GENERATION, left.generation, right.generation),
        _compare_scalar(VariationDimension.CONDITION, left.condition_class, right.condition_class),
    ]

    # Accessory and bundle are booleans that are always "known": absence of the
    # marker is a real claim that the listing is not an accessory.
    comparisons.append(
        _compare_scalar(
            VariationDimension.ACCESSORY,
            bool(left.is_accessory),
            bool(right.is_accessory),
            label="accessory status",
        )
    )
    comparisons.append(
        _compare_scalar(
            VariationDimension.BUNDLE,
            bool(left.is_bundle),
            bool(right.is_bundle),
            label="bundle status",
        )
    )
    if left.region_note or right.region_note:
        comparisons.append(
            _compare_scalar(
                VariationDimension.REGION,
                left.region_note,
                right.region_note,
                label="regional variant",
            )
        )
    return VariationComparison(tuple(comparisons))
