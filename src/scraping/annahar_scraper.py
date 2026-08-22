"""An-Nahar (Lebanon) Arabic scraper implementation.

An-Nahar's category pages (/lebanon, /arab-world, ...) do not carry real
article links in server-rendered HTML -- verified by fetching each one
directly and finding zero matches; only "special dossier" links are
present statically, the live feed itself is client-rendered. The site's
Google-News-format sitemap (declared `Allow: /sitemap.xml` in robots.txt)
is a plain, always-fresh, static XML document instead, so this scraper
treats sitemap-news.xml as its one "listing page": BeautifulSoup's
html.parser reads <loc> tags out of it exactly like <a href> tags out of
regular HTML, so the existing BaseScraper.scrape() machinery (fetch,
extract links, fetch each article, parse) needs no changes to use it.
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

# Matches one to three section segments ahead of the numeric article id,
# e.g. /lebanon/340477/<slug> and /international/united-states/340473/<slug>.
ARTICLE_PATH_RE = re.compile(r"^/[a-z-]+(?:/[a-z-]+){0,2}/\d+/")

SITEMAP_URL = "https://www.annahar.com/sitemap/sitemap-news.xml"


class AnNaharScraper(BaseScraper):
    """Scraper for An-Nahar Arabic (annahar.com), fed by its news sitemap.

    TODO(selector-maintenance): if An-Nahar updates layout or sitemap path,
    adjust `SITEMAP_URL` and the selectors inside `parse_article`.
    """

    NAME = "annahar_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="AnNahar",
            base_url="https://www.annahar.com",
            start_urls=app_settings.annahar_seed_urls or [SITEMAP_URL],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        if parsed.netloc and parsed.netloc not in ("www.annahar.com", "annahar.com"):
            return False
        return parsed.path.startswith("/sitemap/")

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        for loc in soup.find_all("loc"):
            url = loc.get_text(strip=True)
            path = urlparse(url).path
            if ARTICLE_PATH_RE.match(path):
                links.add(url)

        return sorted(links)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        # The page carries a visually-hidden duplicate h1 (SEO/accessibility
        # convention) ahead of the real one; prefer the visible title.
        title_node = soup.select_one("h1.title") or soup.select_one("h1")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        subtitle_node = soup.select_one("h2.subtitle") or soup.select_one("h2")
        subtitle = normalize_whitespace(subtitle_node.get_text(" ", strip=True)) if subtitle_node else None

        # Real body copy lives under bodyText/bodyContentMainParent; other
        # <p> tags on the page are nav/newsletter boilerplate.
        body_parts = [
            p.get_text(" ", strip=True)
            for p in soup.select("div.bodyText p, div.bodyContentMainParent p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".author") or soup.select_one(".writerName")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        # itemprop=datePublished is a plain ISO-8601 meta tag here (unlike
        # some other Arabic sites, no Arabic-Indic digit conversion needed).
        published_date = extract_meta_datetime(soup)
        if published_date is None:
            published_date = extract_json_ld_datetime(soup)
        if published_date is None:
            time_node = soup.select_one("time[datetime]")
            published_date = parse_datetime(time_node.get("datetime") if time_node else None)

        tags = [
            normalize_whitespace(t.get_text(" ", strip=True))
            for t in soup.select("a[href*='/tags/']")
            if t.get_text(strip=True)
        ]

        path_parts = [p for p in urlparse(article_url).path.split("/") if p]
        source_section = path_parts[0] if path_parts else None

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
