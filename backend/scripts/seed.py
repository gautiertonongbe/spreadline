"""Seed a local database from the fixture catalogue.

Runs the real analysis pipeline over every fixture pair, so a fresh checkout has
a populated dashboard, opportunities in every state, and enough price history for
the statistics to be meaningful.

    python -m scripts.seed            # analyse every fixture pair
    python -m scripts.seed --reset    # drop and recreate the schema first

Everything it writes is fixture data. The API flags it as such on every response.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from app.core.database import SessionLocal, engine
from app.core.logging import configure_logging, get_logger
from app.domains.opportunities.analysis import AnalysisOptions, analyze_pair
from app.domains.tenancy.service import bootstrap
from app.models import Base
from app.models.enums import Marketplace
from app.services.providers.fixtures import FIXTURES
from app.services.providers.registry import build_registry

logger = get_logger(__name__)


async def seed(reset: bool = False) -> int:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    registry = build_registry(["mock"])
    session = SessionLocal()
    auth = bootstrap(session)
    session.commit()

    outcomes: Counter[str] = Counter()
    try:
        for fixture in FIXTURES:
            amazon = fixture.listing(Marketplace.AMAZON)
            walmart = fixture.listing(Marketplace.WALMART)
            if amazon is None or walmart is None:
                outcomes["skipped (no counterpart)"] += 1
                continue

            # Source is whichever side is cheaper; the platform evaluates the
            # direction that could plausibly work.
            if walmart.price <= amazon.price:
                source, source_id = Marketplace.WALMART, walmart.external_id
                target, target_id = Marketplace.AMAZON, amazon.external_id
            else:
                source, source_id = Marketplace.AMAZON, amazon.external_id
                target, target_id = Marketplace.WALMART, walmart.external_id

            try:
                result = await analyze_pair(
                    session,
                    auth,
                    source_marketplace=source,
                    source_external_id=source_id,
                    target_marketplace=target,
                    target_external_id=target_id,
                    options=AnalysisOptions(),
                    registry=registry,
                )
            except Exception as exc:  # noqa: BLE001 - report and continue
                session.rollback()
                outcomes["error"] += 1
                logger.warning(
                    "fixture failed", extra={"context": {"key": fixture.key, "error": str(exc)}}
                )
                continue

            session.commit()
            outcomes[result.decision.recommendation.value] += 1
            print(
                f"  {fixture.key:34} {result.decision.recommendation.value.upper():6} "
                f"score={result.score.total:>7} risk={result.risk.level.value:8} "
                f"profit={result.context.profitability.net_profit}"
            )
    finally:
        session.close()
        await registry.close()

    print()
    print("Seeded:", ", ".join(f"{count} {label}" for label, count in sorted(outcomes.items())))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Spreadline with fixture data.")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate every table first.")
    args = parser.parse_args()
    configure_logging()
    return asyncio.run(seed(reset=args.reset))


if __name__ == "__main__":
    sys.exit(main())
