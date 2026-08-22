"""Donia Al Watan (Palestine) Arabic scraper implementation."""

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

# e.g. /arabic/news/2026/08/18/1553555.html
ARTICLE_PATH_RE = re.compile(r"^/arabic/news/\d{4}/\d{2}/\d{2}/\d+\.html$")

# Listing pages carry no plain <a href> to the article itself -- the only
# occurrences of the real article URL are as a query-string value inside
# Facebook/Twitter share widget links (whose own href points at
# facebook.com/twitter.com, which _is_same_domain would reject). Scan the
# raw HTML for the URL text directly instead of walking anchor tags.
ARTICLE_URL_RE = re.compile(
    r"https://www\.alwatanvoice\.com/arabic/news/\d{4}/\d{2}/\d{2}/\d+\.html"
)


class AlWatanVoiceScraper(BaseScraper):
    """Scraper for Donia Al Watan (www.alwatanvoice.com).

    The body lives in a plain <div id="articleText"> with text nodes
    separated by <br/>, not <p> tags -- get_text() on the container is
    used instead of iterating paragraph elements.

    TODO(selector-maintenance): if the site updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "alwatanvoice_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="AlWatanVoice",
            base_url="https://www.alwatanvoice.com",
            start_urls=app_settings.alwatanvoice_seed_urls or [
                "https://www.alwatanvoice.com/arabic",
            ],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        return not parsed.netloc or parsed.netloc in ("www.alwatanvoice.com", "alwatanvoice.com")

    def extract_article_links(self, listing_html: str) -> list[str]:
        return sorted(set(ARTICLE_URL_RE.findall(listing_html)))

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("h1[itemprop='headline']") or soup.select_one("h1")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        body_node = soup.select_one("div#articleText[itemprop='articleBody']") or soup.select_one(
            "div#articleText"
        )
        body = normalize_whitespace(body_node.get_text(" ", strip=True)) if body_node else ""

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
