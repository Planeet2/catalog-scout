import csv
import html
import json
import threading
import unittest
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from openpyxl import load_workbook

from catalog_scout.cli import main

CATALOG = b"""<!doctype html>
<main>
  <button role="combobox"></button><div id="options"></div>
  <p id="total"></p><section id="products"></section>
  <button id="next" aria-label="Next"><svg class="lucide-chevron-right"></svg></button>
</main>
<script src="/_next/static/catalog.js" defer></script>
"""

CATALOG_JS = b"""
const control = document.querySelector('[role=combobox]');
const options = document.querySelector('#options');
const next = document.querySelector('#next');
function render(ids, total, lastPage) {
  document.querySelector('#total').textContent = `${total} results found`;
  document.querySelector('#products').innerHTML = ids.map(id =>
    `<a class="product-card" href="/product/${id}?source=test#card">Product ${id}</a>`
  ).join('');
  next.disabled = lastPage;
}
control.textContent = 'All Categories';
control.onclick = () => {
  options.innerHTML = '<button role="option">Electronics</button>';
  options.firstElementChild.onclick = () => {
    options.innerHTML = '';
    render([1, 3], 2, true);
    control.textContent = 'Electronics';
  };
};
next.onclick = () => setTimeout(() => render([2, 3], 3, true), 75);
render([1, 2], 3, false);
"""


class Storefront(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        self.server.hits[path] += 1
        if path == "/robots.txt":
            self.reply(200, self.server.robots, "text/plain")
        elif path == "/":
            self.reply(200, CATALOG, "text/html")
        elif path == "/_next/static/catalog.js":
            self.reply(200, CATALOG_JS, "text/javascript")
        elif path in {"/product/1", "/product/2", "/product/3"}:
            self.product(int(path[-1]), self.server.hits[path])
        else:
            self.reply(404, b"", "text/plain")

    def product(self, product_id, visits):
        if product_id == self.server.retry_product and visits == 1:
            self.send_response(429)
            self.send_header("Retry-After", "1")
            self.end_headers()
            return
        data = {
            "id": product_id,
            "title": '=HYPERLINK("https://example.test")' if product_id == 1 else f"Device {product_id}",
            "sku": f"SKU-{product_id}",
            "category": "Books" if product_id == 2 else "Electronics",
            "price": 19.95,
            "compareAtPrice": 25 if product_id == 1 else None,
            "rating": 8 if product_id == self.server.bad_product else 4.5,
            "reviewCount": 0,
            "inStock": product_id != 2,
            "image": f"/images/{product_id}.png",
        }
        markup = f"<main><pre>{html.escape(json.dumps(data))}</pre></main>"
        self.reply(200, markup.encode(), "text/html")

    def reply(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Storefront)
        self.server.hits = Counter()
        self.server.robots = b"User-agent: *\nAllow: /\n"
        self.server.bad_product = None
        self.server.retry_product = None
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def run_cli(self, path, *args):
        return main(["--output", str(path), "--timeout", "10", *args], origin=self.origin)

    def test_javascript_pagination_retry_and_csv(self):
        self.server.retry_product = 2
        path = self.folder / "products.csv"
        self.assertEqual(self.run_cli(path), 0)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row["product_id"] for row in rows], ["1", "2", "3"])
        self.assertEqual(rows[0]["original_price"], "25.00")
        self.assertEqual(rows[1]["original_price"], "")
        self.assertEqual(rows[1]["availability"], "out_of_stock")
        self.assertEqual(rows[0]["review_count"], "0")
        self.assertEqual(rows[0]["product_url"], f"{self.origin}/product/1")
        self.assertEqual(rows[0]["image_url"], f"{self.origin}/images/1.png")
        self.assertTrue(rows[0]["name"].startswith("'="))
        self.assertEqual(self.server.hits["/product/2"], 2)

    def test_category_limit_and_typed_excel(self):
        path = self.folder / "electronics.xlsx"
        self.assertEqual(self.run_cli(path, "--category", "Electronics", "--limit", "1"), 0)
        with path.open("rb") as stream:
            workbook = load_workbook(stream)
            self.addCleanup(workbook.close)
            sheet = workbook["Products"]
            self.assertEqual(sheet.max_row, 2)
            self.assertEqual(sheet["D2"].value, "Electronics")
            self.assertEqual(sheet["E2"].value, 19.95)
            self.assertEqual(sheet["I2"].value, 0)
            self.assertEqual(sheet["C2"].data_type, "s")
            self.assertTrue(sheet["C2"].value.startswith("="))
            self.assertEqual(sheet.freeze_panes, "C2")
            self.assertEqual(sheet.auto_filter.ref, "A1:L2")
        self.assertEqual(self.server.hits["/product/2"], 0)
        self.assertEqual(self.server.hits["/product/3"], 0)

    def test_invalid_product_preserves_existing_export(self):
        self.server.bad_product = 2
        path = self.folder / "products.csv"
        path.write_text("previous export\n")
        self.assertEqual(self.run_cli(path), 1)
        self.assertEqual(path.read_text(), "previous export\n")

    def test_robots_blocks_product_requests_before_export(self):
        self.server.robots = b"User-agent: *\nDisallow: /product/2\n"
        path = self.folder / "products.csv"
        self.assertEqual(self.run_cli(path), 1)
        self.assertEqual(self.server.hits["/product/1"], 0)
        self.assertEqual(self.server.hits["/product/2"], 0)
        self.assertFalse(path.exists())
