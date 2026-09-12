# Scoring

The score ranks candidates against each other under one explicit policy. It is
**not** a probability and does not claim to be a likelihood of anything.

## Weights

| Component | Weight | Derived from |
| --- | --- | --- |
| Profit | 25% | Net profit per unit, piecewise-linear curve |
| ROI | 20% | Return on invested capital, piecewise-linear curve |
| Price stability | 15% | Coefficient of variation on the exit market, minus a drawdown penalty |
| Demand | 15% | Relative demand score, discounted when confidence is low |
| Competition | 10% | 100 minus the competition pressure score |
| Match confidence | 5% | Identity confidence |
| Availability | 5% | Source stock and quantity |
| Risk | 5% | 100 minus the risk score |

Weights are validated to sum to 1.0 and are configurable per organization. Every
stored score carries its `model_version`, so a weight change does not silently
invalidate historical comparisons.

## Transparency

A total is never shown without its components. Each component reports its score,
its weight, its contribution and the **basis** it was computed from:

```
Overall 82

Profit            100  x25%   Net profit of 71.65 per unit.
ROI                70  x20%   ROI of 32.6% on invested capital.
Price stability    94  x15%   2% coefficient of variation over 365 days,
                              maximum drawdown 6%.
Demand             64  x15%   Relative demand 64 from a median rank of 141
                              (high confidence).
Competition        60  x10%   6 seller(s), pressure score 40/100.
Match confidence   97   x5%   97% via gtin (confirmed).
Availability      100   x5%   In stock with 40 units available.
Risk               76   x5%   Risk level low (score 24/100).
```

## Unknown components

A component with no data behind it scores 30, not 50, and is flagged
`has_data: false`. Absence of evidence is not neutral when capital is about to
move. The UI renders those bars in a different colour, so a high total built on
three unknowns is visibly different from the same total built on eight measured
components.

## Gating

A candidate that cannot be bought scores **zero**, with the raw weighted total
preserved in `raw_total` and the reason in `gated_reason`. Gating applies when:

- product identity is not established,
- net profit is zero or negative,
- the source is out of stock,
- any blocking risk signal is present.

This exists because of a specific failure: a games console matched against its own
carrying case computes a $265 apparent profit and a raw score in the eighties.
Without gating it sorts to the top of the opportunity table and reads as the best
idea on the screen.

## Curves

Profit and ROI use piecewise-linear curves rather than a linear scale, because the
difference between $0 and $3 of unit profit matters far more than the difference
between $37 and $40.

```
profit:  0 -> 0    3 -> 30    6 -> 50    10 -> 70    20 -> 88    40 -> 100
roi:     0 -> 0   10% -> 25  20% -> 50   30% -> 68   50% -> 86  100% -> 100
```

## Relative demand

Sales rank is ordinal. Converting it to units requires a per-marketplace,
per-category calibration Spreadline does not have, so it reports a **relative**
score on a log scale (rank 1 scores 100, rank 1,000,000 scores 0) and says
plainly that is what it is. `estimated_monthly_units` stays null unless a provider
supplied an estimate and named its basis.

The score uses the **median** rank over the window rather than the latest, so one
good day does not make a product look like a mover.

## Risk aggregation

The highest signal dominates, with the remainder adding a decaying tail:

```
score = max_signal + sum(signal_i / (2 * i))   for the rest, ranked descending
```

A plain sum would let five low-severity signals outweigh one critical one, which
inverts the ordering that actually matters when deciding to spend money.

Levels: `low` below 28, `medium` below 55, `high` above that, and `critical`
whenever any blocking signal is present.
