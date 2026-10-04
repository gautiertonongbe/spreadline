# Interface

The analysis engines are the hard part of this platform. The interface is the
part that decides whether any of it gets used, and it has exactly one job: turn
a page of computed facts into a decision someone can make in under a minute
without losing the ability to audit it afterwards.

## What the field complains about

Two consistent findings from how sellers talk about the existing tools, and both
shaped the layout rather than the copy:

- **The bulk scanners are powerful and hard.** Reviews of Tactical Arbitrage
  repeatedly describe a steep learning curve that makes it unsuitable for
  beginners and slow to productivity even for experienced sellers.
- **The verdict tools are liked precisely because they are opinionated.**
  BuyBotPro's guided approach, which scores a deal and gives a clear buy
  recommendation rather than raw numbers, is what users single out, especially
  newer ones.

And the structural complaint underneath both: most serious sellers run at least
two tools, one for discovery and one for single-product verification, plus a
price-history tool and a fee calculator. The tab switching is the work.

Spreadline's answer is not fewer facts. It is an order: **the call, the money,
the blocker, then everything else**.

## The words

The reader is someone deciding how to spend their own money, not an analyst.
They want three things: what it costs now, what it sells for, and what they
make. So the interface says those things, and the vocabulary of the domain model
stays in the domain model.

| Stored | Shown |
| --- | --- |
| `buy` / `review` / `pass` | Buy it / Check it first / Skip it |
| net profit per unit | You make |
| ROI | Back on your money |
| maximum acquisition cost | Do not pay over |
| match confidence | 97% same item |
| "ROI at or above 20%" | "Returns at least 20% on your money" |
| "Demand evidence exists" | "Evidence that it actually sells" |

The stored taxonomy does not change: filters, the API, the tests and every URL
are written against `buy` / `review` / `pass`. The mapping lives in one place
(`lib/format.ts` for the client, the gate labels for the engine) so the two can
never drift.

The same applies to the generated sentences. A headline that opens with a score
answers a question nobody asked, so every decision headline now leads with the
money and lets the score follow:

> Worth buying. You make $19.02 per item, a 129.4% return after every fee, and
> the risk is low.

Two small things that sound pedantic and are not. Money in prose carries its
symbol (`display_currency`, not `display`), because "makes 71.65 per item" reads
as a quantity of something unnamed. And a percentage in a sentence is written to
the same precision as the figure shown next to it: rounding to "a 32% return"
beside a panel reading 31.8% costs the reader their trust in both numbers.

## The verdict block

Every screen that ends in a decision opens with the same block:

```
THE CALL
Check it first
Worth a look, but check 1 thing first. It would make $19.02 per item,
a 129.4% return after every fee.

  $19.02      129.4%                $32.77
  You make    Back on your money    Do not pay over

WHAT IS STOPPING IT
  No serious warnings. The source price looks like clearance, not a
  repeatable cost: it will not be there for a restock.
```

Three numbers, never four. The third is the acquisition ceiling, because for a
buyer that is the operative figure: profit and return are consequences of what
was paid, and the ceiling is the thing they can act on at a supplier.

A blocker is written as the requirement **and** the finding. A gate label alone
reads as a statement of fact rather than as the thing that failed: "No serious
warnings", sitting under a held candidate, says the opposite of what happened.

The blockers are the failed gates, hard ones first, followed by any blocking
risk signal. A hard gate ends the discussion; a soft gate only says why a
profitable candidate is being held for a human. That distinction is drawn in the
dot colour and stated in full when the gate list is opened.

## The two-market strip

Directly under the verdict: the source price, the exit price, and between them
the match confidence. This is the comparison the whole platform exists to make,
and putting the identity figure physically between the two prices is deliberate.
Two prices side by side are a claim about one product; the confidence is what
makes that claim legitimate rather than a guess about two similar titles.

Each side carries a link to the listing, styled as an action rather than as a
footnote, and the strip states plainly what happens next:

