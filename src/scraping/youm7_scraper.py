"""Youm7 (Egypt) Arabic scraper implementation."""

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

# e.g. /story/2026/8/21/<slug>/7520021
ARTICLE_PATH_RE = re.compile(r"^/story/\d{4}/\d{1,2}/\d{1,2}/.+/\d+$")


class Youm7Scraper(BaseScraper):
    """Scraper for Youm7 Arabic (youm7.com).

    TODO(selector-maintenance): if Youm7 updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "youm7_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="Youm7",
            base_url="https://www.youm7.com",
            start_urls=app_settings.youm7_seed_urls or [
                "https://www.youm7.com/Section/أخبار-عاجلة/65/1",
                "https://www.youm7.com/Section/أخبار-عالمية/286/1",
                "https://www.youm7.com/Section/أخبار-عربية/88/1",
                "https://www.youm7.com/Section/اقتصاد-وبورصة/297/1",
            ],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        if parsed.netloc and parsed.netloc not in ("www.youm7.com", "youm7.com"):
            return False
        return parsed.path.startswith("/Section/") or parsed.path == "/"

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            if href.startswith("/story/"):
                links.add(href)

        return sorted(links)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        # Two plain <h1> tags exist on the page: the real title (first, in
        # the article header) and the site-name logo (later, in the
        # footer). Document order puts the real title first.
        title_node = soup.select_one("h1")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        subtitle_node = soup.select_one("h2")
        subtitle = normalize_whitespace(subtitle_node.get_text(" ", strip=True)) if subtitle_node else None

        body_parts = [
            p.get_text(" ", strip=True) for p in soup.select("div.articleCont p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".writerName") or soup.select_one(".author")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        published_date = extract_meta_datetime(soup)
        if published_date is None:
            published_date = extract_json_ld_datetime(soup)
        if published_date is None:
            time_node = soup.select_one("time[datetime]")
            published_date = parse_datetime(time_node.get("datetime") if time_node else None)

        tags = [
            normalize_whitespace(t.get_text(" ", strip=True))
            for t in soup.select("a[href*='/Tags/']")
            if t.get_text(strip=True)
        ]

        source_section = None
        breadcrumb = soup.select_one(".breadcumb a[href*='/Section/']")
        if breadcrumb:
            source_section = normalize_whitespace(breadcrumb.get_text(" ", strip=True))

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
