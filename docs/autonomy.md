# Autonomy

Spreadline is built to become an inventory investment system that manages
capital within explicit rules. This document describes the machinery that makes
that progression safe and reversible.

The governing principle, and the reason every design decision below goes the way
it does:

> **Autonomy is earned, not assumed.**

Correctness first. Risk control second. Measurement third. Autonomy fourth.
Scale last.

## The stages are the existing engines, named

There is no LLM layer between the engines and the money. The stages below are
the deterministic engines this platform already runs, under the names an
investment organisation would use for them:

| Stage | Engine | Role |
| --- | --- | --- |
| Scout | catalogue and search | Research analyst |
| Underwriting | profitability | Investment analyst |
| Risk | risk engine, by category | Risk officer |
| Eligibility | deterministic policy checks | Compliance |
| Capital | allocator | Portfolio manager |
| Policy | autonomy policy | Mandate |
| Decision | the authorisation | Investment committee |

Naming them buys two things a monolithic pipeline cannot give: each stage's
verdict is recorded separately, and **two stages can disagree**.

## The ladder

| Level | Name | Capital | What it may do |
| --- | --- | --- | --- |
| 0 | Observe | none | Analyse, take no action |
| 1 | Recommend | none | Produce BUY / REVIEW / PASS |
| 2 | Human approval | none | Recommend; a person approves and orders |
| 3 | Controlled | $100 | Decide autonomously within strict limits |
| 4 | Limited | $500 | Only after level 3 has produced results |
| 5 | Expanded | $2,000 | |
| 6 | Portfolio | $10,000 | Multiple simultaneous positions |
| 7 | Full | policy-defined | Continuous management within the policy |

Level 2 with zero capital and observe mode is the **default for an organisation
that has never configured autonomy**, because an organisation that has not
configured it has not authorised any.

## The policy

The human defines the policy; the system executes within it. That division only
holds if the policy a decision was made under can be read back exactly as it
was, so `autonomy_policies` is **append-only**: every change writes a new
version and deactivates the old one. A decision records its policy version, and
a later policy change cannot rewrite it.

Everything is configurable: capital limit, position size and share, loss per
position, daily deployment, minimum ROI, minimum profit, maximum risk, minimum
match confidence, minimum data quality, maximum inventory age, and category,
brand and marketplace exposure.

Four independent switches must all agree before any capital moves:

```
autonomy_level.deploys_capital   and
not require_human_approval       and
not emergency_stop_active        and
execution_mode is not observe
```

They are separate on purpose. One of them being wrong should never be enough.

A policy that deploys capital with no limit is rejected: that is not a policy.

## Eligibility is deterministic

Before allocation, an opportunity passes a set of mandatory checks against the
policy. No weighting, no majority, no override path: one failure ends it, and
the answer becomes REVIEW rather than BUY.

Each check keeps both sides of the comparison and a stable reason code —
`MATCH_CONFIDENCE_TOO_LOW`, `ROI_BELOW_MINIMUM`, `RISK_ABOVE_MAXIMUM`,
`DATA_QUALITY_TOO_LOW`, `UNRESOLVED_BLOCKER` — so refusals can be counted and
asserted on rather than parsed out of log text.

Scoring ranks candidates against each other. Eligibility decides whether one may
be bought without a human. A candidate can score 94 and be ineligible.

## Disagreement escalates

Eligibility checks the aggregate risk level against the policy ceiling. The risk
stage looks at the breakdown underneath it, and objects to two things the
aggregate hides:

- an **individual high-severity signal**, because averaging it into an
  acceptable level is exactly how a clearance price gets waved through;
- a **category with no evidence at all**, because "no evidence" and "no risk"
  produce the same aggregate and are opposite findings.

When one stage objects and the others do not, the outcome is `escalated` with
reason `AGENTS_DISAGREE` — not the average of the opinions.

This only means anything because the stages ask different questions. An earlier
draft had the risk stage re-check the same threshold as eligibility, which made
the disagreement path unreachable; a disagreement path that cannot fire is not a
safeguard.

## Capital allocation

