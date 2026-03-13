"""Al Jazeera Arabic scraper implementation."""

from __future__ import annotations

import re
from urllib.parse import urlparse, urljoin

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

ARTICLE_PATH_RE = re.compile(
    r"^/(news|politics|economy|ebusiness|programs|opinions|culture)/\d{4}/\d{1,2}/\d{1,2}/"
)


class AlJazeeraScraper(BaseScraper):
    """Scraper for Al Jazeera Arabic.

    TODO(selector-maintenance): if Al Jazeera updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="AlJazeeraArabic",
            base_url="https://www.aljazeera.net",
            start_urls=app_settings.aljazeera_seed_urls or ["https://www.aljazeera.net/news"],
            settings=app_settings,
        )
        self.allowed_listing_prefixes = (
            "/news",
            "/politics",
            "/economy",
            "/ebusiness",
            "/where/intl",
            "/where/mideast",
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        path = urlparse(listing_url).path
        return any(path.startswith(prefix) for prefix in self.allowed_listing_prefixes)

    def extract_listing_links(self, listing_url: str, listing_html: str) -> list[str]:
        """Follow only DOM-confirmed pagination controls for Al Jazeera.

        Do not construct numbered URLs blindly; Al Jazeera section pages
        can change routing and often do not expose stable page-number paths.
        """
        soup: BeautifulSoup = self.to_soup(listing_html)
        current_path = urlparse(listing_url).path.rstrip("/")
        links: set[str] = set()

        selectors = [
            "link[rel='next']",
            "a[rel='next']",
            "a[aria-label*='التالي']",
            "a[href^='?page=']",
            "a[href*='?page=']",
            "a[href*='?p=']",
        ]
        for selector in selectors:
            for el in soup.select(selector):
                href = (el.get("href") or "").strip()
                if not href:
                    continue
                candidate = urljoin(listing_url, href)
                parsed = urlparse(candidate)
                if parsed.path.rstrip("/") != current_path:
                    continue
                if "page=" not in parsed.query and "p=" not in parsed.query:
                    continue
                links.add(candidate)

        return sorted(links)

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        # Primary link patterns observed in current layout.
        for selector in [
            "a.u-clickable-card__link",
            "a.gc__title",
            "article a[href*='/news/']",
            "a[href*='/politics/']",
            "a[href*='/economy/']",
        ]:
            for el in soup.select(selector):
                href = (el.get("href") or "").strip()
                if href.startswith("/"):
                    links.add(href)
                elif href.startswith("https://www.aljazeera.net/"):
                    links.add(href.replace("https://www.aljazeera.net", ""))

        # Fallback generic heuristic.
        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            if href.startswith("/") and any(token in href for token in ["/news/", "/politics/", "/economy/", "/ebusiness/"]):
                links.add(href)

        filtered = [href for href in links if ARTICLE_PATH_RE.match(href)]
        return sorted(filtered)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("h1")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""
        subtitle_node = soup.select_one("h2") or soup.select_one("p.article-subtitle")
        subtitle = normalize_whitespace(subtitle_node.get_text(" ", strip=True)) if subtitle_node else None

        # TODO(selector-maintenance): verify body selectors if site structure changes.
        body_parts = [
            p.get_text(" ", strip=True)
            for p in soup.select("main article p, div.wysiwyg p, div.article-content p, article p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one("span.author")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        time_node = soup.select_one("time")
        published_date = parse_datetime(time_node.get("datetime") if time_node else None)
        if published_date is None:
            published_date = extract_meta_datetime(soup)
        if published_date is None:
            published_date = extract_json_ld_datetime(soup)

        tags = [normalize_whitespace(t.get_text(" ", strip=True)) for t in soup.select("a[href*='/tags/']") if t.get_text(strip=True)]

        section_node = soup.select_one("nav a[aria-current='page']") or soup.select_one("a.active")
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
