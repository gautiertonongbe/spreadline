"""Analysing a pair somebody typed in by hand.

The product exists so that nobody has to open four tabs and do the arithmetic.
Getting there needs price feeds, and every one of those sits behind a queue. This
is the path that does not.

A person opens the two product pages, reads the prices, and types them in. That
is not scraping and it is not a workaround: it is a human observing a public
fact, which is what every arbitrage operator already does before they buy
anything. No automated access, no robot, no terms of service to breach, and no
account to put at risk. It is also the most trustworthy observation the platform
can hold, because somebody looked at it.

So a hand-entered price is recorded as **real**, not simulated. That deserves
stating plainly because it is the opposite of the sandbox decision made
elsewhere: a sandbox calls real infrastructure and invents the number, and this
invents nothing at all. What it lacks is not authenticity, it is coverage.

Everything downstream is the ordinary pipeline. The listings are written into the
catalogue through the same choke point a provider answer goes through, and the
analysis then runs offline, which is an existing mode meaning "use what is
stored, call nobody". Identity, profitability, scoring, risk and the decision are
the same code on the same data, so a hand-entered pair is judged by exactly the
standard an API-fed one is.

Two consequences are worth knowing before typing, because they are the system
working rather than failing:

**Leave the identifiers out and the match will not clear.** The identity ladder
caps a title-only match at 0.60, below the policy's confidence threshold, so the
pair will be analysed and then refused. Paste the UPC, EAN or model from both
pages and it resolves properly. This is the single thing worth the extra ten
seconds.

**Leave sales rank and seller count out and demand and competition report no
evidence.** They do not guess, and the risk engine already carries a category
marked unassessed rather than scoring it as safe. The profit arithmetic is
unaffected and is the part you came for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.errors import ValidationError
from app.core.security import AuthContext
from app.domains.history import capture
from app.models.enums import Availability, Condition, IdentifierType, Marketplace
from app.services.providers.base import (
    RawCompetitionPoint,
    RawDemandPoint,
    RawIdentifier,
    RawListing,
    RawPricePoint,
)

#: The provider name a hand-entered observation is attributed to. Not a provider
#: in the registry: nothing fetches, and the registry only knows about things
#: that can be called. Recorded as its own name so a person reading the history
#: can always tell which prices came from a human and which from an endpoint.
MANUAL_PROVIDER = "manual"

#: The capture source, alongside poll, analysis, refresh and provider_history.
SOURCE_MANUAL = "manual"

#: What a hand-entered observation is worth on the platform's own data quality
#: scale. High, and deliberately not perfect: a person read a real page, and a
#: person also mistypes, reads a subscription price, or misses that the offer
#: was from a third-party seller.
MANUAL_QUALITY = Decimal("85")

#: Identifier kinds a person can reasonably find on a product page, in the order
#: the ladder prefers them. Drawn from the enum rather than retyped, so a kind
#: that stops existing breaks here rather than being silently stored and never
#: matched against anything.
IDENTIFIER_FIELDS: tuple[str, ...] = (
    IdentifierType.GTIN.value,
    IdentifierType.UPC.value,
    IdentifierType.EAN.value,
    IdentifierType.ISBN.value,
    IdentifierType.ASIN.value,
    IdentifierType.MPN.value,
    IdentifierType.MODEL.value,
)

#: Everything the ladder understands. A kind outside this set is a typo, and a
#: typo stored as an identifier is worse than no identifier: it looks like
#: evidence and matches nothing.
KNOWN_IDENTIFIERS = frozenset(item.value for item in IdentifierType)


@dataclass
class ManualSide:
    """One side of the pair, as a person can read it off the page."""

    marketplace: Marketplace
    external_id: str
    title: str
    price: Decimal
    url: str | None = None
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    shipping: Decimal = Decimal("0")
    condition: Condition = Condition.NEW
    availability: Availability = Availability.UNKNOWN
    identifiers: dict[str, str] = field(default_factory=dict)
    #: Optional, and absent rather than zero when not supplied. Demand and
    #: competition report no evidence instead of a flattering default.
    sales_rank: int | None = None
    rank_category: str | None = None
    seller_count: int | None = None
    offer_count: int | None = None
    review_count: int | None = None
    rating: Decimal | None = None
    quantity_available: int | None = None
    observed_at: datetime | None = None
    note: str | None = None

    def as_raw(self) -> RawListing:
        identifiers = tuple(
            RawIdentifier(identifier_type=kind.strip().lower(), value=value.strip())
            for kind, value in self.identifiers.items()
            if value and value.strip()
        )
        attributes: dict[str, Any] = {"entered_by_hand": True}
        if self.note:
            attributes["note"] = self.note
        return RawListing(
            marketplace=self.marketplace,
            external_id=self.external_id.strip(),
            title=self.title.strip(),
            brand=self.brand,
            model=self.model,
            category=self.category,
            url=self.url,
            condition=self.condition,
            identifiers=identifiers,
            attributes=attributes,
            price=self.price,
            shipping=self.shipping,
            availability=self.availability,
            seller_count=self.seller_count,
            offer_count=self.offer_count,
            sales_rank=self.sales_rank,
            rank_category=self.rank_category,
            review_count=self.review_count,
            rating=self.rating,
            quantity_available=self.quantity_available,
            observed_at=self.observed_at or utcnow(),
            provider=MANUAL_PROVIDER,
        )


def _validate(side: ManualSide, label: str) -> None:
    for kind in side.identifiers:
        if kind.strip().lower() not in KNOWN_IDENTIFIERS:
            raise ValidationError(
                f"{kind!r} is not an identifier the matcher understands, so storing it "
                "would look like evidence and match nothing. Use one of: "
                f"{', '.join(sorted(KNOWN_IDENTIFIERS))}."
            )
    if not side.external_id.strip():
        raise ValidationError(
            f"The {label} listing needs an identifier from the page: the ASIN, the "
            "item number, or whatever the URL ends in. It is how the same product "
            "is recognised the next time you enter it."
        )
    if not side.title.strip():
        raise ValidationError(f"The {label} listing needs the product title from the page.")
    if side.price <= 0:
        raise ValidationError(
            f"The {label} price must be above zero. If the item is unavailable, that "
            "is not a price of nothing, it is an absence of one."
        )


def record_side(
    session: Session,
    auth: AuthContext,
    side: ManualSide,
) -> capture.CaptureResult:
    """Write one hand-entered listing into the catalogue and the history.

    Through the same choke point a provider answer goes through, so the
    observation carries the same shape, the same timestamps and the same
    identity resolution. Only the attribution differs.
    """
    raw = side.as_raw()
    observed = raw.observed_at

    demand_points = (
        [
            RawDemandPoint(
                sales_rank=side.sales_rank,
                rank_category=side.rank_category,
                review_count=side.review_count,
                rating=side.rating,
                observed_at=observed,
            )
        ]
        if side.sales_rank is not None or side.review_count is not None
        else None
    )
    competition_points = (
        [
            RawCompetitionPoint(
                seller_count=side.seller_count,
                offer_count=side.offer_count,
                observed_at=observed,
            )
        ]
        if side.seller_count is not None or side.offer_count is not None
        else None
    )

    return capture.capture_listing(
        session,
        auth.organization_id,
        raw,
        provider=MANUAL_PROVIDER,
        # A person read a real page. Nothing here is invented, which is why this
        # is the one non-provider source that counts as a market observation.
        is_simulated=False,
        source=SOURCE_MANUAL,
        price_points=[
            RawPricePoint(
                price=side.price,
                shipping=side.shipping,
                availability=side.availability,
                condition=side.condition,
                is_buy_box=False,
                observed_at=observed,
            )
        ],
        demand_points=demand_points,
        competition_points=competition_points,
        quality_score=MANUAL_QUALITY,
        retrieved_at=observed,
    )


def record_pair(
    session: Session,
    auth: AuthContext,
    *,
    source: ManualSide,
    target: ManualSide,
) -> dict[str, Any]:
    """Write both sides, ready for an offline analysis over them."""
    _validate(source, "source")
    _validate(target, "exit")
    if source.marketplace is target.marketplace:
        raise ValidationError(
            "The two sides have to be different marketplaces. Buying and selling in "
            "the same market is not a spread, it is a round trip."
        )

    record_side(session, auth, source)
    record_side(session, auth, target)
    session.flush()

    shared = _shared_identifiers(source, target)
    return {
        "recorded": 2,
        "shared_identifiers": shared,
        "warnings": _warnings(source, target, shared),
    }


def _shared_identifiers(source: ManualSide, target: ManualSide) -> list[str]:
    """Identifier kinds present on both sides, which is what the ladder needs."""
    left = {k.strip().lower(): (v or "").strip() for k, v in source.identifiers.items()}
    right = {k.strip().lower(): (v or "").strip() for k, v in target.identifiers.items()}
    return sorted(kind for kind in IDENTIFIER_FIELDS if left.get(kind) and right.get(kind))


def _warnings(source: ManualSide, target: ManualSide, shared: list[str]) -> list[str]:
    """What this pair will not be able to answer, said before it is analysed.

    Stated up front rather than discovered afterwards in a refusal, because the
    fix costs ten seconds at the keyboard and the refusal costs a repeat.
    """
    notes: list[str] = []
    if not shared:
        notes.append(
            "No identifier appears on both sides, so the match can only be made on "
            "the titles and the identity ladder caps a title-only match at 60%. That "
            "is below the confidence any policy will buy on, so this pair will be "
            "analysed and then refused. Paste the UPC, EAN or model number from both "
            "pages and it resolves properly."
        )
    if target.sales_rank is None and target.review_count is None:
        notes.append(
            "No sales rank or review count on the exit listing, so demand is reported "
            "as having no evidence rather than being guessed at. The profit arithmetic "
            "is unaffected."
        )
    if target.seller_count is None and target.offer_count is None:
        notes.append(
            "No seller or offer count on the exit listing, so competition is reported "
            "as having no evidence. A product with forty sellers and one with two look "
            "identical to the platform until you tell it."
        )
    if not target.category:
        notes.append(
            "No category on the exit listing, so the referral fee falls back to the "
            "marketplace default. The category is what the fee is keyed on, so this is "
            "the field most likely to move the profit figure."
        )
    return notes