Given available capital and an eligible opportunity, the allocator sizes the
position and reports **which limit decided the size**: budget, position share,
daily deployment, maximum loss, brand exposure or category exposure.

Sometimes the right answer is zero. The system is never required to deploy
capital it has, and a limit that produces zero units is reported as the binding
limit rather than rounded up to one.

Observed live on the fixture catalogue, each limit binding in turn:

```
$219.00 per unit against $15.00 allowed by the maximum share of capital  -> POSITION_LIMIT_EXCEEDED
Would put $27.97 into brand 'LEGO', above the $20.00 the policy allows   -> BRAND_EXPOSURE_EXCEEDED
Authorised 1 unit(s) at up to $35.09 each, $27.97 committed              -> authorized
```

## Shadow mode

A shadow position is tracked against the same real market data as a live one and
measured the same way; the only difference is whether money moved. One table
covers both, and only `purchase_id` distinguishes them — a shadow position has
none. Comparing the two is the entire point of paper trading, and it is only
possible if they are the same shape.

`GET /autonomy/performance?execution_mode=shadow|live` returns the same
scorecard for either.

## Circuit breakers and the emergency stop

Four capital breakers, all configurable: daily realised loss, portfolio
drawdown, consecutive blocked decisions, and capital deployed today. These are
distinct from the provider circuit breaker, which protects a provider from being
hammered; these protect capital from the system.

A tripped breaker blocks new deployment and **requires a human to reset it**.
Automatic recovery is deliberately not implemented: a breaker that resets itself
after a cooling-off period hides the thing it tripped on.

The emergency stop is immediate and is deliberately *not* a policy version — it
has to take effect at once and be reversible without rewriting the investment
rules. A policy edit carries the stop forward, so a routine edit can never
become an accidental resume. Stopping does not unwind anything: existing
positions are still held, monitored and reported. What stops is new capital.

## The gate reports; it never promotes

`GET /autonomy/eligibility` answers "is the system eligible for the next level,
and if not, what is missing". It changes nothing. A system that could promote
itself on its own performance report is not governed by a policy.

**A criterion with no sample is not met.** Not "met by default": 80% precision
over four decisions is not evidence of anything, so sample size is a criterion in
its own right and every ratio is checked against one.

Authorising a level is `POST /autonomy/enable`, a named action by a person. If
the gate says not eligible, `acknowledge_not_eligible` is how they say they know
and are proceeding anyway — a legitimate thing for an owner of capital to
decide, and recorded as such.

## The decision record

Every decision, authorised or not, preserves what it knew: prices, economics and
the fee assumptions, match confidence, risk, the eligibility checks, the full
policy, the per-stage verdicts, and the timestamp. Never updated once written.

Refusals are recorded as carefully as authorisations. A system that only logged
its purchases could not demonstrate its restraint.

## The learning loop

    decision -> outcome -> error -> analysis -> rule improvement

`domains/learning/attribution.py` implements the first four. The fifth is a
person's, deliberately: an engine that silently retunes itself on twelve
observations is how a small sampling accident becomes a permanent rule.

The scorecard says how accurate the system has been. It cannot say *where* it is
wrong, and an average error is the one number guaranteed to hide a pattern: a
category overestimated by 20% and another underestimated by 20% average to
nothing at all. So closed outcomes are segmented by category, brand,
marketplace, risk level and price band, and each segment is tested for bias.

A finding requires all three:

- at least **5 closed positions** in the segment,
- an average signed error of at least **15%**,
- at least **70% of those errors pointing the same way**.

The third is what separates a bias from noise. A mean of zero across +50% and
-50% is not accuracy, and two large errors cancelling out is not a finding.

Findings are ranked by **money, not by error size**: a 40% error on one $12
position matters less than a 16% error on eight $300 ones. Segments that were
looked at and had too little behind them are reported too, so the absence of a
finding is visible rather than silent.

`GET /autonomy/learning`.

## Knowing when to get out

`domains/autonomy/selling.py` answers the other half of the decision, from
different evidence: not the spread that justified the purchase, but what the
exit market is doing now, how long the capital has been tied up, and what is
left to make.

