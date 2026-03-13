from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.crud import create_raw_article, list_unprocessed_raw_articles, upsert_processed_article
from src.database.models import Base, ProcessedArticle, RawArticle


def test_raw_and_processed_crud_roundtrip():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with Session() as session:
        raw = create_raw_article(
            session,
            {
                "source": "TestSource",
                "title": "عنوان",
                "subtitle": None,
                "body": "محتوى الخبر",
                "author": None,
                "published_date": None,
                "url": "https://example.com/article-1",
                "tags": ["tag"],
                "source_section": "news",
            },
        )
        session.commit()
        raw_id = raw.id

    with Session() as session:
        pending = list_unprocessed_raw_articles(session)
        assert len(pending) == 1
        assert pending[0].id == raw_id

        upsert_processed_article(
            session,
            raw_article_id=raw_id,
            cleaned_text="محتوى الخبر",
            topic="Politics",
            sentiment_or_escalation="medium",
            country_guess="Syria",
            keyword_matches={"hits": ["حكومة"]},
        )
        session.commit()

    with Session() as session:
        assert session.query(RawArticle).count() == 1
        assert session.query(ProcessedArticle).count() == 1
