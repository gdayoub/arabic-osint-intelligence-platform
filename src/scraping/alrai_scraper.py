"""Al Rai (Kuwait) Arabic scraper implementation."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

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

# e.g. /article/1776759/اقتصاد/بلومبيرغ-... -- some articles have a
# category AND a subcategory segment before the slug, so require one or
# more trailing segments rather than exactly two.
ARTICLE_PATH_RE = re.compile(r"^/article/\d+(?:/[^/]+){2,}$")


class AlRaiScraper(BaseScraper):
    """Scraper for Al Rai (www.alraimedia.com).

    TODO(selector-maintenance): if the site updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "alrai_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="AlRai",
            base_url="https://www.alraimedia.com",
            start_urls=app_settings.alrai_seed_urls or [
                "https://www.alraimedia.com",
            ],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        path = unquote(urlparse(article_url).path)
        return bool(ARTICLE_PATH_RE.match(path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        return not parsed.netloc or parsed.netloc in ("www.alraimedia.com", "alraimedia.com")

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            path = unquote(urlparse(href).path if href.startswith("http") else href)
            if ARTICLE_PATH_RE.match(path):
                links.add(href)

        return sorted(links)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("h1.article-title")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        body_parts = [
            p.get_text(" ", strip=True) for p in soup.select("div.article-desc p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".author-name")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        published_date = extract_meta_datetime(soup)
        if published_date is None:
            published_date = extract_json_ld_datetime(soup)

        tags = [
            normalize_whitespace(t.get_text(" ", strip=True))
            for t in soup.select("a[href*='/tags/']")
            if t.get_text(strip=True)
        ]

        path_parts = [p for p in unquote(urlparse(article_url).path).split("/") if p]
        source_section = path_parts[2] if len(path_parts) > 2 else None

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