| Action | When |
| --- | --- |
| `hold` | Doing what it was bought to do |
| `sell_now` | Still profitable, but the price is falling or the capital is ageing |
| `reprice` | Not selling, and the economics survive a lower price |
| `liquidate` | Past the age limit, or the price has fallen through what the units cost |

Ageing starts to matter at 70% of the policy's inventory age limit, not at it:
capital turnover is worth more than the last few percent well before the
deadline.

Ordered by urgency then by capital, so the positions needing a decision today
are at the top and the largest of those is first. Recommendations only: nothing
lists, reprices or sells, and the output shape is what an authorised marketplace
executor would read.

`GET /autonomy/sell-review`.

## The execution boundary

Spreadline decides **what, where, how much, at what maximum price, and why**.
Placing the retail order remains a human action. There is no automated consumer
checkout, no purchasing bot, no CAPTCHA handling, no anti-bot evasion and no
unauthorised scraping.

The authorisation record carries everything an executor would need, so adding an
authorised supplier integration later means adding an executor — not rewriting
this layer.

## Endpoints

```
GET    /autonomy                      the dashboard, in one read
GET    /autonomy/policy               active policy plus every version
PUT    /autonomy/policy               write a new version
GET    /autonomy/performance          the scorecard
GET    /autonomy/eligibility          gate verdict for the next level
GET    /autonomy/ladder               every level and its requirements
POST   /autonomy/enable               authorise a level
POST   /autonomy/disable              back to human approval, zero capital
POST   /autonomy/emergency-stop       stop or resume, immediately
GET    /autonomy/positions            inventory as capital positions
GET    /autonomy/decisions            every decision, authorised or not
GET    /autonomy/decisions/{id}       one decision with its evidence
POST   /autonomy/decisions/run        run the process over one opportunity
GET    /autonomy/velocity             how long capital stays tied up, measured
GET    /autonomy/allocation           what to buy, across every candidate at once
POST   /autonomy/allocation/commit    act on that plan, line by line
POST   /autonomy/backtest             replay the active policy over recorded history
GET    /autonomy/circuit-breakers     state and current readings
POST   /autonomy/circuit-breakers/{id}/reset
GET    /autonomy/events               the audit log
```

## The interface

| Page | Answers |
| --- | --- |
| `/autonomy` | What it may do, what it holds, what stopped it, how it has performed |
| `/autonomy/policy` | The rules, editable, with every version kept |
| `/autonomy/allocation` | What to buy now, ranked, with what it leaves out |
| `/execution` | What you have been asked to buy, and what you actually paid |
| `/autonomy/positions` | Inventory as capital, shadow and live side by side |
| `/autonomy/decisions` | Every decision, filterable by outcome |
| `/autonomy/decisions/{id}` | One decision with the evidence exactly as it was |
| `/autonomy/sell` | What to do with every open position |
| `/autonomy/backtest` | What the current limits would have done over recorded history |
| `/autonomy/learning` | Where the predictions are consistently wrong |
| `/autonomy/events` | The audit log |
| `/history` | The dataset, and the universe under observation |

The dashboard leads with current authority rather than with performance, because
what the system is allowed to do right now is the question a person opens the
page with. Emergency stop and circuit breaker states are shown above everything
else when they are active.

The policy page shows the four governance switches as a checklist next to the
editor, so "why is nothing happening" is answerable without reading the rules.
Saving writes a new version and says so; the version history sits underneath.

Every metric on screen carries its sample, and a metric with no sample reads
"nothing measured yet" rather than showing a zero.

## Replaying a policy

`POST /autonomy/backtest` answers the question that has to be answerable before
real capital moves: given what was known at the time, what would these limits
have done, and what would have happened. It is the strongest evidence available
short of spending money, and it is worthless if it cheats.

Three properties make it honest, and each of them costs something.

**No lookahead.** Every loader in `domains/opportunities/analysis.py` takes an
`as_of` and truncates to observations recorded on or before it. Prices are read
by carrying the last observation forward, never by interpolating between two,
and an exit is only searched for among observations *after* the entry. A replay
that can see tomorrow's price reports a strategy as brilliant right up to the day
it loses money.

