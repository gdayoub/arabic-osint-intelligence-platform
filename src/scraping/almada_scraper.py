"""Al Mada (Iraq) Arabic scraper implementation."""

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

# e.g. /449131/ -- trailing slash is optional since extract_article_links
# sees the raw href (with slash) while is_valid_article_url sees the
# canonicalized URL (slash stripped by canonicalize_url()).
ARTICLE_PATH_RE = re.compile(r"^/\d{4,}/?$")


class AlMadaScraper(BaseScraper):
    """Scraper for Al Mada (almadapaper.net).

    TODO(selector-maintenance): if the site updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "almada_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="AlMada",
            base_url="https://almadapaper.net",
            start_urls=app_settings.almada_seed_urls or [
                "https://almadapaper.net",
            ],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        return not parsed.netloc or parsed.netloc in ("almadapaper.net", "www.almadapaper.net")

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

        title_node = soup.select_one("h1#article-main-title") or soup.select_one("h1.ct-headline")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        body_parts = [
            p.get_text(" ", strip=True) for p in soup.select("p.wp-block-paragraph")
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
