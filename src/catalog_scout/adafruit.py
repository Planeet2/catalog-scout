import logging
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from .http import USER_AGENT, CrawlError, PublicHttpClient
from .product import Product

ORIGIN = "https://www.adafruit.com"
DEFAULT_CATEGORY = 166
REQUEST_DELAY = 12.0
AVAILABILITY = {
    "InStock": "in_stock",
    "OutOfStock": "out_of_stock",
    "Discontinued": "discontinued",
    "BackOrder": "backorder",
    "PreOrder": "preorder",
    "PreSale": "presale",
    "LimitedAvailability": "limited_availability",
    "SoldOut": "out_of_stock",
}
logger = logging.getLogger(__name__)


def _required_attribute(parent: Tag, selector: str, attribute: str) -> str:
    element = parent.select_one(selector)
    value = element.get(attribute) if element else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing {attribute} on {selector}")
    return value.strip()


def _money(raw: str) -> Decimal:
    value = Decimal(raw)
    if not value.is_finite() or value < 0:
        raise ValueError(f"invalid USD price {raw!r}")
    rounded = value.quantize(Decimal("0.01"))
    if value != rounded:
        raise ValueError(f"USD price has more than two decimal places: {raw!r}")
    return rounded


def _display_price(element: Tag) -> Decimal:
    label = "".join(element.stripped_strings)
    match = re.fullmatch(r"\$(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})", label)
    if match is None:
        raise ValueError(f"invalid displayed USD price {label!r}")
    return _money(f"{match[1].replace(',', '')}.{match[2]}")


def _parse_card(card: Tag, category: str, origin: str) -> Product:
    sku_tag = card.select_one('[itemprop="sku"]')
    sku = sku_tag.get_text(strip=True) if sku_tag else ""
    if not re.fullmatch(r"[1-9][0-9]*", sku):
        raise ValueError("missing or invalid product ID")
    name_tag = card.select_one('[itemprop="name"]')
    name = " ".join(name_tag.get_text(" ", strip=True).split()) if name_tag else ""
    if not name:
        raise ValueError(f"product {sku} has no name")
    url = urljoin(origin, _required_attribute(card, '[itemprop="url"]', "content"))
    if url != f"{origin}/product/{sku}":
        raise ValueError(f"product {sku} has an unexpected URL: {url}")
    image_url = urljoin(origin, _required_attribute(card, "img.productThumb", "src"))
    image = urlsplit(image_url)
    if image.scheme != "https" or not image.hostname:
        raise ValueError(f"product {sku} has an invalid image URL")

    offer = card.select_one('[itemprop="offers"]')
    if offer is None:
        raise ValueError(f"product {sku} has no offer")
    currency = _required_attribute(offer, '[itemprop="priceCurrency"]', "content")
    if currency != "USD":
        raise ValueError(f"product {sku} has unexpected currency {currency!r}")
    status = _required_attribute(offer, '[itemprop="availability"]', "content").rsplit("/", 1)[-1]
    if status not in AVAILABILITY:
        raise ValueError(f"product {sku} has unrecognized availability {status!r}")
    visible_price = offer.select_one(".red-sale-price, .normal-price:not(.strikethrough)")
    price = None
    if visible_price is not None:
        price = _display_price(visible_price)
        offer_price = _required_attribute(offer, '[itemprop="price"]', "content")
        if price != _money(offer_price):
            raise ValueError(f"product {sku} has conflicting displayed and structured prices")

    crossed_out = offer.select_one(".normal-price.strikethrough")
    original_price = _display_price(crossed_out) if crossed_out else None
    if original_price is not None and (price is None or original_price <= price):
        raise ValueError(f"product {sku} has an invalid comparison price")

    return Product(
        product_id=int(sku),
        sku=sku,
        name=name,
        category=category,
        price=price,
        original_price=original_price,
        currency=currency,
        rating=None,
        review_count=None,
        availability=AVAILABILITY[status],
        product_url=url,
        image_url=image_url,
    )


def collect_products(
    client: PublicHttpClient,
    origin: str,
    category_id: int,
    limit: int | None,
) -> list[Product]:
    url = f"{origin}/category/{category_id}"
    robots = client.load_robots(origin)
    if not robots.can_fetch(USER_AGENT, url):
        raise CrawlError(f"robots.txt disallows {url}")

    html = client.get(url).content
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    heading = soup.select_one("#productListHeading")
    category = " ".join(heading.get_text(" ", strip=True).split()) if heading else ""
    cards = soup.select('ul.productListing > li[itemtype$="/Product"]')
    if not category or not cards:
        raise CrawlError(f"No product listing found at {url}")

    products: dict[int, Product] = {}
    for index, card in enumerate(cards, start=1):
        try:
            product = _parse_card(card, category, origin)
        except (ValueError, InvalidOperation) as exc:
            raise CrawlError(f"Invalid product card {index} on {url}: {exc}") from exc
        previous = products.setdefault(product.product_id, product)
        if previous != product:
            raise CrawlError(f"Conflicting rows for product {product.product_id} on {url}")

    result = list(products.values())[:limit]
    logger.info("Adafruit category %s · %d unique products · exporting %d", category, len(products), len(result))
    return result