**The real engine for the money.** Profitability is recomputed on every simulated
day by `calculate_profitability`, at that day's two prices, with the product's
real category because the referral fee is keyed on it. Fees, break-even and the
ceiling are the live ones. A backtest written against a simplified fee model
measures the simplified fee model.

**The exit is an assumption, and it is labelled one.** Nothing in the data says
whether a unit *would* have sold. The `at_market` rule clears the whole position
on the first day the exit price reaches the entry's expected sale price, and
liquidates at the market price if the age limit arrives first. The rule is
reported with the result, because a return figure whose selling assumption is
hidden is a number pretending to be a measurement.

What the replay does **not** re-simulate is stated in the result rather than
buried:

* Identity is held constant. Legitimate: for a fixed pair of listings the match
  does not change with time.
* Risk, demand and competition are held at their analysed values. The observation
  history for those signals is thinner than for price, and re-deriving a risk
  level from two demand points would be inventing precision. So this replays the
  policy's **price-driven** thresholds against real price movement, not the whole
  pipeline.
* Fee assumptions are today's, not the ones in force historically.

A pair with fewer than `MIN_DAYS_COVERAGE` (10) distinct days of price history on
the thinner side is skipped and named, so a thin result is not mistaken for a
negative one. `simulated_share` reports how much of the history behind the replay
was fixture rather than market data; at 100% the first caveat says so, because a
backtest over fixtures measures the fixtures.

Refusals are grouped server-side to one row per candidate and cause, with the day
count and the span. The same product refused for the same reason on ninety
consecutive days is one fact, and the response is capped, so a client counting
the raw list would be counting a truncation.

The page renders the caveats **above** the return figure. That ordering is the
point: a replay is the most persuasive artefact the product can produce and the
easiest to misread.

## Allocating across candidates

Every layer below the allocator answers a question about one candidate. None of
them answers the question an owner of capital actually has, which is about all
of them at once: *I have $500 and eleven things that clear the policy; which
ones, and how many of each?*

Answered one at a time that question has a silent answer. Whichever candidate is
looked at first takes the capital, and the eleventh is refused for "no capital
available" without ever being compared to the first. `GET /autonomy/allocation`
compares them.

**The ranking is profit per dollar of capital**, because capital is the scarce
resource. Ordering by absolute profit buys one expensive position and leaves the
budget idle; ordering by return buys the most profit the budget can hold. Ties
break on score, then on the lower risk level, then on the identifier, so two
reads of the same data produce the same plan and a plan can be reviewed.

What the ranking deliberately excludes is how fast a position turns over. A 20%
return in ten days is worth more than a 30% return in ninety, and the platform
cannot yet say which is which: there is no measured sell-through. Inventing a
days-to-sell estimate would be inventing the most important number in the
ranking, so the age limit is left to bound how long capital stays tied up and
the ranking says what it is.

**The limits are applied across the slate rather than against the database.**
This is the whole difference from the per-opportunity path. As each line is
funded, the remaining budget, the day's deployment and the brand, category and
marketplace exposure all move, so the fourth line is checked against a portfolio
that already contains the first three. A plan cannot propose two positions that
are each inside the brand limit and together outside it, which is exactly what
two sequential decisions can do, because when each is checked neither exists yet.

**Nothing is required to be spent.** A plan that funds three lines and leaves 40%
of the budget idle is a result, and the summary says so in those words. The
request may also name a smaller budget; it can never name a larger one, because
the policy limit less what is already deployed is the ceiling.

Three things are reported alongside the plan, and each exists to stop a
particular misreading:

* **Ranked and not funded**, with the limit that stopped each candidate. An
  exposure limit wins the explanation over the budget when both bind, because
  "you already hold too much of this brand" is actionable and "there was no
  money left" is not, and the exposure limit would still refuse it at twice the
  budget.
* **What more capital would buy**: the best candidate that *only* money stopped,
  and how much more it would take. This is the marginal value of capital, which
  is the number a person needs before authorising more of it. Candidates stopped
  by a rule are deliberately excluded from it: more money would not buy them, and
  listing them there is an argument for raising a limit dressed up as arithmetic.
