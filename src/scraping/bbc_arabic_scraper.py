"""BBC Arabic scraper implementation."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.config.settings import SETTINGS, Settings
from src.scraping.base_scraper import BaseScraper
from src.scraping.scraper_utils import (
    ArticleRecord,
    build_content_hash,
    extract_json_ld_datetime,
    extract_meta_datetime,
    normalize_whitespace,
    parse_datetime,
)

ARTICLE_PATH_RE = re.compile(r"^/arabic/(articles|news|middleeast|business|live)/")


class BBCArabicScraper(BaseScraper):
    """Scraper for BBC Arabic.

    TODO(selector-maintenance): if BBC Arabic updates card/body markup,
    update selectors in this class only.
    """

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="BBCArabic",
            base_url="https://www.bbc.com",
            start_urls=app_settings.bbc_seed_urls or ["https://www.bbc.com/arabic"],
            settings=app_settings,
        )
        self.allowed_listing_prefixes = (
            "/arabic",
            "/arabic/topics/",
            "/arabic/middleeast",
            "/arabic/world",
            "/arabic/business",
            "/arabic/news",
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        path = urlparse(listing_url).path
        return any(path.startswith(prefix) for prefix in self.allowed_listing_prefixes)

    def extract_listing_links(self, listing_url: str, listing_html: str) -> list[str]:
        """Follow BBC DOM-discovered topic and pagination links only."""
        soup: BeautifulSoup = self.to_soup(listing_html)
        current_path = urlparse(listing_url).path.rstrip("/")
        links: set[str] = set()

        # Confirmed pagination style on BBC topic pages: ?page=<n>
        for el in soup.select("a[href^='?page='], a[href*='?page='], a[rel='next']"):
            href = (el.get("href") or "").strip()
            if not href:
                continue
            candidate = urljoin(listing_url, href)
            parsed = urlparse(candidate)
            if parsed.path.rstrip("/") != current_path:
                continue
            if "page=" not in parsed.query:
                continue
            links.add(candidate)

        # Discover additional listing sections/topics from real DOM links.
        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            if not href:
                continue
            candidate = urljoin(listing_url, href)
            parsed = urlparse(candidate)
            path = parsed.path
            if ARTICLE_PATH_RE.match(path):
                continue
            if any(path.startswith(prefix) for prefix in self.allowed_listing_prefixes):
                links.add(candidate)

        return sorted(links)

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        for selector in [
            "a[href*='/arabic/articles/']",
            "a[href*='/arabic/news/']",
            "a[href*='/arabic/middleeast/']",
            "a[href*='/arabic/world/']",
            "a[href*='/arabic/business/']",
        ]:
            for el in soup.select(selector):
                href = (el.get("href") or "").strip()
                if not href:
                    continue
                if href.startswith("http") and "bbc.com" in href:
                    href = href.replace("https://www.bbc.com", "")
                if href.startswith("/"):
                    links.add(href)

        # Fallback sweep to capture additional cards/teasers on section pages.
        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            if not href:
                continue
            if href.startswith("https://www.bbc.com"):
                href = href.replace("https://www.bbc.com", "")
            if href.startswith("/arabic/"):
                links.add(href)

        filtered = [href for href in links if ARTICLE_PATH_RE.match(href)]
        return sorted(filtered)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("h1")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""
        if not title:
            og_title = soup.select_one("meta[property='og:title']")
            title = normalize_whitespace(og_title.get("content")) if og_title else ""

        subtitle_node = soup.select_one("h2") or soup.select_one("p[data-testid='subheadline']")
        subtitle = normalize_whitespace(subtitle_node.get_text(" ", strip=True)) if subtitle_node else None

        # TODO(selector-maintenance): BBC frequently changes container classes.
        body_parts = [
            p.get_text(" ", strip=True)
            for p in soup.select("article p, main p, div[data-component='text-block'] p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[data-testid='byline-new-contributors']") or soup.select_one("span.ssrcss-1u0n4j8-Contributor")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        time_node = soup.select_one("time")
        published_date = parse_datetime(time_node.get("datetime") if time_node else None)
        if published_date is None:
            published_date = extract_meta_datetime(soup)
        if published_date is None:
            published_date = extract_json_ld_datetime(soup)

        tags = [normalize_whitespace(t.get_text(" ", strip=True)) for t in soup.select("a[href*='/topics/']") if t.get_text(strip=True)]

        section_node = soup.select_one("a[aria-current='page']")
        source_section = normalize_whitespace(section_node.get_text(" ", strip=True)) if section_node else None

        if not title or not body:
            return None

        return ArticleRecord(
            source=self.source_name,
            title=title,
            subtitle=subtitle,
            body=body,
            author=author,
            published_date=published_date,
            url=article_url,
            tags=tags,
            source_section=source_section,
            content_hash=build_content_hash(title, body, article_url),
        )
