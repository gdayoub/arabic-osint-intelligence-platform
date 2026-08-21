"""Regression tests for honest, public-safe source ingestion telemetry."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.config.settings import Settings
from src.ops.events import PipelineEventType, PipelineReasonCode
from src.ops.ledger import abandon_expired_runs
from src.ops.runtime import (
    PipelineStage,
    outcome_from_ingest_stats,
    run_orchestrated_pipeline,
)
from src.pipeline import ingest_core
from src.scraping.base_scraper import BaseScraper
from src.scraping.scraper_utils import ArticleRecord
from src.store.orm import DocumentORM, PipelineEventORM
from src.store.provenance import register_extractor_version

PUBLISHED_AT = datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc)
SECRET_TEXT = "api_key=do-not-return response_body=private"


def _test_settings() -> Settings:
    return Settings(
        scrape_delay_seconds=0,
        max_pages_per_source=1,
        scrape_sections_enabled=True,
        scrape_archives_enabled=False,
        min_article_body_chars=1,
    )


def _article(url: str = "https://example.com/article/healthy") -> ArticleRecord:
    return ArticleRecord(
        source="FixtureSource",
        title="خبر صالح",
        subtitle=None,
        body="نص صالح للاختبار.",
        author=None,
        published_date=PUBLISHED_AT,
        url=url,
    )


class _FixtureScraper(BaseScraper):
    NAME = "fixture_scraper"
    VERSION = "1.0.0"

    def __init__(self, links: list[str]):
        super().__init__(
            source_name="FixtureSource",
            base_url="https://example.com",
            start_urls=["https://example.com/listing"],
            settings=_test_settings(),
        )
        self.links = links

    def _polite_delay(self) -> None:
        return None

    def fetch_page(self, url: str) -> tuple[str | None, int | None, str]:
        if url.endswith("/listing"):
            return "<main>listing fixture</main>", 200, url
        return "<article>article fixture</article>", 200, url

    def extract_article_links(self, listing_html: str) -> list[str]:
        return list(self.links)

    def parse_article(
        self, article_url: str, article_html: str
    ) -> ArticleRecord | None:
        if article_url.endswith("/broken"):
            raise RuntimeError(SECRET_TEXT)
        return _article(article_url)


class _ReturnedArticleScraper(_FixtureScraper):
    """A deterministic scraper result for ingestion-only tests."""

    def __init__(self, article: ArticleRecord):
        super().__init__(links=[])
        self.returned_article = article

    def scrape(self, limit: int = 15) -> list[ArticleRecord]:
        stats = self._empty_scrape_stats()
        stats["listing_pages_attempted"] = 1
        stats["listing_pages_valid_visited"] = 1
        stats["listing_pages_visited"] = 1
        stats["article_pages_attempted"] = 1
        stats["articles_scraped"] = 1
        stats["latest_successful_article_at"] = self.returned_article.published_date
        self.last_scrape_stats = stats
        return [self.returned_article]


class _SecretFailureScraper(_FixtureScraper):
    def __init__(self):
        super().__init__(links=[])

    def scrape(self, limit: int = 15) -> list[ArticleRecord]:
        raise RuntimeError(SECRET_TEXT)


def test_empty_listing_is_visible_as_a_broken_selector():
    scraper = _FixtureScraper(links=[])

    assert scraper.scrape(limit=5) == []

    result = scraper.get_last_scrape_stats()
    assert result["listing_pages_attempted"] == 1
    assert result["listing_pages_without_article_links"] == 1
    assert result["selector_failure_count"] == 1
    assert result["article_pages_attempted"] == 0
    assert result["articles_scraped"] == 0
    assert result["reason_code"] == PipelineReasonCode.SOURCE_SELECTOR_FAILED.value


def test_broken_selector_counts_reach_the_per_source_ingestion_result(
    session, blob_store, monkeypatch
):
    scraper = _FixtureScraper(links=[])
    monkeypatch.setattr(ingest_core, "build_scrapers", lambda: [scraper])
    monkeypatch.setattr(
        ingest_core,
        "get_core_session",
        lambda: nullcontext(session),
    )

    stats = ingest_core.run_core_ingestion(blob_store=blob_store)
    source = stats.sources[scraper.source_name]

    assert source["status"] == "degraded"
    assert source["listing_attempt_count"] == 1
    assert source["listing_zero_link_count"] == 1
    assert source["article_attempt_count"] == 0
    assert source["article_yield_count"] == 0
    assert source["inserted_count"] == 0
    assert source["selector_failure_count"] == 1
    assert source["parsing_failure_count"] == 0
    assert source["reason_code"] == PipelineReasonCode.SOURCE_SELECTOR_FAILED.value


def test_one_parser_exception_does_not_erase_the_source_run_or_leak_its_text():
    scraper = _FixtureScraper(links=["/article/broken", "/article/healthy"])

    records = scraper.scrape(limit=5)

    assert [record.url for record in records] == [
        "https://example.com/article/healthy"
    ]
    result = scraper.get_last_scrape_stats()
    assert result["article_pages_attempted"] == 2
    assert result["articles_scraped"] == 1
    assert result["parsing_failure_count"] == 1
    assert result["latest_successful_article_at"] == PUBLISHED_AT
    assert result["reason_code"] == PipelineReasonCode.SOURCE_PARSE_FAILED.value
    assert SECRET_TEXT not in repr(result)


def test_partial_parser_failure_reaches_ingestion_without_leaking_secret_text(
    session, blob_store, monkeypatch
):
    scraper = _FixtureScraper(links=["/article/broken", "/article/healthy"])
    monkeypatch.setattr(ingest_core, "build_scrapers", lambda: [scraper])
    monkeypatch.setattr(
        ingest_core,
        "get_core_session",
        lambda: nullcontext(session),
    )

    stats = ingest_core.run_core_ingestion(blob_store=blob_store)
    source = stats.sources[scraper.source_name]

    assert source["status"] == "degraded"
    assert source["article_attempt_count"] == 2
    assert source["article_yield_count"] == 1
    assert source["inserted_count"] == 1
    assert source["parsing_failure_count"] == 1
    assert source["latest_successful_article_at"] == PUBLISHED_AT
    assert source["reason_code"] == PipelineReasonCode.SOURCE_PARSE_FAILED.value
    assert SECRET_TEXT not in repr(source)


def test_duplicate_only_ingest_distinguishes_yield_from_insert(
    session, ontology, blob_store, monkeypatch
):
    article = _article("https://example.com/article/already-known")
    scraper = _ReturnedArticleScraper(article)
    extractor = register_extractor_version(session, scraper.NAME, scraper.VERSION)
    ingest_core.ingest_article(
        session,
        article.to_dict(),
        blob_store,
        extractor,
        ontology,
    )
    session.commit()

    monkeypatch.setattr(ingest_core, "build_scrapers", lambda: [scraper])
    monkeypatch.setattr(
        ingest_core,
        "get_core_session",
        lambda: nullcontext(session),
    )

    stats = ingest_core.run_core_ingestion(blob_store=blob_store)
    source = stats.sources[scraper.source_name]

    assert stats.attempted == 1
    assert stats.inserted == 0
    assert stats.skipped_existing == 1
    assert source["status"] == "success"
    assert source["article_yield_count"] == 1
    assert source["inserted_count"] == 0
    assert source["skipped_existing_count"] == 1
    assert source["latest_successful_article_at"] == PUBLISHED_AT
    assert source["reason_code"] is None


def test_source_exception_returns_only_a_closed_safe_reason(
    session, blob_store, monkeypatch
):
    scraper = _SecretFailureScraper()
    monkeypatch.setattr(ingest_core, "build_scrapers", lambda: [scraper])
    monkeypatch.setattr(
        ingest_core,
        "get_core_session",
        lambda: nullcontext(session),
    )

    stats = ingest_core.run_core_ingestion(blob_store=blob_store)
    source = stats.sources[scraper.source_name]

    assert source["status"] == "failed"
    assert source["reason_code"] == PipelineReasonCode.UNEXPECTED_ERROR.value
    assert "error" not in source
    assert SECRET_TEXT not in repr(source)


def test_ingestion_callbacks_receive_only_source_alias_and_safe_terminal_summary(
    session, blob_store, monkeypatch
):
    scraper = _FixtureScraper(links=["/article/broken", "/article/healthy"])
    monkeypatch.setattr(ingest_core, "build_scrapers", lambda: [scraper])
    monkeypatch.setattr(
        ingest_core,
        "get_core_session",
        lambda: nullcontext(session),
    )
    started: list[str] = []
    finished: list[tuple[str, dict[str, object]]] = []

    ingest_core.run_core_ingestion(
        blob_store=blob_store,
        on_source_started=started.append,
        on_source_finished=lambda name, result: finished.append((name, result)),
    )

    assert started == [scraper.source_name]
    assert len(finished) == 1
    name, result = finished[0]
    assert name == scraper.source_name
    assert result["reason_code"] == PipelineReasonCode.SOURCE_PARSE_FAILED.value
    assert result["article_yield_count"] == 1
    assert SECRET_TEXT not in repr(finished)


def test_source_terminal_callback_runs_only_after_its_rows_commit(
    session, blob_store, monkeypatch
):
    scraper = _ReturnedArticleScraper(
        _article("https://example.com/article/committed")
    )
    monkeypatch.setattr(ingest_core, "build_scrapers", lambda: [scraper])
    callback_order: list[str] = []

    @contextmanager
    def committed_source_session():
        try:
            yield session
            session.commit()
            callback_order.append("committed")
        except Exception:
            session.rollback()
            raise

    monkeypatch.setattr(ingest_core, "get_core_session", committed_source_session)

    def finished(_name: str, result: dict[str, object]) -> None:
        callback_order.append("finished")
        assert result["status"] == "success"
        assert session.scalar(select(func.count()).select_from(DocumentORM)) == 1

    stats = ingest_core.run_core_ingestion(
        blob_store=blob_store,
        on_source_finished=finished,
    )

    assert callback_order == ["committed", "finished"]
    assert stats.inserted == 1
    assert stats.sources[scraper.source_name]["inserted_count"] == 1


def test_source_commit_failure_rolls_back_before_safe_terminal_and_continues(
    session, blob_store, monkeypatch
):
    failed_scraper = _ReturnedArticleScraper(
        _article("https://example.com/article/rolled-back")
    )
    failed_scraper.source_name = "Failed fixture source"
    failed_scraper.NAME = "failed_fixture_scraper"
    successful_scraper = _ReturnedArticleScraper(
        _article("https://example.com/article/committed-after-failure")
    )
    successful_scraper.source_name = "Healthy fixture source"
    successful_scraper.NAME = "healthy_fixture_scraper"
    monkeypatch.setattr(
        ingest_core,
        "build_scrapers",
        lambda: [failed_scraper, successful_scraper],
    )

    transaction_number = 0

    @contextmanager
    def source_session():
        nonlocal transaction_number
        current_transaction = transaction_number
        transaction_number += 1
        try:
            yield session
            if current_transaction == 0:
                raise RuntimeError(SECRET_TEXT)
            session.commit()
        except Exception:
            session.rollback()
            raise

    monkeypatch.setattr(ingest_core, "get_core_session", source_session)
    terminal_rows: list[tuple[str, dict[str, object]]] = []

    def finished(name: str, result: dict[str, object]) -> None:
        terminal_rows.append((name, dict(result)))
        if name == failed_scraper.source_name:
            assert session.scalar(select(func.count()).select_from(DocumentORM)) == 0

    stats = ingest_core.run_core_ingestion(
        blob_store=blob_store,
        on_source_finished=finished,
    )

    failed = stats.sources[failed_scraper.source_name]
    healthy = stats.sources[successful_scraper.source_name]
    assert stats.attempted == 2
    assert stats.inserted == 1
    assert stats.skipped_existing == 0
    assert failed["status"] == "failed"
    assert failed["reason_code"] == PipelineReasonCode.UNEXPECTED_ERROR.value
    assert failed["inserted_count"] == 0
    assert failed["skipped_existing_count"] == 0
    assert failed["error_count"] >= 1
    assert healthy["status"] == "success"
    assert healthy["inserted_count"] == 1
    assert [name for name, _result in terminal_rows] == [
        failed_scraper.source_name,
        successful_scraper.source_name,
    ]
    assert SECRET_TEXT not in repr(terminal_rows)
    assert session.scalar(select(func.count()).select_from(DocumentORM)) == 1


def test_interrupted_runtime_never_records_uncommitted_source_success(
    session, blob_store, monkeypatch
):
    """A crash after a terminal callback leaves only committed source success."""
    scraper = _ReturnedArticleScraper(
        _article("https://example.com/article/interrupted")
    )
    monkeypatch.setattr(ingest_core, "build_scrapers", lambda: [scraper])
    engine = session.get_bind()

    @contextmanager
    def source_session():
        write_session = Session(engine)
        try:
            yield write_session
            write_session.commit()
        except Exception:
            write_session.rollback()
            raise
        finally:
            write_session.close()

    @contextmanager
    def ledger_session():
        write_session = Session(engine)
        try:
            yield write_session
            write_session.commit()
        except Exception:
            write_session.rollback()
            raise
        finally:
            write_session.close()

    monkeypatch.setattr(ingest_core, "get_core_session", source_session)

    def interrupted_stage(runtime):  # noqa: ANN001
        def record_terminal(name: str, telemetry: dict[str, object]) -> None:
            source_stats = SimpleNamespace(
                attempted=0,
                inserted=0,
                sources={name: telemetry},
            )
            runtime.source_completed(outcome_from_ingest_stats(source_stats).sources[0])

        ingest_core.run_core_ingestion(
            blob_store=blob_store,
            on_source_started=runtime.source_started,
            on_source_finished=record_terminal,
        )
        # The source's transaction and terminal event completed above. This
        # models a process death before the stage/run terminal events.
        raise KeyboardInterrupt

    fixed_now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    run_id = "interrupted-committed-source"
    with pytest.raises(KeyboardInterrupt):
        run_orchestrated_pipeline(
            (
                PipelineStage(
                    name="ingest",
                    source_names=(scraper.source_name,),
                    operation=interrupted_stage,
                ),
            ),
            session_scope=ledger_session,
            run_id=run_id,
            commit_sha="0123456789abcdef",
            clock=lambda: fixed_now,
        )

    with Session(engine) as query_session:
        assert (
            query_session.scalar(select(func.count()).select_from(DocumentORM))
            == 1
        )
        event_types = list(
            query_session.scalars(
                select(PipelineEventORM.event_type)
                .where(PipelineEventORM.run_id == run_id)
                .order_by(PipelineEventORM.id.asc())
            )
        )
        assert PipelineEventType.SOURCE_SUCCEEDED.value in event_types
        assert PipelineEventType.RUN_SUCCEEDED.value not in event_types
        assert PipelineEventType.RUN_FAILED.value not in event_types

        abandoned = abandon_expired_runs(
            query_session,
            now=fixed_now + timedelta(hours=1),
            monitor_commit_sha="0123456789abcdef",
        )
        query_session.commit()

    assert len(abandoned) == 1
    with Session(engine) as query_session:
        terminal_types = list(
            query_session.scalars(
                select(PipelineEventORM.event_type)
                .where(PipelineEventORM.run_id == run_id)
                .order_by(PipelineEventORM.id.asc())
            )
        )
    assert PipelineEventType.RUN_ABANDONED.value in terminal_types
