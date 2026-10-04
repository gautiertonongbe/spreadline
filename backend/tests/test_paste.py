"""Reading a product page somebody pasted in.

The parser drafts and the person decides, and every test here is about keeping
that boundary honest: that a guess is labelled a guess, that a page with no
product on it says so rather than picking one, and that the pasted text never
reaches the database.
"""

from __future__ import annotations

from app.domains.opportunities import paste
from app.models.enums import Marketplace

API = "/api/v1"

# A product page reduced to the parts a copy actually contains, in the order it
# contains them: navigation, the title block, the price, the details table.
PRODUCT_PAGE = """
Skip to Main content
Deliver to Gautier San Franc... 94109
Hello, Gautier
Account & Lists
Visit the Ninja Store
Ninja AF101 Air Fryer 4 Qt, Grey, Air Fry, Roast, Reheat, Dehydrate
4.7 out of 5 stars 52,431 ratings
$89.99
List Price: $129.99
Subscribe & Save: $85.49
Add to Cart
Buy Now
About this item
NOW WITH MORE COOKING VERSATILITY
Product information
Brand : Ninja
Item model number : AF101
ASIN : B07FDJMC9Q
UPC : 622356561235
Best Sellers Rank: #12 in Home & Kitchen  #1 in Air Fryers
6 other sellers
https://www.amazon.com/Ninja-AF101-Air-Fryer/dp/B07FDJMC9Q/ref=sr_1_1
Customers who viewed items in your browsing history also viewed
Instant Vortex Plus Air Fryer $79.00
https://www.amazon.com/Instant-Vortex/dp/B07VTBLQLL/
© 1996-2026, Amazon.com, Inc. or its affiliates
"""

# What a home page copy looks like: many products, none of them the subject.
HOME_PAGE = """
Skip to Main content
Deliver to Gautier San Franc... 94109
Hello, Gautier
Today's Deals
Recommended deals for you
aosu 5MP Ultra HD Wireless Video Doorbell Camera
https://www.amazon.com/Doorbell-Wireless/dp/B09H2T18WD/ 20% off
Blink Mini Camera Adhesive Wall Mount Bracket
https://www.amazon.com/Adhesive-Bracket/dp/B0BGSVKZ89/ $12.99
eufy Security 4K Indoor Camera E30
https://www.amazon.com/eufy-Generation/dp/B0DJVGZQW7/ $54.99
Soundcore Anker 3 Portable Bluetooth Speaker
https://www.amazon.com/Soundcore-Bluetooth/dp/B08BCHKY52/ $35.99
White Ceramic Flower Vase Set of 2
https://www.amazon.com/White-Ceramic/dp/B0CF9K1NJM/ $24.99
The Mountain Is You
https://www.amazon.com/Mountain-You/dp/B0FTGN8CDQ/ $11.31
Travel Size Mouthwash Alcohol-Free
https://www.amazon.com/Travel-Size/dp/B0DSS9H2SM/ $9.99
VASAGLE Computer Desk Steel Frame
https://www.amazon.com/VASAGLE-Computer/dp/B0GL2549H5/ $69.99
TOCOL iPhone 14 Pro Camera Lens Protector
https://www.amazon.com/TOCOL-iPhone/dp/B0FQ4929DH/ $7.99
iTokGok Silicone Electric Toothbrush Travel Case
https://www.amazon.com/iTokGok-Silicone/dp/B0FHFQXPS2/ $8.59
LISEN Airplane Travel Essentials Phone Holder
https://www.amazon.com/LISEN-Essentials/dp/B0CKN1N2YH/ $15.99
Bose SoundLink Plus Portable Bluetooth Speaker
https://www.amazon.com/Bose-SoundLink/dp/B0F7HVC62G/ $199.00
DOSS SoundBox Touch Wireless Bluetooth Speaker
https://www.amazon.com/DOSS-Wireless/dp/B08K78R9XQ/ $29.99
Marshall Emberton III Portable Bluetooth Speaker
https://www.amazon.com/Marshall-Emberton/dp/B0GXH3N61Z/ $144.05
Tribit XSound Go Portable Bluetooth Speaker
https://www.amazon.com/Tribit-XSound/dp/B07594HZ6Y/ $34.99
© 1996-2026, Amazon.com, Inc. or its affiliates
"""


