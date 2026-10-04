# Spreadline's own history

Every window statistic, volatility figure, anomaly and spread verdict in this
platform is computed from observations Spreadline recorded itself. That is the
point of this layer: the intelligence engine should get *less* dependent on
third-party history over time, not more, and the only way that happens is by
observing consistently from the first day.

## Capture

`domains/history/capture.py` is the one place a provider answer becomes a stored
observation. Three rules:

1. **Nothing is invented.** A field the provider did not return is stored as
   null. Buy Box, seller count, sales rank and availability are optional in the
   payload and optional in the row. A provider that cannot serve a capability is
   not asked, and its silence is never recorded as a zero.
2. **Provenance travels with the row.** Which provider answered, when it was
   called, when the market showed the value, and whether that provider calls a
   market at all.
3. **Capture never breaks the caller.** A failure to record history is logged
   and returned, not raised. The observation is a by-product of answering a
   question; letting it fail the question would trade a gap in a dataset for a
   broken feature.

Analysis captures through the same path, so anything analysed starts
accumulating history whether or not anything asks about it again.

## What a snapshot carries

Beyond price, shipping, landed price, currency, availability, condition, buy
box, seller and observed-at, every observation now carries:

| Field | Why |
| --- | --- |
| `external_id`, `gtin`, `asin` | The rows these would otherwise be joined to are mutable: listings get re-matched, products get merged. A price is only meaningful attached to the thing it was a price for. |
| `is_simulated` | Recorded at capture, never inferred from a provider slug later. |
| `retrieved_at` | When the provider was called, as distinct from when the market showed the value. For a live poll they are the same; for a history backfill they are not, and only one of them is a fact about the market. |
| `quality_score` | The data-quality score at the time, where one was computed. |

`gtin` holds the normalised GTIN-14. UPC and EAN are GTINs in shorter encodings
and normalise into that column rather than getting one each, because three
columns holding the same number in three paddings is how they end up
disagreeing.

## Simulated versus real

This is the claim the whole layer rests on, so it is enforced at capture and
reported at every level of readback. `GET /api/v1/history` returns real and
simulated counts separately; so does the per-listing coverage; and the raw
series can be filtered to real observations only.

The migration that introduced the flag backfilled existing rows from the only
evidence they carried — which provider wrote them — and that is the last time
the inference is ever made. Every row written since records the fact.

## The universe

`tracked_listings` is a standing instruction to keep observing one listing on
one marketplace. A listing rather than a product, because a listing is the unit
a provider can actually be asked about; products are how the observations are
read back.

Bounded on purpose. Spreadline does not crawl a marketplace, it observes what
the operator is working on:

- `HISTORY_UNIVERSE_LIMIT` caps how many listings can be tracked at once.
- `HISTORY_REFRESH_BATCH_SIZE` caps how many are polled per run. An unbounded
  scheduled job is a bill waiting to happen.
- Each listing has its own interval, defaulting to the price TTL. Re-polling
  faster than the freshness policy considers stale spends a call to learn
  nothing.
- Priority decides what a run that cannot cover everything covers.
- **Consecutive failures back a listing off geometrically**, capped at 16x. A
  dead external id should cost one call a day, not one call a run.

Every poll goes through `ReliableProvider`, so it inherits the timeout, bounded
retries with jitter, token-bucket rate limit and circuit breaker already in
place. Nothing new was added for rate limiting because nothing new was needed.

## Reading it back

| Endpoint | Answers |
| --- | --- |
| `GET /history` | How big the dataset is, and how much of it is real |
| `GET /history/universe` | What is under observation, and what is due |
| `POST /history/universe` | Start observing a listing (idempotent) |
| `DELETE /history/universe/{id}` | Stop observing, keeping what was observed |
| `GET /history/listings/{id}` | Coverage, window statistics, and the raw series |
| `POST /history/refresh` | Run one bounded pass now |

The raw series is returned alongside the statistics because a statistic is only
as trustworthy as the series under it, and someone checking a volatility figure
has to be able to see the prices it was computed from.

Untracking deactivates rather than deletes. The observations remain, and a
history with a gap in it should be explainable by a row saying when observation
stopped.

## The interface

`/history` leads with how much of the dataset is a real market record, because
that is the whole argument for owning it. A total that mixed real and simulated
observations would undermine exactly the claim it was meant to support.

The universe table shows each listing's cadence, observation count, last poll,
next due time and consecutive failures, with controls to start observing a
listing, stop observing one, and run a single bounded pass immediately.

Stopping keeps everything already observed. Verified live: untracking a listing
moved the universe from 2 to 1 and left the observation count at 17,969.

## Windows

1, 7, 30, 90, 180 and 365 days, each reporting its own observation count, day
coverage and confidence, and returning `null` with a reason rather than a number
when the window cannot support a statistic. That logic already existed in
`domains/pricing/statistics`; this layer feeds it and does not duplicate it.
