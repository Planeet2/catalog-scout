import argparse
import logging
import math
from pathlib import Path
from typing import Sequence

import requests
from playwright.sync_api import Error as BrowserError

from . import adafruit, sandbox
from .export import check_output, export_products
from .http import USER_AGENT, CrawlError, PublicHttpClient
from .product import Product

logger = logging.getLogger(__name__)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("expected an integer greater than zero")
    return number


def positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("expected a finite number greater than zero")
    return number


def collect_sandbox(
    client: PublicHttpClient,
    origin: str,
    category: str | None,
    limit: int | None,
) -> list[Product]:
    robots = client.load_robots(origin)
    urls = sandbox.discover_products(client, robots, origin, category, limit)
    for url in urls:
        if not robots.can_fetch(USER_AGENT, url):
            raise CrawlError(f"robots.txt disallows {url}")

    products = []
    for index, url in enumerate(urls, start=1):
        product = sandbox.parse_product(client.get(url).content, url)
        if category not in (None, "All Categories", product.category):
            raise CrawlError(f"Unexpected category {product.category!r} on {url}")
        products.append(product)
        if index % 50 == 0 or index == len(urls):
            logger.info("Validated %d/%d products", index, len(urls))
    return products


def main(argv: Sequence[str] | None = None, *, origin: str | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export products from a supported storefront.")
    parser.add_argument(
        "--source",
        choices=("sandbox", "adafruit"),
        default="sandbox",
        help="Storefront to collect (default: sandbox)",
    )
    parser.add_argument("--category", help='Sandbox category label, e.g. "Electronics"')
    parser.add_argument(
        "--category-id", type=positive_int,
        help="Adafruit category ID (default: 166 for Raspberry Pi accessories)",
    )
    parser.add_argument("--limit", type=positive_int, help="Collect up to this many products")
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination .csv or .xlsx file (default: output/products.csv or output/adafruit.csv)",
    )
    parser.add_argument(
        "--delay",
        type=positive_seconds,
        help="Minimum interval in seconds between HTTP requests (0.5 for Sandbox, 12 for Adafruit)",
    )
    parser.add_argument(
        "--timeout",
        type=positive_seconds,
        default=20,
        help="Timeout in seconds for HTTP and browser operations (default: 20)",
    )
    args = parser.parse_args(argv)
    is_adafruit = args.source == "adafruit"
    if is_adafruit and args.category is not None:
        parser.error("use --category-id to select an Adafruit category")
    if not is_adafruit and args.category_id is not None:
        parser.error("--category-id requires --source adafruit")
    default_delay = adafruit.REQUEST_DELAY if is_adafruit else 0.5
    if args.delay is not None and args.delay < 0.5:
        parser.error("--delay must be at least 0.5 seconds")
    output = args.output or Path("output/adafruit.csv" if is_adafruit else "output/products.csv")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        check_output(output)
        with requests.Session() as session:
            client = PublicHttpClient(session, args.delay or default_delay, args.timeout)
            if is_adafruit:
                products = adafruit.collect_products(
                    client,
                    origin or adafruit.ORIGIN,
                    args.category_id or adafruit.DEFAULT_CATEGORY,
                    args.limit,
                )
            else:
                products = collect_sandbox(client, origin or sandbox.ORIGIN, args.category, args.limit)

        export_products(output, products)
    except BrowserError as exc:
        message = str(exc).splitlines()[0]
        if "Executable doesn't exist" in message:
            message = "Chromium is missing. Run python -m playwright install chromium"
        logger.error("Browser: %s", message)
        return 1
    except (CrawlError, OSError, requests.RequestException) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.info("Crawl interrupted")
        return 130

    logger.info("Wrote %d products to %s", len(products), output)
    return 0