class TestAPageWithNoProductOnIt:
    """The most likely paste mistake, and the most dangerous one to guess at."""

    def test_a_home_page_is_refused_rather_than_guessed_at(self):
        draft = paste.parse(HOME_PAGE)
        assert not draft.is_product_page
        assert draft.identifiers_seen > paste.CROWD_THRESHOLD
        assert "list of products rather than a page about one" in draft.summary
        assert not draft.fields, "nothing may be proposed from a page about nothing"

    def test_it_says_what_to_do_instead(self):
        draft = paste.parse(HOME_PAGE)
        assert "product's own page" in draft.summary

    def test_empty_text_is_refused(self):
        draft = paste.parse("   ")
        assert not draft.is_product_page
        assert "Nothing was pasted" in draft.problems[0]

    def test_text_with_no_identifier_says_where_to_find_one(self):
        draft = paste.parse("A page about nothing in particular, with a $10.00 price.")
        assert not draft.is_product_page
        assert "ASIN" in draft.problems[0]


class TestWhatItReadsFromAProductPage:
    def test_the_labelled_rows_are_certain(self):
        draft = paste.parse(PRODUCT_PAGE)
        assert draft.is_product_page

        for name, value in (
            ("external_id", "B07FDJMC9Q"),
            ("upc", "622356561235"),
            ("mpn", "AF101"),
            ("brand", "Ninja"),
        ):
            item = draft.get(name)
            assert item is not None, f"{name} was not read"
            assert item.value == value
            assert item.confidence == paste.CERTAIN

    def test_the_page_s_own_product_wins_over_the_carousel_below_it(self):
        """A product page also lists other products. It is still about one."""
        draft = paste.parse(PRODUCT_PAGE)
        assert draft.get("external_id").value == "B07FDJMC9Q"
        assert draft.identifiers_seen > 1, "the carousel is in the text"

    def test_the_marketplace_is_detected(self):
        assert paste.parse(PRODUCT_PAGE).marketplace is Marketplace.AMAZON

    def test_rank_and_category_come_from_the_same_line(self):
        draft = paste.parse(PRODUCT_PAGE)
        assert draft.get("sales_rank").value == "12"
        assert draft.get("category").value == "home & kitchen"

    def test_rating_and_reviews_are_read(self):
        draft = paste.parse(PRODUCT_PAGE)
        assert draft.get("rating").value == "4.7"
        assert draft.get("review_count").value == "52431"

    def test_the_title_is_found_and_labelled_a_guess(self):
        draft = paste.parse(PRODUCT_PAGE)
        title = draft.get("title")
        assert title is not None
        assert "Ninja AF101" in title.value
        assert title.confidence == paste.GUESS, "a title is picked, not read"


# A Walmart page has no labelled identifier row, so the only signal telling the
# product from the carousel beneath it is where each one appears. Both of the
# bugs below were found by driving the real form, not by reading the code.
WALMART_PAGE = """
Walmart.com
Ninja AF101 Air Fryer 4 Qt, Grey, Air Fry, Roast, Reheat, Dehydrate
$56.00
Add to cart
Product details
Brand : Ninja
UPC : 622356561235
Item model number : AF101
https://www.walmart.com/ip/Ninja-AF101-Air-Fryer/874523001
Similar items you might like
Instant Vortex Plus Air Fryer 6 Qt Stainless Steel Large Capacity
$71.00
https://www.walmart.com/ip/Instant-Vortex-Plus-Air-Fryer/998877001
"""


