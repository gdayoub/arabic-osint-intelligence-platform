"""Al Arabiya Arabic scraper implementation."""

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
    r"^/(arab-and-world|politics|economy|science-and-technology|sports|art-and-culture)/\d{4}/\d{1,2}/\d{1,2}/"
)


class AlArabiyaScraper(BaseScraper):
    """Scraper for Al Arabiya Arabic (alarabiya.net).

    TODO(selector-maintenance): if Al Arabiya updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="AlArabiya",
            base_url="https://www.alarabiya.net",
            start_urls=app_settings.alarabiya_seed_urls or [
                "https://www.alarabiya.net/arab-and-world",
                "https://www.alarabiya.net/politics",
                "https://www.alarabiya.net/economy",
            ],
            settings=app_settings,
        )
        self.allowed_listing_prefixes = (
            "/arab-and-world",
            "/politics",
            "/economy",
            "/science-and-technology",
            "/sports",
            "/art-and-culture",
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        path = urlparse(listing_url).path
        return any(path.startswith(prefix) for prefix in self.allowed_listing_prefixes)

    def extract_listing_links(self, listing_url: str, listing_html: str) -> list[str]:
        """Follow DOM-confirmed pagination controls for Al Arabiya."""
        soup: BeautifulSoup = self.to_soup(listing_html)
        current_path = urlparse(listing_url).path.rstrip("/")
        links: set[str] = set()

        selectors = [
            "link[rel='next']",
            "a[rel='next']",
            "a[aria-label*='التالي']",
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

        # Primary link patterns for Al Arabiya layout.
        for selector in [
            "a[href*='/arab-and-world/']",
            "a[href*='/politics/']",
            "a[href*='/economy/']",
            "a[href*='/science-and-technology/']",
            "article a[href]",
            "h2 a[href]",
            "h3 a[href]",
        ]:
            for el in soup.select(selector):
                href = (el.get("href") or "").strip()
                if href.startswith("/"):
                    links.add(href)
                elif "alarabiya.net" in href:
                    path = urlparse(href).path
                    if path:
                        links.add(path)

        # Fallback generic heuristic.
        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            if href.startswith("/") and any(
                token in href
                for token in ["/arab-and-world/", "/politics/", "/economy/"]
            ):
                links.add(href)

        filtered = [href for href in links if ARTICLE_PATH_RE.match(href)]
        return sorted(filtered)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("h1")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        subtitle_node = soup.select_one("h2") or soup.select_one("p.article-subtitle")
        subtitle = normalize_whitespace(subtitle_node.get_text(" ", strip=True)) if subtitle_node else None

        # Body selectors for Al Arabiya article layout.
        body_parts = [
            p.get_text(" ", strip=True)
            for p in soup.select(
                "div.article-body p, div.wysiwyg p, article p, .story-body p, main p"
            )
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".author") or soup.select_one(".byline")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        time_node = soup.select_one("time[datetime]") or soup.select_one("time")
        published_date = parse_datetime(time_node.get("datetime") if time_node else None)
        if published_date is None:
            published_date = extract_meta_datetime(soup)
        if published_date is None:
            published_date = extract_json_ld_datetime(soup)

        tags = [
            normalize_whitespace(t.get_text(" ", strip=True))
            for t in soup.select("a[href*='/tags/'], a[href*='/tag/']")
            if t.get_text(strip=True)
        ]

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