> Spreadline does not place orders. Open the listing and buy it on the
> retailer's own site, then come back and record what you paid so the prediction
> can be checked against what actually happened.

That is a product decision, not a missing feature. Spreadline does not automate
retailer checkout, purchasing, payment or fulfilment, so the order is placed by a
person on the retailer's site. It is written on the screen because it is the
first question anyone asks and it should not need a help page.

## An answer, not an error

Not finding the item on the other market used to surface as a red API error
carrying a 404 message. That reads as "the tool is broken" when what actually
happened is a real answer: without a price on the other side there is no profit
to compute, and inventing one is precisely what this platform refuses to do. It
now renders in the verdict layout, headed "Cannot price it", saying what was
searched, what it costs on the side we do have, and that a barcode or model
number often finds a listing a title search cannot.

## The working

Everything else, folded. Score components, decision gates, the fee-by-fee
breakdown, identity evidence, demand, competition, stress scenarios, price
history windows and the decision log are all still on the page, and none of it
is summarised away or loaded behind a request that could fail. They are native
`details` elements with a one-line summary visible while closed, so the reader
can tell whether to open one without opening it.

This is the whole compromise the spec demands: full transparency, no
overwhelm. An explanation nobody opens is not transparency, it is noise.

## Two views of the list

`/opportunities` defaults to **Decisions**: one row per candidate carrying the
call, the score, the product, the decision engine's own sentence, the first
failed gate, and the same three numbers. **Full table** restores the thirteen
column table for anyone comparing across a portfolio.

The choice is a URL parameter, so a view is a shareable link, and the filter
panel preserves it: clicking a preset changes the filters, never the reader's
chosen layout.

To carry that row the list endpoint denormalises three fields onto
`OpportunitySummary` — `headline`, `primary_blocker` and
`max_acquisition_cost` — batched into the same two extra queries that already
fetch product titles. A hundred-row table costs three queries.

## Light and dark

The light theme is the default, because it is the theme the supplier tab, the
spreadsheet and the invoice next to this window are already in. The dark theme
is the same design with the values inverted, not a second design.

Every colour is a CSS variable holding an RGB triple, consumed through
Tailwind's `<alpha-value>` placeholder, so `bg-buy/12` composes identically in
both themes and no component knows which one it is rendering into. Neither
surface is pure: the dark canvas is near-black with a warm cast, the light one
is warm paper, and text is ink rather than black. Maximum contrast is fatiguing
over the length of time an operator spends on a table.

The theme is resolved by a synchronous script in the document head, from the
stored choice and otherwise from the operating system. Two things make that
work, and both are easy to get wrong:

- `data-theme` is **not** rendered in the server markup. If React owns the
  attribute, hydration reconciles it back to whatever the server rendered and
  the stored theme flips to light on every page load.
- The storage key lives in `lib/theme.ts`, not in the `"use client"` toggle.
  Importing a plain value out of a client module into a server component yields
  a client reference, not the string, and the script silently reads
  `localStorage.getItem(undefined)`: the choice is written correctly and never
  read back.

## Typography and the mark

Playfair Display carries the identity and every display figure. It is never used
for dense numeric columns: at 11px a hairline serif makes an 8 and a 3 hard to
separate, and those columns are money. Those cells are IBM Plex Mono with
tabular figures, so decimal points align down the page. Inter is the reading and
interface face.

The mark is two horizontal rules, staggered, with a vertical measure joining
them: two price levels in two markets and the distance between them, which is
also a stylised S in three strokes. Only the measure is gold, and that is the
idea of the mark. The money is not in either price, it is in the gap. Gold is
never used for a result anywhere in the interface for the same reason.

## Rules that hold everywhere

- Money crosses the wire as a decimal string and is formatted for display. It
  never becomes a JavaScript number.
- A nullable value stays nullable to the component and renders as "not
  available", never as zero.
- Colour is meaning. A recommendation, a risk level and a confidence each own
  one colour, and gold is never one of them.