class TestThePageIsAboutItsOwnProduct:
    """Both of these picked the carousel item before they were fixed."""

    def test_the_identifier_is_the_page_s_own_not_the_one_below_it(self):
        """A tie on mentions breaks on which appeared first, not alphabetically.

        Sorted by value, 998877001 beat 874523001 and the parser confidently
        returned the wrong product. That is a wrong answer shaped exactly like a
        right one.
        """
        draft = paste.parse(WALMART_PAGE, marketplace=Marketplace.WALMART)
        assert draft.is_product_page
        assert draft.get("external_id").value == "874523001"

    def test_the_title_is_not_a_line_from_the_carousel(self):
        """The longest line on the page was a recommendation with a link in it."""
        draft = paste.parse(WALMART_PAGE, marketplace=Marketplace.WALMART)
        title = draft.get("title").value
        assert "Ninja AF101" in title
        assert "Instant Vortex" not in title
        assert "http" not in title

    def test_the_price_is_the_one_above_the_fold(self):
        draft = paste.parse(WALMART_PAGE, marketplace=Marketplace.WALMART)
        assert draft.get("price").value == "56.00"
        assert "71.00" in draft.get("price").alternatives

    def test_a_line_carrying_a_link_is_never_the_title(self):
        text = WALMART_PAGE.replace(
            "Ninja AF101 Air Fryer 4 Qt, Grey, Air Fry, Roast, Reheat, Dehydrate",
            "https://www.walmart.com/ip/a-very-long-link-that-would-otherwise-win/1",
        )
        draft = paste.parse(text, marketplace=Marketplace.WALMART)
        title = draft.get("title")
        assert title is None or "http" not in title.value


# The paste that found the two bugs below: a Walmart clearance search, which is
# what somebody actually reaches for when looking for something to buy. Cut to
# fourteen results, which is all the crowd threshold needs, and kept in the two
# shapes a copy arrives in. The difference between them is the whole problem: a
# copy taken out of a browser carries the text a page displays and none of its
# addresses, and on a marketplace the item number is only ever in the address.
SEARCH_RESULTS = """
Skip to Main Content
Results for "women shoes on clearance"(436)
Best seller Orthopedic Slip on Walking Shoes for Women
https://www.walmart.com/ip/Willtoo-Orthopedic-Shoes-Women/17244314394?from=/search
Now$699 $7.99 +$9.99 shipping
Clearance Women's Elevate Athletic Sneakers, Wide Width Available
https://www.walmart.com/ip/Avia-Women-s-Elevate-Athletic-Sneakers/2517883946?from=/search
Now$1500 $25.00
Clearance Women's Ballet Flats Square Toe Velvet Mary Jane Shoes
https://www.walmart.com/ip/Vibrex-Women-s-Ballet-Flats-Square-Toe/19360811617?from=/search
Now$887 $10.08 +$7.99 shipping
Clearance Women's Vintage Woven Mary Jane Flat Shoes
https://www.walmart.com/ip/Gpaecead-Women-s-Vintage-Woven-Mary-Jane/20613658653?from=/search
Now$1259 $21.39 +$8.50 shipping
Women's 5000 Performance Sneakers, Wide Width Available
https://www.walmart.com/ip/W-AV-5000-SNEAKER/19291156270?from=/search
$2400
Women's Slip On Loafers Casual Comfort Walking Flats
https://www.walmart.com/ip/Sandalup-Women-s-Slip-On-Loafers/11223344556?from=/search
$1899
Women's Memory Foam Running Shoes Lightweight Athletic
https://www.walmart.com/ip/Akk-Women-s-Memory-Foam-Running/22334455667?from=/search
$2299
Women's Winter Snow Boots Waterproof Warm Fur Lined
https://www.walmart.com/ip/Dream-Pairs-Women-s-Winter-Snow-Boots/33445566778?from=/search
$3499
Women's Platform Sandals Open Toe Ankle Strap
https://www.walmart.com/ip/Cushionaire-Women-s-Platform-Sandals/44556677889?from=/search
$1599
Women's Canvas Low Top Lace Up Sneakers
https://www.walmart.com/ip/Time-and-Tru-Women-s-Canvas-Low-Top/55667788990?from=/search
$1200
Women's Knee High Riding Boots Wide Calf
https://www.walmart.com/ip/Journee-Collection-Women-s-Knee-High/66778899001?from=/search
$4599
Women's Fuzzy Slippers Memory Foam House Shoes
https://www.walmart.com/ip/Isotoner-Women-s-Fuzzy-Slippers/77889900112?from=/search
$1450
Women's Pointed Toe Kitten Heel Pumps
https://www.walmart.com/ip/Dream-Pairs-Women-s-Pointed-Toe/88990011223?from=/search
$2750
Women's Chunky Sole Combat Ankle Boots
https://www.walmart.com/ip/Soda-Women-s-Chunky-Sole-Combat/99001122334?from=/search
$3200
"""

