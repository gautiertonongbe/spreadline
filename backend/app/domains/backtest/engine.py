"""Replaying a policy against the history Spreadline recorded itself.

This answers the question that has to be answerable before real capital moves:
*given what we knew at the time, what would this policy have done, and what
would have happened?*

It is the strongest evidence available short of spending money, and it is
worthless if it cheats. Three properties make it honest, and each of them costs
something:

**No lookahead.** At simulated instant T only observations with
``observed_at <= T`` are visible: prices are read by carrying the last
observation forward, and an exit is only found among observations *after* the
entry. A backtest that can see tomorrow's price is not a backtest, it is a
memory of the answer, and it will report a strategy as brilliant right up to the
day it loses money.

**The real engine for the money.** Profitability is recomputed on every
simulated day by ``calculate_profitability``, the same function the live path
calls, at that day's two prices. Fees, break-even and the ceiling are therefore
the real ones. A backtest written against a simplified fee model measures the
simplified fee model.

**The exit is an assumption, and it is labelled one.** Nothing in the data says
whether a unit *would* have sold. The replay states its rule, applies it
consistently, and reports it alongside the result, because a return figure whose
selling assumption is hidden is a number pretending to be a measurement.

What this replay does **not** re-simulate, stated plainly because the value of
the result depends on knowing it:

* **Identity is held constant.** Legitimate: for a fixed pair of listings the
  match does not change with time.
* **Risk level, demand and competition are held at their analysed values.**
  Not because that is ideal but because the observation history for those
  signals is thinner than for price, and re-deriving a risk level from two
  demand points would be inventing precision. So this replays *the policy's
  price-driven thresholds* - profit, return, position sizing, the age limit -
  against real price movement, with the non-price evidence pinned.
* **Fee assumptions are today's**, not the ones in force historically.

That makes it an honest answer to "how would this policy have handled the way
these prices actually moved", and not an answer to "would the whole pipeline
have reached the same conclusions". The second needs a longer history of demand
and competition than the platform has yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc, iso_utc, utcnow
from app.core.money import display_currency, money, pct_of
from app.core.security import AuthContext
from app.domains.autonomy.policy import PolicyView
from app.domains.profitability.engine import ProfitabilityInput, calculate_profitability
from app.models.catalog import MarketplaceListing
from app.models.enums import Marketplace, RiskLevel
from app.models.observations import PriceObservation
from app.models.opportunity import Opportunity

#: Minimum distinct days of price history on both sides before a pair may be
#: replayed at all. Below this the replay would be inventing a market.
MIN_DAYS_COVERAGE = 10


class ExitRule:
    """How the replay decides a position was sold.

    ``at_market`` sells the whole position on the first day the exit price at or
    above the entry's expected sale price appears, and otherwise liquidates at
    the market price on the day the age limit is reached. It assumes the
    operator was listed, priced at the market and able to sell the quantity.

    That assumption is generous and is why it is named. A real listing competes
    for the buy box, sells in ones and twos, and sometimes does not sell at all.
    The replay reports the rule with the result so the figure is read as what it
    is: what the market would have allowed, not what would have happened.
    """

    AT_MARKET = "at_market"


@dataclass
class ReplayTrade:
    """One simulated position, opened and closed by the rules."""

    opportunity_id: str
    title: str
    opened_at: datetime
    entry_price: Decimal
    expected_sale_price: Decimal
    expected_unit_profit: Decimal
    quantity: int
    capital: Decimal
    closed_at: datetime | None = None
    exit_price: Decimal | None = None
    realized_profit: Decimal | None = None
    exit_reason: str = "still open"

    @property
    def days_held(self) -> int:
        if self.closed_at is None:
            return 0
        return max(0, (ensure_utc(self.closed_at) - ensure_utc(self.opened_at)).days)

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "title": self.title,
            "opened_at": iso_utc(self.opened_at),
            "closed_at": iso_utc(self.closed_at),
            "entry_price": str(self.entry_price),
            "expected_sale_price": str(self.expected_sale_price),
            "exit_price": None if self.exit_price is None else str(self.exit_price),
            "quantity": self.quantity,
            "capital": str(self.capital),
            "expected_profit": str(money(self.expected_unit_profit * self.quantity)),
            "realized_profit": (
                None if self.realized_profit is None else str(self.realized_profit)
            ),
            "days_held": self.days_held,
            "exit_reason": self.exit_reason,
            "is_closed": self.is_closed,
        }


@dataclass
class Rejection:
    """A day a candidate was looked at and not bought, with the cause."""

    opportunity_id: str
    at: datetime
    reason_code: str
    detail: str


@dataclass
class RefusalGroup:
    """One candidate refused for one reason, across every day it happened.

    A day-by-day list is the wrong shape to read: the same product refused for
    the same reason on ninety consecutive days is one fact, and printing it
    ninety times buries the refusals that only happened once. The count and the
    span are what carry the information, and the detail is the most recent one
    because the figures in it move with the price.
    """

    opportunity_id: str
    title: str
    reason_code: str
    detail: str
    days: int
    first_at: datetime
    last_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "title": self.title,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "days": self.days,
            "first_at": iso_utc(self.first_at),
            "last_at": iso_utc(self.last_at),
        }


@dataclass
class BacktestResult:
    started_at: datetime
    ended_at: datetime
    policy_version: str
    exit_rule: str
    starting_capital: Decimal
    trades: list[ReplayTrade] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    #: Titles for anything named in the output, filled once the candidates are
    #: known. Missing entries fall back to the identifier rather than to "".
    titles: dict[str, str] = field(default_factory=dict)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    #: Share of the observations used that were fixture rather than market data.
    simulated_share: Decimal = Decimal("0")
    observations_used: int = 0

    @property
    def closed(self) -> list[ReplayTrade]:
        return [trade for trade in self.trades if trade.is_closed]

    @property
    def realized_profit(self) -> Decimal:
        return money(
            sum((trade.realized_profit or Decimal("0") for trade in self.closed), Decimal("0"))
        )

    @property
    def capital_committed(self) -> Decimal:
        return money(sum((trade.capital for trade in self.closed), Decimal("0")))

    @property
    def realized_roi(self) -> Decimal | None:
        return pct_of(self.realized_profit, self.capital_committed)

    @property
    def win_rate(self) -> Decimal | None:
        if not self.closed:
            return None
        winners = [t for t in self.closed if (t.realized_profit or Decimal("0")) > 0]
        return pct_of(Decimal(len(winners)), Decimal(len(self.closed)))

    @property
    def worst_trade(self) -> Decimal | None:
        """The largest single realised loss as a share of the capital in it.

        Position-level rather than a portfolio equity curve, for the same reason
        the live scorecard does it this way: a daily mark-to-market series would
        have to be invented from prices nobody was offered.
        """
        losses = [
            pct_of(abs(trade.realized_profit or Decimal("0")), trade.capital)
            for trade in self.closed
            if (trade.realized_profit or Decimal("0")) < 0 and trade.capital > 0
        ]
        real = [value for value in losses if value is not None]
        return max(real) if real else (Decimal("0") if self.closed else None)

    @property
    def accuracy(self) -> Decimal | None:
        """How close the predicted profit was to the replayed one."""
        pairs = [
            (money(trade.expected_unit_profit * trade.quantity), trade.realized_profit)
            for trade in self.closed
            if trade.realized_profit is not None
        ]
        usable = [(expected, actual) for expected, actual in pairs if expected != 0]
        if not usable:
            return None
        errors = [abs((actual - expected) / abs(expected)) for expected, actual in usable]
        return max(Decimal("0"), Decimal("1") - (sum(errors) / Decimal(len(errors))))

    @property
    def summary(self) -> str:
        if not self.trades:
            return "The policy would have bought nothing over this window. " + (
                f"{len(self.refusals)} candidate(s) were considered and refused across "
                f"{len(self.rejections)} candidate-day(s)."
                if self.rejections
                else "No candidate had enough history to judge."
            )
        parts = [
            f"{len(self.trades)} position(s) opened, {len(self.closed)} closed.",
            f"{display_currency(self.capital_committed)} committed, "
            f"{display_currency(self.realized_profit)} realised",
        ]
        roi = self.realized_roi
        if roi is not None:
            parts.append(f"({roi:.1%} return)")
        if self.simulated_share > 0:
            parts.append(
                f"{self.simulated_share:.0%} of the observations behind this were fixture "
                "data, not a market record."
            )
        return " ".join(parts)

    @property
    def refusals(self) -> list[RefusalGroup]:
        """Refusals collapsed to one row per candidate and cause.

        Ordered by how often the refusal happened, so the limit that did most of
        the refusing is the first thing read.
        """
        groups: dict[tuple[str, str], RefusalGroup] = {}
        for rejection in self.rejections:
            key = (rejection.opportunity_id, rejection.reason_code)
            existing = groups.get(key)
            if existing is None:
                groups[key] = RefusalGroup(
                    opportunity_id=rejection.opportunity_id,
                    title=self.titles.get(rejection.opportunity_id, rejection.opportunity_id),
                    reason_code=rejection.reason_code,
                    detail=rejection.detail,
                    days=1,
                    first_at=rejection.at,
                    last_at=rejection.at,
                )
                continue
            existing.days += 1
            existing.last_at = rejection.at
            existing.detail = rejection.detail
        return sorted(groups.values(), key=lambda group: (-group.days, group.title))

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": {
                "from": iso_utc(self.started_at),
                "to": iso_utc(self.ended_at),
                "days": max(0, (ensure_utc(self.ended_at) - ensure_utc(self.started_at)).days),
            },
            "policy_version": self.policy_version,
            "exit_rule": self.exit_rule,
            "starting_capital": str(self.starting_capital),
            "summary": self.summary,
            "results": {
                "positions_opened": len(self.trades),
                "positions_closed": len(self.closed),
                "capital_committed": str(self.capital_committed),
                "realized_profit": str(self.realized_profit),
                "realized_roi": None if self.realized_roi is None else str(self.realized_roi),
                "win_rate": None if self.win_rate is None else str(self.win_rate),
                "worst_trade": None if self.worst_trade is None else str(self.worst_trade),
                "prediction_accuracy": None if self.accuracy is None else str(self.accuracy),
            },
            "evidence": {
                "observations_used": self.observations_used,
                "simulated_share": str(self.simulated_share),
                "skipped": self.skipped,
            },
            "trades": [trade.as_dict() for trade in self.trades],
            "refusals": [group.as_dict() for group in self.refusals],
            "caveats": _caveats(self),
        }


def _caveats(result: BacktestResult) -> list[str]:
    """What this result does not prove. Always present, never optional."""
    notes = [
        "Every figure here is what the market would have allowed, not what would "
        "have happened. Whether a unit actually sells depends on the listing, the "
        "buy box and the competition on the day.",
        f"Exits use the '{result.exit_rule}' rule: the whole position clears on the "
        "first day the exit price reaches the entry's expected sale price, and is "
        "liquidated at the market price if the age limit is reached first.",
        "Identity is held constant, which is legitimate: the match does not change "
        "over time for a fixed pair of listings.",
        "Risk, demand and competition are held at their analysed values. This replays "
        "the policy's price-driven thresholds against real price movement, not the "
        "whole pipeline: the observation history for those signals is thinner than for "
        "price, and re-deriving a risk level from two demand points would be inventing "
        "precision.",
        "Fees are recomputed by the same engine the live path uses, at each day's "
        "prices, but under today's fee assumptions rather than the ones in force "
        "historically.",
    ]
    if result.simulated_share > 0:
        notes.insert(
            0,
            f"{result.simulated_share:.0%} of the observations behind this replay are "
            "fixture data. A backtest over fixtures measures the fixtures.",
        )
    if len(result.closed) < 10:
        notes.append(
            f"Only {len(result.closed)} position(s) closed. That is too few to draw a "
            "conclusion about the policy from."
        )
    return notes


# ------------------------------------------------------------------ replay


@dataclass
class _Series:
    """One listing's observations, indexed for point-in-time reads."""

    points: list[tuple[datetime, Decimal]]
    simulated: int

    def price_at(self, moment: datetime) -> Decimal | None:
        """The most recent price at or before ``moment``.

        Carried forward rather than interpolated: a price that was last seen on
        Tuesday is the price on Wednesday until something says otherwise, and
        inventing a value between two observations would be fabricating a market.
        """
        latest: Decimal | None = None
        for observed_at, price in self.points:
            if observed_at > moment:
                break
            latest = price
        return latest

    def first_at_or_above(
        self, target: Decimal, *, after: datetime, until: datetime
    ) -> tuple[datetime, Decimal] | None:
        for observed_at, price in self.points:
            if observed_at <= after:
                continue
            if observed_at > until:
                break
            if price >= target:
                return observed_at, price
        return None

    @property
    def distinct_days(self) -> int:
        return len({observed_at.date() for observed_at, _ in self.points})


