# Catalog Scout

Catalog Scout is a Python portfolio project that exports one validated row per product from two storefronts. The Adafruit adapter reads a live electronics catalog from HTML. The Scraping Sandbox adapter follows JavaScript pagination in Chromium.

## Setup

With Python 3.11 or newer, run these commands from the repository root.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

In Windows PowerShell, use `py -m venv .venv` and `.\.venv\Scripts\Activate.ps1` for the first two commands.

## Live catalog · Adafruit

The November 14, 2026 run collected **131 unique products** from Raspberry Pi Accessories, the default category. For a different category, pass the number from its `/category/<id>` URL. [Adafruit category 166](https://www.adafruit.com/category/166)

```bash
catalog-scout --source adafruit --output output/adafruit.csv
```

```bash
python -m pip install '.[excel]'
catalog-scout --source adafruit --category-id 371 --output output/adafruit-pi5.xlsx
```

The scraper checks `robots.txt`, then reads the requested listing page. It extracts Schema.org product IDs, currency and stock status, checks visible prices against the structured offer and reads comparison prices from the crossed-out value. One listing request supplies all rows for the tested category. The default interval between requests is 12 seconds. [Adafruit robots.txt](https://www.adafruit.com/robots.txt)

Ratings and review counts are blank because the adapter reads the category page. The price is also blank when the listing hides it, even if a price is present in the markup. Image URLs are recorded without fetching the images. The live export is generated locally under `output/`.

## JavaScript pagination · Scraping Sandbox

The November 14, 2026 run collected **500 unique products across 21 pages** and 15 categories. [Browse the CSV snapshot](examples/products.csv) · [Inspect a source product](https://scrapingsandbox.com/product/1)

```bash
python -m playwright install chromium
catalog-scout --category Electronics --limit 10 --output output/electronics.csv
```

On Linux, use `python -m playwright install --with-deps chromium` if browser system libraries are missing.

```bash
# Every page in one category
catalog-scout --category Electronics --output output/electronics.csv

# Every category and page. Allow several minutes at the default request interval.
catalog-scout --output output/products.csv

# Excel output with numeric cells, filters and a frozen header
python -m pip install '.[excel]'
catalog-scout --category Electronics --output output/electronics.xlsx
```

Use `catalog-scout --help` for all options. For Sandbox, category names match the storefront labels exactly. The destination extension selects the export format for either source.

## Data schema

| Columns | Meaning |
| --- | --- |
| `product_id`, `sku` | Storefront identifiers. Each product ID appears once. |
| `name`, `category` | Normalized product title and category. |
| `price`, `original_price`, `currency` | Decimal amounts in USD. A price is blank when it is unavailable. |
| `rating`, `review_count` | Rating on a 0–5 scale and the review count, where available. |
| `availability` | Normalized source status, including `in_stock`, `out_of_stock` and `discontinued`. |
| `product_url`, `image_url` | Absolute URLs for the product page and its main image. |

The CSV snapshot contains sample storefront data from Scraping Sandbox. Prices, ratings, stock, and placeholder image URLs are part of the sandbox dataset.

## How the scraper works

1. **Discover.** For Sandbox, Playwright waits for the category control to initialize and follows Next until the last page. After each click it waits for the product links to change. The crawl canonicalizes URLs, detects repeated pages and checks the unique link count against the storefront’s total for a full run.
2. **Extract.** Requests fetches HTML. Beautiful Soup reads the public product JSON from Sandbox detail pages or the product markup from an Adafruit category page. Both adapters validate product IDs, prices and stock status.
3. **Export.** The scraper validates the collected data before replacing the destination. It writes a UTF-8 CSV with a BOM for Excel compatibility or an `.xlsx` file with numeric cells. CSV text beginning with spreadsheet formula characters is escaped.

The HTTP client checks `robots.txt` and extends the request interval when the site specifies a longer one. It retries transient network failures and selected HTTP errors up to twice with backoff and honors `Retry-After` up to 60 seconds. A longer server delay, access denial, or invalid product stops the crawl and preserves an existing export. Sandbox’s browser loads a short burst of listing assets at startup.

Each storefront has its own parser and shares the HTTP client and exporters. [Adafruit adapter](src/catalog_scout/adafruit.py) · [Sandbox adapter](src/catalog_scout/sandbox.py)

## Tests

```bash
python -m pip install '.[excel]'
python -m playwright install chromium
python -m unittest discover -s tests -v
```

The integration tests use local storefront fixtures for JavaScript pagination and Adafruit product markup. GitHub Actions runs the same checks on pushes and pull requests.
