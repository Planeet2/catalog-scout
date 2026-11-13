import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = "CatalogScout/0.1"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER = 60
logger = logging.getLogger(__name__)


class CrawlError(Exception):
    """A crawl could not produce a complete, validated export."""


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0
    value = value.strip()
    try:
        return max(0, int(value))
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
        except (ValueError, OverflowError):
            return 0
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return max(0, (deadline - datetime.now(timezone.utc)).total_seconds())


class PublicHttpClient:
    def __init__(self, session: requests.Session, delay: float, timeout: float):
        self.session = session
        self.session.headers["User-Agent"] = USER_AGENT
        self.session.trust_env = False
        self.delay = delay
        self.timeout = timeout
        self.next_request_at = 0.0

    def get(self, url: str, *, missing_ok: bool = False) -> requests.Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            time.sleep(max(0, self.next_request_at - time.monotonic()))
            self.next_request_at = time.monotonic() + self.delay
            retry_after = 0.0
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=False)
            except requests.exceptions.SSLError as exc:
                raise CrawlError(f"TLS error fetching {url}: {exc}") from exc
            except (requests.Timeout, requests.ConnectionError) as exc:
                reason = type(exc).__name__
            else:
                status = response.status_code
                if status == 200 or (missing_ok and status in {404, 410}):
                    return response
                if status not in RETRY_STATUSES:
                    raise CrawlError(f"HTTP {status} from {url}")
                reason = f"HTTP {status}"
                retry_after = retry_after_seconds(response.headers.get("Retry-After"))

            if attempt == MAX_ATTEMPTS:
                raise CrawlError(f"{reason} from {url} after {attempt} attempts")
            if retry_after > MAX_RETRY_AFTER:
                raise CrawlError(f"{reason} from {url}. Server requested a retry in {retry_after:.0f}s")
            wait = max(self.delay, 2 ** (attempt - 1), retry_after)
            self.next_request_at = time.monotonic() + wait
            logger.warning("%s from %s. Retrying in %.1fs", reason, url, wait)

        raise AssertionError("Retry loop exited without returning a response")

    def load_robots(self, origin: str) -> RobotFileParser:
        url = f"{origin}/robots.txt"
        response = self.get(url, missing_ok=True)
        robots = RobotFileParser(url)
        robots.parse(response.text.splitlines() if response.status_code == 200 else [])
        self.delay = max(self.delay, robots.crawl_delay(USER_AGENT) or 0)
        rate = robots.request_rate(USER_AGENT)
        if rate and rate.requests > 0:
            self.delay = max(self.delay, rate.seconds / rate.requests)
        self.next_request_at = max(self.next_request_at, time.monotonic() + self.delay)
        return robots
