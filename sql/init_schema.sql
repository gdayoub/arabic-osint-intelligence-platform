CREATE TABLE IF NOT EXISTS raw_articles (
    id SERIAL PRIMARY KEY,
    source VARCHAR(100) NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    body TEXT NOT NULL,
    author VARCHAR(255),
    published_date TIMESTAMPTZ,
    url VARCHAR(1024) NOT NULL UNIQUE,
    tags JSONB,
    source_section VARCHAR(255),
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_hash VARCHAR(128) NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_raw_articles_source ON raw_articles(source);
CREATE INDEX IF NOT EXISTS idx_raw_articles_content_hash ON raw_articles(content_hash);

CREATE TABLE IF NOT EXISTS processed_articles (
    id SERIAL PRIMARY KEY,
    raw_article_id INTEGER NOT NULL UNIQUE REFERENCES raw_articles(id) ON DELETE CASCADE,
    cleaned_text TEXT NOT NULL,
    topic VARCHAR(100) NOT NULL,
    sentiment_or_escalation VARCHAR(64) NOT NULL,
    country_guess VARCHAR(100),
    keyword_matches JSONB,
    ml_confidence FLOAT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_processed_articles_topic ON processed_articles(topic);
CREATE INDEX IF NOT EXISTS idx_processed_articles_escalation ON processed_articles(sentiment_or_escalation);
CREATE INDEX IF NOT EXISTS idx_processed_articles_processed_at ON processed_articles(processed_at);
