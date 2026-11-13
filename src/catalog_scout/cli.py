import argparse
import logging
import math
from pathlib import Path
from typing import Sequence

import requests
from playwright.sync_api import Error as BrowserError

from .export import check_output, export_products
from .http import USER_AGENT, CrawlError, PublicHttpClient
from .sandbox import ORIGIN, discover_products, parse_product

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


def main(argv: Sequence[str] | None = None, *, origin: str = ORIGIN) -> int:
    parser = argparse.ArgumentParser(description="Export products from Agenty's Scraping Sandbox.")
    parser.add_argument("--category", help='Exact storefront category label, e.g. "Electronics"')
    parser.add_argument("--limit", type=positive_int, help="Collect up to this many products")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/products.csv"),
        help="Destination .csv or .xlsx file (default: output/products.csv)",
    )
    parser.add_argument(
        "--delay",
        type=positive_seconds,
        default=0.5,
        help="Minimum interval in seconds between sequential HTTP requests (default: 0.5)",
    )
    parser.add_argument(
        "--timeout",
        type=positive_seconds,
        default=20,
        help="Timeout in seconds for HTTP and browser operations (default: 20)",
    )
    args = parser.parse_args(argv)
    if args.delay < 0.5:
        parser.error("--delay must be at least 0.5 seconds")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        check_output(args.output)
        with requests.Session() as session:
            client = PublicHttpClient(session, args.delay, args.timeout)
            robots = client.load_robots(origin)
            urls = discover_products(client, robots, origin, args.category, args.limit)

            # Validate all destinations before the first product request.
            for url in urls:
                if not robots.can_fetch(USER_AGENT, url):
                    raise CrawlError(f"robots.txt disallows {url}")

            products = []
            for index, url in enumerate(urls, start=1):
                product = parse_product(client.get(url).content, url)
                if args.category not in (None, "All Categories", product.category):
                    raise CrawlError(f"Unexpected category {product.category!r} on {url}")
                products.append(product)
                if index % 50 == 0 or index == len(urls):
                    logger.info("Validated %d/%d products", index, len(urls))

        export_products(args.output, products)
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

    logger.info("Wrote %d products to %s", len(products), args.output)
    return 0
