"""Import the Amazon Reviews 2023 open dataset.

The McAuley Lab dataset is the only genuinely open Amazon product data: 571
million reviews across 33 categories, with item metadata carrying parent ASIN,
title, store, categories, price at crawl time, rating and details. It is free,
and using it needs no account, no key and no agreement with anyone.

What it is not is live. The prices are as at the 2023 crawl, and that single fact
governs the whole design of this module:

* Every observation is written with ``observed_at`` set to the **crawl date**,
  never to now. Importing 2023 prices stamped as today would tell the statistics
  engine it has current data, poison every median, and make the anomaly detector
  compare today's real price against a three-year-old baseline while believing
  both were current.
* Every row is written with ``source="dataset"``, so it is distinguishable from a
  live poll in the database and in any audit.
* Because the observation is old, the existing freshness logic marks it stale on
  its own and the data-quality score drops accordingly. Nothing special is needed
  to make the platform cautious about it; the honesty is structural.

What it is good for: populating a realistic development catalogue with real
products, and backtesting, which needs historical prices with honest timestamps
far more than it needs current ones.

The files are gzipped JSONL, one product per line, named ``meta_<Category>.jsonl.gz``.
They are streamed rather than loaded, because a single category file is larger
than memory.
"""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import ensure_utc
from app.core.logging import get_logger
from app.core.money import money
from app.core.security import AuthContext
from app.domains.catalog import service as catalog
from app.models.enums import Availability, Condition, IdentifierType, Marketplace
from app.services.providers.base import RawIdentifier, RawListing, RawPricePoint

logger = get_logger(__name__)

#: The dataset's stated crawl cut-off. Every price is recorded as at this date.
#: Not "now", and not configurable by accident: getting this wrong is the one
#: mistake that would quietly corrupt every statistic downstream.
DEFAULT_CRAWL_DATE = datetime.fromisoformat("2023-09-30T00:00:00+00:00")

#: Weight keys seen in the ``details`` dict, in the order they are trusted.
_WEIGHT_KEYS = ("Item Weight", "Product Dimensions", "Shipping Weight", "Package Weight")
_WEIGHT_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(pounds?|lbs?|ounces?|oz|grams?|g|kilograms?|kg)\b", re.I
)
_TO_POUNDS = {
    "pound": Decimal("1"),
    "pounds": Decimal("1"),
    "lb": Decimal("1"),
    "lbs": Decimal("1"),
    "ounce": Decimal("0.0625"),
    "ounces": Decimal("0.0625"),
    "oz": Decimal("0.0625"),
    "gram": Decimal("0.00220462"),
    "grams": Decimal("0.00220462"),
    "g": Decimal("0.00220462"),
    "kilogram": Decimal("2.20462"),
    "kilograms": Decimal("2.20462"),
    "kg": Decimal("2.20462"),
}


#: Why a row was not imported. A row is skipped, never defaulted: a product with
#: no price is not a product priced at zero, and one with no identifier cannot be
#: matched against anything later.
SkipReason = str

NO_ASIN: SkipReason = "no_asin"
NO_TITLE: SkipReason = "no_title"
NO_PRICE: SkipReason = "no_price"


@dataclass
class PreparedRow:
    """One dataset row, mapped and judged, before anything is written.

    Both the real import and the dry run consume these, so what a preview
    reports and what an import actually does cannot drift apart.
    """

    listing: RawListing | None
    skipped: SkipReason | None

    @property
    def is_usable(self) -> bool:
        return self.listing is not None and self.skipped is None


