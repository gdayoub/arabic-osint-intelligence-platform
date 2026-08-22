"""Libya Al-Ahrar Arabic scraper implementation."""

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

# e.g. /2026/08/21/<slug>/ -- matched both before and after canonicalize_url
# strips the trailing slash, so the pattern doesn't require one.
ARTICLE_PATH_RE = re.compile(r"^/\d{4}/\d{2}/\d{2}/.+")


class LibyaAlAhrarScraper(BaseScraper):
    """Scraper for Libya Al-Ahrar (libyaalahrar.tv), a standard WordPress
    site -- unlike several other WordPress-based candidates checked for
    this project (e.g. Echorouk), its article body is genuinely
    server-rendered inside div.entry-content, confirmed by locating real
    body paragraphs there rather than only in meta/JSON-LD tags.

    TODO(selector-maintenance): if the site changes its WordPress theme,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "libyaalahrar_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="LibyaAlAhrar",
            base_url="https://libyaalahrar.tv",
            start_urls=app_settings.libyaalahrar_seed_urls or [
                "https://libyaalahrar.tv/category/libya/",
                "https://libyaalahrar.tv/category/world/",
                "https://libyaalahrar.tv/category/eco/",
            ],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        if parsed.netloc and parsed.netloc not in ("libyaalahrar.tv", "www.libyaalahrar.tv"):
            return False
        return parsed.path.startswith("/category/")

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            path = urlparse(href).path
            if ARTICLE_PATH_RE.match(path):
                links.add(href)

        return sorted(links)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("h1.cs-entry__title")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        body_parts = [
            p.get_text(" ", strip=True)
            for p in soup.select("div.cs-entry__content-wrap div.entry-content p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".author-name")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        published_date = extract_json_ld_datetime(soup)
        if published_date is None:
            published_date = extract_meta_datetime(soup)
        if published_date is None:
            time_node = soup.select_one("time[datetime]")
            published_date = parse_datetime(time_node.get("datetime") if time_node else None)

        tags = [
            normalize_whitespace(t.get_text(" ", strip=True))
            for t in soup.select("div.cs-entry__tags a[href]")
            if t.get_text(strip=True)
        ]

        source_section = None
        category_node = soup.select_one("a[href*='/category/']")
        if category_node:
            source_section = normalize_whitespace(category_node.get_text(" ", strip=True))

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
