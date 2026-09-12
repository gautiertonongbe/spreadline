"""Bulk analysis (spec §28).

Takes a CSV of identifiers or marketplace URLs, normalises each row, runs the
full analysis pipeline over all of them with bounded concurrency, and ranks the
results.

Per-row failures are contained: one bad identifier in a 400-row file must not
lose the other 399 results, so every row's error is captured and returned
alongside the successes rather than raised.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.domains.identity.normalization import normalize_asin, normalize_gtin
from app.domains.opportunities.analysis import AnalysisOptions, AnalysisResult, analyze_pair
from app.models.enums import IdentifierType, Marketplace
from app.services.providers.base import ProviderCapability
from app.services.providers.registry import ProviderRegistry, get_registry

logger = get_logger(__name__)

#: Rows per request. A cap exists so a pasted 50,000-row file fails fast with a
#: clear message instead of exhausting provider quota silently.
MAX_ROWS = 500

_AMAZON_URL = re.compile(r"amazon\.[a-z.]+/(?:.*/)?(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", re.I)
_WALMART_URL = re.compile(r"walmart\.com/ip/(?:[^/]+/)?(\d+)", re.I)

#: Column names accepted for each field, so an operator's own export works
#: without being reshaped by hand first.
COLUMN_ALIASES = {
    "identifier": {"identifier", "id", "input", "value", "product", "sku", "query"},
    "asin": {"asin", "amazon_asin", "amazon_id"},
    "walmart_id": {"walmart_id", "walmart_item_id", "item_id", "wmid"},
    "upc": {"upc", "gtin", "ean", "barcode"},
    "url": {"url", "link", "product_url"},
    "source_marketplace": {"source_marketplace", "source", "buy_from"},
    "target_marketplace": {"target_marketplace", "target", "sell_on"},
    "acquisition_cost": {"acquisition_cost", "cost", "buy_price", "unit_cost"},
    "notes": {"notes", "note", "comment"},
}


@dataclass
class BulkRow:
    """One normalised input row."""

    line_number: int
    raw: dict[str, str]
    marketplace: Marketplace | None = None
    external_id: str | None = None
    identifier_type: str | None = None
    identifier_value: str | None = None
    source_marketplace: Marketplace | None = None
    target_marketplace: Marketplace | None = None
    acquisition_cost: Decimal | None = None
    notes: str | None = None
    error: str | None = None

    @property
    def is_resolvable(self) -> bool:
        return self.error is None and (
            (self.marketplace is not None and self.external_id is not None)
            or self.identifier_value is not None
        )


@dataclass
class BulkRowResult:
    line_number: int
    input: str
    status: str  # "analyzed" | "skipped" | "error"
    result: AnalysisResult | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "line_number": self.line_number,
            "input": self.input,
            "status": self.status,
            "message": self.message,
        }
        if self.result is not None:
            context = self.result.context
            payload.update(
                {
                    "opportunity_id": self.result.opportunity_id,
                    "title": context.title,
                    "recommendation": self.result.decision.recommendation.value,
                    "score": str(self.result.score.total),
                    "net_profit": str(context.profitability.net_profit),
                    "roi": None
                    if context.profitability.roi is None
                    else str(context.profitability.roi),
                    "risk_level": self.result.risk.level.value,
                    "match_confidence": str(context.match.confidence),
                    "headline": self.result.decision.headline,
                }
            )
        return payload


@dataclass
class BulkAnalysisResult:
    rows: list[BulkRowResult]
    total: int
    analyzed: int
    errors: int
    skipped: int
    notes: list[str] = field(default_factory=list)

    @property
    def ranked(self) -> list[BulkRowResult]:
        """Successful rows, best first. Gated candidates score zero and sink."""
        scored = [row for row in self.rows if row.result is not None]
        return sorted(scored, key=lambda row: row.result.score.total, reverse=True)  # type: ignore[union-attr]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "analyzed": self.analyzed,
            "errors": self.errors,
            "skipped": self.skipped,
            "notes": self.notes,
            "results": [row.as_dict() for row in self.ranked],
            "failures": [row.as_dict() for row in self.rows if row.status in {"error", "skipped"}],
        }

    def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "line",
                "input",
                "status",
                "title",
                "recommendation",
                "score",
                "net_profit",
                "roi",
                "risk_level",
                "match_confidence",
                "headline",
            ]
        )
        for row in self.ranked:
            payload = row.as_dict()
            writer.writerow(
                [
                    payload["line_number"],
                    payload["input"],
                    payload["status"],
                    payload.get("title", ""),
                    payload.get("recommendation", ""),
                    payload.get("score", ""),
                    payload.get("net_profit", ""),
                    payload.get("roi", ""),
                    payload.get("risk_level", ""),
                    payload.get("match_confidence", ""),
                    payload.get("headline", ""),
                ]
            )
        for row in self.rows:
            if row.status in {"error", "skipped"}:
                writer.writerow(
                    [
                        row.line_number,
                        row.input,
                        row.status,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        row.message,
                    ]
                )
        return buffer.getvalue()


def _canonical_column(name: str) -> str | None:
    key = name.strip().lower().replace(" ", "_")
    for canonical, aliases in COLUMN_ALIASES.items():
        if key in aliases:
            return canonical
    return None


def classify_identifier(
    value: str,
) -> tuple[Marketplace | None, str | None, str | None, str | None]:
    """Work out what a single pasted value is.

    Returns (marketplace, external_id, identifier_type, identifier_value). An
    operator pastes URLs, ASINs, item ids and barcodes interchangeably, and
    making them retype it into the right column is how a bulk tool goes unused.
    """
    candidate = value.strip()
    if not candidate:
        return None, None, None, None

    amazon = _AMAZON_URL.search(candidate)
    if amazon:
        return Marketplace.AMAZON, amazon.group(1).upper(), None, None
    walmart = _WALMART_URL.search(candidate)
    if walmart:
        return Marketplace.WALMART, walmart.group(1), None, None

    gtin = normalize_gtin(candidate)
    if gtin:
        return None, None, IdentifierType.UPC.value, candidate

    asin = normalize_asin(candidate)
    # An ASIN is ten alphanumerics; a Walmart item id is digits only, and a
    # ten-digit number is far more likely to be the latter.
    if asin and not candidate.strip().isdigit():
        return Marketplace.AMAZON, asin, None, None

    if candidate.isdigit() and 6 <= len(candidate) <= 12:
        return Marketplace.WALMART, candidate, None, None

    # Anything else that still looks like an identifier is handed to the
    # providers to resolve rather than rejected here. Operators paste vendor
    # SKUs, catalogue codes and marketplace-specific ids that this function has
    # no way to recognise, and a lookup that comes back empty is a better answer
    # than a parse error that never tried.
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{3,63}", candidate):
        return None, None, None, candidate

    return None, None, None, None


def parse_csv(content: str) -> list[BulkRow]:
    """Parse a CSV (or a bare newline-separated list) into normalised rows."""
    text = content.strip()
    if not text:
        raise ValidationError("The uploaded file is empty.")

    sample = text[:4096]
    has_header = any(
        _canonical_column(cell) is not None for cell in next(csv.reader(io.StringIO(sample)), [])
    )

    rows: list[BulkRow] = []
    reader = csv.reader(io.StringIO(text))
    header: list[str] = []

    for index, cells in enumerate(reader, start=1):
        if not cells or all(not cell.strip() for cell in cells):
            continue
        if index == 1 and has_header:
            header = [(_canonical_column(cell) or cell.strip().lower()) for cell in cells]
            continue

        raw = (
            {
                header[position]: cell.strip()
                for position, cell in enumerate(cells)
                if position < len(header)
            }
            if header
            else {"identifier": cells[0].strip()}
        )
        row = BulkRow(line_number=index, raw=raw)

        explicit = raw.get("asin") or raw.get("walmart_id") or raw.get("upc")
        value = raw.get("identifier") or raw.get("url") or explicit or ""
        if raw.get("asin"):
            row.marketplace, row.external_id = Marketplace.AMAZON, raw["asin"].upper()
        elif raw.get("walmart_id"):
            row.marketplace, row.external_id = Marketplace.WALMART, raw["walmart_id"]
        else:
            marketplace, external_id, identifier_type, identifier_value = classify_identifier(value)
            row.marketplace = marketplace
            row.external_id = external_id
            row.identifier_type = identifier_type
            row.identifier_value = identifier_value

        for key, target in (
            ("source_marketplace", "source_marketplace"),
            ("target_marketplace", "target_marketplace"),
        ):
            if raw.get(key):
                try:
                    setattr(row, target, Marketplace(raw[key].strip().lower()))
                except ValueError:
                    row.error = f"Unknown marketplace '{raw[key]}'."

        if raw.get("acquisition_cost"):
            try:
                row.acquisition_cost = Decimal(raw["acquisition_cost"].replace("$", "").strip())
            except (ValueError, ArithmeticError):
                row.error = f"Could not read acquisition cost '{raw['acquisition_cost']}'."

        row.notes = raw.get("notes")
        if row.error is None and not row.is_resolvable:
            row.error = (
                f"Could not identify '{value}'. Provide an ASIN, a Walmart item id, "
                "a UPC/EAN, or a marketplace URL."
            )
        rows.append(row)

    if not rows:
        raise ValidationError("No usable rows were found in the file.")
    if len(rows) > MAX_ROWS:
        raise ValidationError(
            f"{len(rows)} rows submitted; the limit is {MAX_ROWS} per request. "
            "Split the file so provider quota stays predictable."
        )
    return rows


async def _resolve_row(
    row: BulkRow, registry: ProviderRegistry, default_source: Marketplace
) -> tuple[Marketplace, str] | None:
    """Turn an identifier-only row into a concrete listing on some marketplace."""
    if row.marketplace is not None and row.external_id is not None:
        return row.marketplace, row.external_id
    if row.identifier_value is None:
        return None

    order = [default_source] + [
        item for item in (Marketplace.AMAZON, Marketplace.WALMART) if item is not default_source
    ]
    for marketplace in order:
        try:
            provider = registry.for_marketplace(marketplace, capability=ProviderCapability.PRODUCT)
            raw = await provider.get_product_by_identifier(
                row.identifier_type or IdentifierType.UPC.value, row.identifier_value
            )
        except Exception as exc:  # noqa: BLE001 - one marketplace failing is not fatal
            logger.info(
                "bulk identifier lookup failed",
                extra={"context": {"marketplace": marketplace.value, "error": str(exc)}},
            )
            continue
        if raw is not None:
            return marketplace, raw.external_id
    return None


async def run_bulk_analysis(
    session: Session,
    auth: AuthContext,
    rows: Sequence[BulkRow],
    *,
    default_source: Marketplace = Marketplace.WALMART,
    default_target: Marketplace = Marketplace.AMAZON,
    options: AnalysisOptions | None = None,
    registry: ProviderRegistry | None = None,
) -> BulkAnalysisResult:
    """Analyse every resolvable row, one at a time against a shared session.

    Rows run sequentially rather than concurrently: they share one database
    session, and SQLAlchemy sessions are not safe to use from several tasks at
    once. Concurrency belongs at the provider layer, where the rate limiter can
    see it.
    """
    options = options or AnalysisOptions()
    registry = registry or get_registry()
    results: list[BulkRowResult] = []
    notes: list[str] = []

    for row in rows:
        label = row.raw.get("identifier") or row.raw.get("url") or str(row.raw)
        if row.error:
            results.append(BulkRowResult(row.line_number, label, "error", message=row.error))
            continue

        resolved = await _resolve_row(row, registry, row.source_marketplace or default_source)
        if resolved is None:
            results.append(
                BulkRowResult(
                    row.line_number,
                    label,
                    "skipped",
                    message=(f"No listing found for '{label}' on any configured marketplace."),
                )
            )
            continue

        marketplace, external_id = resolved
        source_marketplace = row.source_marketplace or marketplace
        target_marketplace = row.target_marketplace or (
            default_target if source_marketplace is not default_target else default_source
        )

        try:
            result = await analyze_pair(
                session,
                auth,
                source_marketplace=source_marketplace,
                source_external_id=external_id,
                target_marketplace=target_marketplace,
                options=options,
                registry=registry,
            )
        except Exception as exc:  # noqa: BLE001 - a bad row must not lose the file
            session.rollback()
            logger.info(
                "bulk row failed", extra={"context": {"line": row.line_number, "error": str(exc)}}
            )
            results.append(BulkRowResult(row.line_number, label, "error", message=str(exc)[:300]))
            continue

        session.commit()
        results.append(BulkRowResult(row.line_number, label, "analyzed", result=result))

    analyzed = len([row for row in results if row.status == "analyzed"])
    errors = len([row for row in results if row.status == "error"])
    skipped = len([row for row in results if row.status == "skipped"])
    if errors:
        notes.append(f"{errors} row(s) could not be analysed; each carries its own reason.")

    return BulkAnalysisResult(
        rows=results,
        total=len(rows),
        analyzed=analyzed,
        errors=errors,
        skipped=skipped,
        notes=notes,
    )
