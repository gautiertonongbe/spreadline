"""Import the Amazon Reviews 2023 open dataset into the catalogue.

The McAuley Lab dataset is the only genuinely open source of Amazon product
data: free, no account, no key, no agreement. What it is not is live. Every
price is as at the 2023 crawl, and this importer stamps every observation with
that date rather than with now, so the freshness logic marks the rows stale on
their own and the data-quality score drops accordingly. See
``app/domains/catalog/dataset_import.py`` for why that one decision governs the
whole module.

Get a category file first (they are large; ``meta_`` files are the item
metadata, the review files are not used here):

    https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023
    raw_meta_<Category> -> meta_<Category>.jsonl.gz

Then:

    python -m scripts.import_amazon_dataset meta_Electronics.jsonl.gz --dry-run
    python -m scripts.import_amazon_dataset meta_Electronics.jsonl.gz --limit 5000
    python -m scripts.import_amazon_dataset meta_*.jsonl.gz

``--dry-run`` reads the file and reports exactly what an import would do without
writing anything. A category file can take a long time, and checking one first
costs a read.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.core.database import SessionLocal, engine
from app.core.logging import configure_logging
from app.domains.catalog.dataset_import import (
    DEFAULT_CRAWL_DATE,
    ImportStats,
    import_file,
    scan_file,
)
from app.domains.tenancy.service import bootstrap
from app.models import Base

DATASET_URL = "https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023"


def parse_crawl_date(value: str) -> datetime:
    """A calendar date, read as UTC midnight.

    Stamping the wrong date here is the one mistake that quietly corrupts every
    statistic downstream, so it is parsed strictly rather than best-effort.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not an ISO date, for example 2023-09-30."
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def report(path: Path, stats: ImportStats, *, dry_run: bool) -> None:
    verb, count = ("would import", stats.usable) if dry_run else ("imported", stats.imported)
    skipped = stats.skipped_no_price + stats.skipped_no_asin + stats.skipped_no_title
    print(f"  {path.name}")
    print(f"    read          {stats.read:>9,}")
    print(f"    {verb:<13} {count:>9,}")
    print(f"    with a weight {stats.with_weight:>9,}  (no fulfilment-fee assumption needed)")
    print(f"    skipped       {skipped:>9,}", end="")
    if skipped:
        print(
            f"  (no price {stats.skipped_no_price:,},"
            f" no asin {stats.skipped_no_asin:,},"
            f" no title {stats.skipped_no_title:,})"
        )
    else:
        print()
    if not dry_run:
        print(f"    observations  {stats.price_observations:>9,}")


def run(
    paths: list[Path],
    *,
    crawl_date: datetime,
    limit: int | None,
    dry_run: bool,
) -> int:
    missing = [path for path in paths if not path.is_file()]
    if missing:
        for path in missing:
            print(f"No such file: {path}", file=sys.stderr)
        print(f"Category files come from {DATASET_URL}", file=sys.stderr)
        return 2

    print(
        f"Crawl date {crawl_date.date()}. Every price is recorded as at that date, "
        "never as current."
    )
    if dry_run:
        print("Dry run: nothing will be written.")
    print()

    totals = ImportStats()

    if dry_run:
        for path in paths:
            stats = scan_file(path, crawl_date=crawl_date, limit=limit)
            report(path, stats, dry_run=True)
            _accumulate(totals, stats)
        _summarise(totals, paths, dry_run=True)
        return 0

    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        auth = bootstrap(session)
        session.commit()
        for path in paths:
            try:
                stats = import_file(session, auth, path, crawl_date=crawl_date, limit=limit)
            except Exception as exc:  # noqa: BLE001 - one bad file must not lose the rest
                session.rollback()
                print(f"  {path.name}: failed, {exc}", file=sys.stderr)
                continue
            report(path, stats, dry_run=False)
            _accumulate(totals, stats)
    finally:
        session.close()

    _summarise(totals, paths, dry_run=False)
    return 0


def _accumulate(totals: ImportStats, stats: ImportStats) -> None:
    totals.read += stats.read
    totals.usable += stats.usable
    totals.imported += stats.imported
    totals.skipped_no_price += stats.skipped_no_price
    totals.skipped_no_asin += stats.skipped_no_asin
    totals.skipped_no_title += stats.skipped_no_title
    totals.price_observations += stats.price_observations
    totals.with_weight += stats.with_weight


def _summarise(totals: ImportStats, paths: list[Path], *, dry_run: bool) -> None:
    if len(paths) > 1:
        print()
        report(Path("all files"), totals, dry_run=dry_run)
    if not dry_run and totals.imported:
        print()
        print(
            "These listings carry 2023 prices and are marked stale by the freshness "
            "logic. Use them for a realistic catalogue and for backtesting, not as "
            "current market data."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import Amazon Reviews 2023 item metadata into the catalogue.",
        epilog=f"Category files: {DATASET_URL}",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="meta_<Category>.jsonl.gz file(s).")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Read at most this many rows per file. Useful for a first look.",
    )
    parser.add_argument(
        "--crawl-date",
        type=parse_crawl_date,
        default=DEFAULT_CRAWL_DATE,
        help=(
            "The date the file was crawled, ISO format. Every price is recorded as at "
            f"this date. Default {DEFAULT_CRAWL_DATE.date()}."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be imported without writing anything.",
    )
    args = parser.parse_args()
    configure_logging()
    return run(
        args.paths,
        crawl_date=args.crawl_date,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