@dataclass
class ImportStats:
    read: int = 0
    #: Rows that mapped cleanly and carry a price. A dry run reports this; a real
    #: import writes exactly these, so the two counts agree.
    usable: int = 0
    imported: int = 0
    skipped_no_price: int = 0
    skipped_no_asin: int = 0
    skipped_no_title: int = 0
    price_observations: int = 0
    with_weight: int = 0

    def record(self, row: PreparedRow) -> None:
        """Count one prepared row. Writing, if any, is the caller's business."""
        self.read += 1
        if row.skipped == NO_ASIN:
            self.skipped_no_asin += 1
            return
        if row.skipped == NO_TITLE:
            self.skipped_no_title += 1
            return
        if row.skipped == NO_PRICE:
            self.skipped_no_price += 1
            return

        self.usable += 1
        # Counted only over rows that are actually imported, because the figure
        # is reported next to the import count and reads as a share of it.
        if row.listing is not None and row.listing.attributes.get("weight_lb"):
            self.with_weight += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "read": self.read,
            "usable": self.usable,
            "imported": self.imported,
            "skipped_no_price": self.skipped_no_price,
            "skipped_no_asin": self.skipped_no_asin,
            "skipped_no_title": self.skipped_no_title,
            "price_observations": self.price_observations,
            "with_weight": self.with_weight,
        }


