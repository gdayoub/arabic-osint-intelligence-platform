"""SANA (Syrian Arab News Agency) Arabic scraper implementation.

SANA is the official Syrian state news agency. It is included for source
diversity (this project otherwise has no state-media perspective at all),
not as an independent source -- flag it accordingly in any Admiralty-code
style source-reliability scoring (see docs M8 in AGENT_BRIEF.md).
"""

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
    parse_datetime,
)

# e.g. /culture-and-arts/2558685/
ARTICLE_PATH_RE = re.compile(r"^/[a-z-]+/\d+")


class SanaScraper(BaseScraper):
    """Scraper for SANA Arabic (sana.sy).

    TODO(selector-maintenance): if SANA updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "sana_scraper"
    VERSION = "1.0.0"

    # robots.txt declares Crawl-delay: 5 for this host.
    MIN_DELAY_SECONDS = 5.0

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="SANA",
            base_url="https://sana.sy",
            start_urls=app_settings.sana_seed_urls or [
                "https://sana.sy/syria-news/",
                "https://sana.sy/international/",
                "https://sana.sy/economy/",
            ],
            settings=app_settings,
        )
        self.allowed_listing_prefixes = ("/syria-news/", "/international/", "/economy/")

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        if parsed.netloc and parsed.netloc not in ("sana.sy", "www.sana.sy"):
            return False
        return any(parsed.path.startswith(prefix) for prefix in self.allowed_listing_prefixes)

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            path = urlparse(href).path if href.startswith("http") else href
            if ARTICLE_PATH_RE.match(path) and path not in self.allowed_listing_prefixes:
                links.add(href)

        return sorted(links)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("h1.s-title")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        body_parts = [
            p.get_text(" ", strip=True) for p in soup.select("div.entry-content p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".author")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        published_date = extract_json_ld_datetime(soup)
        if published_date is None:
            published_date = extract_meta_datetime(soup)
        if published_date is None:
            time_node = soup.select_one("time[datetime]")
            published_date = parse_datetime(time_node.get("datetime") if time_node else None)

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