* **What the portfolio would hold**, held plus planned against each cap, closest
  to binding first.

**A plan is a proposal.** `POST /autonomy/allocation/commit` recomputes the plan
server-side rather than accepting lines from the client, refuses if the plan has
changed since it was read, and then hands each line to `engine.authorize` with
its own capital as the ceiling. Every gate therefore still applies: the emergency
stop, the circuit breakers, eligibility, stage disagreement. The allocator can
only ever propose less than the engine would allow, never more, and a line the
engine refuses on the way through is recorded as carefully as one it authorises.

Committing writes a `plan_committed` event alongside the per-line decisions.

## Time, once it has been measured

Every return figure in this product used to be a return on capital with no time
in it. A 20% return in ten days and a 30% return in ninety are not comparable,
and until there was something to measure, nothing could tell them apart.

`domains/learning/velocity.py` measures it from the only evidence that can say:
positions that actually closed, opening to last unit sold.

Three rules, and the third is the one that matters.

**Only closed positions count.** An open position has no holding period yet, and
counting today's age as if it were final drags every median towards whatever is
currently unsold — which is exactly the slow inventory the measurement exists to
warn about.

**A segment earns its own number.** Below five closed positions a segment gets no
median of its own and falls back to the portfolio-wide figure, which is still
measured rather than assumed. Every allocation line reports which one it used, so
a reader knows whether a number describes this kind of product or the portfolio
in general.

**No history, no number.** With nothing closed there is no median, no fallback
and no estimate. The allocator then ranks on return per dollar exactly as before
and says so. A default holding period invented to fill the gap would be the most
important number in the ranking and the only one nobody measured.

Where it *is* measured, the allocator ranks on return per year of capital tied
up, and the plan carries the evidence: the sample, the median, and the segments
that have enough closed positions to be measured on their own.

## Execution: the way back across the boundary

Spreadline decides what, where, how many and at what maximum price. It does not
place retail orders. There is no checkout here, no purchasing bot, no stored
payment method and no browser driving a shop, and `domains/execution` is where
that boundary lives.

What was missing was never the outgoing half. It was the return.

An authorised decision opened a position at the figures that were *authorised*
and nothing corrected it with the figures that were *paid*. Buy three at up to
$35.09, actually find two at $37.20, and the platform went on measuring its own
accuracy against a purchase that never happened — realised return, prediction
accuracy and holding period all inheriting the same fiction.

So an authorisation now issues an **instruction**: what to buy, how many, at no
more than what, from where, valid until when. Closing it out does three things.

**It records what happened**, including nothing happening. "The stock was gone"
is an outcome, and recording it releases the capital the position was holding —
capital the system believes is working and is not distorts every limit computed
from it.

**It names the departures rather than absorbing them.** Paying above the ceiling
is the interesting case: the ceiling was the condition the authorisation rested
on, so an order above it is recorded with `PAID_ABOVE_CEILING` rather than
quietly averaged into the cost basis. The purchase is still written into the
books — refusing to record something that already happened only makes them wrong
— but the record says the decision and the execution disagreed. The other codes
are `OVER_QUANTITY`, `UNDER_QUANTITY` (ordinary: stock runs out) and
`EXPIRED_WHEN_EXECUTED`.

**It corrects the position to reality.** Quantity, unit cost and capital are
rewritten to what was bought at what was paid. That is the point of the module.

Instructions expire, because "buy at up to $35.09" is true while the price that
justified it holds and not indefinitely. One authorisation cannot be closed out
twice: a second purchase is a second decision.

An authorised supplier or marketplace integration, if there is ever one, reads
exactly these fields and writes exactly the same ones back. That is what makes
it an executor rather than a rewrite of this layer.

## Not built yet

A supplier or marketplace integration that places orders under a real account.
The seam is now the `executor` field on an instruction, which reads `human`
today. Whether that is ever worth building is a business question rather than an
architectural one, and the answer does not change what this layer does.


