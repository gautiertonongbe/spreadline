# Evidence

Someone looking at an opportunity has to be able to answer four questions from
the record alone: why is this a good purchase, how fragile is it, how much
should I pay, and how strong is the evidence behind the answer. Everything here
exists to make those answerable by inspection rather than by trust.

## Timestamps state their zone

Every datetime leaves the API with an explicit UTC offset. SQLite round-trips a
`DateTime(timezone=True)` column without the tzinfo, so rows read back arrive
naive and used to serialise as `2026-09-12T21:05:45` with nothing saying which
zone that was. A browser parses that as *local* time: west of UTC the value
lands hours in the future, and an analysis four minutes old rendered as
"analysed in 24,515 seconds".

Two places enforce it, and both are boundaries rather than per-field fixes:

- `APIModel` stamps any naive datetime as UTC before serialising, so every
  endpoint inherits the invariant, including ones not written yet.
- `clock.iso_utc()` is used for anything hand-built into a payload dict.

`test_every_timestamp_states_its_timezone` walks the detail payload, the list
row, the events and the price windows, and fails on any stamp without an offset
or dated in the future.

The client is defensive too: `relativeDate` never prints "in X". A timestamp up
to two minutes ahead reads "just now" (two clocks disagreeing); further ahead it
falls back to the absolute date, which is legible without hiding that something
upstream is wrong.

## The score is reproducible

Every component publishes what went in and the arithmetic that ran on it:

| Field | Meaning |
| --- | --- |
| `inputs` | The measured values, already formatted |
| `calculation` | The operation actually performed, with the real numbers |
| `score` / `weight` / `contribution` | The component, its weight, and what it added |

The explanation is generated from the branch the code took, not written
alongside it, so it cannot describe an interpolation that did not happen:

> `$71.65 is at or above the top of the curve ($40.00), so it scores 100`
> `32.6% sits 13% of the way from 30.0% (scores 68) to 50.0% (scores 86), giving 70`

`contribution_total` and `weight_total` are published so the column adds up on
the page. A component with no data behind it says so, keeps
`unknown_component_score` (30, deliberately below the mid-point), and appears in
`unknown_components`. Absence of evidence is not neutral when capital is about
to move, and it is never a zero.

## Risk is eight questions, not one

Risk is reported per category, because the categories fail differently and are
fixed differently: a volatile exit price is waited out or bought lower, a
rejected product match is not a risk to be priced, thin data is fixed by
fetching more.

`price`, `competition`, `demand`, `inventory`, `product_match`, `data_quality`,
`brand_category`, `economics`.

Every category is reported on every analysis, including the clean ones, so the
block can be read as "these eight things were checked". Each carries its own
score, level, signals and a `has_evidence` flag — separate from the score on
purpose, because a category with no evidence and a category checked and found
clean both produce no signals, and those are opposite findings.
`driving_category` names where the overall level came from.

New signals this phase, all from data already collected: `target_price_trend_falling`
(the exit price is drifting down, which volatility alone cannot say),
`competition_increasing` (net new sellers in 30 days) and `stale_evidence`
(present but past its freshness window, which fails differently from missing).

## The spread ladder

Shown in full and in order, because the two ends on their own ask the reader to
take the middle on trust:

```
Buy on Walmart              $219.00
Getting it to the shelf       $0.95   inbound shipping, tax, operator costs
Sell on Amazon              $328.00
Gross spread                $109.00   selling price less what you paid
Gross spread %                49.8%   measured against what you paid
Marketplace fees             $36.40
Total cost                  $256.35
Net profit                   $71.65
Return on your money          32.6%
Margin                        21.8%
Most you should pay         $290.65
```

`gross_spread_pct` is named against its denominator because "spread %" and
"margin" are different numbers that both read as a percentage: the first is
measured on cost, the second on the sale price.

`other_unit_costs` exists because the ladder did not previously reconcile.
`total_cost` included inbound shipping, tax and operator-entered costs while
`total_fees` did not, so a reader adding the figures on screen found a gap with
no name. The identity now closes:

    total_cost = landed acquisition + other unit costs + total fees

The whole ladder travels on the list row as well, so the list answers the same
question without opening the record, and the stored snapshot renders the derived
terms rather than leaving them blank.

## Is the spread real, or is it today's accident?

`domains/pricing/spread_evidence.py` classifies the gap against the price
history of both sides. Four verdicts, in precedence order:

| Verdict | Meaning |
| --- | --- |
| `unsupported` | Not enough history on one or both sides. The absence of an answer, reported as such. |
| `unstable` | The prices swing enough that the gap on the day of sale is a range, not a number. |
| `temporary` | The gap did not exist historically, or today's is at least 1.5x the usual one. |
| `structural` | Today's gap is in line with how these two markets have priced this item. |

Instability is checked first and deliberately outranks the rest: the other
verdicts reason from the medians, and a price that swings that much makes its
own median a poor description of it.

Confidence is the weaker of the two sides, capped at low when either side has
fewer than 20 observations. A conclusion about a spread cannot be more confident
than the weaker of the two prices it is drawn from.

Everything behind the verdict is published: both medians, both windows, both
observation counts, volatility, the typical gap, today's gap and the ratio.

## Provenance

Every analysis and every stored record publishes, per side: the marketplace, the
external id, which provider answered, when it was observed, and whether it was a
live market call. Liveness is resolved through the provider registry rather than
guessed from the slug, and a row written by a provider no longer registered
reports `null` rather than claiming to be fixture data.

Today both sides are fixtures and the block says so. Connecting a real adapter
changes the values and nothing about the shape, which is the point of publishing
it before the adapters land.