def _series(session: Session, listing_id: str) -> _Series:
    rows = list(
        session.scalars(
            select(PriceObservation)
            .where(PriceObservation.listing_id == listing_id)
            .order_by(PriceObservation.observed_at)
        )
    )
    return _Series(
        points=[(ensure_utc(row.observed_at), row.landed_price) for row in rows],
        simulated=sum(1 for row in rows if row.is_simulated),
    )


def _candidates(session: Session, auth: AuthContext) -> list[Opportunity]:
    """Pairs with both sides stored. The universe the replay walks."""
    return list(
        session.scalars(
            select(Opportunity).where(Opportunity.organization_id == auth.organization_id)
        )
    )


def run(
    session: Session,
    auth: AuthContext,
    policy: PolicyView,
    *,
    days: int = 90,
    starting_capital: Decimal | None = None,
    step_days: int = 1,
    exit_rule: str = ExitRule.AT_MARKET,
    now: datetime | None = None,
) -> BacktestResult:
    """Replay the policy over the last ``days`` of recorded history."""
    end = ensure_utc(now or utcnow())
    start = end - timedelta(days=days)
    capital_limit = money(
        starting_capital if starting_capital is not None else policy.capital_limit
    )

    result = BacktestResult(
        started_at=start,
        ended_at=end,
        policy_version=policy.version,
        exit_rule=exit_rule,
        starting_capital=capital_limit,
    )

    listings: dict[str, MarketplaceListing] = {}
    series: dict[str, _Series] = {}
    pairs: list[tuple[Opportunity, _Series, _Series]] = []
    skipped_rows: list[Opportunity] = []
    observations = simulated = 0

    for opportunity in _candidates(session, auth):
        source = listings.setdefault(
            opportunity.source_listing_id,
            session.get(MarketplaceListing, opportunity.source_listing_id),
        )
        target = listings.setdefault(
            opportunity.target_listing_id,
            session.get(MarketplaceListing, opportunity.target_listing_id),
        )
        if source is None or target is None:
            continue

        source_series = series.setdefault(source.id, _series(session, source.id))
        target_series = series.setdefault(target.id, _series(session, target.id))

        thin = min(source_series.distinct_days, target_series.distinct_days)
        if thin < MIN_DAYS_COVERAGE:
            skipped_rows.append(opportunity)
            result.skipped.append(
                {
                    "opportunity_id": opportunity.id,
                    "reason": (
                        f"only {thin} distinct day(s) of price history on the thinner side; "
                        f"{MIN_DAYS_COVERAGE} are needed before a replay says anything."
                    ),
                }
            )
            continue

        pairs.append((opportunity, source_series, target_series))
        observations += len(source_series.points) + len(target_series.points)
        simulated += source_series.simulated + target_series.simulated

    result.observations_used = observations
    result.simulated_share = pct_of(Decimal(simulated), Decimal(observations)) or Decimal("0")

    # A skipped candidate is named, not counted: "3 were skipped" tells nobody
    # which products the replay had nothing to say about.
    if skipped_rows:
        skipped_titles, _ = _product_fields(session, skipped_rows)
        for entry in result.skipped:
            entry["title"] = skipped_titles.get(entry["opportunity_id"], "Unknown product")

    if not pairs:
        return result

    opportunity_by_id = {opportunity.id: opportunity for opportunity, _, _ in pairs}
    titles, categories = _product_fields(session, [opportunity for opportunity, _, _ in pairs])
    result.titles = titles

    open_trades: list[tuple[ReplayTrade, _Series]] = []
    deployed = Decimal("0")
    bought: set[str] = set()

    moment = start
    while moment <= end:
        # --- close what the rules say has sold -------------------------
        still_open: list[tuple[ReplayTrade, _Series]] = []
        for trade, target_series in open_trades:
            age = (moment - ensure_utc(trade.opened_at)).days
            hit = target_series.first_at_or_above(
                trade.expected_sale_price, after=trade.opened_at, until=moment
            )
            if hit is not None:
                sold_at, price = hit
                _close(
                    session,
                    trade,
                    opportunity_by_id[trade.opportunity_id],
                    price,
                    sold_at,
                    category=categories.get(trade.opportunity_id),
                )
                trade.exit_reason = "exit price reached the expected sale price"
                deployed = money(deployed - trade.capital)
                continue
            if age >= policy.maximum_inventory_age_days:
                price = target_series.price_at(moment) or trade.entry_price
                _close(
                    session,
                    trade,
                    opportunity_by_id[trade.opportunity_id],
                    price,
                    moment,
                    category=categories.get(trade.opportunity_id),
                )
                trade.exit_reason = (
                    f"liquidated at the {policy.maximum_inventory_age_days} day age limit"
                )
                deployed = money(deployed - trade.capital)
                continue
            still_open.append((trade, target_series))
        open_trades = still_open

        # --- consider what to open -------------------------------------
        for opportunity, source_series, target_series in pairs:
            if opportunity.id in bought:
                continue
            available = money(capital_limit - deployed)
            if available <= 0:
                break

            entry = source_series.price_at(moment)
            exit_price = target_series.price_at(moment)
            if entry is None or exit_price is None or entry <= 0:
                continue

            economics = _price_it(
                session, opportunity, entry, exit_price, category=categories.get(opportunity.id)
            )
            if economics is None:
                continue
            verdict = _eligible(opportunity, economics, policy)
            if verdict is not None:
                result.rejections.append(
                    Rejection(
                        opportunity_id=opportunity.id,
                        at=moment,
                        reason_code=verdict[0],
                        detail=verdict[1],
                    )
                )
                continue

            cap = min(available, policy.position_cap(capital_limit))
            quantity = int(cap // entry)
            if quantity <= 0:
                result.rejections.append(
                    Rejection(
                        opportunity_id=opportunity.id,
                        at=moment,
                        reason_code="POSITION_LIMIT_EXCEEDED",
                        detail=(
                            f"{display_currency(entry)} per unit against "
                            f"{display_currency(cap)} allowed."
                        ),
                    )
                )
                continue

            trade = ReplayTrade(
                opportunity_id=opportunity.id,
                title=titles.get(opportunity.id) or opportunity.id,
                opened_at=moment,
                entry_price=entry,
                expected_sale_price=exit_price,
                expected_unit_profit=economics["net_profit"],
                quantity=quantity,
                capital=money(entry * quantity),
            )
            result.trades.append(trade)
            open_trades.append((trade, target_series))
            deployed = money(deployed + trade.capital)
            bought.add(opportunity.id)

        moment += timedelta(days=step_days)

    return result


def _close(
    session: Session,
    trade: ReplayTrade,
    opportunity: Opportunity,
    price: Decimal,
    at: datetime,
    *,
    category: str | None = None,
) -> None:
    """Realise a position at the price the market actually showed.

    Fees are recomputed at the exit price rather than carried from entry. A
    referral fee is a share of the sale, so a position that sells 20% below
    plan does not pay the fee it would have paid at plan, and reusing the entry
    figure would quietly overstate every loss and understate every gain.
    """
    realised = _price_it(session, opportunity, trade.entry_price, price, category=category)
    trade.closed_at = at
    trade.exit_price = price
    trade.realized_profit = money(realised["net_profit"] * trade.quantity)


def _product_fields(
    session: Session, opportunities: list[Opportunity]
) -> tuple[dict[str, str], dict[str, str | None]]:
    """Titles and categories, batched.

    Category is not cosmetic: the referral fee is keyed on it, so pricing a
    replay without it prices a different product.
    """
    from app.models.catalog import Product

    product_ids = {row.product_id for row in opportunities if row.product_id}
    if not product_ids:
        return {}, {}
    products = {
        product.id: product
        for product in session.scalars(select(Product).where(Product.id.in_(product_ids)))
    }
    titles: dict[str, str] = {}
    categories: dict[str, str | None] = {}
    for row in opportunities:
        product = products.get(row.product_id or "")
        if product is None:
            continue
        titles[row.id] = product.title
        categories[row.id] = product.category
    return titles, categories


def _price_it(
    session: Session,
    opportunity: Opportunity,
    entry: Decimal,
    exit_price: Decimal,
    *,
    category: str | None = None,
) -> dict[str, Decimal]:
    """Price one unit at these two prices, with the real engine.

    The same code the live path runs. A backtest written against a simplified
    fee model measures the simplified fee model.

    Deliberately not wrapped in a try. An earlier draft swallowed exceptions and
    skipped the candidate, which made a broken replay indistinguishable from one
    that found nothing worth buying: the run reported "no candidate had enough
    history" while the real cause was a bad call signature.
    """
    result = calculate_profitability(
        ProfitabilityInput(
            sale_price=exit_price,
            acquisition_cost=entry,
            target_marketplace=Marketplace(opportunity.target_marketplace),
            category=category,
        )
    )
    return {
        "net_profit": result.net_profit,
        "roi": result.roi if result.roi is not None else Decimal("0"),
        "margin": result.margin if result.margin is not None else Decimal("0"),
    }


def _eligible(
    opportunity: Opportunity, economics: dict[str, Decimal], policy: PolicyView
) -> tuple[str, str] | None:
    """The policy thresholds that can be evaluated from point-in-time prices.

    Identity, demand and competition are held at their analysed values; the
    thresholds that move with price are re-evaluated on every simulated day.
    Returns the failure, or None when the candidate passes.
    """
    if opportunity.recommendation != "buy":
        return ("NOT_A_BUY", f"recommendation was {opportunity.recommendation}")
    if economics["net_profit"] <= 0:
        return (
            "NOT_PROFITABLE",
            f"{display_currency(economics['net_profit'])} per item at these prices",
        )
    if economics["net_profit"] < policy.minimum_profit:
        return (
            "PROFIT_BELOW_MINIMUM",
            f"{display_currency(economics['net_profit'])} against a "
            f"{display_currency(policy.minimum_profit)} minimum",
        )
    if economics["roi"] < policy.minimum_roi:
        return (
            "ROI_BELOW_MINIMUM",
            f"{economics['roi']:.1%} against a {policy.minimum_roi:.1%} minimum",
        )
    if (
        opportunity.match_confidence is not None
        and opportunity.match_confidence < policy.minimum_match_confidence
    ):
        return (
            "MATCH_CONFIDENCE_TOO_LOW",
            f"{opportunity.match_confidence:.1%} against {policy.minimum_match_confidence:.1%}",
        )
    risk = RiskLevel(opportunity.risk_level)
    if risk.rank > policy.maximum_risk.rank:
        return ("RISK_ABOVE_MAXIMUM", f"{risk.value} against {policy.maximum_risk.value}")
    return None
