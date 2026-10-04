"""Reading a product page somebody pasted in.

Typing eleven fields twice is a form nobody fills in a second time. Select all,
copy, paste is three keystrokes, and it is still a person looking at a page with
their own eyes and moving text with their own clipboard: no automated access, no
robot, nothing fetched, and nothing in anybody's terms of service about it.

What changes is not the permission, it is the certainty. A typed field is what a
person meant. A parsed field is what a regular expression guessed, and those are
not the same thing and must never be presented as though they were.

So this module **drafts, it does not decide**. Every field comes back with the
line it was read from and how sure the parser is, the draft fills in a form the
person confirms, and nothing is analysed until they do. The human stays the
observer; the parser only saves them the typing.

Three rules follow from that:

**Certainty is graded and reported.** A labelled row like ``ASIN : B07FDJMC9Q``
is certain. A price picked out of forty dollar figures on the page is a guess
with alternatives attached. Collapsing those two into one "extracted value" is
how a parser starts lying quietly.

**A page with no product on it says so.** The single most likely paste mistake is
a marketplace home page or a search results page, which carries dozens of
products and no subject. Guessing one would be worse than finding nothing, so
the draft reports that it is not a product page and names what it saw instead.

**The identifier is usually not in the text at all.** On both marketplaces the
ASIN and the item number live in the page's web address, and a copy taken out of
a browser is the text a page *displays*: the addresses stay behind in the address
bar. So the parser reads three things in order, and the order matters. What the
page calls itself, which is the only signal that survives that copy and is why a
search page is caught by the words "Results for" rather than by counting.
Labelled rows in the details table, which print the identifier on the page where
a copy can reach it. And the addresses, when a copy happened to bring them.

**A refusal names which of those failed.** They fail differently and the fix
differs: a search page needs a different page opened, a copy with no addresses in
it needs the address bar pasted in as well. One sentence covering both told
somebody who had pasted a search page that "0 different products appear in it and
none is the subject", which is a count of nothing described as a crowd, followed
by advice about a thing that was not wrong.

**The paste is never stored.** A logged-in product page carries the reader's name,
their delivery address, their cart, their browsing history and their customer id
threaded through the tracking parameters of every link. None of that is the
platform's business. The text is parsed in memory, the extracted fields are
returned, and the original goes nowhere near the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.enums import Marketplace

#: How sure the parser is, and the words are load-bearing.
#:
#: ``certain``  read from a labelled row: "ASIN : B07FDJMC9Q".
#: ``likely``   matched a strong pattern in the right context.
#: ``guess``    a heuristic among candidates, with the others kept.
CERTAIN = "certain"
LIKELY = "likely"
GUESS = "guess"

#: Above this many distinct product identifiers with no dominant one, the paste
#: is a listing of many products rather than a page about one. A home page
#: carries dozens; a product page carries its own plus a few recommendations.
CROWD_THRESHOLD = 12

#: A product page mentions its own identifier repeatedly: the canonical link,
#: the details table, the review link. A carousel entry appears once.
DOMINANCE_MINIMUM = 3

#: How far the page's own product has to outrun the next one before a crowded
#: page counts as being about it.
#:
#: Repetition alone is not dominance. A search results page repeats its
#: sponsored placements too, so "mentioned three times" is a bar that products
#: which are the subject of nothing clear comfortably. On a real search page the
#: leader had seven mentions and the runner-up five, and the parser called it a
#: product page and picked the sponsored shoe. What actually separates a product
#: page is the margin: its own identifier outnumbers every other by a wide one.
DOMINANCE_RATIO = 2

_ASIN = re.compile(r"\b(B0[A-Z0-9]{8})\b")
_WALMART_ITEM = re.compile(r"/ip/[^/\s]+/(\d{6,12})\b")
#: Walmart prints the item number in the details section as well as the address.
#: Unlike the address, this one survives a copy of what the page displays.
_WALMART_LABELLED = re.compile(r"\b(?:Walmart|Item)\s*#\s*(\d{6,12})\b", re.I)

#: A search or category page says what it is, in words, in the visible text.
#:
#: This matters more than it looks. Every other way of telling a listing from a
#: product page reads the identifiers, and those live in the links. Copy a page
#: out of a browser and the links do not come with it, so on the commonest paste
#: of all there is nothing to count and these phrases are the only evidence
#: left. "Results for "women shoes on clearance"(436)" is unambiguous, and it is
#: still there in a copy that carries no addresses at all.
#: Phrasing no product page carries: a result range, or a heading that opens
#: with the search itself. These refuse a page on their own.
_STRONG_LISTING = (
    re.compile(r"\b1\s*-\s*\d+\s+of\s+(?:over\s+)?[\d,]+\s+results", re.I),
    re.compile(r"^\s*results for\b", re.I | re.M),
    re.compile(r"\bresults for\s*[\"“][^\"”\n]{1,60}[\"”]\s*\(\d+\)", re.I),
)
#: Phrasing that also turns up mid-page on a product page, in a "see all
#: results for" link. Only counts when nothing else says the page has a subject.
_WEAK_LISTING = (
    re.compile(r"\bresults for\b", re.I),
    re.compile(r"\b[\d,]+\s+results\b", re.I),
)
#: The search phrase itself, quoted back so the refusal names what it saw.
_SEARCH_PHRASE = re.compile(r"results for\s*[\"“]([^\"”\n]{1,60})", re.I)
_MONEY = re.compile(r"\$\s?([0-9][0-9,]*\.[0-9]{2})\b")

#: Walmart prints the cents as superscript, so a copy loses the decimal point
#: and $6.99 arrives as "$699". The money pattern above needs a decimal point,
#: so on a page rendered that way it skipped the real price and matched the
#: struck-through was-price beside it: "Now$699~~$7.99~~" was read as 7.99.
#:
#: That is the worst shape a parser bug can take. It is silent, it is
#: consistent, and it is wrong in the direction that matters, because the rows
#: rendered this way are the discounted ones and those are the rows worth
#: buying. Six clearance rows out of six read the pre-discount price.
#:
#: The last two digits are the cents. Applied only where the page shows it is
#: rendered that way, because "$160" in an Amazon price filter is one hundred
#: and sixty dollars and reading it as $1.60 would be the same class of error
#: in the other direction.
_COMPACT_MONEY = re.compile(r"(?<![.\d])\$\s?([0-9]{3,})(?![\d.,])")
#: Walmart's current-price widget. Unambiguous wherever it appears: no page
#: writes "Now$699" meaning six hundred and ninety-nine dollars.
_NOW_PRICE = re.compile(r"\bNow\s?\$\s?([0-9]{3,})(?![\d.,])")
#: A figure that is the postage, not the price.
_SHIPPING = re.compile(r"\$\s?[0-9][0-9,]*(?:\.[0-9]{2})?\s*(?:\+\s*)?shipping", re.I)
_LABELLED = {
    "asin": re.compile(r"\bASIN\s*[: ]\s*([A-Z0-9]{10})\b", re.I),
    "upc": re.compile(r"\bUPC\s*[: ]\s*([0-9]{12})\b", re.I),
    "ean": re.compile(r"\bEAN\s*[: ]\s*([0-9]{13})\b", re.I),
    "gtin": re.compile(r"\bGTIN\s*[: ]\s*([0-9]{8,14})\b", re.I),
    "isbn": re.compile(r"\bISBN(?:-1[03])?\s*[: ]\s*([0-9Xx-]{10,17})\b", re.I),
    "mpn": re.compile(
        r"\b(?:Item model number|Part Number|Model Number|MPN)\s*[: ]\s*(\S+)", re.I
    ),
    "brand": re.compile(r"^\s*(?:Brand|Manufacturer)\s*[: ]\s*(.+?)\s*$", re.I | re.M),
}
_STORE_BRAND = re.compile(r"\bVisit the (.+?) Store\b")
_RATING = re.compile(r"([0-9](?:\.[0-9])?)\s+out of 5 stars")
_REVIEWS = re.compile(r"([0-9][0-9,]*)\s+(?:ratings|reviews|global ratings)", re.I)
_RANK = re.compile(r"#\s?([0-9][0-9,]*)\s+in\s+([A-Za-z][A-Za-z &'/,-]{2,60})")
_SELLERS = re.compile(
    r"([0-9]+)\s+(?:other sellers|new (?:&|and) used|used (?:&|and) new|offers? from)", re.I
)

#: Lines that are page furniture on any marketplace. Cheap, and it keeps the
#: title heuristic from proposing "Skip to Main content".
_FURNITURE = re.compile(
    r"^(?:skip to|keyboard shortcuts|deliver to|all departments|hello,|sign in|"
    r"your account|add to cart|buy now|back to top|sponsored|see more|"
    r"customer reviews|about this item|product information|© ?\d{4})",
    re.I,
)


@dataclass
class Extracted:
    """One field the parser found, and how much to trust it."""

    name: str
    value: str
    confidence: str
    #: The line it came from, so a wrong value is traceable to the text rather
    #: than to the parser's reputation.
    evidence: str
    #: Other readings that were rejected. Present where a choice was made.
    alternatives: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "confidence": self.confidence,
            "evidence": self.evidence[:200],
            "alternatives": self.alternatives[:8],
        }


@dataclass
class PasteDraft:
    """What a paste yielded, as a proposal for a person to confirm."""

    marketplace: Marketplace | None = None
    is_product_page: bool = True
    fields: list[Extracted] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    #: Distinct product identifiers seen, used to tell a product page from a
    #: catalogue of them.
    identifiers_seen: int = 0
    #: Why the text was refused, in the words shown to the person.
    #:
    #: The refusal used to be one fixed sentence covering every way a paste can
    #: fail, which meant the commonest failure of all reported "0 different
    #: products appear in it and none is the subject": a count of nothing,
    #: described as a crowd, followed by advice to look at an address bar that
    #: was never the problem. A refusal has to say which thing went wrong.
    refusal: str | None = None

    def get(self, name: str) -> Extracted | None:
        return next((item for item in self.fields if item.name == name), None)

    @property
    def summary(self) -> str:
        if not self.is_product_page:
            return self.refusal or "That text was not read as a product page."
        found = [item.name for item in self.fields]
        if not found:
            return (
                "Nothing recognisable was found in that text. If it came from a product "
                "page, paste the whole page rather than a selection: the price, the "
                "identifier and the details table are usually far apart on it."
            )
        certain = [item.name for item in self.fields if item.confidence == CERTAIN]
        parts = [f"Read {len(found)} field(s) from the page"]
        if certain:
            parts.append(f", {len(certain)} of them from labelled rows")
        parts.append(". Check them before analysing: a parsed field is a guess about ")
        parts.append("what you were looking at, not a statement that you were.")
        return "".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "marketplace": None if self.marketplace is None else self.marketplace.value,
            "is_product_page": self.is_product_page,
            "summary": self.summary,
            "fields": [item.as_dict() for item in self.fields],
            "problems": self.problems,
            "identifiers_seen": self.identifiers_seen,
            "note": (
                "A draft, not an answer. Every value here was guessed from text you "
                "pasted; confirm it before anything is analysed. The text itself is not "
                "stored: a signed-in product page carries your name, your address and "
                "your browsing history, and none of that is the platform's business."
            ),
        }


# ------------------------------------------------------------------ helpers


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _money(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _detect_marketplace(text: str) -> Marketplace | None:
    lowered = text.lower()
    for needle, marketplace in (
        ("amazon.com", Marketplace.AMAZON),
        ("walmart.com", Marketplace.WALMART),
        ("ebay.com", Marketplace.EBAY),
        ("bestbuy.com", Marketplace.BESTBUY),
    ):
        if needle in lowered:
            return marketplace
    return None


@dataclass
class _Crowd:
    """What the identifiers in a paste add up to."""

    #: The product the page is about, or None when no one of them is.
    identifier: str | None
    #: How many distinct products the text mentions, counting every shape.
    distinct: int
    #: Which marketplaces those identifiers came from. More than one means the
    #: paste spans two pages, which is its own mistake and its own message.
    sites: set[Marketplace]


def _mentions(text: str) -> list[tuple[str, Marketplace]]:
    """Every product identifier in the text, in the order it appears.

    Both shapes are counted whatever marketplace was detected, because the
    detection only ever reports the first site named in the text. A paste
    holding a Walmart page followed by an Amazon one was read as Amazon, and
    counting ASINs alone reported 48 products where there were 98: half a crowd
    presented as the whole of it.
    """
    found: list[tuple[int, str, Marketplace]] = [
        (match.start(), match.group(1), Marketplace.AMAZON)
        for match in _ASIN.finditer(text)
    ]
    found += [
        (match.start(), match.group(1), Marketplace.WALMART)
        for match in _WALMART_ITEM.finditer(text)
    ]
    found.sort()
    return [(value, site) for _, value, site in found]


def _dominant_identifier(text: str, marketplace: Marketplace | None) -> _Crowd:
    """The product this page is about, and how many others it mentions.

    A product page repeats its own identifier: the canonical link, the details
    table, the reviews link. A carousel entry appears once, so counting is
    enough to tell a page about one thing from a page listing many.
    """
    mentions = _mentions(text)
    if not mentions:
        return _Crowd(None, 0, set())

    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    origin: dict[str, Marketplace] = {}
    for position, (value, site) in enumerate(mentions):
        counts[value] = counts.get(value, 0) + 1
        first_seen.setdefault(value, position)
        origin.setdefault(value, site)

    distinct = len(counts)
    sites = set(origin.values())

    # When the side being filled in is known, the page's own product is one of
    # that marketplace's. The count stays over everything, because the number
    # reported to the person should be what is in the text.
    candidates = [
        value
        for value in counts
        if marketplace is None or origin[value] is marketplace
    ] or list(counts)

    # Most mentions wins, and a tie is broken by whichever appeared first, not
    # alphabetically. A page is about its own product before it is about the
    # "similar items" underneath, and on a marketplace with no labelled
    # identifier row that ordering is the only signal there is. Sorting a tie by
    # value instead picked the carousel entry, which is a wrong answer that
    # looks exactly like a right one.
    best = min(candidates, key=lambda value: (-counts[value], first_seen[value]))
    best_count = counts[best]

    if distinct > CROWD_THRESHOLD:
        runner_up = max((count for value, count in counts.items() if value != best), default=0)
        if best_count < DOMINANCE_MINIMUM or best_count < DOMINANCE_RATIO * runner_up:
            return _Crowd(None, distinct, sites)
    return _Crowd(best, distinct, sites)


#: What to do instead, appended to every refusal. Named once because a refusal
#: that does not end in an action is a dead end.
_OPEN_THE_PRODUCT = (
    "Open one product's own page, the one with a single Add to cart button on "
    "it, and copy that."
)


def _refusal(text: str, crowd: _Crowd) -> str | None:
    """Why this text is not a product page, or None when it is one.

    Three different things go wrong and they need three different answers. The
    one that matters most is the last: a copy of what a page displays contains
    no addresses, so it contains no item numbers, and telling somebody that
    "none of the products in it is the subject" when the parser found no
    products at all sends them to fix something that was never broken.
    """
    strong = any(pattern.search(text) for pattern in _STRONG_LISTING)

    if crowd.identifier is not None:
        # A page that names its own search is a search page however few results
        # came with the copy. Anything weaker is not allowed to overrule a page
        # that does have a subject, because "see all results for" appears on
        # product pages too.
        return _listing_refusal(text, crowd) if strong else None

    if strong or any(pattern.search(text) for pattern in _WEAK_LISTING):
        return _listing_refusal(text, crowd)

    if crowd.distinct:
        return (
            f"{crowd.distinct} different products appear in that text and no one of "
            "them is its subject, so it reads as a list of products rather than a "
            f"page about one. {_OPEN_THE_PRODUCT}"
        )

    return (
        "No item number was found in that text, and a copy of what a page displays "
        "usually has none in it: the ASIN or Walmart item number lives in the page's "
        "web address, and the address bar is not part of what you copy. Two ways to "
        "give it one. Copy the address out of the address bar and paste it in as "
        "well, on a line of its own. Or scroll down to the product details table, "
        "which prints the same number on the page, and include that in the "
        "selection."
    )


def _listing_refusal(text: str, crowd: _Crowd) -> str:
    """A search or category page, named as one and quoting what it searched."""
    phrase = _SEARCH_PHRASE.search(text)
    opening = (
        f'That is a page of search results for "{phrase.group(1).strip()}"'
        if phrase
        else "That is a search or category listing"
    )
    counted = (
        f", listing {crowd.distinct} products"
        if crowd.distinct
        else ", listing many products"
    )
    return f"{opening}{counted} and about none of them. {_OPEN_THE_PRODUCT}"


def _title_candidate(lines: list[str]) -> tuple[str, str] | None:
    """The most plausible product title, and the line it came from.

    Heuristic and labelled as such. A product title is a long line, near the
    top, that is not page furniture and is not a sentence of marketing prose.
    The rating line is a reliable floor: on every marketplace the title sits
    above it.
    """
    # The title sits above both the rating and the price on every marketplace, so
    # the earlier of the two is the floor. Without it the longest line on the
    # page wins, and the longest line is usually a recommendation from the
    # carousel at the bottom.
    ceiling = len(lines)
    for index, line in enumerate(lines):
        if _RATING.search(line) or _MONEY.search(line):
            ceiling = index
            break

    best: tuple[int, str] | None = None
    for line in lines[:ceiling][:120]:
        if _FURNITURE.match(line) or line.startswith(("*", "[", "#", "$")):
            continue
        # A line carrying a link is a link, not a title. This is what let a
        # "similar items" row win: it was the longest thing on the page.
        if "http" in line or "www." in line:
            continue
        if len(line) < 25 or len(line) > 400:
            continue
        # A title is dense with content words and light on punctuation runs.
        if line.count("|") > 2 or line.count("·") > 1:
            continue
        score = len(line)
        if best is None or score > best[0]:
            best = (score, line)
    return (best[1], best[1]) if best else None


def _renders_compact_cents(text: str, marketplace: Marketplace | None) -> bool:
    """Whether this page drops the decimal point out of its prices.

    Two ways to know, and both are evidence from the text rather than a guess
    about it. The page uses the "Now$699" widget, which nothing else writes. Or
    the side being filled in is Walmart, which renders every price that way.
    """
    return marketplace is Marketplace.WALMART or _NOW_PRICE.search(text) is not None


def _price_candidates(
    lines: list[str], *, compact: bool
) -> list[tuple[Decimal, str]]:
    """Every dollar figure on the page, in the order it appears.

    In the order it appears, and that ordering is what makes the first one the
    price: on a discounted row the current price is printed before the one it
    replaced.
    """
    found: list[tuple[Decimal, str]] = []
    for line in lines:
        # The postage is not a price, and on a search row it sits close enough
        # to one to be taken for it.
        without_postage = _SHIPPING.sub(" ", line)
        seen: list[tuple[int, Decimal]] = []
        for match in _MONEY.finditer(without_postage):
            value = _money(match.group(1))
            if value is not None and value > 0:
                seen.append((match.start(), value))
        if compact:
            for match in _NOW_PRICE.finditer(without_postage):
                value = _cents(match.group(1))
                if value is not None:
                    seen.append((match.start(), value))
            for match in _COMPACT_MONEY.finditer(without_postage):
                # A "Now$" figure is already counted, at an earlier offset
                # because of the word in front of it.
                if any(start <= match.start() <= start + 6 for start, _ in seen):
                    continue
                value = _cents(match.group(1))
                if value is not None:
                    seen.append((match.start(), value))
        found.extend((value, line) for _, value in sorted(seen))
    return found


def _cents(digits: str) -> Decimal | None:
    """A price whose decimal point did not survive the copy. "699" is 6.99."""
    try:
        value = Decimal(digits[:-2] + "." + digits[-2:])
    except InvalidOperation:
        return None
    return value if value > 0 else None


# ------------------------------------------------------------------ parse


def parse(text: str, *, marketplace: Marketplace | None = None) -> PasteDraft:
    """Read a pasted product page into a draft for a person to confirm."""
    draft = PasteDraft()
    if not text or not text.strip():
        draft.problems.append("Nothing was pasted.")
        draft.is_product_page = False
        return draft

    lines = _lines(text)
    draft.marketplace = marketplace or _detect_marketplace(text)

    crowd = _dominant_identifier(text, draft.marketplace)
    identifier = crowd.identifier
    draft.identifiers_seen = crowd.distinct

    labelled: dict[str, tuple[str, str]] = {}
    for name, pattern in _LABELLED.items():
        match = pattern.search(text)
        if match:
            line = next(
                (line for line in lines if match.group(1) in line), match.group(0)
            )
            labelled[name] = (match.group(1).strip(), line)

    # A labelled identifier row settles it: that is the page's own product,
    # whatever else appears in the carousels below it. Read before the page is
    # judged, so a product page carrying the word "results" somewhere in its
    # recommendations is never refused as a search page.
    walmart_row = _WALMART_LABELLED.search(text)
    if "asin" in labelled:
        identifier = labelled["asin"][0]
    elif walmart_row and draft.marketplace is not Marketplace.AMAZON:
        identifier = walmart_row.group(1)
    elif (refusal := _refusal(text, crowd)) is not None:
        draft.is_product_page = False
        draft.refusal = refusal
        draft.problems.append(refusal)
        if len(crowd.sites) > 1:
            draft.problems.append(
                "That text also spans two marketplaces. Each side of the comparison "
                "goes in its own box: the page you would buy from on one, the page "
                "you would sell on on the other."
            )
        return draft

    draft.fields.append(
        Extracted(
            name="external_id",
            value=identifier,
            confidence=CERTAIN if "asin" in labelled else LIKELY,
            evidence=labelled.get("asin", (identifier, f"identifier {identifier}"))[1],
        )
    )

    for name in ("upc", "ean", "gtin", "isbn", "mpn"):
        if name in labelled:
            value, line = labelled[name]
            draft.fields.append(
                Extracted(name=name, value=value, confidence=CERTAIN, evidence=line)
            )

    if "brand" in labelled:
        value, line = labelled["brand"]
        draft.fields.append(
            Extracted(name="brand", value=value, confidence=CERTAIN, evidence=line)
        )
    else:
        store = _STORE_BRAND.search(text)
        if store:
            draft.fields.append(
                Extracted(
                    name="brand",
                    value=store.group(1).strip(),
                    confidence=LIKELY,
                    evidence=store.group(0),
                )
            )

    title = _title_candidate(lines)
    if title:
        draft.fields.append(
            Extracted(name="title", value=title[0], confidence=GUESS, evidence=title[1])
        )
    else:
        draft.problems.append(
            "No title could be picked out. Type it in: it is one field and it is the "
            "one a person recognises the product by."
        )

    prices = _price_candidates(
        lines, compact=_renders_compact_cents(text, draft.marketplace)
    )
    if prices:
        chosen, line = prices[0]
        draft.fields.append(
            Extracted(
                name="price",
                value=str(chosen),
                confidence=GUESS,
                evidence=line,
                # Every other figure on the page, so a wrong pick is one click to
                # correct rather than a retype. A product page carries the list
                # price, the subscription price, the trade-in offer and forty
                # prices belonging to other products.
                alternatives=[str(value) for value, _ in prices[1:12]],
            )
        )
        if len(prices) > 6:
            draft.problems.append(
                f"{len(prices)} prices appear on that page. The first one was taken, "
                "which is usually the buy box, and the rest are listed as alternatives. "
                "Check it."
            )
    else:
        draft.problems.append("No price was found. It is the one field worth typing by hand.")

    rank = _RANK.search(text)
    if rank:
        draft.fields.append(
            Extracted(
                name="sales_rank",
                value=rank.group(1).replace(",", ""),
                confidence=LIKELY,
                evidence=rank.group(0),
            )
        )
        draft.fields.append(
            Extracted(
                name="category",
                value=rank.group(2).strip().lower(),
                confidence=LIKELY,
                evidence=rank.group(0),
            )
        )

    rating = _RATING.search(text)
    if rating:
        draft.fields.append(
            Extracted(
                name="rating",
                value=rating.group(1),
                confidence=LIKELY,
                evidence=rating.group(0),
            )
        )
    reviews = _REVIEWS.search(text)
    if reviews:
        draft.fields.append(
            Extracted(
                name="review_count",
                value=reviews.group(1).replace(",", ""),
                confidence=LIKELY,
                evidence=reviews.group(0),
            )
        )
    sellers = _SELLERS.search(text)
    if sellers:
        draft.fields.append(
            Extracted(
                name="seller_count",
                value=sellers.group(1),
                confidence=GUESS,
                evidence=sellers.group(0),
            )
        )

    if not draft.get("category"):
        draft.problems.append(
            "No category was found, and the referral fee is keyed on it. It is the "
            "field most likely to move the profit figure, so it is worth setting."
        )
    return draft