# The same page, copied the way people actually copy: select all, and the
# addresses stay behind in the address bar.
SEARCH_AS_DISPLAYED = "\n".join(
    line for line in SEARCH_RESULTS.splitlines() if "http" not in line
)

# And the paste that arrived in one box: the Walmart search with an Amazon one
# concatenated onto it, because both sides of the comparison went in together.
AMAZON_SEARCH = """
Amazon.com : women shoes
1-48 of over 60,000 results for "women shoes"
Skechers Women's Go Walk Joy Walking Shoe
https://www.amazon.com/Skechers-Womens-Walk-Joy/dp/B0DT1KBPZ8/ref=sr_1_1
$44.99
adidas Women's Cloudfoam Pure Running Shoe
https://www.amazon.com/adidas-Womens-Cloudfoam-Pure/dp/B0BHPTW8PP/ref=sr_1_2
$59.99
Crocs Women's Classic Clog
https://www.amazon.com/Crocs-Womens-Classic-Clog/dp/B0DK5TN2XV/ref=sr_1_3
$49.99
Hey Dude Women's Wendy Slip On Loafer
https://www.amazon.com/Hey-Dude-Womens-Wendy/dp/B0CTBD9XMK/ref=sr_1_4
$59.95
New Balance Women's 574 Core Sneaker
https://www.amazon.com/New-Balance-Womens-574/dp/B073WHN5BL/ref=sr_1_5
$89.99
"""
BOTH_PAGES = SEARCH_RESULTS + AMAZON_SEARCH


class TestASearchResultsPage:
    """The commonest paste there is, and it was answered with a contradiction.

    The refusal was one fixed sentence covering every way a paste can fail, so
    a copy carrying no addresses at all was told that "0 different products
    appear in it and none is the subject": a count of nothing, described as a
    crowd, followed by advice about an address bar that was never the problem.
    """

    def test_a_search_page_is_refused_by_what_it_calls_itself(self):
        draft = paste.parse(SEARCH_RESULTS)
        assert not draft.is_product_page
        assert not draft.fields

    def test_it_is_refused_when_the_copy_carries_no_addresses(self):
        """The shape that produced the wrong answer. Nothing to count here."""
        draft = paste.parse(SEARCH_AS_DISPLAYED)
        assert not draft.is_product_page
        assert draft.identifiers_seen == 0
        assert "search results" in draft.summary

    def test_a_count_of_nothing_is_never_described_as_a_crowd(self):
        draft = paste.parse(SEARCH_AS_DISPLAYED)
        assert "0 different products" not in draft.summary
        assert "none is the subject" not in draft.summary

    def test_it_quotes_back_what_was_searched_for(self):
        """Naming it is how somebody recognises their own mistake."""
        for text in (SEARCH_RESULTS, SEARCH_AS_DISPLAYED):
            assert "women shoes on clearance" in paste.parse(text).summary

    def test_a_crowd_with_a_repeated_member_still_has_no_subject(self):
        """Repetition is not dominance.

        The leader on the real page had seven mentions and the runner-up five,
        which cleared "mentioned at least three times" and had the parser
        announce a sponsored shoe as the product the page was about. That is a
        wrong answer shaped exactly like a right one.
        """
        crowded = SEARCH_RESULTS.replace("Results for", "Deals for") + (
            "\nSponsored\n"
            + "\n".join(
                "https://www.walmart.com/ip/Willtoo-Orthopedic-Shoes-Women/17244314394"
                for _ in range(4)
            )
            + "\n"
            + "\n".join(
                "https://www.walmart.com/ip/Avia-Women-s-Elevate-Athletic/2517883946"
                for _ in range(3)
            )
        )
        draft = paste.parse(crowded)
        assert not draft.is_product_page, "no margin, so no subject"
        assert draft.get("external_id") is None


