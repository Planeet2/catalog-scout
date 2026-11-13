import csv
import unittest
from collections import Counter
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from catalog_scout.cli import main
from test_pipeline import Storefront


def card(product_id, status, visible_prices, offer_price):
    return f"""<li itemscope itemtype="https://schema.org/Product">
      <a itemprop="name" href="/product/{product_id}">Test product {product_id}</a>
      <span itemprop="sku">{product_id}</span>
      <meta itemprop="url" content="/product/{product_id}">
      <img class="productThumb" src="https://images.example.test/{product_id}.png">
      <div itemprop="offers">
        <meta itemprop="priceCurrency" content="USD">
        <meta itemprop="price" content="{offer_price}">
        <meta itemprop="availability" content="https://schema.org/{status}">
        {visible_prices}
      </div>
    </li>"""


def listing():
    discounted = card(1, "InStock", """
      <span class="normal-price strikethrough">$<span>19.95</span></span>
      <span class="red-sale-price">$4.95</span>
    """, "4.9500")
    hidden_price = card(2, "OutOfStock", "", "5.9500")
    return f"""<h1 id="productListHeading">Raspberry Pi / Accessories</h1>
      <ul class="productListing">{discounted}{discounted}{hidden_price}</ul>""".encode()


class AdafruitStorefront(Storefront):
    def do_GET(self):
        if self.path == "/category/166":
            self.server.hits[self.path] += 1
            self.reply(200, listing(), "text/html")
        else:
            super().do_GET()


class AdafruitTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), AdafruitStorefront)
        self.server.hits = Counter()
        self.server.robots = b"User-agent: *\nAllow: /\n"
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name) / "products.csv"

    def run_cli(self, *args):
        return main(
            ["--source", "adafruit", "--delay", "0.5", "--output", str(self.output), *args],
            origin=self.origin,
        )

    def test_listing_exports_visible_prices_and_deduplicates(self):
        self.assertEqual(self.run_cli(), 0)
        with self.output.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row["product_id"] for row in rows], ["1", "2"])
        self.assertEqual(rows[0]["price"], "4.95")
        self.assertEqual(rows[0]["original_price"], "19.95")
        self.assertEqual(rows[0]["rating"], "")
        self.assertEqual(rows[1]["price"], "")
        self.assertEqual(rows[1]["availability"], "out_of_stock")
        self.assertEqual(dict(self.server.hits), {"/robots.txt": 1, "/category/166": 1})

    def test_disallowed_category_preserves_output(self):
        self.server.robots = b"User-agent: *\nDisallow: /category/166\n"
        self.output.write_text("previous export\n")
        self.assertEqual(self.run_cli(), 1)
        self.assertEqual(self.output.read_text(), "previous export\n")
        self.assertEqual(dict(self.server.hits), {"/robots.txt": 1})