def parse_price(value: Any) -> Decimal | None:
    """Parse the dataset's price field.

    Documented as a float, but the real files carry ``None``, empty strings and
    occasional strings like ``"$12.99"`` or ``"from $9.99"``. A row whose price
    cannot be read is skipped rather than defaulted: a product with no price is
    not a product priced at zero.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return money(str(value)) if value > 0 else None
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return None
    parsed = money(match.group(0))
    return parsed if parsed > 0 else None


def parse_weight_lb(details: Any) -> Decimal | None:
    """Pull a shipping weight in pounds out of the free-text ``details`` dict.

    The fee engine needs a weight, and without one it assumes a default and says
    so in its warnings. Recovering a real weight here removes that assumption for
    a large share of rows.
    """
    if not isinstance(details, dict):
        return None
    for key in _WEIGHT_KEYS:
        raw = details.get(key)
        if not raw:
            continue
        match = _WEIGHT_PATTERN.search(str(raw))
        if not match:
            continue
        amount, unit = match.group(1), match.group(2).lower()
        factor = _TO_POUNDS.get(unit)
        if factor is None:
            continue
        weight = Decimal(amount) * factor
        # A plausibility guard: dimension strings sometimes parse into absurd
        # weights, and a wrong weight silently changes every fulfilment fee.
        if Decimal("0.01") <= weight <= Decimal("150"):
            return weight.quantize(Decimal("0.01"))
    return None


def parse_category(item: dict[str, Any]) -> str | None:
    """The most specific category available, which is what fee tables key on."""
    categories = item.get("categories")
    if isinstance(categories, list) and categories:
        leaf = categories[-1]
        if isinstance(leaf, str) and leaf.strip():
            return leaf.strip()
    main = item.get("main_category")
    return main.strip() if isinstance(main, str) and main.strip() else None


def to_listing(item: dict[str, Any], *, observed_at: datetime) -> RawListing | None:
    """Map one dataset row to a ``RawListing``, or None when it is unusable."""
    asin = str(item.get("parent_asin") or "").strip()
    title = str(item.get("title") or "").strip()
    if not asin or not title:
        return None

    price = parse_price(item.get("price"))
    store = item.get("store")
    details = item.get("details") if isinstance(item.get("details"), dict) else {}

    attributes: dict[str, Any] = {"dataset": "amazon_reviews_2023"}
    weight = parse_weight_lb(details)
    if weight is not None:
        attributes["weight_lb"] = str(weight)
    for key in ("Brand", "Manufacturer", "Color", "Size", "Material"):
        if details.get(key):
            attributes[key.lower()] = str(details[key])

    model = details.get("Item model number") or details.get("Model Number")

    return RawListing(
        marketplace=Marketplace.AMAZON,
        external_id=asin,
        title=title,
        brand=(str(store).strip() or None) if store else None,
        manufacturer=(str(details.get("Manufacturer")).strip() or None)
        if details.get("Manufacturer")
        else None,
        model=str(model).strip() if model else None,
        category=parse_category(item),
        sku=None,
        url=f"https://www.amazon.com/dp/{asin}",
        image_url=None,
        condition=Condition.NEW,
        identifiers=(RawIdentifier(identifier_type=IdentifierType.ASIN.value, value=asin),),
        attributes=attributes,
        price=price,
        shipping=None,
        # The dataset says nothing about stock, and unknown is the honest answer.
        availability=Availability.UNKNOWN,
        seller_count=None,
        offer_count=None,
        sales_rank=None,
        rank_category=None,
        review_count=item.get("rating_number")
        if isinstance(item.get("rating_number"), int)
        else None,
        rating=(
            Decimal(str(item["average_rating"]))
            if isinstance(item.get("average_rating"), (int, float))
            else None
        ),
        quantity_available=None,
        observed_at=observed_at,
        provider="amazon_reviews_2023",
    )


def read_rows(path: Path, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Stream a gzipped JSONL metadata file.

    Streamed line by line because one category file is larger than memory, and a
    malformed line is skipped rather than aborting an import that is otherwise
    hours in.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for index, line in enumerate(handle):
            if limit is not None and index >= limit:
                return
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.debug("skipping malformed line", extra={"context": {"line": index}})


def prepare(item: dict[str, Any], *, observed_at: datetime) -> PreparedRow:
    """Map one dataset row and say whether it can be used, without writing."""
    listing = to_listing(item, observed_at=observed_at)
    if listing is None:
        missing = NO_ASIN if not str(item.get("parent_asin") or "").strip() else NO_TITLE
        return PreparedRow(listing=None, skipped=missing)
    if listing.price is None:
        # Without a price there is no economics to compute, so the row would
        # only add noise to the catalogue.
        return PreparedRow(listing=listing, skipped=NO_PRICE)
    return PreparedRow(listing=listing, skipped=None)


def scan_file(
    path: Path,
    *,
    crawl_date: datetime = DEFAULT_CRAWL_DATE,
    limit: int | None = None,
) -> ImportStats:
    """Read a file and report what an import would do, writing nothing.

    A category file takes a long time to import. Being able to check what is in
    one first, against the same mapping the import uses, is worth the read.
    """
    observed_at = ensure_utc(crawl_date)
    stats = ImportStats()
    for item in read_rows(path, limit=limit):
        stats.record(prepare(item, observed_at=observed_at))
    return stats


def import_file(
    session: Session,
    auth: AuthContext,
    path: Path,
    *,
    crawl_date: datetime = DEFAULT_CRAWL_DATE,
    limit: int | None = None,
    commit_every: int = 500,
) -> ImportStats:
    """Import one ``meta_<Category>.jsonl.gz`` file into the catalogue."""
    observed_at = ensure_utc(crawl_date)
    stats = ImportStats()

    for item in read_rows(path, limit=limit):
        prepared = prepare(item, observed_at=observed_at)
        stats.record(prepared)
        listing = prepared.listing
        if prepared.skipped is not None or listing is None or listing.price is None:
            continue

        row = catalog.upsert_listing(session, auth.organization_id, listing)
        catalog.resolve_product(session, auth.organization_id, listing, row)
        # Real observations of a real market, stated explicitly.
        #
        # The default resolves liveness through the provider registry, and this
        # importer is not a registered provider, so it would fall through to
        # "simulated". That would be wrong: these are prices Amazon actually
        # showed, from a published crawl. What makes them unusable as current
        # data is their *age*, which the 2023 timestamp already says, and the
        # freshness logic already acts on. Conflating old with fabricated would
        # lose the distinction the whole layer exists to preserve.
        stamp = catalog.observation_stamp(
            session,
            row,
            provider="amazon_reviews_2023",
            is_simulated=False,
            retrieved_at=observed_at,
        )
        stats.price_observations += catalog.record_price_observations(
            session,
            auth.organization_id,
            row,
            [
                RawPricePoint(
                    price=listing.price,
                    shipping=Decimal("0"),
                    availability=Availability.UNKNOWN,
                    condition=Condition.NEW,
                    is_buy_box=False,
                    # Dated to the crawl, never to now. This is the line that
                    # keeps three-year-old prices from being treated as current.
                    observed_at=observed_at,
                )
            ],
            provider="amazon_reviews_2023",
            source="dataset",
            stamp=stamp,
        )
        stats.imported += 1
        if stats.imported % commit_every == 0:
            session.commit()

    session.commit()
    logger.info("dataset import complete", extra={"context": stats.as_dict()})
    return stats