class TestAPasteHoldingTwoPages:
    def test_every_product_is_counted_whichever_site_it_came_from(self):
        """Detection returns the first site named, and counting followed it.

        A Walmart page with an Amazon one after it was read as Amazon, so only
        the ASINs were counted and the report said 48 products where the text
        held 98.
        """
        draft = paste.parse(BOTH_PAGES)
        walmart_only = paste.parse(SEARCH_RESULTS).identifiers_seen
        amazon_only = paste.parse(AMAZON_SEARCH).identifiers_seen
        assert walmart_only and amazon_only
        assert draft.identifiers_seen == walmart_only + amazon_only

    def test_it_says_the_two_pages_belong_in_two_boxes(self):
        draft = paste.parse(BOTH_PAGES)
        assert not draft.is_product_page
        assert any("spans two marketplaces" in note for note in draft.problems)


# A product page copied the same lossy way: no addresses, but the details table
# prints the identifier on the page itself, which is what rescues it.
AMAZON_AS_DISPLAYED = "\n".join(
    line for line in PRODUCT_PAGE.splitlines() if "http" not in line
)
WALMART_AS_DISPLAYED = """
Ninja AF101 Air Fryer 4 Qt, Grey, Air Fry, Roast, Reheat, Dehydrate
$56.00
Add to cart
Product details
Brand : Ninja
UPC : 622356561235
Walmart # 874523001
"""


class TestAProductPageCopiedWithoutItsAddresses:
    """The same lossy copy, on a page that does have a subject."""

    def test_amazon_survives_on_its_details_table(self):
        draft = paste.parse(AMAZON_AS_DISPLAYED)
        assert draft.is_product_page
        assert draft.get("external_id").value == "B07FDJMC9Q"

    def test_walmart_reads_the_item_number_off_the_page(self):
        draft = paste.parse(WALMART_AS_DISPLAYED, marketplace=Marketplace.WALMART)
        assert draft.is_product_page
        assert draft.get("external_id").value == "874523001"

    def test_with_no_identifier_anywhere_it_says_where_to_get_one(self):
        """Not "none of them is the subject". There are none of them."""
        draft = paste.parse(
            "Ninja AF101 Air Fryer 4 Qt, Grey\n$56.00\nAdd to cart\nFree shipping"
        )
        assert not draft.is_product_page
        assert draft.identifiers_seen == 0
        assert "address bar" in draft.summary
        assert "product details" in draft.summary


class TestPriceIsTheDangerousOne:
    def test_the_first_price_is_taken_and_the_rest_are_kept(self):
        """A product page carries the list price, the subscription price and
        forty prices belonging to other products."""
        draft = paste.parse(PRODUCT_PAGE)
        price = draft.get("price")
        assert price.value == "89.99"
        assert price.confidence == paste.GUESS
        assert "129.99" in price.alternatives
        assert "85.49" in price.alternatives

    def test_a_page_thick_with_prices_says_so(self):
        """A real page carries dozens. Taking the first silently would be luck."""
        crowded = PRODUCT_PAGE + "\n".join(
            f"Recommended item {index} $ {index + 10}.99" for index in range(10)
        )
        draft = paste.parse(crowded)
        assert any("prices appear on that page" in note for note in draft.problems)
        assert len(draft.get("price").alternatives) > 5

    def test_no_price_is_reported_rather_than_defaulted(self):
        text = PRODUCT_PAGE.replace("$89.99", "").replace("$129.99", "")
        text = text.replace("$85.49", "").replace("$79.00", "")
        draft = paste.parse(text)
        assert draft.get("price") is None
        assert any("No price was found" in note for note in draft.problems)


