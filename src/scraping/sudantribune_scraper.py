"""Sudan Tribune Arabic scraper implementation."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.config.settings import SETTINGS, Settings
from src.scraping.base_scraper import BaseScraper
from src.scraping.scraper_utils import (
    ArticleRecord,
    build_content_hash,
    extract_json_ld_datetime,
    extract_meta_datetime,
    normalize_whitespace,
)

# e.g. /article/317716
ARTICLE_PATH_RE = re.compile(r"^/article/\d+$")


class SudanTribuneScraper(BaseScraper):
    """Scraper for Sudan Tribune (sudantribune.net).

    Note: sudantribune.com is bot-protected; .net is the live, scrapable
    mirror (see verification notes written after live testing).

    TODO(selector-maintenance): if the site updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "sudantribune_scraper"
    VERSION = "1.0.0"

    # robots.txt declares Crawl-delay: 5 for this host.
    MIN_DELAY_SECONDS = 5.0

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="SudanTribune",
            base_url="https://sudantribune.net",
            start_urls=app_settings.sudantribune_seed_urls or [
                "https://sudantribune.net",
            ],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        return not parsed.netloc or parsed.netloc in ("sudantribune.net", "www.sudantribune.net")

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            path = urlparse(href).path if href.startswith("http") else href
            if ARTICLE_PATH_RE.match(path):
                links.add(href)

        return sorted(links)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("h1.single-post__entry-title")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        body_parts = [
            p.get_text(" ", strip=True) for p in soup.select("div.entry__article p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".author-name")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        published_date = extract_json_ld_datetime(soup)
        if published_date is None:
            published_date = extract_meta_datetime(soup)

        tags = [
            normalize_whitespace(t.get_text(" ", strip=True))
            for t in soup.select("a[rel='tag']")
            if t.get_text(strip=True)
        ]

        path_parts = [p for p in urlparse(article_url).path.split("/") if p]
        source_section = path_parts[0] if path_parts else None

        if not title or not body:
            return None

        return ArticleRecord(
            source=self.source_name,
            title=title,
            subtitle=None,
            body=body,
            author=author,
            published_date=published_date,
            url=article_url,
            tags=tags,
            source_section=source_section,
            content_hash=build_content_hash(title, body, article_url),
        )
