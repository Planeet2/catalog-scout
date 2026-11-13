import json
import logging
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from playwright.sync_api import Route, sync_playwright

from .http import USER_AGENT, CrawlError, PublicHttpClient
from .product import Product

ORIGIN = "https://scrapingsandbox.com"
CARDS = "main a.product-card[href]"
NEXT = "main button:has(svg.lucide-chevron-right)"
RESULTS = re.compile(r"^([\d,]+)\s+results? found$", re.IGNORECASE)
PRODUCT_PATH = re.compile(r"/product/([1-9]\d*)/?$")
logger = logging.getLogger(__name__)


def product_url(href: str, origin: str) -> str:
    parts = urlsplit(urljoin(f"{origin}/", href))
    path = PRODUCT_PATH.fullmatch(parts.path)
    if f"{parts.scheme}://{parts.netloc}" != origin or path is None:
        raise CrawlError(f"Unexpected product link: {href!r}")
    return f"{origin}/product/{path[1]}"


def discover_products(
    client: PublicHttpClient,
    robots: RobotFileParser,
    origin: str,
    category: str | None,
    limit: int | None,
) -> list[str]:
    home = f"{origin}/"
    if not robots.can_fetch(USER_AGENT, home):
        raise CrawlError(f"robots.txt disallows {home}")
    landing = client.get(home)
    timeout_ms = client.timeout * 1000

    def route_catalog(route: Route) -> None:
        request = route.request
        if request.url == home and request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=landing.content)
        elif request.url.startswith(f"{origin}/_next/static/") and request.resource_type in {
            "script", "stylesheet",
        } and robots.can_fetch(USER_AGENT, request.url):
            route.continue_()
        else:
            route.abort()

    urls: dict[str, None] = {}
    visited_pages: set[tuple[str, ...]] = set()
    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        context = browser.new_context(user_agent=USER_AGENT, service_workers="block")
        context.route("**/*", route_catalog)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(home, wait_until="domcontentloaded")

        # The category label is filled in by JavaScript after the static HTML is loaded.
        category_control = page.get_by_role("combobox").first
        category_control.filter(has_text=re.compile(r"^All Categories$")).wait_for()
        if category:
            category_control.click()
            options = page.get_by_role("option").all_inner_texts()
            if category not in options:
                raise CrawlError(f"Unknown category {category!r}. Choose from: {', '.join(options)}")
            page.get_by_role("option", name=category, exact=True).click()
            category_control.filter(has_text=re.compile(f"^{re.escape(category)}$")).wait_for()

        count_text = page.get_by_text(RESULTS).inner_text()
        match = RESULTS.fullmatch(count_text.strip())
        if match is None:
            raise CrawlError(f"Unrecognized catalog result count: {count_text!r}")
        total = int(match[1].replace(",", ""))
        target = min(limit, total) if limit is not None else total
        logger.info("Catalog reports %d products. Collecting %d", total, target)
        if target == 0:
            raise CrawlError("The selected catalog contains no products")

        cards = page.locator(CARDS)
        next_button = page.locator(NEXT)
        while True:
            hrefs = cards.evaluate_all("links => links.map(link => link.getAttribute('href'))")
            signature = tuple(product_url(href, origin) for href in hrefs)
            if not signature or signature in visited_pages:
                raise CrawlError("Pagination returned an empty or repeated page")
            visited_pages.add(signature)
            urls.update(dict.fromkeys(signature))
            logger.info("Listing page %d · %d unique product links", len(visited_pages), len(urls))
            if limit is not None and limit < total and len(urls) >= target:
                return list(urls)[:target]
            if next_button.count() == 0 or next_button.is_disabled():
                break
            next_button.click()
            page.wait_for_function(
                """({selector, previous}) => {
                    const links = [...document.querySelectorAll(selector)];
                    const current = links.map(link => link.getAttribute('href'));
                    return current.length > 0
                        && JSON.stringify(current) !== JSON.stringify(previous);
                }""",
                arg={"selector": CARDS, "previous": hrefs},
            )

    if len(urls) != target:
        raise CrawlError(
            f"Catalog reports {target} products but pagination yielded {len(urls)} unique links"
        )
    return list(urls)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return " ".join(value.split())


def _decimal(value: object, field: str) -> Decimal:
    if type(value) not in (int, Decimal):
        raise ValueError(f"{field} must be a number")
    result = Decimal(value)
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return result


def parse_product(html: bytes, url: str) -> Product:
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    block = soup.select_one("main pre")
    if block is None:
        raise CrawlError(f"Public product JSON is missing on {url}")

    try:
        data = json.loads(block.get_text(), parse_float=Decimal)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        product_id = data["id"]
        if type(product_id) is not int or str(product_id) != url.rsplit("/", 1)[1]:
            raise ValueError("product ID does not match the URL")
        price = _decimal(data["price"], "price").quantize(Decimal("0.01"))
        original_price = None
        if data["compareAtPrice"] is not None:
            candidate = _decimal(data["compareAtPrice"], "compareAtPrice").quantize(Decimal("0.01"))
            if candidate > price:
                original_price = candidate
        rating = _decimal(data["rating"], "rating")
        if rating > 5:
            raise ValueError("rating is outside 0–5")
        review_count = data["reviewCount"]
        if type(review_count) is not int or review_count < 0:
            raise ValueError("reviewCount must be a non-negative integer")
        in_stock = data["inStock"]
        if type(in_stock) is not bool:
            raise ValueError("inStock must be a boolean")
        image_url = urljoin(url, _text(data["image"], "image"))
        image = urlsplit(image_url)
        if image.scheme not in {"http", "https"} or not image.hostname:
            raise ValueError("image must resolve to an HTTP(S) URL")
        return Product(
            product_id=product_id,
            sku=_text(data["sku"], "sku"),
            name=_text(data["title"], "title"),
            category=_text(data["category"], "category"),
            price=price,
            original_price=original_price,
            currency="USD",
            rating=rating,
            review_count=review_count,
            availability="in_stock" if in_stock else "out_of_stock",
            product_url=url,
            image_url=image_url,
        )
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise CrawlError(f"Invalid product data on {url}: {exc}") from exc
