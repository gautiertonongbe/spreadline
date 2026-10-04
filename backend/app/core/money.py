"""Deterministic money and ratio arithmetic.

Financial results in Spreadline are never produced by floating point and never
by a language model. Every monetary value is a Decimal quantized at a single
documented precision, and every ratio is rounded once, at the edge, for
presentation.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: Money is stored and computed at 4 decimal places and presented at 2. Fees are
#: frequently fractions of a cent per unit; truncating them at 2 places during
#: intermediate steps introduces drift that shows up as unexplained pennies.
MONEY_PRECISION = Decimal("0.0001")
CENT = Decimal("0.01")
RATIO_PRECISION = Decimal("0.0001")

ZERO = Decimal("0")


def to_decimal(
    value: Decimal | int | float | str | None, default: Decimal | None = None
) -> Decimal:
    """Coerce a value to Decimal without ever going through binary float parsing.

    floats are routed through ``str`` so that 0.1 becomes Decimal("0.1") rather
    than Decimal("0.1000000000000000055511151231257827021181583404541015625").
    """
    if value is None:
        if default is None:
            raise ValueError("cannot coerce None to Decimal without a default")
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"not a decimal value: {value!r}") from exc


def money(value: Decimal | int | float | str | None, default: Decimal | None = None) -> Decimal:
    """Quantize to storage precision, rounding half up (the accounting default)."""
    return to_decimal(value, default).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)


def cents(value: Decimal | int | float | str) -> Decimal:
    """Quantize to presentation precision."""
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def ratio(value: Decimal | int | float | str) -> Decimal:
    return to_decimal(value).quantize(RATIO_PRECISION, rounding=ROUND_HALF_UP)


def pct_of(value: Decimal, base: Decimal) -> Decimal | None:
    """value / base as a ratio, or None when the base is zero.

    Returning None rather than 0 matters: an ROI of "unknown because acquisition
    cost is zero" is not an ROI of 0%, and the difference changes decisions.
    """
    base = to_decimal(base)
    if base == 0:
        return None
    return ratio(to_decimal(value) / base)


def apply_pct(value: Decimal, pct: Decimal) -> Decimal:
    """value * pct, quantized as money. ``pct`` is a ratio (0.15 == 15%)."""
    return money(to_decimal(value) * to_decimal(pct))


def safe_mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return money(sum(values, ZERO) / Decimal(len(values)))


def display(value: Decimal | int | float | str | None) -> str:
    """Format a money amount for a human-readable string.

    Storage precision is 4 decimal places so intermediate fee arithmetic does not
    drift, but "71.6496 profit" in an explanation reads as noise. Anything the
    operator reads goes through here; anything a machine reads keeps full
    precision.
    """
    if value is None:
        return "not available"
    return f"{cents(value):,.2f}"


def display_currency(value: Decimal | int | float | str | None, currency: str = "USD") -> str:
    """Format a money amount as a person reads it, with its symbol.

    ``display`` deliberately leaves the currency off, because most of its uses
    sit next to a column header or a label that already says what the number is.
    Prose is different: "makes 71.65 per item" reads as a quantity of something
    unnamed. Anything written as a sentence for a buyer goes through here.

    The symbol table is small on purpose. An unknown currency gets its code
    rather than a guessed symbol, because the wrong symbol is worse than none.
    """
    if value is None:
        return "not available"
    amount = display(value)
    symbol = {"USD": "$", "GBP": "£", "EUR": "€"}.get(currency.upper())
    return f"{symbol}{amount}" if symbol else f"{amount} {currency.upper()}"


def display_score(value: Decimal | int | float | None) -> str:
    """Format a 0-100 score for display, without false precision."""
    if value is None:
        return "not available"
    return f"{to_decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP)}"
