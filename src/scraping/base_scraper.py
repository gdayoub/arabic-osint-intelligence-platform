"""Base scraper class with retries, DOM-driven listing traversal, and scrape telemetry."""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config.settings import SETTINGS, Settings
from src.scraping.scraper_utils import ArticleRecord, canonicalize_url


class BaseScraper(ABC):
    """Abstract base class for Arabic source scrapers.

    Scaling strategy:
    - crawl configured section/listing URLs
    - discover additional listing pages from DOM controls (not guessed URLs)
    - dedupe article links before fetching article pages
    """

    def __init__(
        self,
        source_name: str,
        base_url: str,
        start_urls: list[str],
        settings: Settings | None = None,
    ):
        self.source_name = source_name
        self.base_url = base_url
        self.start_urls = start_urls
        self.settings = settings or SETTINGS
        self.min_body_chars = self.settings.min_article_body_chars
        self.logger = logging.getLogger(f"scraper.{source_name}")
        self.session = self._build_session()
        self.last_scrape_stats: dict[str, int] = self._empty_scrape_stats()

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status_forcelist=(429, 500, 502, 503, 504),
            backoff_factor=0.75,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                )
            }
        )
        return session

    def fetch_page(self, url: str) -> tuple[str | None, int | None, str]:
        """Fetch a page and return HTML, status code, and final resolved URL."""
        try:
            response = self.session.get(url, timeout=self.settings.request_timeout_seconds)
            final_url = response.url or url
            status_code = response.status_code
            if status_code >= 400:
                self.logger.warning(
                    "Failed to fetch URL %s | HTTP %s", final_url, status_code
                )
                return None, status_code, final_url

            response.encoding = response.apparent_encoding or response.encoding or "utf-8"
            return response.text, status_code, final_url
        except Exception as exc:
            self.logger.warning("Failed to fetch URL %s | %s", url, exc)
            return None, None, url

    def fetch_html(self, url: str) -> str | None:
        """Backward-compatible HTML fetch helper."""
        html, _, _ = self.fetch_page(url)
        return html

    def _polite_delay(self) -> None:
        base = self.settings.scrape_delay_seconds
        jitter = random.uniform(0.0, 0.35)
        time.sleep(base + jitter)

    def _to_absolute_url(self, link: str) -> str:
        return urljoin(self.base_url, link)

    def _is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc.endswith(urlparse(self.base_url).netloc)

    @abstractmethod
    def extract_article_links(self, listing_html: str) -> list[str]:
        """Parse listing HTML and return article links."""

    @abstractmethod
    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        """Parse a single article page to structured data."""

    def extract_listing_links(self, listing_url: str, listing_html: str) -> list[str]:
        """Extract pagination/listing links discovered from DOM.

        Default behavior only follows explicit pagination controls when present.
        Source scrapers can override for custom listing expansion rules.
        """
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        selectors = [
            "link[rel='next']",
            "a[rel='next']",
            "a[aria-label*='التالي']",
            "a[aria-label*='Next']",
            "a[href^='?page=']",
            "a[href*='?page=']",
        ]
        for selector in selectors:
            for el in soup.select(selector):
                href = (el.get("href") or "").strip()
                if not href:
                    continue
                links.add(self._to_absolute_url(urljoin(listing_url, href)))

        return sorted(links)

    def is_valid_article_url(self, article_url: str) -> bool:
        """Source-specific URL validation hook for article URLs."""
        return True

    def is_valid_listing_url(self, listing_url: str) -> bool:
        """Source-specific URL validation hook for listing traversal URLs."""
        return True

    def get_section_urls(self) -> list[str]:
        """Return section URLs to crawl for listing pages."""
        if not self.start_urls:
            return []
        if self.settings.scrape_sections_enabled:
            return self._dedupe_preserve_order([self._to_absolute_url(u) for u in self.start_urls])
        return [self._to_absolute_url(self.start_urls[0])]

    def scrape(self, limit: int = 15) -> list[ArticleRecord]:
        """Scrape listing pages and then article pages with deduped link set."""
        max_articles = max(1, limit)
        max_listing_pages = max(1, self.settings.max_pages_per_source)

        stats = self._empty_scrape_stats()
        section_urls = self.get_section_urls()
        queue = self._dedupe_preserve_order([
            self._normalize_listing_url(url) for url in section_urls if self.is_valid_listing_url(url)
        ])
        stats["listing_pages_discovered"] = len(queue)
        stats["listing_pages_planned"] = len(queue)

        visited_listing: set[str] = set()
        unique_candidate_links: list[str] = []
        seen_links: set[str] = set()
        branch_404_counts: dict[str, int] = {}
        blocked_branches: set[str] = set()

        while queue and stats["listing_pages_valid_visited"] < max_listing_pages:
            listing_url = queue.pop(0)
            branch_key = urlsplit(listing_url).path.rstrip("/")
            if branch_key in blocked_branches:
                continue
            if listing_url in visited_listing:
                continue
            visited_listing.add(listing_url)

            listing_html, status_code, final_url = self.fetch_page(listing_url)
            final_listing_url = self._normalize_listing_url(final_url)
            if final_listing_url not in visited_listing:
                visited_listing.add(final_listing_url)

            if not listing_html:
                stats["listing_pages_invalid_skipped"] += 1
                stats["listing_pages_failed"] += 1
                if status_code == 404:
                    branch_404_counts[branch_key] = branch_404_counts.get(branch_key, 0) + 1
                    if branch_404_counts[branch_key] >= 2:
                        blocked_branches.add(branch_key)
                        queue[:] = [
                            u for u in queue if urlsplit(u).path.rstrip("/") != branch_key
                        ]
                        self.logger.info(
                            "%s blocking listing branch after repeated 404s path=%s",
                            self.source_name,
                            branch_key or "/",
                        )
                self.logger.info(
                    "%s invalid listing skipped status=%s url=%s",
                    self.source_name,
                    status_code,
                    listing_url,
                )
                continue

            stats["listing_pages_valid_visited"] += 1
            stats["listing_pages_visited"] += 1
            links = self.extract_article_links(listing_html)
            stats["article_links_found"] += len(links)

            for link in links:
                article_url = canonicalize_url(self._to_absolute_url(link))
                if not article_url:
                    continue
                if not self._is_same_domain(article_url):
                    continue
                if not self.is_valid_article_url(article_url):
                    continue
                if article_url in seen_links:
                    continue
                seen_links.add(article_url)
                unique_candidate_links.append(article_url)

            if self.settings.scrape_archives_enabled:
                listing_links = self.extract_listing_links(final_url, listing_html)
                for next_link in listing_links:
                    candidate = self._normalize_listing_url(self._to_absolute_url(next_link))
                    if not candidate:
                        continue
                    if not self._is_same_domain(candidate):
                        continue
                    if not self.is_valid_listing_url(candidate):
                        continue
                    if candidate in visited_listing or candidate in queue:
                        continue
                    queue.append(candidate)
                    stats["listing_pages_discovered"] += 1
                    stats["listing_pages_planned"] += 1

            self._polite_delay()

        stats["unique_article_links"] = len(unique_candidate_links)

        records: list[ArticleRecord] = []
        for article_url in unique_candidate_links:
            if len(records) >= max_articles:
                break

            stats["article_pages_attempted"] += 1
            article_html = self.fetch_html(article_url)
            if not article_html:
                stats["article_pages_failed"] += 1
                continue

            article = self.parse_article(article_url, article_html)
            if not article or not article.body or not article.title:
                continue

            if len(article.body.strip()) < self.min_body_chars:
                stats["short_body_skipped"] += 1
                continue

            records.append(article)
            stats["articles_scraped"] += 1
            self._polite_delay()

        self.last_scrape_stats = stats
        self.logger.info(
            "%s valid_listing_pages=%d invalid_listing_pages=%d links_found=%d unique_links=%d scraped=%d",
            self.source_name,
            stats["listing_pages_valid_visited"],
            stats["listing_pages_invalid_skipped"],
            stats["article_links_found"],
            stats["unique_article_links"],
            stats["articles_scraped"],
        )
        return records

    def get_last_scrape_stats(self) -> dict[str, int]:
        return dict(self.last_scrape_stats)

    @staticmethod
    def to_soup(html_text: str) -> BeautifulSoup:
        return BeautifulSoup(html_text, "html.parser")

    @staticmethod
    def _dedupe_preserve_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                deduped.append(value)
        return deduped

    @staticmethod
    def _normalize_listing_url(url: str) -> str:
        """Normalize listing URL while preserving query params (e.g. ?page=2)."""
        if not url:
            return ""
        split = urlsplit(url.strip())
        path = split.path.rstrip("/") or "/"
        return urlunsplit((split.scheme, split.netloc.lower(), path, split.query, ""))

    @staticmethod
    def _empty_scrape_stats() -> dict[str, int]:
        return {
            "listing_pages_planned": 0,
            "listing_pages_discovered": 0,
            "listing_pages_visited": 0,
            "listing_pages_failed": 0,
            "listing_pages_valid_visited": 0,
            "listing_pages_invalid_skipped": 0,
            "article_links_found": 0,
            "unique_article_links": 0,
            "article_pages_attempted": 0,
            "article_pages_failed": 0,
            "articles_scraped": 0,
            "short_body_skipped": 0,
        }
