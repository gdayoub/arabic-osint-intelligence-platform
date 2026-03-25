"""Premium dark intelligence monitoring console — red/black command center aesthetic."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from html import escape

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import text

from src.config.logging_config import setup_logging
from src.database.db import get_db_session
from src.utils.text_utils import safe_truncate

setup_logging()
logger = logging.getLogger("dashboard")

st.set_page_config(
    page_title="OSINT Intelligence Console",
    page_icon="",
    layout="wide",
)


# ---------------------------------------------------------------------------
#  Design System Constants
# ---------------------------------------------------------------------------

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

# Chart color palette — red-dominant with neutrals
CHART_RED_SCALE = [
    "#DC2626", "#EF4444", "#F87171", "#FCA5A5",
    "#A3A3A3", "#737373", "#525252", "#404040",
]

ESCALATION_COLORS = {
    "high": "#DC2626",
    "medium": "#F59E0B",
    "low": "#22C55E",
    "unknown": "#525252",
}

SOURCE_COLORS = ["#DC2626", "#F5F5F5", "#737373", "#404040", "#991B1B"]


# ---------------------------------------------------------------------------
#  Global CSS — Design System
# ---------------------------------------------------------------------------

def inject_global_css() -> None:
    """Inject the full design system CSS."""
    st.markdown(
        """
        <style>
        /* ── Import Premium Fonts ─────────────────────────────── */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

        /* ── Design Tokens ────────────────────────────────────── */
        :root {
            --bg-base:       #0A0A0A;
            --bg-surface:    #111111;
            --bg-elevated:   #171717;
            --bg-overlay:    #1A1A1A;
            --border-subtle: #1F1F1F;
            --border-default:#262626;
            --border-strong: #404040;
            --text-primary:  #F5F5F5;
            --text-secondary:#A3A3A3;
            --text-tertiary: #737373;
            --text-muted:    #525252;
            --accent:        #DC2626;
            --accent-hover:  #EF4444;
            --accent-muted:  #991B1B;
            --accent-subtle: rgba(220,38,38,0.12);
            --success:       #22C55E;
            --warning:       #F59E0B;
            --danger:        #DC2626;
            --radius-sm:     6px;
            --radius-md:     8px;
            --radius-lg:     12px;
            --shadow-sm:     0 1px 2px rgba(0,0,0,0.4);
            --shadow-md:     0 4px 12px rgba(0,0,0,0.5);
            --shadow-lg:     0 8px 24px rgba(0,0,0,0.6);
            --font-sans:     'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            --font-mono:     'JetBrains Mono', 'Fira Code', monospace;
            --transition:    150ms cubic-bezier(0.4,0,0.2,1);
        }

        /* ── Base Reset ───────────────────────────────────────── */
        [data-testid="stAppViewContainer"] {
            background: var(--bg-base) !important;
            color: var(--text-primary);
            font-family: var(--font-sans);
        }

        [data-testid="stHeader"] {
            background: transparent !important;
        }

        [data-testid="stMainBlockContainer"] {
            max-width: 1340px;
            padding-top: 1rem;
        }

        /* ── Sidebar ──────────────────────────────────────────── */
        [data-testid="stSidebar"] {
            background: var(--bg-surface) !important;
            border-right: 1px solid var(--border-subtle) !important;
        }

        [data-testid="stSidebar"] > div:first-child {
            padding-top: 1.5rem;
        }

        [data-testid="stSidebar"] * {
            font-family: var(--font-sans) !important;
        }

        [data-testid="stSidebar"] .stMarkdown p,
        [data-testid="stSidebar"] label {
            color: var(--text-secondary) !important;
            font-size: 0.8rem !important;
            letter-spacing: 0.03em;
            text-transform: uppercase;
            font-weight: 500;
        }

        [data-testid="stSidebar"] .stTextInput input,
        [data-testid="stSidebar"] .stDateInput input,
        [data-testid="stSidebar"] div[data-baseweb="select"] > div {
            background: var(--bg-elevated) !important;
            border: 1px solid var(--border-default) !important;
            border-radius: var(--radius-sm) !important;
            color: var(--text-primary) !important;
            font-family: var(--font-sans) !important;
            font-size: 0.85rem !important;
            transition: border-color var(--transition);
        }

        [data-testid="stSidebar"] .stTextInput input:focus,
        [data-testid="stSidebar"] .stDateInput input:focus {
            border-color: var(--accent) !important;
            box-shadow: 0 0 0 1px var(--accent-muted) !important;
        }

        [data-testid="stSidebar"] div[data-baseweb="tag"] {
            background: var(--accent-subtle) !important;
            border: 1px solid var(--accent-muted) !important;
            border-radius: 4px !important;
        }

        [data-testid="stSidebar"] div[data-baseweb="tag"] span {
            color: var(--text-primary) !important;
            font-size: 0.78rem !important;
        }

        /* ── Typography ───────────────────────────────────────── */
        h1, h2, h3, h4 {
            font-family: var(--font-sans) !important;
            color: var(--text-primary) !important;
            font-weight: 600 !important;
            letter-spacing: -0.02em;
        }

        /* ── Scrollbar ────────────────────────────────────────── */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: var(--bg-base); }
        ::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

        /* ── Hero / Command Header ────────────────────────────── */
        .cmd-header {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-lg);
            padding: 1.5rem 1.75rem;
            margin-bottom: 1.25rem;
            position: relative;
            overflow: hidden;
        }

        .cmd-header::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 2px;
            background: linear-gradient(90deg, var(--accent), transparent 70%);
        }

        .cmd-header-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
        }

        .cmd-header-left {
            flex: 1;
        }

        .cmd-title {
            font-family: var(--font-sans);
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--text-primary);
            letter-spacing: -0.03em;
            margin: 0 0 0.35rem 0;
            line-height: 1.2;
        }

        .cmd-title-accent {
            color: var(--accent);
        }

        .cmd-subtitle {
            font-family: var(--font-sans);
            font-size: 0.85rem;
            color: var(--text-tertiary);
            line-height: 1.5;
            max-width: 600px;
        }

        .cmd-status {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            background: var(--bg-elevated);
            border: 1px solid var(--border-default);
            border-radius: var(--radius-sm);
            padding: 0.4rem 0.75rem;
            flex-shrink: 0;
        }

        .cmd-status-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--success);
            box-shadow: 0 0 6px rgba(34,197,94,0.4);
            animation: pulse-dot 2s ease-in-out infinite;
        }

        @keyframes pulse-dot {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .cmd-status-text {
            font-family: var(--font-mono);
            font-size: 0.72rem;
            color: var(--text-secondary);
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }

        /* ── Section Headers ──────────────────────────────────── */
        .section-header {
            margin: 1.5rem 0 0.75rem 0;
            padding: 0;
        }

        .section-title {
            font-family: var(--font-sans);
            font-size: 1.05rem;
            font-weight: 600;
            color: var(--text-primary);
            letter-spacing: -0.01em;
            margin: 0 0 0.2rem 0;
        }

        .section-subtitle {
            font-family: var(--font-sans);
            font-size: 0.78rem;
            color: var(--text-tertiary);
            line-height: 1.5;
        }

        /* ── KPI Metric Cards ─────────────────────────────────── */
        .kpi-card {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-md);
            padding: 1rem 1.1rem;
            min-height: 110px;
            transition: border-color var(--transition), transform var(--transition);
            position: relative;
            overflow: hidden;
        }

        .kpi-card:hover {
            border-color: var(--border-default);
            transform: translateY(-1px);
        }

        .kpi-card--danger {
            border-left: 2px solid var(--accent);
        }

        .kpi-card--danger .kpi-value {
            color: var(--accent-hover);
        }

        .kpi-tag {
            display: inline-block;
            font-family: var(--font-mono);
            font-size: 0.62rem;
            font-weight: 600;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--text-tertiary);
            background: var(--bg-elevated);
            border: 1px solid var(--border-default);
            border-radius: 3px;
            padding: 0.15rem 0.4rem;
            margin-bottom: 0.65rem;
        }

        .kpi-label {
            font-family: var(--font-sans);
            font-size: 0.78rem;
            font-weight: 500;
            color: var(--text-secondary);
            margin-bottom: 0.25rem;
        }

        .kpi-value {
            font-family: var(--font-mono);
            font-size: 1.85rem;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1.1;
            letter-spacing: -0.02em;
        }

        /* ── Briefing Panel ───────────────────────────────────── */
        .briefing-panel {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-md);
            padding: 1.1rem 1.25rem;
            position: relative;
        }

        .briefing-panel::before {
            content: '';
            position: absolute;
            left: 0; top: 0; bottom: 0;
            width: 2px;
            background: var(--accent);
            border-radius: var(--radius-md) 0 0 var(--radius-md);
        }

        .briefing-label {
            font-family: var(--font-mono);
            font-size: 0.65rem;
            font-weight: 600;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: var(--accent);
            margin-bottom: 0.65rem;
        }

        .briefing-row {
            display: flex;
            align-items: baseline;
            padding: 0.35rem 0;
            border-bottom: 1px solid var(--border-subtle);
        }

        .briefing-row:last-child {
            border-bottom: none;
        }

        .briefing-key {
            font-family: var(--font-sans);
            font-size: 0.82rem;
            color: var(--text-tertiary);
            min-width: 180px;
            flex-shrink: 0;
        }

        .briefing-val {
            font-family: var(--font-sans);
            font-size: 0.85rem;
            font-weight: 500;
            color: var(--text-primary);
        }

        .briefing-val--danger {
            color: var(--accent-hover);
            font-weight: 600;
        }

        /* ── Priority Article Cards ───────────────────────────── */
        .pri-card {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-md);
            padding: 1rem 1.1rem;
            margin-bottom: 0.65rem;
            transition: border-color var(--transition), transform var(--transition);
            position: relative;
        }

        .pri-card:hover {
            border-color: var(--border-default);
            transform: translateY(-1px);
        }

        .pri-card--high {
            border-left: 2px solid var(--accent);
        }

        .pri-card--medium {
            border-left: 2px solid var(--warning);
        }

        .pri-card--low {
            border-left: 2px solid var(--success);
        }

        .pri-rank {
            font-family: var(--font-mono);
            font-size: 0.65rem;
            font-weight: 600;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: 0.4rem;
        }

        .pri-rank-num {
            color: var(--accent);
        }

        .pri-score {
            color: var(--text-tertiary);
            margin-left: 0.5rem;
        }

        .pri-title {
            font-family: var(--font-sans);
            font-size: 0.92rem;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            margin-bottom: 0.4rem;
        }

        .pri-meta {
            font-family: var(--font-sans);
            font-size: 0.75rem;
            color: var(--text-tertiary);
            margin-bottom: 0.4rem;
        }

        .pri-meta-sep {
            color: var(--text-muted);
            margin: 0 0.35rem;
        }

        .pri-reason {
            font-family: var(--font-sans);
            font-size: 0.75rem;
            color: var(--text-tertiary);
            font-style: italic;
            margin-bottom: 0.4rem;
        }

        .pri-body {
            font-family: var(--font-sans);
            font-size: 0.82rem;
            color: var(--text-secondary);
            line-height: 1.5;
            margin-bottom: 0.45rem;
        }

        .pri-keywords {
            font-family: var(--font-mono);
            font-size: 0.7rem;
            color: var(--text-tertiary);
            margin-bottom: 0.35rem;
        }

        .pri-link a {
            font-family: var(--font-mono);
            font-size: 0.72rem;
            color: var(--accent) !important;
            text-decoration: none !important;
            letter-spacing: 0.02em;
            transition: color var(--transition);
        }

        .pri-link a:hover {
            color: var(--accent-hover) !important;
        }

        /* ── Badges / Tags ────────────────────────────────────── */
        .tag {
            display: inline-block;
            font-family: var(--font-mono);
            font-size: 0.65rem;
            font-weight: 500;
            letter-spacing: 0.04em;
            padding: 0.18rem 0.5rem;
            border-radius: 3px;
            margin-right: 0.3rem;
            margin-bottom: 0.3rem;
            text-transform: uppercase;
        }

        .tag--topic {
            background: var(--bg-elevated);
            border: 1px solid var(--border-default);
            color: var(--text-secondary);
        }

        .tag--high {
            background: rgba(220,38,38,0.15);
            border: 1px solid rgba(220,38,38,0.3);
            color: #FCA5A5;
        }

        .tag--medium {
            background: rgba(245,158,11,0.12);
            border: 1px solid rgba(245,158,11,0.25);
            color: #FCD34D;
        }

        .tag--low {
            background: rgba(34,197,94,0.12);
            border: 1px solid rgba(34,197,94,0.25);
            color: #86EFAC;
        }

        .tag--unknown {
            background: var(--bg-elevated);
            border: 1px solid var(--border-default);
            color: var(--text-tertiary);
        }

        /* ── Chart Panels ─────────────────────────────────────── */
        .chart-panel {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-md);
            padding: 0.5rem 0.6rem 0.25rem 0.6rem;
        }

        /* ── Article Cards (Explorer) ─────────────────────────── */
        .art-card {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-md);
            padding: 0.85rem 1rem;
            margin-bottom: 0.5rem;
            transition: border-color var(--transition);
        }

        .art-card:hover {
            border-color: var(--border-default);
        }

        .art-title {
            font-family: var(--font-sans);
            font-size: 0.88rem;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 0.3rem;
        }

        .art-meta {
            font-family: var(--font-sans);
            font-size: 0.75rem;
            color: var(--text-tertiary);
            margin-bottom: 0.25rem;
        }

        .art-preview {
            font-family: var(--font-sans);
            font-size: 0.82rem;
            color: var(--text-secondary);
            line-height: 1.5;
        }

        /* ── Data Table Override ───────────────────────────────── */
        [data-testid="stDataFrame"] {
            border: 1px solid var(--border-subtle) !important;
            border-radius: var(--radius-md) !important;
            overflow: hidden;
        }

        /* ── Expander Override ─────────────────────────────────── */
        [data-testid="stExpander"] {
            background: var(--bg-surface) !important;
            border: 1px solid var(--border-subtle) !important;
            border-radius: var(--radius-md) !important;
        }

        [data-testid="stExpander"] summary p {
            color: var(--text-primary) !important;
            font-family: var(--font-sans) !important;
            font-weight: 500;
            font-size: 0.85rem;
        }

        /* ── Divider ──────────────────────────────────────────── */
        .divider {
            height: 1px;
            background: var(--border-subtle);
            margin: 1.25rem 0;
            border: none;
        }

        /* ── Metric Override ──────────────────────────────────── */
        [data-testid="stMetric"] {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-md);
            padding: 0.75rem 0.85rem;
        }

        [data-testid="stMetric"] label {
            color: var(--text-tertiary) !important;
            font-size: 0.75rem !important;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        [data-testid="stMetric"] [data-testid="stMetricValue"] {
            font-family: var(--font-mono) !important;
            color: var(--text-primary) !important;
        }

        /* ── Sidebar Filter Title ─────────────────────────────── */
        .sidebar-brand {
            font-family: var(--font-sans);
            font-size: 0.95rem;
            font-weight: 700;
            color: var(--text-primary);
            letter-spacing: -0.02em;
            margin-bottom: 0.15rem;
        }

        .sidebar-brand-accent {
            color: var(--accent);
        }

        .sidebar-tagline {
            font-family: var(--font-mono);
            font-size: 0.62rem;
            color: var(--text-muted);
            letter-spacing: 0.06em;
            text-transform: uppercase;
            margin-bottom: 1.25rem;
        }

        .sidebar-divider {
            height: 1px;
            background: var(--border-subtle);
            margin: 0.85rem 0;
        }

        .sidebar-section-label {
            font-family: var(--font-mono);
            font-size: 0.62rem;
            font-weight: 600;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: 0.5rem;
        }

        /* ── Info/Warning Box Override ─────────────────────────── */
        [data-testid="stAlert"] {
            background: var(--bg-surface) !important;
            border: 1px solid var(--border-default) !important;
            border-radius: var(--radius-md) !important;
        }

        /* ── Select Box Override ───────────────────────────────── */
        div[data-baseweb="select"] > div {
            background: var(--bg-elevated) !important;
            border: 1px solid var(--border-default) !important;
            border-radius: var(--radius-sm) !important;
        }

        div[data-baseweb="popover"] > div {
            background: var(--bg-elevated) !important;
            border: 1px solid var(--border-default) !important;
        }

        /* ── Hide Streamlit branding ──────────────────────────── */
        #MainMenu { visibility: hidden; }
        footer { visibility: hidden; }
        header[data-testid="stHeader"] { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
#  Data Helpers (preserved logic)
# ---------------------------------------------------------------------------

def parse_keyword_matches(value: object) -> dict:
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
    if not payload:
        return "None captured"
    collected: list[str] = []
    for key in ("topic_matches", "escalation_matches"):
        block = payload.get(key, {})
        if isinstance(block, dict):
            for values in block.values():
                if isinstance(values, list):
                    collected.extend([str(v) for v in values if v])
    deduped = list(dict.fromkeys(collected))
    if not deduped:
        return "None captured"
    return ", ".join(deduped[:limit])


def keyword_match_count(payload: dict) -> int:
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


# ---------------------------------------------------------------------------
#  Data Loading
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_article_dataframe() -> pd.DataFrame:
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
                "raw_article_id", "source", "title", "body", "author", "url",
                "source_section", "collected_at", "published_date",
                "processed_article_id", "topic", "sentiment_or_escalation",
                "country_guess", "keyword_matches", "processed_at",
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


# ---------------------------------------------------------------------------
#  Chart Styling
# ---------------------------------------------------------------------------

def style_figure(fig, title: str = "") -> None:
    """Apply premium dark theme to Plotly figures."""
    fig.update_layout(
        title=dict(
            text=title,
            font=dict(family="Inter, sans-serif", size=14, color="#A3A3A3"),
            x=0.02,
            y=0.97,
        ) if title else None,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color="#737373", size=11),
        margin=dict(l=12, r=12, t=40 if title else 12, b=12),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            borderwidth=0,
            font=dict(color="#737373", size=10, family="Inter, sans-serif"),
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
        xaxis=dict(
            gridcolor="rgba(38,38,38,0.6)",
            zerolinecolor="rgba(38,38,38,0.6)",
            tickfont=dict(size=10, color="#525252"),
        ),
        yaxis=dict(
            gridcolor="rgba(38,38,38,0.6)",
            zerolinecolor="rgba(38,38,38,0.6)",
            tickfont=dict(size=10, color="#525252"),
        ),
        hoverlabel=dict(
            bgcolor="#171717",
            bordercolor="#262626",
            font=dict(color="#F5F5F5", size=12, family="Inter, sans-serif"),
        ),
    )


# ---------------------------------------------------------------------------
#  UI Components
# ---------------------------------------------------------------------------

def render_header() -> None:
    st.markdown(
        """
        <div class="cmd-header">
          <div class="cmd-header-row">
            <div class="cmd-header-left">
              <div class="cmd-title">
                OSINT <span class="cmd-title-accent">Intelligence</span> Console
              </div>
              <div class="cmd-subtitle">
                Real-time monitoring of Arabic-language news sources. Classified by topic, escalation risk, and geographic signal.
              </div>
            </div>
            <div class="cmd-status">
              <div class="cmd-status-dot"></div>
              <span class="cmd-status-text">System Active</span>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_header(title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="section-header">
          <div class="section-title">{title}</div>
          <div class="section-subtitle">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _escalation_tag(level: str) -> str:
    """Return an HTML tag for escalation level."""
    lvl = level.lower()
    cls = f"tag--{lvl}" if lvl in ("high", "medium", "low") else "tag--unknown"
    return f'<span class="tag {cls}">{escape(level)}</span>'


def _topic_tag(topic: str) -> str:
    return f'<span class="tag tag--topic">{escape(topic)}</span>'


# ---------------------------------------------------------------------------
#  Sidebar Filters
# ---------------------------------------------------------------------------

def apply_sidebar_filters(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    with st.sidebar:
        st.markdown(
            '<div class="sidebar-brand">OSINT <span class="sidebar-brand-accent">Console</span></div>'
            '<div class="sidebar-tagline">Intelligence Filters</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="sidebar-section-label">Sources</div>', unsafe_allow_html=True)
        sources = sorted(df["source"].dropna().unique().tolist())
        selected_sources = st.multiselect("Source", sources, default=sources, label_visibility="collapsed")

        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-section-label">Classification</div>', unsafe_allow_html=True)
        topics = sorted(df["topic"].dropna().unique().tolist())
        selected_topics = st.multiselect("Topic", topics, default=topics, label_visibility="collapsed")

        escalations = sorted(df["sentiment_or_escalation"].dropna().unique().tolist())
        selected_escalations = st.multiselect("Escalation", escalations, default=escalations, label_visibility="collapsed")

        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-section-label">Geography</div>', unsafe_allow_html=True)
        countries = sorted(df["country_guess"].dropna().unique().tolist())
        selected_countries = st.multiselect("Country", countries, default=countries, label_visibility="collapsed")

        sections = sorted(df["source_section"].dropna().unique().tolist())
        if sections:
            selected_sections = st.multiselect("Section", sections, default=sections, label_visibility="collapsed")
        else:
            selected_sections = []

        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-section-label">Timeframe</div>', unsafe_allow_html=True)
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
            label_visibility="collapsed",
        )

        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-section-label">Search</div>', unsafe_allow_html=True)
        keyword = st.text_input("Keyword", placeholder="Search articles...", label_visibility="collapsed")

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
                | filtered["country_guess"].fillna("").str.contains(pattern, case=False, regex=False)
            ]

    return filtered.sort_values("analysis_date", ascending=False)


# ---------------------------------------------------------------------------
#  KPI Row
# ---------------------------------------------------------------------------

def render_kpi_row(filtered_df: pd.DataFrame) -> None:
    raw_count = int(filtered_df["raw_article_id"].nunique())
    processed_count = int(filtered_df["processed_article_id"].dropna().nunique())
    source_count = int(filtered_df["source"].nunique())
    high_count = int((filtered_df["sentiment_or_escalation"].str.lower() == "high").sum())

    kpis = [
        ("INGEST", "Total Articles", raw_count, ""),
        ("PROC", "Processed", processed_count, ""),
        ("SRC", "Active Sources", source_count, ""),
        ("ALERT", "High Escalation", high_count, " kpi-card--danger"),
    ]

    cols = st.columns(4)
    for col, (tag, label, value, extra_cls) in zip(cols, kpis):
        with col:
            st.markdown(
                f"""
                <div class="kpi-card{extra_cls}">
                  <div class="kpi-tag">{tag}</div>
                  <div class="kpi-label">{label}</div>
                  <div class="kpi-value">{value:,}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
#  Intelligence Briefing
# ---------------------------------------------------------------------------

def generate_intelligence_briefing(filtered_df: pd.DataFrame) -> dict[str, object]:
    if filtered_df.empty:
        return {
            "top_topic": "N/A", "top_country": "N/A",
            "high_escalation_count": 0, "most_active_source": "N/A",
            "new_articles_24h": 0, "window_anchor": "N/A",
        }

    top_topic = filtered_df["topic"].value_counts().idxmax()
    country_series = filtered_df["country_guess"].replace("Unknown", pd.NA).dropna()
    top_country = country_series.value_counts().idxmax() if not country_series.empty else "N/A"
    high_escalation_count = int((filtered_df["sentiment_or_escalation"].str.lower() == "high").sum())
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


def render_intelligence_briefing(filtered_df: pd.DataFrame) -> None:
    render_section_header(
        "Intelligence Briefing",
        "Dominant signals across the current filter context.",
    )

    if filtered_df.empty:
        st.info("No records available for the selected filters.")
        return

    b = generate_intelligence_briefing(filtered_df)
    danger_cls = ' briefing-val--danger' if b['high_escalation_count'] > 0 else ''

    st.markdown(
        f"""
        <div class="briefing-panel">
          <div class="briefing-label">Analyst Snapshot</div>
          <div class="briefing-row">
            <span class="briefing-key">Dominant Topic</span>
            <span class="briefing-val">{escape(str(b['top_topic']))}</span>
          </div>
          <div class="briefing-row">
            <span class="briefing-key">Top Country</span>
            <span class="briefing-val">{escape(str(b['top_country']))}</span>
          </div>
          <div class="briefing-row">
            <span class="briefing-key">High Escalation Count</span>
            <span class="briefing-val{danger_cls}">{b['high_escalation_count']}</span>
          </div>
          <div class="briefing-row">
            <span class="briefing-key">Most Active Source</span>
            <span class="briefing-val">{escape(str(b['most_active_source']))}</span>
          </div>
          <div class="briefing-row">
            <span class="briefing-key">New (24h Window)</span>
            <span class="briefing-val">{b['new_articles_24h']}</span>
          </div>
          <div class="briefing-row">
            <span class="briefing-key">Window Anchor</span>
            <span class="briefing-val" style="font-family:var(--font-mono);font-size:0.78rem;">{escape(str(b['window_anchor']))}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
#  Priority Articles
# ---------------------------------------------------------------------------

def compute_priority_scores(df: pd.DataFrame) -> pd.DataFrame:
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
    return ranked.sort_values(["priority_score", "analysis_date"], ascending=[False, False])


def build_priority_reason(row: pd.Series) -> str:
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
        reasons.append("very recent")
    if int(row.get("keyword_match_count", 0)) >= 3:
        reasons.append("multiple indicators")
    if not reasons:
        reasons.append("contextual relevance")
    return "; ".join(reasons)


def render_priority_articles(filtered_df: pd.DataFrame, top_n: int = 8) -> None:
    render_section_header(
        "Priority Intelligence",
        "Ranked by escalation severity, recency, strategic topic weight, and indicator density.",
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
            title = escape(safe_truncate(str(row.get("title", "N/A")), 110))
            source = escape(str(row.get("source", "N/A")))
            topic = str(row.get("topic", "Uncategorized"))
            escalation = str(row.get("sentiment_or_escalation", "unknown"))
            country = escape(str(row.get("country_guess", "Unknown")))
            published = pd.to_datetime(row.get("analysis_date"), errors="coerce")
            pub_label = published.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(published) else "N/A"
            reason = escape(str(row.get("priority_reason", "contextual relevance")))
            preview = escape(safe_truncate(str(row.get("body", "")), 220))
            url = escape(str(row.get("url", "#")))
            matched_kw = escape(flatten_keyword_matches(row.get("keyword_matches", {})))
            score = row.get("priority_score", 0)

            esc_lower = escalation.lower()
            card_cls = f"pri-card--{esc_lower}" if esc_lower in ("high", "medium", "low") else ""

            st.markdown(
                f"""
                <div class="pri-card {card_cls}">
                  <div class="pri-rank">
                    <span class="pri-rank-num">#{idx}</span>
                    <span class="pri-score">Score {score:.1f}</span>
                  </div>
                  <div class="pri-title">{title}</div>
                  <div class="pri-meta">
                    {source}<span class="pri-meta-sep">/</span>{pub_label}<span class="pri-meta-sep">/</span>{country}
                  </div>
                  {_topic_tag(topic)} {_escalation_tag(escalation)}
                  <div class="pri-reason">{reason}</div>
                  <div class="pri-body">{preview}</div>
                  <div class="pri-keywords">Keywords: {matched_kw}</div>
                  <div class="pri-link"><a href="{url}" target="_blank">View source article &rarr;</a></div>
                </div>
                """,
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
#  Trend Charts
# ---------------------------------------------------------------------------

def render_trend_section(filtered_df: pd.DataFrame) -> None:
    render_section_header(
        "Trend Monitoring",
        "Temporal topic shifts, escalation trajectories, source output, and geographic concentration.",
    )

    if filtered_df.empty:
        st.info("No records match the current filters.")
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

    # Row 1: Topic + Escalation over time
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="chart-panel">', unsafe_allow_html=True)
        fig = px.line(
            topic_time_df, x="day", y="count", color="topic",
            markers=True,
            color_discrete_sequence=CHART_RED_SCALE,
        )
        style_figure(fig, "Topic Frequency")
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="chart-panel">', unsafe_allow_html=True)
        esc_color_map = {k: v for k, v in ESCALATION_COLORS.items()}
        fig = px.area(
            escalation_time_df, x="day", y="count",
            color="sentiment_or_escalation",
            color_discrete_map=esc_color_map,
        )
        style_figure(fig, "Escalation Trajectory")
        fig.update_traces(line=dict(width=1.5))
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    # Row 2: Source + Country
    c3, c4 = st.columns(2)
    with c3:
        st.markdown('<div class="chart-panel">', unsafe_allow_html=True)
        fig = px.bar(
            source_df, x="source", y="count", color="source",
            color_discrete_sequence=SOURCE_COLORS,
        )
        style_figure(fig, "Source Volume")
        fig.update_layout(showlegend=False)
        fig.update_traces(marker_line_width=0)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with c4:
        st.markdown('<div class="chart-panel">', unsafe_allow_html=True)
        if country_df.empty:
            placeholder = pd.DataFrame({"country_guess": ["N/A"], "count": [0]})
            fig = px.bar(placeholder, x="country_guess", y="count")
        else:
            fig = px.bar(
                country_df.head(10), x="country_guess", y="count",
                color="count",
                color_continuous_scale=[[0, "#262626"], [0.5, "#991B1B"], [1, "#DC2626"]],
            )
            fig.update_layout(coloraxis_showscale=False)
        style_figure(fig, "Country Distribution")
        fig.update_layout(showlegend=False)
        fig.update_traces(marker_line_width=0)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
#  Country Analysis
# ---------------------------------------------------------------------------

def render_country_analysis(filtered_df: pd.DataFrame) -> None:
    render_section_header(
        "Country Analysis",
        "Country-level concentration, escalation pressure, and deep-dive by selected region.",
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
        st.markdown('<div class="chart-panel">', unsafe_allow_html=True)
        if country_df.empty:
            fig = px.bar(pd.DataFrame({"country_guess": ["N/A"], "article_count": [0]}), x="country_guess", y="article_count")
        else:
            fig = px.bar(
                country_df.head(10), x="country_guess", y="article_count",
                color="article_count",
                color_continuous_scale=[[0, "#262626"], [0.5, "#525252"], [1, "#A3A3A3"]],
            )
            fig.update_layout(coloraxis_showscale=False)
        style_figure(fig, "Top Countries")
        fig.update_layout(showlegend=False)
        fig.update_traces(marker_line_width=0)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="chart-panel">', unsafe_allow_html=True)
        if high_country_df.empty:
            fig = px.bar(pd.DataFrame({"country_guess": ["N/A"], "high_escalation_count": [0]}), x="country_guess", y="high_escalation_count")
        else:
            fig = px.bar(
                high_country_df.head(10), x="country_guess", y="high_escalation_count",
                color="high_escalation_count",
                color_continuous_scale=[[0, "#404040"], [0.5, "#991B1B"], [1, "#DC2626"]],
            )
            fig.update_layout(coloraxis_showscale=False)
        style_figure(fig, "High-Escalation by Country")
        fig.update_layout(showlegend=False)
        fig.update_traces(marker_line_width=0)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    # Country deep-dive
    focus_options = ["All Countries"] + country_df["country_guess"].tolist()
    selected_country = st.selectbox("Country Deep Dive", options=focus_options, index=0)

    scoped = (
        filtered_df if selected_country == "All Countries"
        else filtered_df[filtered_df["country_guess"] == selected_country]
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
        ["analysis_date", "source", "title", "topic", "sentiment_or_escalation", "country_guess"]
    ].copy()
    country_table["analysis_date"] = pd.to_datetime(
        country_table["analysis_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d %H:%M")
    country_table["title"] = country_table["title"].map(lambda v: safe_truncate(str(v), 100))
    country_table = country_table.rename(columns={
        "analysis_date": "Date", "source": "Source", "title": "Title",
        "topic": "Topic", "sentiment_or_escalation": "Escalation", "country_guess": "Country",
    })
    st.dataframe(country_table.head(20), use_container_width=True, hide_index=True, height=250)


# ---------------------------------------------------------------------------
#  Articles Explorer
# ---------------------------------------------------------------------------

def render_articles_section(filtered_df: pd.DataFrame) -> None:
    render_section_header(
        "Article Explorer",
        "Browse records with classification, escalation, and keyword-match detail.",
    )

    if filtered_df.empty:
        st.info("No article records available for the active filters.")
        return

    table_df = filtered_df[
        ["analysis_date", "source", "topic", "sentiment_or_escalation", "country_guess", "title", "url"]
    ].copy()
    table_df = table_df.rename(columns={
        "analysis_date": "Date", "source": "Source", "topic": "Topic",
        "sentiment_or_escalation": "Escalation", "country_guess": "Country",
        "title": "Title", "url": "URL",
    })
    table_df["Date"] = pd.to_datetime(table_df["Date"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
    table_df["Title"] = table_df["Title"].map(lambda x: safe_truncate(str(x), 130))
    st.dataframe(table_df.head(80), use_container_width=True, hide_index=True, height=300)

    st.markdown('<div class="section-header"><div class="section-title">Article Detail</div></div>', unsafe_allow_html=True)
    for _, row in filtered_df.head(14).iterrows():
        source = row.get("source", "N/A")
        title = safe_truncate(str(row.get("title", "")), 100)
        with st.expander(f"{source}  /  {title}"):
            st.markdown(f"**Title:** {row.get('title', 'N/A')}")
            st.markdown(f"**Source:** {source}")
            st.markdown(f"**Published:** {row.get('published_date', row.get('analysis_date', 'N/A'))}")
            st.markdown(f"**Topic:** {row.get('topic', 'Uncategorized')}")
            st.markdown(f"**Escalation:** {row.get('sentiment_or_escalation', 'unknown')}")
            st.markdown(f"**Country:** {row.get('country_guess', 'Unknown')}")
            st.markdown(f"**Keywords:** {flatten_keyword_matches(row.get('keyword_matches', {}), limit=12)}")
            st.markdown(f"**URL:** {row.get('url', 'N/A')}")
            st.markdown(f"**Preview:** {safe_truncate(str(row.get('body', '')), 950)}")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main() -> None:
    inject_global_css()
    df = load_article_dataframe()
    render_header()

    if df.empty:
        st.warning(
            "No data available. Run `python3 main.py ingest` and `python3 main.py process` first."
        )
        return

    filtered_df = apply_sidebar_filters(df)

    render_section_header("Key Metrics", "System health and volume indicators for the active filter context.")
    render_kpi_row(filtered_df)
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    render_intelligence_briefing(filtered_df)
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    render_priority_articles(filtered_df, top_n=8)
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    render_trend_section(filtered_df)
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    render_country_analysis(filtered_df)
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    render_articles_section(filtered_df)


if __name__ == "__main__":
    main()
