"""Dark, analyst-focused Streamlit intelligence monitoring console."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from html import escape

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

from src.config.logging_config import setup_logging
from src.database.db import get_db_session
from src.utils.text_utils import safe_truncate

setup_logging()
logger = logging.getLogger("dashboard")

st.set_page_config(
    page_title="Arabic Geopolitical OSINT Console",
    layout="wide",
)


ESCALATION_WEIGHT = {
    "high": 4.0,
    "medium": 2.3,
    "low": 1.0,
    "unknown": 0.4,
}

TOPIC_WEIGHT = {
    "Military": 2.4,
    "Politics": 2.2,
    "Protests": 1.7,
    "Economy": 1.5,
    "Humanitarian": 1.4,
    "Uncategorized": 1.0,
}


def inject_global_css() -> None:
    """Apply dashboard-wide dark UI styling."""
    st.markdown(
        """
        <style>
        :root {
            --bg-main: #0b1220;
            --bg-panel: #111b2e;
            --bg-panel-2: #0f1728;
            --bg-sidebar: #0d1627;
            --text-main: #e6edf7;
            --text-muted: #9cb0c7;
            --accent: #4cc9f0;
            --accent-2: #2dd4bf;
            --border: #22334d;
            --danger: #fb7185;
            --shadow: 0 10px 26px rgba(4, 8, 18, 0.35);
        }

        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(1000px 420px at 94% -10%, rgba(76, 201, 240, 0.22), transparent 55%),
                radial-gradient(900px 380px at -5% -15%, rgba(45, 212, 191, 0.16), transparent 55%),
                linear-gradient(180deg, #0b1220 0%, #0a1221 48%, #09101c 100%);
            color: var(--text-main);
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #0d1627 0%, #0a1323 100%);
            border-right: 1px solid var(--border);
        }

        [data-testid="stSidebar"] * {
            color: var(--text-main) !important;
        }

        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] .stMarkdown p {
            color: var(--text-muted) !important;
        }

        [data-testid="stSidebar"] .stTextInput input,
        [data-testid="stSidebar"] .stDateInput input,
        [data-testid="stSidebar"] div[data-baseweb="select"] > div {
            background: #0f1b30 !important;
            border: 1px solid #2a3d5c !important;
            border-radius: 10px !important;
        }

        h1, h2, h3, h4 {
            color: var(--text-main) !important;
            letter-spacing: 0.02em;
        }

        .hero {
            background: linear-gradient(
                120deg,
                rgba(17, 27, 46, 0.92) 0%,
                rgba(14, 22, 37, 0.94) 48%,
                rgba(18, 36, 55, 0.90) 100%
            );
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 1.2rem 1.35rem;
            box-shadow: var(--shadow);
            margin-bottom: 0.85rem;
        }

        .hero-title {
            font-size: 1.8rem;
            font-weight: 700;
            color: #f2f6fc;
            margin-bottom: 0.3rem;
        }

        .hero-subtitle {
            color: #bcd0e7;
            font-size: 1rem;
            margin-bottom: 0.45rem;
        }

        .hero-note {
            color: #8fabc9;
            font-size: 0.88rem;
        }

        .section-header {
            margin-top: 0.7rem;
            margin-bottom: 0.55rem;
            padding-left: 0.1rem;
        }

        .section-title {
            font-size: 1.2rem;
            font-weight: 650;
            color: #eaf1fa;
            margin-bottom: 0.15rem;
        }

        .section-subtitle {
            color: var(--text-muted);
            font-size: 0.9rem;
        }

        .kpi-card {
            border: 1px solid var(--border);
            border-radius: 14px;
            background: linear-gradient(165deg, rgba(18, 30, 49, 0.96), rgba(13, 22, 38, 0.95));
            padding: 0.9rem 0.95rem 1rem 0.95rem;
            box-shadow: var(--shadow);
            min-height: 118px;
            transition: transform 0.18s ease, border-color 0.18s ease;
        }

        .kpi-card:hover {
            transform: translateY(-2px);
            border-color: #35537a;
        }

        .kpi-chip {
            display: inline-block;
            border: 1px solid #2f4666;
            border-radius: 999px;
            color: #9cc0e7;
            font-size: 0.7rem;
            letter-spacing: 0.06em;
            padding: 0.16rem 0.45rem;
            margin-bottom: 0.48rem;
            text-transform: uppercase;
        }

        .kpi-label {
            color: #a8bfd8;
            font-size: 0.82rem;
            margin-bottom: 0.15rem;
        }

        .kpi-value {
            color: #f2f6fc;
            font-size: 1.95rem;
            line-height: 1.12;
            font-weight: 720;
            letter-spacing: 0.02em;
        }

        .panel {
            border: 1px solid var(--border);
            border-radius: 14px;
            background: linear-gradient(180deg, rgba(16, 26, 43, 0.94), rgba(14, 22, 36, 0.94));
            padding: 0.8rem 0.95rem;
            box-shadow: var(--shadow);
        }

        .briefing-panel {
            border: 1px solid #2d466a;
            border-radius: 14px;
            padding: 0.95rem 1rem;
            background: linear-gradient(145deg, rgba(17, 27, 46, 0.95), rgba(14, 20, 33, 0.98));
            box-shadow: var(--shadow);
        }

        .briefing-title {
            color: #eff5ff;
            font-size: 1rem;
            margin-bottom: 0.45rem;
            font-weight: 650;
        }

        .briefing-item {
            color: #b8cde5;
            margin-bottom: 0.25rem;
            font-size: 0.92rem;
        }

        .badge {
            display: inline-block;
            border: 1px solid #355074;
            border-radius: 999px;
            padding: 0.12rem 0.52rem;
            margin-right: 0.25rem;
            margin-bottom: 0.35rem;
            color: #bcd4ef;
            font-size: 0.73rem;
            letter-spacing: 0.02em;
        }

        .priority-card {
            border: 1px solid #284367;
            border-radius: 12px;
            background: linear-gradient(155deg, rgba(18, 31, 52, 0.97), rgba(12, 21, 35, 0.98));
            padding: 0.9rem 0.95rem;
            margin-bottom: 0.7rem;
            box-shadow: var(--shadow);
        }

        .priority-rank {
            color: #67d5ff;
            font-size: 0.76rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 0.25rem;
        }

        .priority-title {
            color: #f0f6ff;
            font-size: 1rem;
            font-weight: 640;
            margin-bottom: 0.35rem;
        }

        .priority-meta {
            color: #adc4df;
            font-size: 0.82rem;
            margin-bottom: 0.38rem;
        }

        .priority-reason {
            color: #8fdbca;
            font-size: 0.82rem;
            margin-bottom: 0.35rem;
        }

        .priority-preview {
            color: #c4d6eb;
            font-size: 0.88rem;
            line-height: 1.42;
            margin-bottom: 0.35rem;
        }

        .article-card {
            border: 1px solid var(--border);
            border-radius: 12px;
            background: linear-gradient(160deg, rgba(16, 27, 45, 0.96), rgba(12, 20, 34, 0.96));
            padding: 0.85rem 0.9rem;
            margin-bottom: 0.55rem;
        }

        .article-title {
            font-size: 1rem;
            color: #ecf3fd;
            font-weight: 620;
            margin-bottom: 0.35rem;
        }

        .article-meta {
            color: #a7bfd9;
            font-size: 0.82rem;
            margin-bottom: 0.45rem;
        }

        .article-preview {
            color: #c0d3ea;
            font-size: 0.88rem;
            line-height: 1.42;
        }

        .article-link a {
            color: #75d0ff !important;
            text-decoration: none;
        }

        .article-link a:hover {
            text-decoration: underline;
        }

        [data-testid="stDataFrame"] {
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: var(--shadow);
        }

        [data-testid="stExpander"] {
            border: 1px solid #2a3e5f !important;
            border-radius: 12px !important;
            background: rgba(17, 27, 45, 0.68);
        }

        [data-testid="stExpander"] summary p {
            color: #e4edf8 !important;
            font-weight: 560;
        }

        .chart-wrap {
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 0.35rem 0.45rem 0.15rem 0.45rem;
            background: linear-gradient(180deg, rgba(15, 24, 40, 0.96), rgba(12, 20, 34, 0.98));
            box-shadow: var(--shadow);
        }

        hr {
            border: none;
            border-top: 1px solid #223550;
            margin-top: 0.85rem;
            margin-bottom: 0.85rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def parse_keyword_matches(value: object) -> dict:
    """Normalize keyword_matches field into a dictionary."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def flatten_keyword_matches(payload: dict, limit: int = 8) -> str:
    """Flatten nested keyword match payload for readable UI output."""
    if not payload:
        return "No keyword evidence captured"

    collected: list[str] = []

    topic_matches = payload.get("topic_matches", {})
    if isinstance(topic_matches, dict):
        for values in topic_matches.values():
            if isinstance(values, list):
                collected.extend([str(v) for v in values if v])

    escalation_matches = payload.get("escalation_matches", {})
    if isinstance(escalation_matches, dict):
        for values in escalation_matches.values():
            if isinstance(values, list):
                collected.extend([str(v) for v in values if v])

    deduped = []
    seen: set[str] = set()
    for keyword in collected:
        if keyword not in seen:
            deduped.append(keyword)
            seen.add(keyword)
    if not deduped:
        return "No keyword evidence captured"
    return ", ".join(deduped[:limit])


def keyword_match_count(payload: dict) -> int:
    """Count matched keywords from payload."""
    if not payload:
        return 0
    count = 0
    for key in ("topic_matches", "escalation_matches"):
        block = payload.get(key, {})
        if isinstance(block, dict):
            for values in block.values():
                if isinstance(values, list):
                    count += len(values)
    return count


@st.cache_data(ttl=300)
def load_article_dataframe() -> pd.DataFrame:
    """Load joined raw+processed records for dashboard rendering."""
    query = text(
        """
        SELECT
            r.id AS raw_article_id,
            r.source,
            r.title,
            r.body,
            r.author,
            r.url,
            r.source_section,
            r.collected_at,
            r.published_date,
            p.id AS processed_article_id,
            p.topic,
            p.sentiment_or_escalation,
            p.country_guess,
            p.keyword_matches,
            p.processed_at
        FROM raw_articles r
        LEFT JOIN processed_articles p ON p.raw_article_id = r.id
        ORDER BY COALESCE(r.published_date, r.collected_at) DESC
        """
    )
    try:
        with get_db_session() as session:
            df = pd.read_sql(query, session.bind)
    except Exception as exc:
        logger.exception("Failed to query article data: %s", exc)
        return pd.DataFrame(
            columns=[
                "raw_article_id",
                "source",
                "title",
                "body",
                "author",
                "url",
                "source_section",
                "collected_at",
                "published_date",
                "processed_article_id",
                "topic",
                "sentiment_or_escalation",
                "country_guess",
                "keyword_matches",
                "processed_at",
            ]
        )

    if df.empty:
        return df

    df["collected_at"] = pd.to_datetime(df["collected_at"], errors="coerce", utc=True)
    df["published_date"] = pd.to_datetime(df["published_date"], errors="coerce", utc=True)
    df["processed_at"] = pd.to_datetime(df["processed_at"], errors="coerce", utc=True)
    df["analysis_date"] = df["published_date"].fillna(df["collected_at"])
    df["topic"] = df["topic"].fillna("Uncategorized")
    df["sentiment_or_escalation"] = df["sentiment_or_escalation"].fillna("unknown")
    df["country_guess"] = df["country_guess"].fillna("Unknown")
    df["keyword_matches"] = df["keyword_matches"].apply(parse_keyword_matches)
    return df


def render_header() -> None:
    """Render top hero/banner section."""
    st.markdown(
        """
        <div class="hero">
          <div class="hero-title">Arabic Geopolitical OSINT Console</div>
          <div class="hero-subtitle">
            Live monitoring of Arabic-language news sources, classified by topic and escalation risk.
          </div>
          <div class="hero-note">
            Focus: geopolitical developments, escalation signals, source activity, and country-level trend intelligence.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_header(title: str, subtitle: str) -> None:
    """Render consistent section heading."""
    st.markdown(
        f"""
        <div class="section-header">
          <div class="section-title">{title}</div>
          <div class="section-subtitle">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def apply_sidebar_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Render and apply sidebar filter controls."""
    if df.empty:
        return df

    with st.sidebar:
        st.markdown("### Filter Controls")
        st.markdown(
            "<span style='color:#9cb0c7;'>Refine by source, classification, geography, and timeframe.</span>",
            unsafe_allow_html=True,
        )

        sources = sorted(df["source"].dropna().unique().tolist())
        topics = sorted(df["topic"].dropna().unique().tolist())
        escalations = sorted(df["sentiment_or_escalation"].dropna().unique().tolist())
        countries = sorted(df["country_guess"].dropna().unique().tolist())
        sections = sorted(df["source_section"].dropna().unique().tolist())

        selected_sources = st.multiselect("Source", sources, default=sources)
        selected_topics = st.multiselect("Topic", topics, default=topics)
        selected_escalations = st.multiselect(
            "Escalation",
            escalations,
            default=escalations,
        )
        selected_countries = st.multiselect("Country", countries, default=countries)
        if sections:
            selected_sections = st.multiselect("Section", sections, default=sections)
        else:
            selected_sections = []

        min_date = (
            df["analysis_date"].dt.date.min()
            if df["analysis_date"].notna().any()
            else date.today() - timedelta(days=7)
        )
        max_date = (
            df["analysis_date"].dt.date.max()
            if df["analysis_date"].notna().any()
            else date.today()
        )
        selected_dates = st.date_input(
            "Date range",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
        keyword = st.text_input("Keyword Search")

    filtered = df.copy()
    filtered = filtered[filtered["source"].isin(selected_sources)]
    filtered = filtered[filtered["topic"].isin(selected_topics)]
    filtered = filtered[filtered["sentiment_or_escalation"].isin(selected_escalations)]
    filtered = filtered[filtered["country_guess"].isin(selected_countries)]
    if selected_sections:
        filtered = filtered[filtered["source_section"].isin(selected_sections)]

    if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
        start_date, end_date = selected_dates
        if start_date and end_date:
            filtered = filtered[
                filtered["analysis_date"]
                .dt.date.between(start_date, end_date, inclusive="both")
            ]

    if keyword:
        pattern = keyword.strip()
        if pattern:
            filtered = filtered[
                filtered["title"].fillna("").str.contains(pattern, case=False, regex=False)
                | filtered["body"].fillna("").str.contains(pattern, case=False, regex=False)
                | filtered["source"].fillna("").str.contains(pattern, case=False, regex=False)
                | filtered["country_guess"].fillna("").str.contains(
                    pattern, case=False, regex=False
                )
            ]

    return filtered.sort_values("analysis_date", ascending=False)


def render_kpi_row(filtered_df: pd.DataFrame) -> None:
    """Render premium KPI cards."""
    raw_count = int(filtered_df["raw_article_id"].nunique())
    processed_count = int(filtered_df["processed_article_id"].dropna().nunique())
    source_count = int(filtered_df["source"].nunique())
    high_escalation_count = int(
        (filtered_df["sentiment_or_escalation"].str.lower() == "high").sum()
    )

    kpis = [
        ("Raw", "Total Raw Articles", raw_count),
        ("Proc", "Total Processed Articles", processed_count),
        ("Src", "Active Sources", source_count),
        ("Risk", "High-Escalation Articles", high_escalation_count),
    ]

    cols = st.columns(4)
    for col, (chip, label, value) in zip(cols, kpis):
        with col:
            st.markdown(
                f"""
                <div class="kpi-card">
                  <div class="kpi-chip">{chip}</div>
                  <div class="kpi-label">{label}</div>
                  <div class="kpi-value">{value:,}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def style_figure(fig, title: str) -> None:
    """Apply a consistent dark look across Plotly figures."""
    fig.update_layout(
        title=title,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#d6e4f4"),
        title_font=dict(size=17, color="#eef5ff"),
        margin=dict(l=14, r=14, t=52, b=14),
        legend=dict(
            bgcolor="rgba(12,20,34,0.45)",
            bordercolor="#294264",
            borderwidth=1,
            font=dict(color="#c7daef", size=11),
        ),
        xaxis=dict(gridcolor="rgba(83,109,141,0.25)", zerolinecolor="rgba(83,109,141,0.25)"),
        yaxis=dict(gridcolor="rgba(83,109,141,0.25)", zerolinecolor="rgba(83,109,141,0.25)"),
    )


def generate_intelligence_briefing(filtered_df: pd.DataFrame) -> dict[str, object]:
    """Build top-line intelligence briefing values from filtered data."""
    if filtered_df.empty:
        return {
            "top_topic": "N/A",
            "top_country": "N/A",
            "high_escalation_count": 0,
            "most_active_source": "N/A",
            "new_articles_24h": 0,
            "window_anchor": "N/A",
        }

    top_topic = filtered_df["topic"].value_counts().idxmax()
    country_series = filtered_df["country_guess"].replace("Unknown", pd.NA).dropna()
    top_country = country_series.value_counts().idxmax() if not country_series.empty else "N/A"
    high_escalation_count = int(
        (filtered_df["sentiment_or_escalation"].str.lower() == "high").sum()
    )
    most_active_source = filtered_df["source"].value_counts().idxmax()

    newest_ts = filtered_df["analysis_date"].max()
    if pd.notna(newest_ts):
        recent_cutoff = newest_ts - pd.Timedelta(hours=24)
        new_articles_24h = int((filtered_df["analysis_date"] >= recent_cutoff).sum())
        window_anchor = pd.to_datetime(newest_ts).strftime("%Y-%m-%d %H:%M UTC")
    else:
        new_articles_24h = 0
        window_anchor = "N/A"

    return {
        "top_topic": top_topic,
        "top_country": top_country,
        "high_escalation_count": high_escalation_count,
        "most_active_source": most_active_source,
        "new_articles_24h": new_articles_24h,
        "window_anchor": window_anchor,
    }


def render_intelligence_briefing_top(filtered_df: pd.DataFrame) -> None:
    """Render high-signal briefing near top of dashboard."""
    render_section_header(
        "Intelligence Briefing",
        "Immediate answers: what dominates, who is most active, and where escalation pressure is rising.",
    )

    if filtered_df.empty:
        st.info("No records available to generate a briefing for the selected filters.")
        return

    briefing = generate_intelligence_briefing(filtered_df)
    st.markdown(
        f"""
        <div class="briefing-panel">
          <div class="briefing-title">Analyst Snapshot</div>
          <div class="briefing-item">Top topic in selected range: <strong>{escape(str(briefing['top_topic']))}</strong>.</div>
          <div class="briefing-item">Top country in selected range: <strong>{escape(str(briefing['top_country']))}</strong>.</div>
          <div class="briefing-item">High-escalation article count: <strong>{briefing['high_escalation_count']}</strong>.</div>
          <div class="briefing-item">Most active source: <strong>{escape(str(briefing['most_active_source']))}</strong>.</div>
          <div class="briefing-item">New articles in most recent 24h window: <strong>{briefing['new_articles_24h']}</strong> (anchor: {escape(str(briefing['window_anchor']))}).</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def compute_priority_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Score articles so analysts see most important items first."""
    if df.empty:
        return df

    ranked = df.copy()
    newest_ts = ranked["analysis_date"].max()

    if pd.isna(newest_ts):
        ranked["recency_component"] = 0.0
    else:
        age_hours = (newest_ts - ranked["analysis_date"]).dt.total_seconds().div(3600)
        ranked["recency_component"] = ((48 - age_hours).clip(lower=0, upper=48) / 48) * 3.0

    ranked["escalation_component"] = ranked["sentiment_or_escalation"].str.lower().map(
        ESCALATION_WEIGHT
    ).fillna(0.0) * 4.0
    ranked["topic_component"] = ranked["topic"].map(TOPIC_WEIGHT).fillna(1.0) * 2.0

    ranked["keyword_match_count"] = ranked["keyword_matches"].apply(keyword_match_count)
    ranked["keyword_component"] = ranked["keyword_match_count"].clip(upper=8) * 0.2

    ranked["priority_score"] = (
        ranked["escalation_component"]
        + ranked["topic_component"]
        + ranked["recency_component"]
        + ranked["keyword_component"]
    ).round(2)

    ranked["priority_reason"] = ranked.apply(build_priority_reason, axis=1)
    return ranked.sort_values(
        ["priority_score", "analysis_date"], ascending=[False, False]
    )


def build_priority_reason(row: pd.Series) -> str:
    """Generate analyst-friendly explanation for article priority score."""
    reasons: list[str] = []

    escalation = str(row.get("sentiment_or_escalation", "unknown")).lower()
    if escalation == "high":
        reasons.append("high escalation signal")
    elif escalation == "medium":
        reasons.append("medium escalation signal")

    topic = str(row.get("topic", "Uncategorized"))
    if topic in {"Military", "Politics"}:
        reasons.append("strategic topic")

    recency = float(row.get("recency_component", 0))
    if recency >= 2.0:
        reasons.append("very recent publication")

    if int(row.get("keyword_match_count", 0)) >= 3:
        reasons.append("multiple matched indicators")

    if not reasons:
        reasons.append("contextual monitoring relevance")

    return "; ".join(reasons)


def render_priority_articles(filtered_df: pd.DataFrame, top_n: int = 8) -> None:
    """Render highest-priority articles first with explainability cues."""
    render_section_header(
        "Priority Articles",
        "Ranked by escalation, recency, strategic topic weighting, and matched indicator density.",
    )

    if filtered_df.empty:
        st.info("No records available for priority ranking.")
        return

    ranked = compute_priority_scores(filtered_df).head(top_n)
    if ranked.empty:
        st.info("No ranked articles available.")
        return

    cols = st.columns(2)
    for idx, (_, row) in enumerate(ranked.iterrows(), start=1):
        col = cols[(idx - 1) % 2]
        with col:
            title = escape(safe_truncate(str(row.get("title", "N/A")), 115))
            source = escape(str(row.get("source", "N/A")))
            topic = escape(str(row.get("topic", "Uncategorized")))
            escalation = escape(str(row.get("sentiment_or_escalation", "unknown")))
            country = escape(str(row.get("country_guess", "Unknown")))
            published = pd.to_datetime(row.get("analysis_date"), errors="coerce")
            published_label = (
                published.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(published) else "N/A"
            )
            reason = escape(str(row.get("priority_reason", "contextual relevance")))
            preview = escape(safe_truncate(str(row.get("body", "")), 260))
            url = escape(str(row.get("url", "#")))
            matched_keywords = escape(flatten_keyword_matches(row.get("keyword_matches", {})))

            st.markdown(
                f"""
                <div class="priority-card">
                  <div class="priority-rank">Priority Rank #{idx} | Score {row.get('priority_score', 0):.2f}</div>
                  <div class="priority-title">{title}</div>
                  <div class="priority-meta">{source} | {published_label} | {country}</div>
                  <span class="badge">{topic}</span>
                  <span class="badge">{escalation}</span>
                  <div class="priority-reason">Why this ranks high: {reason}</div>
                  <div class="priority-preview">{preview}</div>
                  <div class="priority-meta">Matched keywords: {matched_keywords}</div>
                  <div class="article-link"><a href="{url}" target="_blank">Open source article</a></div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_trend_section(filtered_df: pd.DataFrame) -> None:
    """Render trend visualizations including temporal change signals."""
    render_section_header(
        "Trend Monitoring",
        "Temporal topic shifts, escalation trajectories, source output, and country concentration.",
    )

    if filtered_df.empty:
        st.info("No records match your current filters. Adjust filters to view trend analytics.")
        return

    topic_time_df = (
        filtered_df.dropna(subset=["analysis_date"])
        .assign(day=lambda d: d["analysis_date"].dt.date)
        .groupby(["day", "topic"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
        .sort_values("day")
    )

    escalation_time_df = (
        filtered_df.dropna(subset=["analysis_date"])
        .assign(day=lambda d: d["analysis_date"].dt.date)
        .groupby(["day", "sentiment_or_escalation"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
        .sort_values("day")
    )

    source_df = (
        filtered_df.groupby("source", as_index=False)
        .size()
        .rename(columns={"size": "count"})
        .sort_values("count", ascending=False)
    )

    country_df = (
        filtered_df[filtered_df["country_guess"] != "Unknown"]
        .groupby("country_guess", as_index=False)
        .size()
        .rename(columns={"size": "count"})
        .sort_values("count", ascending=False)
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="chart-wrap">', unsafe_allow_html=True)
        fig = px.line(
            topic_time_df,
            x="day",
            y="count",
            color="topic",
            markers=True,
            color_discrete_sequence=px.colors.qualitative.Vivid,
        )
        style_figure(fig, "Topic Frequency Over Time")
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="chart-wrap">', unsafe_allow_html=True)
        fig = px.area(
            escalation_time_df,
            x="day",
            y="count",
            color="sentiment_or_escalation",
            color_discrete_sequence=["#fb7185", "#fbbf24", "#4ade80", "#4cc9f0"],
        )
        style_figure(fig, "Escalation Frequency Over Time")
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    c3, c4 = st.columns(2)
    with c3:
        st.markdown('<div class="chart-wrap">', unsafe_allow_html=True)
        fig = px.bar(
            source_df,
            x="source",
            y="count",
            color="source",
            color_discrete_sequence=px.colors.qualitative.Bold,
        )
        style_figure(fig, "Source Comparison")
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with c4:
        st.markdown('<div class="chart-wrap">', unsafe_allow_html=True)
        if country_df.empty:
            placeholder = pd.DataFrame({"country_guess": ["N/A"], "count": [0]})
            fig = px.bar(placeholder, x="country_guess", y="count")
        else:
            fig = px.bar(
                country_df.head(10),
                x="country_guess",
                y="count",
                color="country_guess",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
        style_figure(fig, "Top Countries Distribution")
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)


def render_country_analysis(filtered_df: pd.DataFrame) -> None:
    """Render country-focused analysis and deep-dive exploration."""
    render_section_header(
        "Country Analysis",
        "Country-level concentration, escalation pressure, and deep-dive records by selected country.",
    )

    if filtered_df.empty:
        st.info("No records available for country analysis.")
        return

    country_df = (
        filtered_df[filtered_df["country_guess"] != "Unknown"]
        .groupby("country_guess", as_index=False)
        .size()
        .rename(columns={"size": "article_count"})
        .sort_values("article_count", ascending=False)
    )

    high_country_df = (
        filtered_df[
            (filtered_df["country_guess"] != "Unknown")
            & (filtered_df["sentiment_or_escalation"].str.lower() == "high")
        ]
        .groupby("country_guess", as_index=False)
        .size()
        .rename(columns={"size": "high_escalation_count"})
        .sort_values("high_escalation_count", ascending=False)
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="chart-wrap">', unsafe_allow_html=True)
        if country_df.empty:
            fig = px.bar(pd.DataFrame({"country_guess": ["N/A"], "article_count": [0]}), x="country_guess", y="article_count")
        else:
            fig = px.bar(
                country_df.head(10),
                x="country_guess",
                y="article_count",
                color="country_guess",
                color_discrete_sequence=px.colors.qualitative.Set3,
            )
        style_figure(fig, "Top Countries Mentioned")
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="chart-wrap">', unsafe_allow_html=True)
        if high_country_df.empty:
            fig = px.bar(pd.DataFrame({"country_guess": ["N/A"], "high_escalation_count": [0]}), x="country_guess", y="high_escalation_count")
        else:
            fig = px.bar(
                high_country_df.head(10),
                x="country_guess",
                y="high_escalation_count",
                color="country_guess",
                color_discrete_sequence=px.colors.qualitative.Pastel,
            )
        style_figure(fig, "High-Escalation Articles by Country")
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    focus_options = ["All Countries"] + country_df["country_guess"].tolist()
    selected_country = st.selectbox("Country Deep Dive", options=focus_options, index=0)

    scoped = (
        filtered_df if selected_country == "All Countries" else filtered_df[filtered_df["country_guess"] == selected_country]
    )

    stat1, stat2, stat3, stat4 = st.columns(4)
    stat1.metric("Articles", int(len(scoped)))
    stat2.metric(
        "High Escalation",
        int((scoped["sentiment_or_escalation"].str.lower() == "high").sum()),
    )
    stat3.metric(
        "Top Topic",
        scoped["topic"].value_counts().idxmax() if not scoped.empty else "N/A",
    )
    stat4.metric(
        "Top Source",
        scoped["source"].value_counts().idxmax() if not scoped.empty else "N/A",
    )

    country_table = scoped[
        [
            "analysis_date",
            "source",
            "title",
            "topic",
            "sentiment_or_escalation",
            "country_guess",
        ]
    ].copy()
    country_table["analysis_date"] = pd.to_datetime(
        country_table["analysis_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d %H:%M")
    country_table["title"] = country_table["title"].map(lambda v: safe_truncate(str(v), 100))
    country_table = country_table.rename(columns={"analysis_date": "Date", "source": "Source", "title": "Title", "topic": "Topic", "sentiment_or_escalation": "Escalation", "country_guess": "Country"})
    st.dataframe(country_table.head(20), use_container_width=True, hide_index=True, height=250)


def render_articles_section(filtered_df: pd.DataFrame) -> None:
    """Render table + detailed explainability for articles."""
    render_section_header(
        "Recent Articles Explorer",
        "Browse latest records with topic, escalation, country, and keyword-match explainability.",
    )

    if filtered_df.empty:
        st.info("No article records available for the active filters.")
        return

    table_df = filtered_df[
        [
            "analysis_date",
            "source",
            "topic",
            "sentiment_or_escalation",
            "country_guess",
            "title",
            "url",
        ]
    ].copy()
    table_df = table_df.rename(
        columns={
            "analysis_date": "Date",
            "source": "Source",
            "topic": "Topic",
            "sentiment_or_escalation": "Escalation",
            "country_guess": "Country",
            "title": "Title",
            "url": "URL",
        }
    )
    table_df["Date"] = pd.to_datetime(table_df["Date"], errors="coerce").dt.strftime(
        "%Y-%m-%d %H:%M"
    )
    table_df["Title"] = table_df["Title"].map(lambda x: safe_truncate(str(x), 130))

    st.dataframe(table_df.head(80), use_container_width=True, hide_index=True, height=300)

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown("#### Article Detail Expanders")
    for _, row in filtered_df.head(14).iterrows():
        source = row.get("source", "N/A")
        title = safe_truncate(str(row.get("title", "")), 100)
        with st.expander(f"[{source}] {title}"):
            st.markdown(f"**Title:** {row.get('title', 'N/A')}")
            st.markdown(f"**Source:** {source}")
            st.markdown(f"**Published Date:** {row.get('published_date', row.get('analysis_date', 'N/A'))}")
            st.markdown(f"**Topic:** {row.get('topic', 'Uncategorized')}")
            st.markdown(f"**Escalation:** {row.get('sentiment_or_escalation', 'unknown')}")
            st.markdown(f"**Country:** {row.get('country_guess', 'Unknown')}")
            st.markdown(f"**Matched Keywords:** {flatten_keyword_matches(row.get('keyword_matches', {}), limit=12)}")
            st.markdown(f"**URL:** {row.get('url', 'N/A')}")
            st.markdown(f"**Body Preview:** {safe_truncate(str(row.get('body', '')), 950)}")
    st.markdown("</div>", unsafe_allow_html=True)


def main() -> None:
    """Main Streamlit entrypoint."""
    inject_global_css()
    df = load_article_dataframe()
    render_header()

    if df.empty:
        st.warning(
            "No data available yet. Run `python3 main.py ingest` and `python3 main.py process` first."
        )
        return

    filtered_df = apply_sidebar_filters(df)

    render_section_header(
        "Key Metrics",
        "High-level system health and volume indicators for the active filter context.",
    )
    render_kpi_row(filtered_df)
    st.markdown("<hr/>", unsafe_allow_html=True)

    render_intelligence_briefing_top(filtered_df)
    st.markdown("<hr/>", unsafe_allow_html=True)

    render_priority_articles(filtered_df, top_n=8)
    st.markdown("<hr/>", unsafe_allow_html=True)

    render_trend_section(filtered_df)
    st.markdown("<hr/>", unsafe_allow_html=True)

    render_country_analysis(filtered_df)
    st.markdown("<hr/>", unsafe_allow_html=True)

    render_articles_section(filtered_df)


if __name__ == "__main__":
    main()
