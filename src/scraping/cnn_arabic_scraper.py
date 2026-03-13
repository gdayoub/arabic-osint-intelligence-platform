"""CNN Arabic scraper implementation."""

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

ARTICLE_PATH_RE = re.compile(r"^/(middle-east|business|world|science|sport)/article/\d{4}/")


class CNNArabicScraper(BaseScraper):
    """Scraper for CNN Arabic.

    TODO(selector-maintenance): verify selectors for cards/article body when CNN updates layout.
    """

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="CNNArabic",
            base_url="https://arabic.cnn.com",
            start_urls=app_settings.cnn_seed_urls or ["https://arabic.cnn.com/middle-east"],
            settings=app_settings,
        )
        self.allowed_listing_prefixes = (
            "/middle-east",
            "/world",
            "/business",
            "/science-and-health",
            "/sport",
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        path = urlparse(listing_url).path
        return any(path.startswith(prefix) for prefix in self.allowed_listing_prefixes)

    def extract_listing_links(self, listing_url: str, listing_html: str) -> list[str]:
        """CNN Arabic currently exposes section traversal more than explicit pagination.

        If pagination controls appear in DOM (e.g., ?page=), they are followed.
        Otherwise we only follow section-level listing links discovered in page HTML.
        """
        soup: BeautifulSoup = self.to_soup(listing_html)
        current_path = urlparse(listing_url).path.rstrip("/")
        links: set[str] = set()

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
            "a[href^='/']",
            "article a[href]",
            "h3 a[href]",
            "a[href*='/article/']",
        ]:
            for el in soup.select(selector):
                href = (el.get("href") or "").strip()
                if not href:
                    continue
                if href.startswith("http") and "arabic.cnn.com" in href:
                    href = href.replace("https://arabic.cnn.com", "")
                if href.startswith("/") and "/article/" in href:
                    links.add(href)

        # Fallback catch-all for teaser links that include full URLs.
        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            if not href:
                continue
            if href.startswith("https://arabic.cnn.com"):
                href = href.replace("https://arabic.cnn.com", "")
            if href.startswith("/") and "/article/" in href:
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

        subtitle_node = soup.select_one("h2") or soup.select_one("p.subtitle")
        subtitle = normalize_whitespace(subtitle_node.get_text(" ", strip=True)) if subtitle_node else None

        # TODO(selector-maintenance): article paragraph wrappers may vary by vertical.
        body_parts = [
            p.get_text(" ", strip=True)
            for p in soup.select("article p, div.article-body p, div[itemprop='articleBody'] p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one("span.author-name")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        time_node = soup.select_one("time")
        published_date = parse_datetime(time_node.get("datetime") if time_node else None)
        if published_date is None:
            published_date = extract_meta_datetime(soup)
        if published_date is None:
            published_date = extract_json_ld_datetime(soup)

        tags = [normalize_whitespace(t.get_text(" ", strip=True)) for t in soup.select("a[href*='/tags/']") if t.get_text(strip=True)]

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