class TestAPriceWhoseDecimalPointDidNotSurviveTheCopy:
    """Walmart prints the cents as superscript, so a copy loses the point.

    Every row here is real, off the clearance search that found this. The money
    pattern needed a decimal point, so it skipped "Now$699" and matched the
    struck-through "$7.99" beside it. Silent, consistent, and wrong in the
    direction that matters: the rows rendered this way are the discounted ones,
    which are the rows worth buying.
    """

    ROWS = (
        ("Now$699~~$7.99~~+$9.99 shipping", "6.99", "7.99"),
        ("Now$1500~~$25.00~~", "15.00", "25.00"),
        ("Now$887~~$10.08~~+$7.99 shipping", "8.87", "10.08"),
        ("Now$1259~~$21.39~~+$8.50 shipping", "12.59", "21.39"),
        ("Now$499~~$6.99~~+$4.99 shipping", "4.99", "6.99"),
    )

    def test_the_now_price_is_read_and_not_the_one_it_replaced(self):
        for line, now, was in self.ROWS:
            found = [str(value) for value, _ in paste._price_candidates([line], compact=True)]
            assert found[0] == now, f"{line} was read as {found}"
            assert was in found, "the was-price is worth keeping as an alternative"

    def test_a_price_with_no_discount_is_read_the_same_way(self):
        """The whole page renders that way, not only the reduced rows."""
        found = paste._price_candidates(["$2400"], compact=True)
        assert str(found[0][0]) == "24.00"

    def test_the_postage_is_not_a_price(self):
        row = ["Now$699~~$7.99~~+$9.99 shipping"]
        found = [str(v) for v, _ in paste._price_candidates(row, compact=True)]
        assert "9.99" not in found

    def test_a_walmart_page_reads_it_without_being_told(self):
        draft = paste.parse(
            "Orthopedic Slip on Walking Shoes for Women\nNow$699~~$7.99~~\n"
            "Walmart # 17244314394\n",
            marketplace=Marketplace.WALMART,
        )
        assert draft.get("price").value == "6.99"

    def test_the_now_widget_is_enough_on_its_own(self):
        """No marketplace given, and the page still says how it renders."""
        draft = paste.parse(
            "Orthopedic Slip on Walking Shoes for Women\nNow$699~~$7.99~~\n"
            "https://www.walmart.com/ip/Willtoo-Orthopedic/17244314394\n"
        )
        assert draft.get("price").value == "6.99"

    def test_a_figure_that_is_not_rendered_that_way_is_left_alone(self):
        """An Amazon price filter reads $2 to $160, not $1.60.

        The same class of error in the other direction, so the reading only
        fires where the page shows evidence of the rendering.
        """
        assert paste._price_candidates(["Price $2-$160+"], compact=False) == []
        draft = paste.parse(PRODUCT_PAGE)
        assert draft.get("price").value == "89.99"

    def test_an_ordinary_decimal_price_is_untouched(self):
        draft = paste.parse(WALMART_PAGE, marketplace=Marketplace.WALMART)
        assert draft.get("price").value == "56.00"


class TestEveryFieldCarriesItsEvidence:
    def test_each_value_names_the_line_it_came_from(self):
        draft = paste.parse(PRODUCT_PAGE)
        for item in draft.fields:
            assert item.evidence, f"{item.name} has no evidence"
            assert item.confidence in {paste.CERTAIN, paste.LIKELY, paste.GUESS}

    def test_the_summary_says_a_parsed_field_is_a_guess(self):
        draft = paste.parse(PRODUCT_PAGE)
        assert "not a statement that you were" in draft.summary


class TestApi:
    def test_a_paste_returns_a_draft_and_writes_nothing(self, client):
        before = client.get(f"{API}/history").json()["price_observations"]

        response = client.post(
            f"{API}/products/analyze/paste", json={"text": PRODUCT_PAGE}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_product_page"] is True
        assert any(item["name"] == "external_id" for item in body["fields"])

        after = client.get(f"{API}/history").json()["price_observations"]
        assert after == before, "reading a paste must not record anything"

    def test_the_home_page_is_refused_through_the_api(self, client):
        body = client.post(
            f"{API}/products/analyze/paste", json={"text": HOME_PAGE}
        ).json()
        assert body["is_product_page"] is False
        assert body["fields"] == []

    def test_the_note_says_the_text_is_not_kept(self, client):
        body = client.post(
            f"{API}/products/analyze/paste", json={"text": PRODUCT_PAGE}
        ).json()
        assert "not stored" in body["note"]

    def test_an_empty_paste_is_a_422(self, client):
        assert (
            client.post(f"{API}/products/analyze/paste", json={"text": ""}).status_code
            == 422
        )

    def test_reading_a_paste_needs_a_session(self, anonymous_client):
        response = anonymous_client.post(
            f"{API}/products/analyze/paste", json={"text": PRODUCT_PAGE}
        )
        assert response.status_code == 401
