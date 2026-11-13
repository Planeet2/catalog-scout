# Catalog Scout

Catalog Scout is a Python scraper that collects product data from a JavaScript storefront and exports one validated row per product. It is a portfolio project using Agenty’s public [Scraping Sandbox](https://scrapingsandbox.com/).

[**Browse the full CSV snapshot**](examples/products.csv) · [Inspect a source product](https://scrapingsandbox.com/product/1) · [Read the scraper](src/catalog_scout/)

The November 14, 2026 run collected **500 unique products across 21 pages**. The snapshot covers 15 categories and includes 189 products with a comparison price.

| Product | Price (USD) | Original (USD) | Rating | Reviews | Availability |
| --- | ---: | ---: | ---: | ---: | --- |
| Lightweight Probiotics | 155.62 | 206.69 | 4.0 | 52 | In stock |
| Wireless LED Desk Lamp | 145.40 | 210.01 | 4.6 | 12 | Out of stock |
| Deluxe Tea Collection | 185.01 | 313.98 | 3.5 | 211 | In stock |

## Quick start

With Python 3.11 or newer, run these commands from the repository root.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m playwright install chromium
catalog-scout --category Electronics --limit 10 --output output/electronics.csv
```

In Windows PowerShell, use `py -m venv .venv` and `.\.venv\Scripts\Activate.ps1` for the first two commands. On Linux, use `python -m playwright install --with-deps chromium` if browser system libraries are missing.

```bash
# Every page in one category
catalog-scout --category Electronics --output output/electronics.csv

# Every category and page. Allow several minutes at the default request interval.
catalog-scout --output output/products.csv

# Excel output with numeric cells, filters and a frozen header
python -m pip install '.[excel]'
catalog-scout --category Electronics --output output/electronics.xlsx
```

Use `catalog-scout --help` for all options. Category names match the storefront labels exactly. The destination extension selects the export format.

## Data schema

| Columns | Meaning |
| --- | --- |
| `product_id`, `sku` | Storefront identifiers. Each product ID appears once. |
| `name`, `category` | Normalized product title and category. |
| `price`, `original_price`, `currency` | Decimal amounts in USD. The original price is blank when the source shows no higher comparison price. |
| `rating`, `review_count` | Rating on a 0–5 scale and the public review count. |
| `availability` | `in_stock` or `out_of_stock`. |
| `product_url`, `image_url` | Absolute URLs for the product page and its main image. |

The CSV snapshot contains sample storefront data from Scraping Sandbox. Prices, ratings, stock, and placeholder image URLs are part of the sandbox dataset.

## How the scraper works

1. **Discover.** Playwright waits for the category control to initialize, optionally applies a category, then follows the storefront’s Next button. After each click it waits for the product links to change. The crawl canonicalizes product URLs, detects repeated pages and checks the unique link count against the storefront’s total for a full run.
2. **Extract.** Requests fetches product pages sequentially. Beautiful Soup locates the JSON block shown on each public product page. The parser checks the product ID, converts prices to decimals and validates ratings, review counts and stock status. The browser loads only the scripts and styles required by the listing, and product images are recorded as URLs.
3. **Export.** The scraper validates every requested product before replacing the destination file. It writes a UTF-8 CSV with a BOM for Excel compatibility or an `.xlsx` file with numeric price and rating cells. CSV text beginning with spreadsheet formula characters is escaped.

The HTTP client checks `robots.txt`, spaces requests at least 0.5 seconds apart by default and applies a longer interval when the site specifies one. It retries transient network failures and selected HTTP errors up to twice with backoff and honors `Retry-After` up to 60 seconds. A longer server delay, access denial, or invalid product stops the crawl and preserves an existing export. The browser makes a short burst of listing assets at startup.

The included storefront adapter is specific to Scraping Sandbox. For another store, adapt the listing selectors, pagination and product parser. The HTTP client and export code can be reused. [Storefront adapter](src/catalog_scout/sandbox.py)

## Tests

```bash
python -m pip install '.[excel]'
python -m playwright install chromium
python -m unittest discover -s tests -v
```

The integration tests run Chromium against a local storefront with JavaScript pagination. GitHub Actions runs the same checks on pushes and pull requests.
