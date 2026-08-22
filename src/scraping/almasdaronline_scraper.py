"""Al Masdar Online (Yemen) Arabic scraper implementation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
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

# e.g. /articles/337987
ARTICLE_PATH_RE = re.compile(r"^/articles/\d+$")

# The site's <time datetime="..."> attribute holds Arabic prose like
# "24 فبراير 2026" instead of an ISO string -- no numeric date exists
# anywhere else on the page, so this table is required, not optional.
ARABIC_MONTHS = {
    "يناير": 1,
    "فبراير": 2,
    "مارس": 3,
    "أبريل": 4,
    "ابريل": 4,
    "مايو": 5,
    "يونيو": 6,
    "يوليو": 7,
    "أغسطس": 8,
    "اغسطس": 8,
    "سبتمبر": 9,
    "أكتوبر": 10,
    "اكتوبر": 10,
    "نوفمبر": 11,
    "ديسمبر": 12,
}

ARABIC_DATE_RE = re.compile(r"(\d{1,2})\s+([؀-ۿ]+)\s+(\d{4})")


def _parse_arabic_date(text: str | None) -> datetime | None:
    if not text:
        return None
    match = ARABIC_DATE_RE.search(text)
    if not match:
        return None
    day, month_name, year = match.groups()
    month = ARABIC_MONTHS.get(month_name)
    if month is None:
        return None
    try:
        return datetime(int(year), month, int(day), tzinfo=timezone.utc)
    except ValueError:
        return None


class AlMasdarOnlineScraper(BaseScraper):
    """Scraper for Al Masdar Online (almasdaronline.com).

    TODO(selector-maintenance): if the site updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "almasdaronline_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="AlMasdarOnline",
            base_url="https://almasdaronline.com",
            start_urls=app_settings.almasdaronline_seed_urls or [
                "https://almasdaronline.com",
            ],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        return not parsed.netloc or parsed.netloc in ("almasdaronline.com", "www.almasdaronline.com")

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

        title_node = soup.select_one("h1#subject-title") or soup.select_one("h1[itemprop='name']")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        body_parts = [
            p.get_text(" ", strip=True)
            for p in soup.select("div.page_content[itemprop='articleBody'] p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".author-name")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        published_date = extract_json_ld_datetime(soup)
        if published_date is None:
            published_date = extract_meta_datetime(soup)
        if published_date is None:
            time_node = soup.select_one("time[datetime]")
            date_text = time_node.get("datetime") if time_node else None
            published_date = _parse_arabic_date(date_text)

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
