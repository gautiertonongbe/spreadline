"""Are the real providers actually working?

Answers one question and answers it honestly: for each provider Spreadline knows
about, is a credential present, does a real call succeed, and is what comes back
a market observation or a fixture.

    python -m scripts.check_providers
    ENABLED_PROVIDERS=free python -m scripts.check_providers --search "sony headphones"

Nothing here is mocked. Every provider marked live is called for real, so a pass
means the key works against the vendor's own endpoint today, not that the code
compiles.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderCapabilityError, ProviderError, ProviderNotConfiguredError
from app.core.logging import configure_logging
from app.services.providers.registry import build_registry

#: Where each credential comes from, so a failure is actionable rather than sad.
#: Every one of these has a queue. None is instant, whatever the vendor's
#: marketing page implies, so the wait is stated rather than discovered.
SIGNUP: dict[str, str] = {
    "bestbuy": (
        "https://developer.bestbuy.com  (free, no seller account; the key request "
        "is reviewed before it is issued)"
    ),
    "ebay": (
        "https://developer.ebay.com  (free; the developer account itself is "
        "reviewed, at least one business day, before any keyset can be created. "
        "A sandbox keyset is then immediate; production Buy API access needs a "
        "separate eBay Partner Network application)"
    ),
    "amazon": "Requires a Professional seller account and SP-API credentials.",
    "walmart": "Requires a Walmart Marketplace seller account.",
}

OK = "  ok  "
NO = " not  "
SKIP = " skip "


@dataclass
class Result:
    slug: str
    kind: str
    configured: bool
    reached: bool | None
    detail: str
    sample: str | None = None


async def check(slug: str, provider: Any, query: str) -> Result:
    kind = getattr(provider, "kind", "unknown")
    if not provider.is_live:
        return Result(
            slug=slug,
            kind=kind,
            configured=provider.is_configured,
            reached=None,
            detail="Fixture provider. Answers instantly and is never a market observation.",
        )
    if not provider.is_configured:
        return Result(
            slug=slug,
            kind=kind,
            configured=False,
            reached=None,
            detail=(provider.configuration_note or "No credentials configured."),
        )

    try:
        found = await provider.search_products(query, limit=1)
    except ProviderNotConfiguredError as exc:
        return Result(slug, kind, False, None, str(exc))
    except ProviderCapabilityError as exc:
        return Result(slug, kind, True, True, f"Reached, but cannot search: {exc}")
    except ProviderError as exc:
        return Result(slug, kind, True, False, f"Call failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - the point is to report, not to raise
        return Result(slug, kind, True, False, f"Call failed: {type(exc).__name__}: {exc}")

    items = list(getattr(found, "items", []) or [])
    if not items:
        return Result(
            slug, kind, True, True, f"Reached, and the market returned no match for {query!r}."
        )
    first = items[0]
    return Result(
        slug,
        kind,
        True,
        True,
        f"Reached. {len(items)} result(s).",
        sample=f"{first.title[:60]} at {first.price} ({first.external_id})",
    )


async def run(query: str) -> int:
    registry = build_registry(settings.enabled_providers)
    try:
        results = [await check(p.slug, p, query) for p in registry.all()]
    finally:
        await registry.close()

    print(f"ENABLED_PROVIDERS={','.join(settings.enabled_providers)}")
    print()
    live_ok = 0
    live_total = 0
    for result in sorted(results, key=lambda item: (item.kind, item.slug)):
        if result.kind == "fixture":
            mark = SKIP
        elif result.reached:
            mark = OK
            live_ok += 1
            live_total += 1
        elif result.reached is False:
            mark = NO
            live_total += 1
        else:
            mark = NO
            live_total += 1
        print(f"[{mark}] {result.slug:12} {result.kind:8} {result.detail}")
        if result.sample:
            print(f"{'':22}{result.sample}")
        if mark == NO and result.slug in SIGNUP:
            print(f"{'':22}Get it: {SIGNUP[result.slug]}")
    print()

    if live_total == 0:
        print(
            "No live provider is enabled. Every price on screen is fixture data, and\n"
            "the platform labels it as such everywhere it appears.\n\n"
            "To use real market data, set ENABLED_PROVIDERS=free and add a key:\n"
            f"  BESTBUY_API_KEY        {SIGNUP['bestbuy']}\n"
            f"  EBAY_CLIENT_ID/SECRET  {SIGNUP['ebay']}"
        )
        return 0

    print(f"{live_ok} of {live_total} live provider(s) reachable.")
    return 0 if live_ok == live_total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Check every configured provider for real.")
    parser.add_argument(
        "--search",
        default="wireless headphones",
        help="The query used to prove a provider actually answers.",
    )
    args = parser.parse_args()
    configure_logging()
    return asyncio.run(run(args.search))


if __name__ == "__main__":
    sys.exit(main())
